"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { createHost, MAX_MARKERS } = require("../host.js");

function fixture() {
  const calls = [];
  const markers = [];
  let locked = false;
  let transacting = false;
  const clip = {
    name: "Camera A", kind: "clip", getId: () => "source-1",
    isSequence: async () => false, isOffline: async () => false,
    isMergedClip: async () => false, isMulticamClip: async () => false,
    getMediaFilePath: async () => "C:\\Footage\\camera-a.mp4",
    getContentType: async () => "MEDIA",
    getFootageInterpretation: async () => ({ getFrameRate: () => 24 }),
  };
  const root = { kind: "folder", getId: () => "root", getItems: async () => [clip] };
  const owner = {
    getMarkers: () => markers,
    createAddMarkerAction(name, type, start, duration, comments) {
      assert.equal(locked, true, "actions must be created under project lock");
      assert.equal(transacting, true, "actions must stay in transaction scope");
      calls.push(["marker-action", name, type, start.seconds, duration.seconds]);
      return { name, type, start, duration, comments };
    },
  };
  const project = {
    guid: { toString: () => "project-1" }, name: "Test edit", path: "C:\\Edits\\test.prproj",
    getRootItem: async () => root,
    lockedAccess(callback) {
      calls.push(["lock"]);
      locked = true;
      try { assert.equal(callback() instanceof Promise, false); }
      finally { locked = false; }
    },
    executeTransaction(callback, label) {
      assert.equal(locked, true);
      calls.push(["transaction", label]);
      const pending = [];
      transacting = true;
      try {
        assert.equal(callback({ addAction: action => { pending.push(action); return true; } }) instanceof Promise, false);
      } finally { transacting = false; }
      for (const action of pending) {
        markers.push({ getName: () => action.name, getComments: () => action.comments,
          getStart: () => action.start, getDuration: () => action.duration });
      }
      return true;
    },
  };
  let active = project;
  let selected = [clip];
  let monitorItem;
  let monitorPosition = { seconds: 0 };
  const api = {
    Project: { getActiveProject: async () => active },
    ProjectUtils: { getSelection: async () => ({ getItems: async () => selected }) },
    ClipProjectItem: { cast: item => item.kind === "clip" ? item : null },
    FolderItem: { cast: item => item.kind === "folder" ? item : null },
    Constants: { ContentType: { MEDIA: "MEDIA" } },
    TickTime: { createWithSeconds: seconds => ({ seconds }) },
    Marker: { MARKER_TYPE_COMMENT: "Comment" },
    Markers: { getMarkers: async (item) => { assert.equal(item, clip); return owner; } },
    SourceMonitor: {
      openProjectItem: async item => { calls.push(["open", item.getId()]); monitorItem = item; return true; },
      setPosition: async position => { calls.push(["seek", position.seconds]); monitorPosition = position; return true; },
      getPosition: async () => monitorPosition,
      getProjectItem: async () => monitorItem,
    },
  };
  return { host: createHost(api), api, project, clip, root, owner, calls, markers,
    binding: { project_id: "project-1", item_id: "source-1", media_path: "C:\\Footage\\camera-a.mp4" },
    select: value => { selected = value; }, setActive: value => { active = value; } };
}
function errorCode(code) { return error => error && error.code === code; }

test("Project selection returns stable identities and original path, without copying or touching timelines", async () => {
  const f = fixture();
  const result = await f.host.getSelection();
  assert.equal(result.project_id, "project-1");
  assert.equal(result.clips.length, 1);
  assert.deepEqual(result.clips[0], { item_id: "source-1", name: "Camera A", media_path: f.binding.media_path, source_fps: 24 });
  assert.equal(result.capabilities.source_seek, true);
  assert.equal(result.capabilities.timeline_selection, false);
  assert.deepEqual(f.calls, []);
});

test("Selected bins expand recursively, deduplicate and terminate cycles", async () => {
  const f = fixture();
  const folder = { kind: "folder", getId: () => "bin-1", getItems: async () => [f.clip, folder] };
  f.select([folder, f.clip]);
  assert.equal((await f.host.getSelection()).clips.length, 1);
});

test("Offline, nested, multicam and merged clips produce useful warnings", async t => {
  for (const method of ["isOffline", "isSequence", "isMulticamClip", "isMergedClip"]) {
    await t.test(method, async () => {
      const f = fixture();
      f.clip[method] = async () => true;
      const result = await f.host.getSelection();
      assert.equal(result.clips.length, 0);
      assert.equal(result.warnings.length, 1);
    });
  }
});

