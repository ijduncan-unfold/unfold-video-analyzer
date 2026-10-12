"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const { createHost } = require("../host");
const NS = "http://ns.adobe.com/premierePrivateProjectMetaData/1.0/";
const DM = "http://ns.adobe.com/xmp/1.0/DynamicMedia/", DC = "http://purl.org/dc/elements/1.1/";
class XMPMeta {
  static registerNamespace() {}
  constructor(packet) { this.properties = JSON.parse(packet || "{}"); }
  key(ns, key) { return ns === NS ? key : ns + '|' + key; }
  getProperty(ns, key) { key = this.key(ns, key); return Object.hasOwn(this.properties, key) ? { value: this.properties[key] } : null; }
  setProperty(ns, key, value) { this.properties[this.key(ns, key)] = value; }
  getLocalizedText(ns, key, generic, specific) { assert.ok(generic); assert.equal(specific, 'x-default'); return this.getProperty(ns, key); }
  setLocalizedText(ns, key, generic, specific, value) { assert.ok(generic); assert.equal(specific, 'x-default'); this.setProperty(ns, key, value); }
  countArrayItems(ns, key) { return (this.properties[this.key(ns, key)] || []).length; }
  getArrayItem(ns, key, index) { return { value: this.properties[this.key(ns, key)][index - 1] }; }
  deleteProperty(ns, key) { delete this.properties[this.key(ns, key)]; }
  appendArrayItem(ns, key, value, itemOptions, arrayOptions) { assert.equal(arrayOptions, 512); (this.properties[this.key(ns, key)] ||= []).push(value); }
  serialize() { return JSON.stringify(this.properties); }
}
const binding = { project_id: "project", item_id: "clip", media_path: "C:\\source.mp4", source_fps: 24 };
const mapping = { schema_version: "1.0", fields: [
  { premiere: "Column.PropertyText.Description", value: "A desert dam", label: "Description" },
  { premiere: "Column.PropertyText.Scene", value: "012B", label: "Scene" },
  { premiere: "VideoAnalyzerDialogue", value: "00:01–00:02 Alex: Hello", label: "Dialogue" },
  { premiere: "Column.Intrinsic.MediaTime", value: "DO NOT WRITE", label: "Timecode" }
], record: { schema: "video-analyzer.library", analysis: { deep_results: [{ lighting: "Soft" }] }, transcript: [{ start_seconds: 1, end_seconds: 2, text: "Hello" }] } };
function fixture(options = {}) {
  let properties = { "Column.PropertyText.Description": "Human notes", unrelated: "Keep me" }, locked = false, transactions = 0;
  let fileProperties = { [DM + '|videoFrameRate']: '24000/1001', [DM + '|startTimecode']: { timeValue: '01:00:00:00' }, ...(options.fileProperties || {}) };
  let staleReadback = false;
  const schema = [];
  const clip = { kind: "clip", getId: () => "clip", isSequence: async () => false, isOffline: async () => false,
    getMediaFilePath: async () => options.relinked ? "C:\\other.mp4" : binding.media_path, getFootageInterpretation: async () => ({ getFrameRate: () => 24 }) };
  const root = { getItems: async () => [clip] };
  const project = { guid: "project", getRootItem: async () => root,
    lockedAccess(callback) { locked = true; try { assert.notEqual(callback() instanceof Promise, true); } finally { locked = false; } },
    executeTransaction(callback) { assert.ok(locked); transactions++; callback({ addAction(action) { if (!options.falseSuccess && !options.reject) { if(action.type === 'xmp') fileProperties = JSON.parse(action.packet); else properties = JSON.parse(action.packet); } return true; } }); return !options.reject; } };
  const api = { Project: { getActiveProject: async () => project }, ClipProjectItem: { cast: item => item.kind === "clip" ? { ...item, getId: undefined } : null },
    Constants: { MetadataType: { TEXT: 2 } }, Metadata: {
      getProjectMetadata: async item => { assert.equal(item, clip, 'Metadata APIs require the original ProjectItem handle'); if (transactions && options.delayedReadback && !staleReadback) { staleReadback = true; return '{}'; } return JSON.stringify(properties); },
      getXMPMetadata: async item => { assert.equal(item, clip); return JSON.stringify(fileProperties); },
      async addPropertyToProjectMetadataSchema(name, label, type) { assert.equal(type, 2); schema.push([name, label]); if (options.concurrent) properties.unrelated = "Concurrent edit"; if (options.concurrentXmp) fileProperties[DM+'|director'] = 'Concurrent director'; return false; },
      createSetProjectMetadataAction(item, packet, fields) { assert.ok(locked); assert.equal(item, clip); assert.ok(fields.includes("VideoAnalyzerAnalysis")); return { packet }; },
      createSetXMPMetadataAction(item, packet) { assert.ok(locked); assert.equal(item, clip); return { packet, type: 'xmp' }; }
    } };
  return { host: createHost(api, { XMPMeta, XMPConst: { PROP_IS_ARRAY: 512 } }), properties: () => ({ ...properties }), fileProperties: () => structuredClone(fileProperties), edit(values) { Object.assign(properties, values); }, editFile(values) { Object.assign(fileProperties, values); }, transactions: () => transactions, schema };
}

