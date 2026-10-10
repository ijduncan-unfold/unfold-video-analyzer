"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { createHost, timebase, prepareCues, pathKey, MARKER_NAMESPACE, markerMap } = require("../host");
const technical = { frame_rate: 24, nominal_frame_rate_fraction: "24/1", nominal_frame_rate_verified: true, frame_rate_verified_frames: 240, duration_seconds: 10 };
const binding = { host: "resolve", project_id: "project-1", item_id: "clip-1", media_path: "C:\\Media\\original.mp4", source_fps: 24 };
const cues = [{ start: 1, end: 2, name: "Shot 01", text: "A centered circle" }, { start: 3, end: 4, name: "Shot 02", text: "A person at right" }];
const noMarkerResponse = "GetMarkerCustomData: Error response (errorStr:No marker exists at the input frameId argument) (errorCode:2)";
function mock(options = {}) {
  const properties = { "Clip Name": "Original", "File Path": binding.media_path, FPS: "24", Frames: "240", Start: "0", ...options.properties };
  const markers = { ...options.markers }, events = [];
  let selected = [], currentProject = "project-1", writeCount = 0, reads = 0;
  const item = { async GetUniqueId() { return "clip-1"; }, async GetClipProperty() { return { ...properties }; }, async GetMarkers() {
    reads++; if (options.onRead) options.onRead({ reads, markers });
    if (options.invalidReadAfterWrite && writeCount) return null;
    const result = structuredClone(markers);
    return Object.fromEntries(Object.entries(result).map(([key, value]) => {
      const nativeKey = Number(key) + (options.dictionaryOffset || 0);
      return [options.decimalKeys ? nativeKey.toFixed(6) : String(nativeKey), value];
    }));
  },
    async GetMarkerCustomData(frame) {
      if (options.frameReadError) throw new Error(options.frameReadError);
      if (!Object.hasOwn(markers, frame)) throw new Error(noMarkerResponse);
      return markers[frame].customData;
    },
    async AddMarker(frame, color, name, note, duration, customData) {
      writeCount++; events.push(["add", frame]);
      if (options.failure === writeCount) return false;
      markers[frame] = { color, name, note, duration, customData };
      if (options.afterWrite) options.afterWrite({ writeCount, properties, markers, setProject(id) { currentProject = id; } });
      if (options.lostAcknowledgement === writeCount) throw new Error("Native acknowledgement lost");
      return true;
    },
    async DeleteMarkerByCustomData(customData) {
      const entry = Object.entries(markers).find(([, marker]) => marker.customData === customData);
      if (!entry) return false;
      const frame = Number(entry[0]); events.push(["delete", frame]);
      if (options.rollbackFailure) return false;
      if (!options.falseDeleteSuccess) delete markers[frame];
      return true;
    },
  };
  const nested = { async GetClipList() { return [item]; }, async GetSubFolderList() { return []; } };
  const root = { async GetClipList() { return []; }, async GetSubFolderList() { return [nested]; } };
  const pool = { async GetSelectedClips() { return selected; }, async SetSelectedClip(value) { if (!options.selectionFailure) selected = [value]; return true; }, async GetRootFolder() { return root; } };
  const project = { async GetUniqueId() { return currentProject; }, async GetName() { return "Film"; }, async GetMediaPool() { return pool; } };
  const resolve = { async GetProjectManager() { return { async GetCurrentProject() { return options.noProject ? null : project; } }; } };
  return { host: createHost(resolve), markers, properties, events, item, pool, select() { selected = [item]; }, setProject(id) { currentProject = id; } };
}
test("Promise host returns stable project and original Media Pool selection", async () => {
  const m = mock(); m.select(); const result = await m.host.getSelection();
  assert.equal(result.project_id, "project-1"); assert.equal(result.project_name, "Film"); assert.equal(result.clips[0].item_id, "clip-1"); assert.equal(result.clips[0].source_fps, 24); assert.equal(result.clips[0].source_frames, 240); assert.equal(result.capabilities.source_seek, false);
});
test("missing current project returns an actionable error", async () => { await assert.rejects(mock({ noProject: true }).host.getSelection(), { code: "NO_PROJECT" }); });
test("missing stable project API never substitutes project name", async () => { const host = createHost({ GetProjectManager: async () => ({ GetCurrentProject: async () => ({ GetName: async () => "Film" }) }) }); await assert.rejects(host.getSelection(), { code: "UNSUPPORTED_API" }); });
test("timeline, generator and unavailable paths are warnings rather than imports", async () => { const m = mock({ properties: { "File Path": "" } }); m.select(); const result = await m.host.getSelection(); assert.equal(result.clips.length, 0); assert.match(result.warnings[0], /original video/); });
test("source selection is confirmed and never claims viewer positioning", async () => { const m = mock(); assert.deepEqual(await m.host.selectClip(binding), { selected: true, source_seek: false }); });
test("unconfirmed native selection is reported", async () => { await assert.rejects(mock({ selectionFailure: true }).host.selectClip(binding), { code: "SELECT_VERIFY_FAILED" }); });
test("a switched project cannot receive selection or marker actions", async () => { const m = mock(); m.setProject("other"); await assert.rejects(m.host.selectClip(binding), { code: "PROJECT_CHANGED" }); await assert.rejects(m.host.addMarkers(binding, technical, cues), { code: "PROJECT_CHANGED" }); assert.equal(m.events.length, 0); });
test("relinked clip path blocks native actions", async () => { const m = mock({ properties: { "File Path": "C:\\Media\\different.mp4" } }); await assert.rejects(m.host.selectClip(binding), { code: "MEDIA_CHANGED" }); });
test("interpreted clip frame rate blocks native actions", async () => { const m = mock({ properties: { FPS: "25" } }); await assert.rejects(m.host.selectClip(binding), { code: "SOURCE_TIMEBASE_CHANGED" }); });
test("cross-host bindings cannot touch Resolve", async () => { const m = mock(); await assert.rejects(m.host.selectClip({ ...binding, host: "premiere" }), { code: "INVALID_BINDING" }); });
test("Windows paths are normalized but Mac paths retain case and literal backslashes", () => { assert.equal(pathKey("C:\\MEDIA\\a.mp4"), pathKey("c:/media/a.mp4")); assert.notEqual(pathKey("/Volumes/Film/A.mp4"), pathKey("/Volumes/film/A.mp4")); assert.notEqual(pathKey("/Volumes/Film/a\\b.mp4"), pathKey("/Volumes/Film/a/b.mp4")); });
test("Mac volumes and Unicode filenames preserve source identity", async () => { const path = "/Volumes/Footage/Été/scene \\ one.mp4"; const m = mock({ properties: { "File Path": path } }); await m.host.selectClip({ ...binding, media_path: path }); });
test("verified original source receives Blue clip markers in source frames", async () => { const m = mock(); assert.deepEqual(await m.host.addMarkers(binding, technical, cues), { added: 2, duplicates: 0, collisions: 0 }); assert.equal(m.markers[24].duration, 24); assert.equal(m.markers[72].color, "Blue"); assert.ok(m.markers[24].customData.startsWith(MARKER_NAMESPACE)); });
test("same cues do not duplicate markers", async () => { const m = mock(); await m.host.addMarkers(binding, technical, cues); assert.deepEqual(await m.host.addMarkers(binding, technical, cues), { added: 0, duplicates: 2, collisions: 0 }); });
test("occupied source frames preserve existing editor markers", async () => { const original = { name: "User marker", customData: "", duration: 1, note: "Keep" }; const m = mock({ markers: { 24: original } }); assert.deepEqual(await m.host.addMarkers(binding, technical, cues), { added: 1, duplicates: 0, collisions: 1 }); assert.deepEqual(m.markers[24], original); });
test("multiple cues sharing one frame skip the collision", async () => { const m = mock(); assert.deepEqual(await m.host.addMarkers(binding, technical, [cues[0], { ...cues[1], start: 1 }]), { added: 1, duplicates: 0, collisions: 1 }); });
test("partial marker failure rolls back only our confirmed writes", async () => { const original = { name: "User", customData: "mine", duration: 1, note: "Keep" }; const m = mock({ failure: 2, markers: { 5: original } }); await assert.rejects(m.host.addMarkers(binding, technical, cues), { code: "MARKER_WRITE_FAILED" }); assert.deepEqual(m.markers, { 5: original }); assert.deepEqual(m.events, [["add", 24], ["add", 72], ["delete", 24]]); });
test("lost write acknowledgement still rolls back matching customData", async () => { const m = mock({ lostAcknowledgement: 1 }); await assert.rejects(m.host.addMarkers(binding, technical, cues), /acknowledgement lost/); assert.deepEqual(m.markers, {}); });
test("rollback failure reports retained marker count", async () => { const m = mock({ failure: 2, rollbackFailure: true }); await assert.rejects(m.host.addMarkers(binding, technical, cues), { code: "MARKER_ROLLBACK_INCOMPLETE" }); assert.equal(Object.keys(m.markers).length, 1); });
test("relink during marker batch aborts and rolls back prior writes", async () => { const m = mock({ afterWrite({ writeCount, properties }) { if (writeCount === 1) properties["File Path"] = "C:\\new.mp4"; } }); await assert.rejects(m.host.addMarkers(binding, technical, cues), { code: "MEDIA_CHANGED" }); assert.deepEqual(m.markers, {}); });
test("frame-rate reinterpretation during batch aborts and rolls back", async () => { const m = mock({ afterWrite({ writeCount, properties }) { if (writeCount === 1) properties.FPS = "25"; } }); await assert.rejects(m.host.addMarkers(binding, technical, cues), { code: "SOURCE_TIMEBASE_CHANGED" }); assert.deepEqual(m.markers, {}); });
test("project switch during marker batch aborts and rolls back", async () => { const m = mock({ afterWrite({ writeCount, setProject }) { if (writeCount === 1) setProject("other"); } }); await assert.rejects(m.host.addMarkers(binding, technical, cues), { code: "PROJECT_CHANGED" }); assert.deepEqual(m.markers, {}); });
test("concurrent edits to a marker are preserved during failed rollback", async () => { const m = mock({ failure: 2, afterWrite({ markers }) { markers[24].name = "User edited name"; } }); await assert.rejects(m.host.addMarkers(binding, technical, cues), { code: "MARKER_ROLLBACK_INCOMPLETE" }); assert.equal(m.markers[24].name, "User edited name"); });
test("variable-rate and indeterminate source counts cannot receive markers", async () => { for (const patch of [{ nominal_frame_rate_verified: false }, { frame_rate_verified_frames: null }, { nominal_frame_rate_fraction: "0/0" }]) { const m = mock(); await assert.rejects(m.host.addMarkers(binding, { ...technical, ...patch }, cues), { code: "UNVERIFIED_TIMEBASE" }); assert.equal(m.events.length, 0); } });
test("trimmed or subclip sources are blocked when Start or frame count differs", async () => { for (const properties of [{ Start: "10" }, { Start: undefined }, { Frames: "120" }]) { const m = mock({ properties }); await assert.rejects(m.host.addMarkers(binding, technical, cues), error => ["UNVERIFIED_TIMEBASE", "SOURCE_TIMEBASE_CHANGED"].includes(error.code)); assert.equal(m.events.length, 0); } });
test("fractional source rates use exact probed fraction, accepting rounded Resolve FPS", () => { const timing = timebase({ source_fps: 23.976, source_frames: 240, source_start: 0 }, { ...technical, nominal_frame_rate_fraction: "24000/1001" }); const [cue] = prepareCues([{ start: 1.001, end: 2.002, name: "Match", text: "" }], timing); assert.equal(cue.frame, 24); assert.equal(cue.duration, 24); });
test("cue bounds and count limits are validated before native writes", async () => { for (const invalid of [[{ ...cues[0], start: -1 }], [{ ...cues[0], end: 11 }], [{ ...cues[0], end: 1 }], new Array(501).fill(cues[0])]) { const m = mock(); await assert.rejects(m.host.addMarkers(binding, technical, invalid)); assert.equal(m.events.length, 0); } });
test("floating native dictionary keys verify successful marker writes and duplicates", async () => { const m = mock({ decimalKeys: true }); assert.deepEqual(await m.host.addMarkers(binding, technical, cues), { added: 2, duplicates: 0, collisions: 0 }); assert.deepEqual(await m.host.addMarkers(binding, technical, cues), { added: 0, duplicates: 2, collisions: 0 }); assert.equal(Object.keys(m.markers).length, 2); });
test("floating frame keys preserve pre-existing and newly occupied marker frames", async () => {
  const existing = { color: "Red", name: "Existing", note: "Keep", duration: 1, customData: "" };
  const m = mock({ decimalKeys: true, markers: { 24: existing }, onRead({ reads, markers }) { if (reads === 2) markers[72] = { ...existing, name: "Concurrent" }; } });
  assert.deepEqual(await m.host.addMarkers(binding, technical, cues), { added: 0, duplicates: 0, collisions: 2 }); assert.equal(m.events.length, 0); assert.equal(m.markers[72].name, "Concurrent");
});
test("floating frame keys roll back prior writes after a partial native failure", async () => { const m = mock({ decimalKeys: true, failure: 2 }); await assert.rejects(m.host.addMarkers(binding, technical, cues), { code: "MARKER_WRITE_FAILED" }); assert.deepEqual(m.markers, {}); assert.ok(m.events.some(([name, frame]) => name === "delete" && frame === 24)); });
test("unreadable native dictionaries report incomplete rollback rather than claiming cleanup", async () => { const m = mock({ invalidReadAfterWrite: true }); await assert.rejects(m.host.addMarkers(binding, technical, cues), { code: "MARKER_ROLLBACK_INCOMPLETE" }); assert.equal(Object.keys(m.markers).length, 1); });
test("rollback verifies deletion rather than trusting a true native return alone", async () => { const m = mock({ failure: 2, falseDeleteSuccess: true }); await assert.rejects(m.host.addMarkers(binding, technical, cues), { code: "MARKER_ROLLBACK_INCOMPLETE" }); assert.equal(Object.keys(m.markers).length, 1); });
test("ambiguous or invalid native record keys fail safely without rejecting shifted keys", () => { for (const value of [{ "24": {}, "24.0": {} }, { arbitrary: {} }, { "Infinity": {} }, { "1": null }, []]) assert.throws(() => markerMap(value), { code: "MARKER_READ_FAILED" }); assert.equal(markerMap({ "24.0": { name: "Marker" } }).get(24).name, "Marker"); assert.equal(markerMap({ "-1": { name: "Frame zero" } }).get(-1).name, "Frame zero"); });