test("Timeline-only selection gives instructions instead of inventing source offsets", async () => {
  const f = fixture();
  f.select([]);
  f.project.getActiveSequence = async () => { throw new Error("timeline must not be read"); };
  const result = await f.host.getSelection();
  assert.equal(result.clips.length, 0);
  assert.match(result.warnings[0], /Project panel/);
});

test("Missing project and invalid identity fail usefully", async () => {
  const f = fixture();
  f.setActive(null);
  await assert.rejects(f.host.getSelection(), errorCode("NO_PROJECT"));
  f.setActive({ ...f.project, guid: {} });
  await assert.rejects(f.host.getProjectIdentity(), errorCode("INVALID_PROJECT"));
});

test("Project switches during selection are rejected", async () => {
  const f = fixture();
  f.clip.getMediaFilePath = async () => { f.setActive({ ...f.project, guid: "other-project" }); return f.binding.media_path; };
  await assert.rejects(f.host.getSelection(), errorCode("PROJECT_CHANGED"));
});

test("Source open seeks native monitor and verifies item and position", async () => {
  const f = fixture();
  const result = await f.host.openSource(f.binding, 12.75);
  assert.equal(result.opened, true);
  assert.equal(result.seconds, 12.75);
  assert.deepEqual(f.calls, [["open", "source-1"], ["seek", 12.75]]);
  assert.equal(f.markers.length, 0);
});

test("Source action rejects stale project and relinked media before native changes", async t => {
  await t.test("project", async () => {
    const f = fixture();
    f.setActive({ ...f.project, guid: "other" });
    await assert.rejects(f.host.openSource(f.binding, 1), errorCode("PROJECT_CHANGED"));
    assert.deepEqual(f.calls, []);
  });
  await t.test("media", async () => {
    const f = fixture();
    f.clip.getMediaFilePath = async () => "C:\\Other\\different.mp4";
    await assert.rejects(f.host.openSource(f.binding, 1), errorCode("MEDIA_CHANGED"));
    assert.deepEqual(f.calls, []);
  });
});

test("Windows paths match case and slash variations", async () => {
  const f = fixture();
  assert.equal((await f.host.openSource({ ...f.binding, media_path: "c:/footage/CAMERA-A.MP4" }, 1)).opened, true);
});

test("Windows UNC identity accepts slash and case variations", async () => {
  const f = fixture();
  f.clip.getMediaFilePath = async () => "\\\\MediaServer\\Film\\Camera A.mov";
  assert.equal((await f.host.openSource({ ...f.binding, media_path: "//mediaserver/film/camera a.mov" }, 1)).opened, true);
});

test("Mac volume paths preserve spaces, Unicode and native UUID identities", async () => {
  const f = fixture();
  const projectId = "d9f9c8b6-5f1c-4b4b-b3bb-17833e94d7d9";
  const itemId = "c6b15b87-b6d4-44d0-a0ca-9cda4f3d5b32";
  const mediaPath = "/Volumes/Footage/Cámara A/Take #1.mov";
  f.project.guid = { toString: () => projectId };
  f.project.path = "/Users/editor/Documents/Film edit.prproj";
  f.clip.getId = () => itemId;
  f.clip.getMediaFilePath = async () => mediaPath;
  const selection = await f.host.getSelection();
  assert.equal(selection.project_id, projectId);
  assert.equal(selection.clips[0].media_path, mediaPath);
  const binding = { project_id: projectId, item_id: itemId, media_path: mediaPath, source_fps: 24 };
  assert.equal((await f.host.openSource(binding, 12.75)).opened, true);
  assert.deepEqual(await f.host.addMarkers(binding, [{ start: 12.75, end: 13.5, name: "Cámara A" }]), { added: 1, skipped: 0 });
  assert.equal(f.markers[0].getStart().seconds, 12.75);
});

test("Mac path identity rejects case changes before native seeks or markers", async () => {
  const f = fixture();
  f.clip.getMediaFilePath = async () => "/Volumes/Footage/Camera A.mov";
  const binding = { ...f.binding, media_path: "/Volumes/footage/Camera A.mov" };
  await assert.rejects(f.host.openSource(binding, 1), errorCode("MEDIA_CHANGED"));
  await assert.rejects(f.host.addMarkers(binding, [{ start: 1 }]), errorCode("MEDIA_CHANGED"));
  assert.deepEqual(f.calls, []);
});