const slateMapping = { ...mapping, fields: [...mapping.fields,
  { key: 'cinematographer', premiere: 'VideoAnalyzerCinematographer', value: 'Clay Westervelt' },
  { key: 'director', premiere: 'VideoAnalyzerDirector', value: 'I. Duncan' },
  { key: 'scene', premiere: 'Column.PropertyText.Scene', value: '7B' },
  { key: 'take', premiere: 'VideoAnalyzerTake', value: '04' },
  { key: 'camera_roll', premiere: 'Column.Intrinsic.TapeName', value: 'A001 / B001' },
  { key: 'production_title', premiere: 'VideoAnalyzerProduction', value: 'Freeman Chronicles' },
  { key: 'description', premiere: 'Column.PropertyText.Description', value: 'A continuous take with a camera pan.' },
  { key: 'keywords', premiere: 'VideoAnalyzerKeywords', value: 'dusk, blue, green', xmp_value: ['dusk', 'blue, green'] },
  { key: 'source_timecode', value: 'DO NOT WRITE' },
  { key: 'date', value: '1/2/15' },
] };

test('slate values fill standard Premiere XMP fields while timed descriptions and full analysis stay searchable', async () => {
  const f = fixture();
  const record = { ...slateMapping.record, technical: { codec: 'F55RAW' }, shots: [{ shot_number: 1, start_time: '00:00', end_time: '00:58',
    visual_description: 'A continuous take with a camera pan.', slates: [{ cinematographer: 'Clay Westervelt', scene: '7B' }], section_analysis: { lighting: 'Low key' }, notes: 'Keep this take' }] };
  await f.host.syncMetadata(binding, { ...slateMapping, record });
  const xmp = f.fileProperties(), project = f.properties();
  assert.equal(xmp[DM+'|directorPhotography'], 'Clay Westervelt');
  assert.equal(xmp[DM+'|director'], 'I. Duncan');
  assert.equal(xmp[DM+'|scene'], '7B'); assert.equal(xmp[DM+'|takeNumber'], 4);
  assert.equal(xmp[DM+'|projectName'], 'Freeman Chronicles');
  assert.equal(xmp[DM+'|tapeName'], 'A001 / B001');
  assert.deepEqual(xmp[DC+'|subject'], ['dusk', 'blue, green']);
  assert.equal(xmp[DC+'|description'], 'A continuous take with a camera pan.');
  assert.equal(xmp[DM+'|videoFrameRate'], '24000/1001');
  assert.deepEqual(xmp[DM+'|startTimecode'], { timeValue: '01:00:00:00' });
  assert.equal(xmp[DM+'|shotDate'], undefined, 'Ambiguous slate dates must not overwrite camera dates');
  assert.match(project.VideoAnalyzerClipDescriptions, /00:00–00:58.*Clip 1/);
  assert.match(project.VideoAnalyzerDetails, /Low key/); assert.match(project.VideoAnalyzerDetails, /Keep this take/);
  assert.match(project.VideoAnalyzerSlates, /Clay Westervelt/); assert.match(project.VideoAnalyzerTechnical, /F55RAW/);
  assert.deepEqual(JSON.parse(project.VideoAnalyzerAnalysis), record);
});

test('XMP sync preserves existing and subsequently edited director of photography, and skips nonnumeric takes', async () => {
  const f = fixture({ fileProperties: { [DM+'|directorPhotography']: 'Editor DP' } });
  await f.host.syncMetadata(binding, slateMapping);
  assert.equal(f.fileProperties()[DM+'|directorPhotography'], 'Editor DP');
  f.editFile({ [DM+'|director']: 'Edited director' });
  await f.host.syncMetadata(binding, { ...slateMapping, fields: slateMapping.fields.filter(field => field.key !== 'take').concat({ key: 'take', premiere: 'VideoAnalyzerTake', value: 'MOS' }) });
  assert.equal(f.fileProperties()[DM+'|director'], 'Edited director');
  assert.equal(f.fileProperties()[DM+'|takeNumber'], undefined);
  assert.equal(f.properties().VideoAnalyzerTake, 'MOS');
});

test('concurrent XMP edits prevent both metadata writes', async () => {
  const f = fixture({ concurrentXmp: true });
  await assert.rejects(f.host.syncMetadata(binding, slateMapping), { code: 'METADATA_CHANGED' });
  assert.equal(f.transactions(), 0);
});

