"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const { createHost } = require("../host");
const NS = "http://ns.adobe.com/premierePrivateProjectMetaData/1.0/";
class XMPMeta {
  static registerNamespace() {}
  constructor(packet) { this.properties = JSON.parse(packet || "{}"); }
  getProperty(ns, key) { assert.equal(ns, NS); return Object.hasOwn(this.properties, key) ? { value: this.properties[key] } : null; }
  setProperty(ns, key, value) { assert.equal(ns, NS); this.properties[key] = value; }
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
  const schema = [];
  const clip = { kind: "clip", getId: () => "clip", isSequence: async () => false, isOffline: async () => false,
    getMediaFilePath: async () => options.relinked ? "C:\\other.mp4" : binding.media_path, getFootageInterpretation: async () => ({ getFrameRate: () => 24 }) };
  const root = { getItems: async () => [clip] };
  const project = { guid: "project", getRootItem: async () => root,
    lockedAccess(callback) { locked = true; try { assert.notEqual(callback() instanceof Promise, true); } finally { locked = false; } },
    executeTransaction(callback) { assert.ok(locked); transactions++; callback({ addAction(action) { if (!options.falseSuccess && !options.reject) properties = JSON.parse(action.packet); return true; } }); return !options.reject; } };
  const api = { Project: { getActiveProject: async () => project }, ClipProjectItem: { cast: item => item.kind === "clip" ? item : null },
    Constants: { MetadataType: { TEXT: 2 } }, Metadata: {
      getProjectMetadata: async () => JSON.stringify(properties),
      async addPropertyToProjectMetadataSchema(name, label, type) { assert.equal(type, 2); schema.push([name, label]); if (options.concurrent) properties.unrelated = "Concurrent edit"; return false; },
      createSetProjectMetadataAction(item, packet, fields) { assert.ok(locked); assert.equal(item, clip); assert.ok(fields.includes("VideoAnalyzerAnalysis")); return { packet }; },
      createSetXMPMetadataAction() { assert.fail("Never write file XMP"); }
    } };
  return { host: createHost(api, { XMPMeta }), properties: () => ({ ...properties }), edit(values) { Object.assign(properties, values); }, transactions: () => transactions, schema };
}
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
