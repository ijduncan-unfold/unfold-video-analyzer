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
  let metadata = { Description: 'Human description', Comments: 'Original human notes', Keywords: 'human' }, third = {}, writes = 0;
  const props = { 'File Path': binding.media_path, FPS: 24, Frames: 240, Start: 0 };
  const item = { GetUniqueId: async () => 'source', GetClipProperty: async () => ({ ...props }),
    GetMetadata: async () => ({ ...metadata }), SetMetadata: async value => { writes++; if (options.failure || 'Roll/Card' in value) return false; if (!options.falseSuccess) metadata = { ...metadata, ...value }; return true; },
    GetThirdPartyMetadata: async () => ({ ...third }),
    SetThirdPartyMetadata: async values => { if (options.thirdFailure) return false; if (!options.falseThirdSuccess) third = { ...third, ...values }; return true; } };
  const root = { GetClipList: async () => [item], GetSubFolderList: async () => [] };
  const pool = { GetRootFolder: async () => root };
  const project = { GetUniqueId: async () => 'p', GetMediaPool: async () => pool };
  return { host: createHost({ GetProjectManager: async () => ({ GetCurrentProject: async () => project }) }),
    metadata: () => metadata, third: () => third, writes: () => writes, edit(values) { metadata = { ...metadata, ...values }; }, editThird(values) { third = { ...third, ...values }; }, props };
}
test('source metadata sync promotes slate fields, keeps complete timed analysis and human notes', async () => {
  const f = fixture(), result = await f.host.syncMetadata(binding, mapping), metadata = f.metadata();
  assert.equal(result.preserved, 1); assert.equal(metadata.Description, 'Human description');
  assert.equal(metadata.Scene, '012B'); assert.equal(metadata['Roll Card #'], 'A001'); assert.equal(metadata['Roll/Card'], undefined); assert.equal(metadata.FPS, undefined);
  assert.match(metadata.Keywords, /^human, circle, blue$/); assert.match(metadata.Comments, /^Original human notes/);
  assert.match(metadata.Comments, /Low key/); assert.match(metadata.Comments, /"start_seconds":1/); assert.match(metadata.Comments, /Hello/);
  f.edit({ Scene: 'Editor scene' }); await f.host.syncMetadata(binding, mapping);
  assert.equal(f.metadata().Scene, 'Editor scene'); assert.equal(f.metadata().Comments.split('[Video Analyzer analysis]').length, 2);
  await f.host.syncMetadata(binding, { ...mapping, fields: [] });
  assert.equal(f.metadata()['Roll Card #'], ''); assert.equal(f.metadata().Scene, 'Editor scene');
});

test('canonical camera-roll metadata writes preserve editor values with either mapping version', async () => {
  const f = fixture(), updated = structuredClone(mapping);
  updated.fields.find(field => field.resolve === 'Roll/Card').resolve = 'Roll Card #';
  await f.host.syncMetadata(binding, updated);
  assert.equal(f.metadata()['Roll Card #'], 'A001');
  f.edit({ 'Roll Card #': 'Editor roll' });
  await f.host.syncMetadata(binding, mapping);
  assert.equal(f.metadata()['Roll Card #'], 'Editor roll');
});
test('relinked media and rejected or false-success metadata writes cannot report success', async t => {
  for (const [options, code] of [[{ failure: true }, 'METADATA_WRITE_FAILED'], [{ falseSuccess: true }, 'METADATA_VERIFY_FAILED']]) await t.test(code, async () => {
    const f = fixture(options); await assert.rejects(f.host.syncMetadata(binding, mapping), { code });
  });
  const f = fixture(); f.props['File Path'] = 'C:\\other.mxf';
  await assert.rejects(f.host.syncMetadata(binding, mapping), { code: 'MEDIA_CHANGED' }); assert.equal(f.writes(), 0);
});

