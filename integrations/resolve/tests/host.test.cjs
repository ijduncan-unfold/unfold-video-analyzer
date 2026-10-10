"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { createHost, timebase, prepareCues, pathKey, MARKER_NAMESPACE } = require("../host");
const technical = { frame_rate: 24, nominal_frame_rate_fraction: "24/1", nominal_frame_rate_verified: true, frame_rate_verified_frames: 240, duration_seconds: 10 };
const binding = { host: "resolve", project_id: "project-1", item_id: "clip-1", media_path: "C:\\Media\\original.mp4", source_fps: 24 };
const cues = [{ start: 1, end: 2, name: "Shot 01", text: "A centered circle" }, { start: 3, end: 4, name: "Shot 02", text: "A person at right" }];
function mock(options = {}) {
  const properties = { "Clip Name": "Original", "File Path": binding.media_path, FPS: "24", Frames: "240", Start: "0", ...options.properties };
  const markers = { ...options.markers }, events = [];
  let selected = [], currentProject = "project-1", writeCount = 0;
  const item = { async GetUniqueId() { return "clip-1"; }, async GetClipProperty() { return { ...properties }; }, async GetMarkers() { return structuredClone(markers); },
    async AddMarker(frame, color, name, note, duration, customData) {
      writeCount++; events.push(["add", frame]);
      if (options.failure === writeCount) return false;
      markers[frame] = { color, name, note, duration, customData };
      if (options.afterWrite) options.afterWrite({ writeCount, properties, markers, setProject(id) { currentProject = id; } });
      if (options.lostAcknowledgement === writeCount) throw new Error("Native acknowledgement lost");
      return true;
    },
    async DeleteMarkerAtFrame(frame) { events.push(["delete", frame]); if (options.rollbackFailure) return false; delete markers[frame]; return true; },
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