test("native shifted dictionary keys verify source frame zero and prevent duplicates", async () => {
  const nativeCues = [{ start: 0, end: 2, name: "Shot 01", text: "Synthetic red" }, { start: 2, end: 4, name: "Shot 02", text: "Synthetic blue" }];
  const m = mock({ dictionaryOffset: -1 });
  assert.deepEqual(await m.host.addMarkers(binding, technical, nativeCues), { added: 2, duplicates: 0, collisions: 0 });
  const readback = await m.item.GetMarkers(); assert.equal(readback[-1].name, "Shot 01"); assert.equal(readback[47].name, "Shot 02");
  assert.deepEqual(await m.host.addMarkers(binding, technical, nativeCues), { added: 0, duplicates: 2, collisions: 0 });
  assert.equal(m.markers[0].duration, 48); assert.equal(m.markers[48].duration, 48);
});

test("native empty customData at a requested frame preserves a foreign manual marker", async () => {
  const original = { color: "Yellow", name: "QA Manual Marker", note: "", duration: 1, customData: "" };
  const m = mock({ dictionaryOffset: -1, markers: { 24: original } });
  assert.deepEqual(await m.host.addMarkers(binding, technical, cues), { added: 1, duplicates: 0, collisions: 1 });
  assert.deepEqual(m.markers[24], original); assert.equal(m.events.some(([action, frame]) => action === "add" && frame === 24), false);
});