test('all analysis is readable, source timed and lossless in additional metadata', async () => {
  const f = fixture(), rich = structuredClone(mapping);
  rich.fields.push({ resolve: 'DOP', label: 'Cinematographer', value: 'C. WESTERVELT' }, { resolve: 'Director', label: 'Director', value: 'I. DUNCAN' });
  rich.record.shots = [{ shot_number: 1, start_seconds: 0, end_seconds: 8, visual_description: 'Camera pans across the room', slates: [{ scene: '012B', cinematographer: 'C. WESTERVELT', raw_text: 'Board text' }], annotations: { notes: 'Keep this take' } },
    { shot_number: 2, start_seconds: 8, end_seconds: 10, visual_description: 'An actual cut' }];
  rich.record.technical = { source_timecode: '12:46:55:08', fps: '24000/1001' };
  rich.record.rough_cut = { keep: [{ start: 1, end: 8 }] };
  rich.record.transcript[0].speaker = 'Guard';
  await f.host.syncMetadata(binding, rich);
  assert.equal(f.metadata().DOP, 'C. WESTERVELT'); assert.equal(f.metadata().Director, 'I. DUNCAN');
  const third = f.third();
  assert.match(third['Video Analyzer Clip descriptions'], /0–8 · Clip 1\nCamera pans/);
  assert.match(third['Video Analyzer Clip descriptions'], /8–10 · Clip 2\nAn actual cut/);
  assert.equal(third['Video Analyzer Dialogue'], '1–2 Guard: Hello');
  assert.equal(f.metadata()['Dialog Notes'], '1–2 Guard: Hello');
  assert.match(third['Video Analyzer Detailed analysis'], /Low key/);
  assert.match(third['Video Analyzer Detailed analysis'], /Keep this take/);
  assert.match(third['Video Analyzer Slate readings'], /Board text/);
  assert.match(third['Video Analyzer Source inspection'], /12:46:55:08/);
  assert.deepEqual(JSON.parse(third['Video Analyzer Complete analysis JSON']), rich.record);
  assert.match(f.metadata().Comments, /Dialogue\n1–2 Guard: Hello/);
  assert.match(f.metadata().Comments, /Clip descriptions\n0–8/);
  await f.host.syncMetadata(binding, rich);
  assert.equal(f.metadata().Comments.split('[Video Analyzer details]').length, 2);
});

test('native dialogue notes respect existing human transcripts and manual corrections', async () => {
  const f = fixture(); f.edit({ 'Dialog Notes': 'Human transcript' });
  await f.host.syncMetadata(binding, mapping);
  assert.equal(f.metadata()['Dialog Notes'], 'Human transcript');
  assert.match(f.third()['Video Analyzer Dialogue'], /Hello/);
  const owned = fixture(); await owned.host.syncMetadata(binding, mapping);
  owned.edit({ 'Dialog Notes': 'Editor correction' });
  await owned.host.syncMetadata(binding, mapping);
  assert.equal(owned.metadata()['Dialog Notes'], 'Editor correction');
});

test('manual changes to readable notes and individual additional fields survive resync', async () => {
  const f = fixture(); await f.host.syncMetadata(binding, mapping);
  f.edit({ Comments: f.metadata().Comments.replace('Dialogue\n1–2 Hello', 'Dialogue\nEditor corrected dialogue') });
  f.editThird({ 'Video Analyzer Dialogue': 'Editor transcript', Unrelated: 'My metadata' });
  const revised = structuredClone(mapping); revised.record.transcript[0].text = 'Updated AI dialogue';
  const result = await f.host.syncMetadata(binding, revised);
  assert.ok(result.preserved >= 3);
  assert.match(f.metadata().Comments, /Dialogue\nEditor corrected dialogue/);
  assert.equal(f.metadata().Comments.split('[Video Analyzer details]').length, 2);
  assert.equal(f.third()['Video Analyzer Dialogue'], 'Editor transcript'); assert.equal(f.third().Unrelated, 'My metadata');
  assert.equal(JSON.parse(f.third()['Video Analyzer Complete analysis JSON']).transcript[0].text, 'Updated AI dialogue');
});

test('rejected or incomplete additional metadata writes never report success', async t => {
  for (const [options, code] of [[{ thirdFailure: true }, 'METADATA_WRITE_FAILED'], [{ falseThirdSuccess: true }, 'METADATA_VERIFY_FAILED']]) {
    await t.test(code, async () => { const f = fixture(options); await assert.rejects(f.host.syncMetadata(binding, mapping), { code }); });
  }
});

test('cosplay source sync exports descriptions and corrections without accumulating old character keywords', async () => {
  const f = fixture(); f.edit({ Description: '', Keywords: 'human, Half-Life' });
  const fields = [
    { key: 'description', resolve: 'Description', value: 'Gordon Freeman · Half-Life · HEV Suit [Suggested]', label: 'Description' },
    { key: 'keywords', resolve: 'Keywords', value: 'Cosplay, Gordon Freeman, Half-Life', label: 'Keywords' },
    { key: 'cosplay_characters', resolve: null, value: 'Gordon Freeman', label: 'Cosplay characters' },
  ];
  await f.host.syncMetadata(binding, { ...mapping, fields });
  assert.equal(f.metadata().Description, fields[0].value);
  assert.equal(f.third()['Video Analyzer Cosplay characters'], 'Gordon Freeman');
  assert.match(f.metadata().Comments, /Cosplay characters/);
  await f.host.syncMetadata(binding, { ...mapping, fields: [
    { ...fields[0], value: 'Batman · DC [Confirmed by you]' },
    { ...fields[1], value: 'Cosplay, Batman, DC' },
    { ...fields[2], value: 'Batman' },
  ] });
  assert.equal(f.metadata().Keywords, 'human, Half-Life, Cosplay, Batman, DC');
  assert.equal(f.metadata().Description, 'Batman · DC [Confirmed by you]');
  await f.host.syncMetadata(binding, { ...mapping, fields: [] });
  assert.equal(f.metadata().Keywords, 'human, Half-Life');
});