test("POSIX literal backslashes and double-slash paths are not Windows aliases", async () => {
  const f = fixture();
  const original = "/Volumes/Film/Take\\1.mov";
  f.clip.getMediaFilePath = async () => original;
  const binding = { ...f.binding, media_path: original };
  assert.equal((await f.host.openSource(binding, 1)).opened, true);
  f.calls.length = 0;
  await assert.rejects(f.host.openSource({ ...binding, media_path: "/Volumes/Film/Take/1.mov" }, 1), errorCode("MEDIA_CHANGED"));
  f.clip.getMediaFilePath = async () => "//Volumes/Film/Take.mov";
  await assert.rejects(f.host.openSource({ ...binding, media_path: "//volumes/film/take.mov" }, 1), errorCode("MEDIA_CHANGED"));
  assert.deepEqual(f.calls, []);
});

test("Changed footage interpretation is rejected before source seeks or marker transactions", async () => {
  const f = fixture();
  const binding = { ...f.binding, source_fps: 24 };
  f.clip.getFootageInterpretation = async () => ({ getFrameRate: () => 25 });
  await assert.rejects(f.host.openSource(binding, 1), errorCode("FOOTAGE_INTERPRETATION_CHANGED"));
  await assert.rejects(f.host.addMarkers(binding, [{ start: 1, name: "Shot" }]), errorCode("FOOTAGE_INTERPRETATION_CHANGED"));
  assert.deepEqual(f.calls, []);
  assert.equal(f.markers.length, 0);
});

test("Rounded fractional source rates are accepted without accepting a different rate", async () => {
  const f = fixture();
  f.clip.getFootageInterpretation = async () => ({ getFrameRate: () => 24000 / 1001 });
  assert.equal((await f.host.openSource({ ...f.binding, source_fps: 23.976 }, 1)).opened, true);
  f.calls.length = 0;
  await assert.rejects(f.host.openSource({ ...f.binding, source_fps: 24 }, 1), errorCode("FOOTAGE_INTERPRETATION_CHANGED"));
  assert.deepEqual(f.calls, []);
});

test("Required source rates must be valid and verifiable before native actions", async () => {
  const f = fixture();
  for (const source_fps of [0, -1, Infinity, NaN, "24"]) {
    await assert.rejects(f.host.openSource({ ...f.binding, source_fps }, 1), errorCode("INVALID_BINDING"));
  }
  delete f.clip.getFootageInterpretation;
  await assert.rejects(f.host.openSource({ ...f.binding, source_fps: 24 }, 1), errorCode("UNSUPPORTED_API"));
  await assert.rejects(f.host.addMarkers({ ...f.binding, source_fps: 24 }, [{ start: 1 }]), errorCode("UNSUPPORTED_API"));
  assert.deepEqual(f.calls, []);
  assert.equal(f.markers.length, 0);
});

test("Unsupported seeking is reported before opening a source", async () => {
  const f = fixture();
  delete f.api.SourceMonitor.setPosition;
  assert.equal((await f.host.getSelection()).capabilities.source_seek, false);
  await assert.rejects(f.host.openSource(f.binding, 1), error => error.code === "UNSUPPORTED_API" && /26\.3/.test(error.message));
  assert.deepEqual(f.calls, []);
});

test("Native refusal and inaccurate position are never reported as success", async t => {
  await t.test("open refusal", async () => {
    const f = fixture();
    f.api.SourceMonitor.openProjectItem = async () => false;
    await assert.rejects(f.host.openSource(f.binding, 1), errorCode("SOURCE_OPEN_FAILED"));
  });
  await t.test("seek refusal", async () => {
    const f = fixture();
    f.api.SourceMonitor.setPosition = async () => false;
    await assert.rejects(f.host.openSource(f.binding, 1), errorCode("SOURCE_SEEK_FAILED"));
  });
  await t.test("read-back mismatch", async () => {
    const f = fixture();
    f.api.SourceMonitor.getPosition = async () => ({ seconds: 0 });
    await assert.rejects(f.host.openSource(f.binding, 20), errorCode("SOURCE_VERIFY_FAILED"));
  });
});

test("Malformed times and incomplete bindings do not touch native monitor", async () => {
  const f = fixture();
  for (const value of [-1, NaN, Infinity, "1", 864001]) {
    await assert.rejects(f.host.openSource(f.binding, value), errorCode("INVALID_TIME"));
  }
  await assert.rejects(f.host.openSource({ item_id: "source-1" }, 1), errorCode("INVALID_BINDING"));
  assert.deepEqual(f.calls, []);
});

