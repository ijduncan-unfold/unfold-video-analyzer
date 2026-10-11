"use strict";
const test = require('node:test'), assert = require('node:assert/strict');
const { createHost } = require('../host');
const binding = { host: 'resolve', project_id: 'p', item_id: 'source', media_path: 'C:\\original.mxf', source_fps: 24 };
const mapping = { schema_version: '1.0', fields: [
  { resolve: 'Description', value: 'AI description' }, { resolve: 'Keywords', value: 'circle, blue' },
  { resolve: 'Scene', value: '012B' }, { resolve: 'Roll/Card', value: 'A001' },
  { resolve: 'FPS', value: '99' }
], record: { schema: 'video-analyzer.library', asset: { id: 'asset' }, analysis: { deep_results: [{ lighting: 'Low key' }] }, transcript: [{ start_seconds: 1, end_seconds: 2, text: 'Hello' }] } };
function fixture(options = {}) {
  let metadata = { Description: 'Human description', Comments: 'Original human notes', Keywords: 'human' }, writes = 0;
  const props = { 'File Path': binding.media_path, FPS: 24, Frames: 240, Start: 0 };
  const item = { GetUniqueId: async () => 'source', GetClipProperty: async () => ({ ...props }),
    GetMetadata: async () => ({ ...metadata }), SetMetadata: async value => { writes++; if (options.failure) return false; if (!options.falseSuccess) metadata = { ...metadata, ...value }; return true; },
    SetThirdPartyMetadata: async values => { assert.ok(values['VideoAnalyzer.Analysis']); return true; } };
  const root = { GetClipList: async () => [item], GetSubFolderList: async () => [] };
  const pool = { GetRootFolder: async () => root };
  const project = { GetUniqueId: async () => 'p', GetMediaPool: async () => pool };
  return { host: createHost({ GetProjectManager: async () => ({ GetCurrentProject: async () => project }) }),
    metadata: () => metadata, writes: () => writes, edit(values) { metadata = { ...metadata, ...values }; }, props };
}
test('source metadata sync promotes slate fields, keeps complete timed analysis and human notes', async () => {
  const f = fixture(), result = await f.host.syncMetadata(binding, mapping), metadata = f.metadata();
  assert.equal(result.preserved, 1); assert.equal(metadata.Description, 'Human description');
  assert.equal(metadata.Scene, '012B'); assert.equal(metadata['Roll/Card'], 'A001'); assert.equal(metadata.FPS, undefined);
  assert.match(metadata.Keywords, /^human, circle, blue$/); assert.match(metadata.Comments, /^Original human notes/);
  assert.match(metadata.Comments, /Low key/); assert.match(metadata.Comments, /"start_seconds":1/); assert.match(metadata.Comments, /Hello/);
  f.edit({ Scene: 'Editor scene' }); await f.host.syncMetadata(binding, mapping);
  assert.equal(f.metadata().Scene, 'Editor scene'); assert.equal(f.metadata().Comments.split('[Video Analyzer analysis]').length, 2);
  await f.host.syncMetadata(binding, { ...mapping, fields: [] });
  assert.equal(f.metadata()['Roll/Card'], ''); assert.equal(f.metadata().Scene, 'Editor scene');
});
test('relinked media and rejected or false-success metadata writes cannot report success', async t => {
  for (const [options, code] of [[{ failure: true }, 'METADATA_WRITE_FAILED'], [{ falseSuccess: true }, 'METADATA_VERIFY_FAILED']]) await t.test(code, async () => {
    const f = fixture(options); await assert.rejects(f.host.syncMetadata(binding, mapping), { code });
  });
  const f = fixture(); f.props['File Path'] = 'C:\\other.mxf';
  await assert.rejects(f.host.syncMetadata(binding, mapping), { code: 'MEDIA_CHANGED' }); assert.equal(f.writes(), 0);
});