test("a foreign marker at zero is preserved despite native dictionary key minus one", async () => {
  const original = { color: "Yellow", name: "User at start", note: "", duration: 1, customData: "" };
  const m = mock({ dictionaryOffset: -1, markers: { 0: original } });
  assert.deepEqual(await m.host.addMarkers(binding, technical, [{ ...cues[0], start: 0 }]), { added: 0, duplicates: 0, collisions: 1 });
  assert.deepEqual(m.markers[0], original); assert.deepEqual(m.events, []);
});

test("native shifted records roll back by exact customData and preserve unrelated markers", async () => {
  const original = { color: "Yellow", name: "Manual", note: "Keep", duration: 1, customData: "" };
  const m = mock({ dictionaryOffset: -1, failure: 2, markers: { 5: original } });
  await assert.rejects(m.host.addMarkers(binding, technical, [{ ...cues[0], start: 0 }, cues[1]]), { code: "MARKER_WRITE_FAILED" });
  assert.deepEqual(m.markers, { 5: original }); assert.deepEqual(m.events, [["add", 0], ["add", 72], ["delete", 0]]);
});

test("only the precise native no-marker exception permits a new marker", async () => {
  for (const frameReadError of ["GetMarkerCustomData: API timed out", "OtherMethod: " + noMarkerResponse, noMarkerResponse.replace("errorCode:2", "errorCode:3")]) {
    const m = mock({ frameReadError });
    await assert.rejects(m.host.addMarkers(binding, technical, cues), error => error.message === frameReadError);
    assert.deepEqual(m.events, []);
  }
});