test('briefly stale metadata readback is verified without a second write transaction', async () => {
  const f = fixture({ delayedReadback: true });
  await f.host.syncMetadata(binding, slateMapping);
  assert.equal(f.transactions(), 1);
  assert.equal(f.fileProperties()[DM+'|directorPhotography'], 'Clay Westervelt');
});
test("project metadata sync preserves human fields and retains the entire timed record", async () => {
  const f = fixture(); const result = await f.host.syncMetadata(binding, mapping), metadata = f.properties();
  assert.equal(result.preserved, 1); assert.equal(result.complete_record, true);
  assert.equal(metadata['Column.PropertyText.Description'], "Human notes"); assert.equal(metadata['Column.PropertyText.Scene'], "012B");
  assert.equal(metadata.unrelated, "Keep me"); assert.equal(metadata['Column.Intrinsic.MediaTime'], undefined);
  assert.deepEqual(JSON.parse(metadata.VideoAnalyzerAnalysis), mapping.record);
  assert.match(metadata.VideoAnalyzerDialogue, /Alex: Hello/); assert.equal(f.transactions(), 1);
});
test("subsequent sync updates only owned values, preserving edits and removing stale ownership", async () => {
  const f = fixture(); await f.host.syncMetadata(binding, mapping);
  f.edit({ 'Column.PropertyText.Scene': 'Editor revised scene' });
  await f.host.syncMetadata(binding, { ...mapping, fields: mapping.fields.map(field => field.premiere === 'Column.PropertyText.Scene' ? { ...field, value: '013' } : field) });
  assert.equal(f.properties()['Column.PropertyText.Scene'], 'Editor revised scene');
  assert.equal(JSON.parse(f.properties().VideoAnalyzerReceipt)['Column.PropertyText.Scene'], undefined);
  await f.host.syncMetadata(binding, { ...mapping, fields: [] });
  assert.equal(f.properties().VideoAnalyzerDialogue, '');
  assert.equal(f.properties()['Column.PropertyText.Scene'], 'Editor revised scene');
});
test("changed source, concurrent edits, rejected writes and false success are reported", async t => {
  for (const [options, code] of [[{ relinked: true }, 'MEDIA_CHANGED'], [{ concurrent: true }, 'METADATA_CHANGED'], [{ reject: true }, 'METADATA_WRITE_FAILED'], [{ falseSuccess: true }, 'METADATA_VERIFY_FAILED']]) {
    await t.test(code, async () => { const f = fixture(options); await assert.rejects(f.host.syncMetadata(binding, mapping), { code }); if (options.concurrent || options.relinked) assert.equal(f.transactions(), 0); });
  }
});
test("oversized record is rejected without silently truncating dialogue or native writes", async () => {
  const f = fixture(); await assert.rejects(f.host.syncMetadata(binding, { ...mapping, record: { ...mapping.record, huge: 'a'.repeat(4000001) } }), { code: 'METADATA_LIMIT' });
  assert.equal(f.transactions(), 0); assert.equal(f.schema.length, 0);
});

test('cosplay sync writes standard description and keywords plus dedicated columns, preserving editor edits', async () => {
  const f = fixture(); f.edit({ 'Column.PropertyText.Description': '' });
  const fields = [
    { key: 'description', premiere: 'Column.PropertyText.Description', value: 'Gordon Freeman · Half-Life · HEV Suit [Suggested]\nScene description: Posing at a booth.' },
    { key: 'keywords', premiere: 'VideoAnalyzerKeywords', value: 'Cosplay, Gordon Freeman, Half-Life', xmp_value: ['Cosplay', 'Gordon Freeman', 'Half-Life'] },
    { key: 'cosplay_characters', premiere: 'VideoAnalyzerCosplayCharacters', value: 'Gordon Freeman', label: 'Cosplay characters' },
    { key: 'cosplay_description', premiere: 'VideoAnalyzerCosplayDescription', value: 'HEV Suit [Suggested]', label: 'Cosplay description' },
    { key: 'scene_description', premiere: 'VideoAnalyzerSceneDescription', value: 'Posing at a booth.', label: 'Scene description' },
  ];
  await f.host.syncMetadata(binding, { ...mapping, fields });
  assert.equal(f.properties().VideoAnalyzerCosplayCharacters, 'Gordon Freeman');
  assert.equal(f.properties().VideoAnalyzerSceneDescription, 'Posing at a booth.');
  assert.equal(f.properties()['Column.PropertyText.Description'], fields[0].value);
  assert.equal(f.fileProperties()[DC+'|description'], fields[0].value);
  assert.deepEqual(f.fileProperties()[DC+'|subject'], fields[1].xmp_value);
  f.edit({ VideoAnalyzerCosplayCharacters: 'Editor correction' });
  await f.host.syncMetadata(binding, { ...mapping, fields: [] });
  assert.equal(f.properties().VideoAnalyzerCosplayCharacters, 'Editor correction');
  assert.equal(f.properties().VideoAnalyzerCosplayDescription, '');
});
