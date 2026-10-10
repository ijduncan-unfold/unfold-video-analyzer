"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const { createHost, prepareShots, shotRecord, SHOT_BIN } = require("../host");
const technical = { nominal_frame_rate_fraction: "24/1", nominal_frame_rate_verified: true, frame_rate_verified_frames: 240 };
const binding = { host: "resolve", project_id: "p", item_id: "original", media_path: "C:\\original.mp4", source_fps: 24 };
const pack = { asset_id: "asset", filename: "Film.mp4", analysis_session: "run", technical, shots: [
  { shot_number: 1, start_seconds: 1, end_seconds: 2, visual_description: "A red circle", tags: ["circle"], dominant_colors: ["red"], section_analysis: { lighting: "Soft light" }, transcript_segments: [{ text: "Hello" }] },
  { shot_number: 2, start_seconds: 3, end_seconds: 4, visual_description: "A person at right", tags: ["person"], project_metadata: { tags: ["campaign"] } }
] };
function fixture(options = {}) {
  let folder, timelineId = "timeline", projectId = "p", calls = 0;
  const events = [], saved = [], appended = [], originalMetadata = { Description: "Human original notes", Keywords: "original" };
  const original = { GetUniqueId: async () => "original", GetClipProperty: async () => ({ "File Path": binding.media_path, FPS: 24, Frames: 240, Start: 0, End: 239 }), GetMetadata: async () => ({ ...originalMetadata }) };
  const bin = { GetName: async () => SHOT_BIN, GetUniqueId: async () => "bin", GetClipList: async () => saved };
  const root = { GetUniqueId: async () => "root", GetClipList: async () => [original], GetSubFolderList: async () => [bin] };
  folder = root;
  function clip(info) {
    let metadata = { Comments: "Human note", Keywords: "human" }, name = "Imported";
    const properties = { "File Path": info.media, Start: info.startFrame, End: info.endFrame, Frames: info.endFrame - info.startFrame + 1, FPS: 24, ...options.subclipProperties };
    const id = options.sameId ? "original" : "shot-" + saved.length;
    return { GetUniqueId: async () => id, GetClipProperty: async () => ({ ...properties }),
      GetMetadata: async () => ({ ...metadata }), SetMetadata: async value => { events.push(["metadata", value]); if (options.metadataFailure) return false; if (!options.falseMetadataSuccess) metadata = { ...metadata, ...value }; return true; },
      SetThirdPartyMetadata: async value => { events.push(["structured", value]); return true; },
      SetName: async value => { name = value; events.push(["name", name]); return true; }, GetName: async () => name,
      edit(value) { metadata = { ...metadata, ...value }; }, properties };
  }
  let selectsItems = [];
  const timeline = { GetUniqueId: async () => timelineId, GetItemListInTrack: async () => options.incompleteSelects ? [] : selectsItems.map(item => ({ GetMediaPoolItem: async () => item })) };
  const media = { GetRootFolder: async () => root, GetCurrentFolder: async () => folder,
    SetCurrentFolder: async value => { folder = value; return true; }, AddSubFolder: async () => bin,
    AppendToTimeline: async items => { events.push(["append", items.length]); appended.push(...items); if (options.appendThrows) throw new Error("Lost acknowledgement"); return options.falseAppend ? [] : items.map(item => ({ GetMediaPoolItem: async () => item })); },
    CreateTimelineFromClips: async (name, items) => { events.push(["selects", name, items.length]); selectsItems = items; return options.falseSelects ? null : timeline; } };
  const project = { GetUniqueId: async () => projectId, GetMediaPool: async () => media, GetCurrentTimeline: async () => options.noTimeline ? null : timeline };
  const storage = { AddItemListToMediaPool: async infos => {
    calls++; events.push(["import", structuredClone(infos)]);
    if (options.onImport) options.onImport({ setTimeline(id) { timelineId = id; }, setProject(id) { projectId = id; }, original });
    const item = clip(infos[0]); saved.push(item); return [item];
  } };
  const resolve = { GetProjectManager: async () => ({ GetCurrentProject: async () => project }), GetMediaStorage: async () => storage };
  return { host: createHost(resolve), events, saved, appended, originalMetadata, original, getFolder: () => folder, root, imports: () => calls };
}
test("subclip intervals convert exclusive seconds to inclusive frame ends", () => {
  const result = prepareShots(binding, pack, { fps: 24, frames: 240 });
  assert.deepEqual(result.map(x => [x.startFrame, x.endFrame]), [[24, 47], [72, 95]]);
  assert.ok(result[0].keywords.includes("red")); assert.match(result[0].json, /Soft light/); assert.match(result[0].json, /Hello/);
});
test("invalid, duplicate, out-of-bounds and subframe shot ranges fail before native writes", async t => {
  for (const shots of [[], [pack.shots[0], pack.shots[0]], [{ ...pack.shots[0], end_seconds: 20 }], [{ ...pack.shots[0], end_seconds: 1.001 }]]) {
    await t.test(JSON.stringify(shots), async () => { const m = fixture(); await assert.rejects(m.host.exportShots(binding, { ...pack, shots })); assert.deepEqual(m.events, []); });
  }
});
test("saved shots are distinct source references with their own complete searchable metadata", async () => {
  const m = fixture(); const summary = await m.host.exportShots(binding, pack);
  assert.deepEqual(summary, { created: 2, reused: 0, added: 0, destination: "pool", metadata: true });
  assert.equal(m.getFolder(), m.root); assert.equal(m.imports(), 2); assert.deepEqual(m.originalMetadata, { Description: "Human original notes", Keywords: "original" });
  const first = await m.saved[0].GetMetadata(), second = await m.saved[1].GetMetadata();
  assert.equal(first.Description, "A red circle"); assert.match(first.Keywords, /human, circle, red/); assert.match(first.Comments, /^Human note/);
  assert.equal(shotRecord(first.Comments).record.shot.section_analysis.lighting, "Soft light");
  assert.equal(shotRecord(second.Comments).record.shot.shot_number, 2); assert.match(second.Keywords, /campaign/);
});
test("repeat exports reuse the same subclips and preserve human comments, descriptions and keywords", async () => {
  const m = fixture(); await m.host.exportShots(binding, pack);
  const before = await m.saved[0].GetMetadata(); m.saved[0].edit({ Description: "Editor revised description", Keywords: before.Keywords + ", approved", Comments: "Editor's prefix\n" + before.Comments + "\nEditor's suffix" });
  const summary = await m.host.exportShots(binding, pack);
  assert.equal(summary.reused, 2); assert.equal(summary.created, 0); assert.equal(m.imports(), 2);
  const after = await m.saved[0].GetMetadata(); assert.equal(after.Description, "Editor revised description"); assert.match(after.Keywords, /approved/); assert.match(after.Comments, /Editor's prefix/); assert.match(after.Comments, /Editor's suffix/);
  assert.equal(after.Comments.split("[Video Analyzer shot]").length, 2);
});
test("an original ID or incorrect source range blocks all metadata and timeline writes", async t => {
  for (const options of [{ sameId: true }, { subclipProperties: { Start: 0 } }, { subclipProperties: { Frames: 240 } }, { subclipProperties: { FPS: 25 } }, { subclipProperties: { FPS: null } }, { subclipProperties: { "File Path": "C:\\other.mp4" } }]) {
    await t.test(JSON.stringify(options), async () => { const m = fixture(options); await assert.rejects(m.host.exportShots(binding, pack, "current"), { code: "SUBCLIP_VERIFY_FAILED" }); assert.equal(m.events.some(e => e[0] === "metadata" || e[0] === "append"), false); });
  }
});
test("an absent current timeline prevents even subclip creation", async () => {
  const m = fixture({ noTimeline: true }); await assert.rejects(m.host.exportShots(binding, pack, "current"), { code: "NO_TIMELINE" }); assert.deepEqual(m.events, []);
});
test("metadata failure or false success stops before a timeline edit", async t => {
  for (const options of [{ metadataFailure: true }, { falseMetadataSuccess: true }]) await t.test(JSON.stringify(options), async () => { const m = fixture(options); await assert.rejects(m.host.exportShots(binding, pack, "current")); assert.equal(m.appended.length, 0); assert.equal(m.getFolder(), m.root); });
});
test("current timeline append carries the actual metadata-bearing subclips including audio", async () => {
  const m = fixture(); const summary = await m.host.exportShots(binding, pack, "current");
  assert.equal(summary.added, 2); assert.equal(m.appended[0], m.saved[0]); assert.equal(m.appended[1], m.saved[1]);
  assert.equal(m.events.some(event => event[0] === "import" && "mediaType" in event[1][0]), false);
  assert.match((await m.appended[0].GetMetadata()).Keywords, /circle/);
});
test("a new selects timeline uses the saved shots in source order", async () => {
  const m = fixture(); const summary = await m.host.exportShots(binding, { ...pack, shots: [...pack.shots].reverse() }, "new"); assert.equal(summary.added, 2);
  assert.equal(m.saved[0].properties.Start, 24); const event = m.events.find(e => e[0] === "selects"); assert.match(event[1], /Analyzer selects/); assert.equal(event[2], 2);
});
test("timeline or project changes during export cannot receive native edits", async t => {
  for (const kind of ["timeline", "project"]) await t.test(kind, async () => { const m = fixture({ onImport(ctx) { if (kind === "timeline") ctx.setTimeline("different"); else ctx.setProject("different"); } }); await assert.rejects(m.host.exportShots(binding, pack, "current")); assert.equal(m.appended.length, 0); assert.equal(m.events.some(e => e[0] === "metadata"), false); });
});
test("unacknowledged or incomplete native append requires inspection rather than automatic replay", async t => {
  for (const options of [{ appendThrows: true }, { falseAppend: true }]) await t.test(JSON.stringify(options), async () => { const m = fixture(options); await assert.rejects(m.host.exportShots(binding, pack, "current"), { code: "TIMELINE_WRITE_UNCERTAIN" }); assert.equal(m.events.filter(e => e[0] === "append").length, 1); assert.equal(m.saved.length, 2); });
});
test("a partially populated selects timeline is reported without deleting clips or replaying", async () => { const m = fixture({ incompleteSelects: true }); await assert.rejects(m.host.exportShots(binding, pack, "new"), { code: "TIMELINE_WRITE_UNCERTAIN" }); assert.equal(m.saved.length, 2); assert.equal(m.events.filter(e => e[0] === "selects").length, 1); });
test("unverified original timebase blocks subclips as well as markers", async () => { const m = fixture(); await assert.rejects(m.host.exportShots(binding, { ...pack, technical: { ...technical, nominal_frame_rate_verified: false } }), { code: "UNVERIFIED_TIMEBASE" }); assert.deepEqual(m.events, []); });