test("an ambiguous direct frame result never counts as an empty source frame", async () => {
  for (const response of [null, undefined, false, {}]) {
    const m = mock(); m.item.GetMarkerCustomData = async () => response;
    await assert.rejects(m.host.addMarkers(binding, technical, cues), { code: "MARKER_READ_FAILED" }); assert.deepEqual(m.events, []);
  }
});

test("duplicated customData identities cannot be verified or deleted", async () => {
  const [cue] = prepareCues([cues[0]], { fps: 24, frames: 240 });
  const record = { color: "Blue", name: cue.name, note: cue.note, duration: cue.duration, customData: cue.key };
  const m = mock({ dictionaryOffset: -1, markers: { 24: record, 25: record } });
  await assert.rejects(m.host.addMarkers(binding, technical, cues), { code: "MARKER_READ_FAILED" }); assert.deepEqual(m.events, []);
});

test("moved owned markers are preserved instead of silently duplicated", async () => {
  const [cue] = prepareCues([cues[0]], { fps: 24, frames: 240 });
  const record = { color: "Blue", name: cue.name, note: cue.note, duration: cue.duration, customData: cue.key };
  const m = mock({ dictionaryOffset: -1, markers: { 25: record } });
  await assert.rejects(m.host.addMarkers(binding, technical, [cues[0]]), { code: "MARKER_VERIFY_FAILED" }); assert.deepEqual(m.events, []); assert.deepEqual(m.markers[25], record);
});

test("concurrent marker color changes are preserved during guarded rollback", async () => {
  const m = mock({ dictionaryOffset: -1, failure: 2, afterWrite({ markers }) { markers[24].color = "Yellow"; } });
  await assert.rejects(m.host.addMarkers(binding, technical, cues), { code: "MARKER_ROLLBACK_INCOMPLETE" }); assert.equal(m.markers[24].color, "Yellow");
});