test("Marker batch is one synchronous undo transaction attached to source clip", async () => {
  const f = fixture();
  const result = await f.host.addMarkers(f.binding, [
    { start: 1, end: 3, name: "Shot 01", comment: "Dam, desert" },
    { start_seconds: 4, end_seconds: 4.5, name: "Dialogue", text: "Hello" },
  ]);
  assert.deepEqual(result, { added: 2, skipped: 0 });
  assert.equal(f.calls.filter(call => call[0] === "transaction").length, 1);
  assert.equal(f.markers[0].getStart().seconds, 1);
  assert.equal(f.markers[0].getDuration().seconds, 2);
  assert.equal(f.markers[0].getComments(), "Video Analyzer\nDam, desert");
  assert.equal(f.calls.some(call => call[0] === "open"), false);
});

test("Duplicate export and duplicate rows do not create duplicate markers", async () => {
  const f = fixture();
  const cue = { start: 1, end: 2, name: "Shot 01", comment: "Dam" };
  assert.deepEqual(await f.host.addMarkers(f.binding, [cue, cue]), { added: 1, skipped: 1 });
  assert.deepEqual(await f.host.addMarkers(f.binding, [cue]), { added: 0, skipped: 1 });
  assert.equal(f.markers.length, 1);
  assert.equal(f.calls.filter(call => call[0] === "transaction").length, 1);
});

test("Existing unrelated native markers are preserved", async () => {
  const f = fixture();
  f.markers.push({ getComments: () => "Director note" });
  assert.deepEqual(await f.host.addMarkers(f.binding, [{ start: 1, name: "Shot" }]), { added: 1, skipped: 0 });
  assert.equal(f.markers.length, 2);
  assert.equal(f.markers[0].getComments(), "Director note");
});

test("Marker input is completely validated before native mutation", async () => {
  const f = fixture();
  for (const cues of [
    [{ start: 1 }, { start: 2, end: 1 }], [{ start: NaN }], [{ start: "1" }], [null],
    [{ start: 1, comment: "x".repeat(20001) }], Array.from({ length: MAX_MARKERS + 1 }, () => ({ start: 1 })),
  ]) {
    await assert.rejects(f.host.addMarkers(f.binding, cues));
  }
  assert.deepEqual(f.calls, []);
  assert.equal(f.markers.length, 0);
});

test("A rejected transaction is not reported as successful marker export", async () => {
  const f = fixture();
  f.project.executeTransaction = () => false;
  await assert.rejects(f.host.addMarkers(f.binding, [{ start: 1 }]), errorCode("MARKER_TRANSACTION_FAILED"));
  assert.equal(f.markers.length, 0);
});

test("Transaction success without actual markers fails verification", async () => {
  const f = fixture();
  f.project.executeTransaction = () => true;
  await assert.rejects(f.host.addMarkers(f.binding, [{ start: 1 }]), errorCode("MARKER_VERIFY_FAILED"));
});

test("Unavailable marker lock API does not fall back to nonundoable mutation", async () => {
  const f = fixture();
  delete f.project.lockedAccess;
  await assert.rejects(f.host.addMarkers(f.binding, [{ start: 1 }]), errorCode("UNSUPPORTED_API"));
  assert.deepEqual(f.calls, []);
});

test("Missing source item does not fall back to media path or a different clip", async () => {
  const f = fixture();
  f.root.getItems = async () => [];
  await assert.rejects(f.host.openSource(f.binding, 1), errorCode("SOURCE_UNAVAILABLE"));
  await assert.rejects(f.host.addMarkers(f.binding, [{ start: 1 }]), errorCode("SOURCE_UNAVAILABLE"));
  assert.deepEqual(f.calls, []);
});

test("Aliases expose selected media and selected-source actions for adapter callers", async () => {
  const f = fixture();
  const result = await f.host.getSelectedMedia();
  assert.equal(result.project.id, "project-1");
  assert.equal(result.items[0].itemId, "source-1");
  assert.equal((await f.host.openSourceAt("source-1", 2, "project-1")).seconds, 2);
  assert.deepEqual(await f.host.addSourceMarkers("source-1", [{ start: 2 }], "project-1"), { added: 1, skipped: 0 });
});
