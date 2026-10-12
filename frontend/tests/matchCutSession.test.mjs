import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readMatchCutSession, saveMatchCutSession, forgetMatchCutSession } from '../src/components/library/matchCutSession.ts'

const records = new Map()
globalThis.localStorage = {
  getItem: key => records.get(key) ?? null,
  setItem: (key, value) => records.set(key, value),
  removeItem: key => records.delete(key),
}
const session = {
  run: 'analysis-1', shotNumber: 61, seconds: 169.7,
  targetIds: ['film', 'teaser'], weights: { shape: 1, composition: 0, color: 0 },
  region: [.2, .3, .4, .4], sourceShapeId: null, alignShape: true,
  knownShapes: null, indexes: [], result: { matches: [], indexes: [] },
  resultKey: 'query', searchIntent: 'query', selected: null, incoming: 183.8, handle: 3,
}

test('round-trips a session and isolates projects', () => {
  saveMatchCutSession('film', session)
  saveMatchCutSession('teaser', { ...session, shotNumber: 2 })
  assert.deepEqual(readMatchCutSession('film', 'analysis-1'), session)
  assert.equal(readMatchCutSession('teaser', 'analysis-1').shotNumber, 2)
  assert.equal(readMatchCutSession('unvisited', 'analysis-1'), null)
})

test('reads persisted sessions without an in-memory copy', () => {
  records.set('video-analyzer:match-cuts:v1:reload', JSON.stringify(session))
  assert.deepEqual(readMatchCutSession('reload', 'analysis-1'), session)
})

test('preserves a freehand outline with its selection tool', () => {
  const drawn = { ...session, region: null, drawingMode: 'outline', sourceOutline: [[100, 100], [500, 200], [400, 900], [50, 700]] }
  records.set('video-analyzer:match-cuts:v1:drawn', JSON.stringify(drawn))
  assert.deepEqual(readMatchCutSession('drawn', 'analysis-1'), drawn)
  records.set('video-analyzer:match-cuts:v1:bad-outline', JSON.stringify({ ...drawn, sourceOutline: [[-1, 100], [10, 50], [80, 90]] }))
  assert.equal(readMatchCutSession('bad-outline', 'analysis-1'), null)
})

test('invalidates a previous analysis run', () => {
  saveMatchCutSession('reanalyzed', session)
  assert.equal(readMatchCutSession('reanalyzed', 'analysis-2'), null)
})

test('restores folder and whole-library scopes without losing legacy media selections', () => {
  const scoped = { ...session, targetScope: { mode: 'selected', mediaIds: ['teaser'], folderIds: ['day1', 'unfiled'] } }
  records.set('video-analyzer:match-cuts:v1:folder-scope', JSON.stringify(scoped))
  assert.deepEqual(readMatchCutSession('folder-scope', 'analysis-1').targetScope, scoped.targetScope)
  records.set('video-analyzer:match-cuts:v1:bad-scope', JSON.stringify({ ...session, targetScope: { mode: 'all', mediaIds: 'bad', folderIds: [] } }))
  assert.equal(readMatchCutSession('bad-scope', 'analysis-1'), null)
})

test('removes the session when its project is deleted', () => {
  saveMatchCutSession('deleted-project', session)
  forgetMatchCutSession('deleted-project')
  assert.equal(readMatchCutSession('deleted-project', 'analysis-1'), null)
  assert.equal(records.has('video-analyzer:match-cuts:v1:deleted-project'), false)
})

test('ignores broken JSON and invalid settings', () => {
  for (const [id, raw] of Object.entries({
    broken: '{', empty: '{}', invalid: JSON.stringify({ ...session, weights: null }),
    region: JSON.stringify({ ...session, region: 'bad' }),
    forms: JSON.stringify({ ...session, knownShapes: {} }),
  })) {
    records.set(`video-analyzer:match-cuts:v1:${id}`, raw)
    assert.equal(readMatchCutSession(id, 'analysis-1'), null)
  }
})

test('keeps the latest session if persistent storage fills up or is blocked', () => {
  saveMatchCutSession('quota', session)
  const storage = globalThis.localStorage
  globalThis.localStorage = {
    getItem() { throw new Error('Blocked') },
    setItem() { throw new Error('Quota exceeded') },
  }
  try {
    const latest = { ...session, seconds: 170 }
    saveMatchCutSession('quota', latest)
    assert.deepEqual(readMatchCutSession('quota', 'analysis-1'), latest)
    assert.equal(readMatchCutSession('quota', 'analysis-2'), null)
  } finally { globalThis.localStorage = storage }
  assert.equal(readMatchCutSession('quota', 'analysis-1').seconds, 170)
})
