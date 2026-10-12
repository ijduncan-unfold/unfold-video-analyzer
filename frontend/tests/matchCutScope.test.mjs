import assert from 'node:assert/strict'
import { test } from 'node:test'
import { resolveMatchCutScope } from '../src/components/library/matchCutScope.ts'
import { searchAcrossMedia } from '../src/components/library/matchCutBatch.ts'

const media = [
  { id: 'source', status: 'complete', shot_count: 1, folder_id: 'day1', has_local_media: true },
  { id: 'take2', status: 'complete', shot_count: 1, folder_id: 'day1', has_local_media: true },
  { id: 'film', status: 'complete', shot_count: 40, folder_id: 'day2', has_local_media: true },
  { id: 'loose', status: 'error', shot_count: 1, folder_id: null, has_local_media: true },
  { id: 'busy', status: 'analyzing', shot_count: 1, folder_id: 'day1', has_local_media: true },
  { id: 'new', status: 'ready', shot_count: 0, folder_id: 'day1', has_local_media: true },
  { id: 'youtube', status: 'complete', shot_count: 12, folder_id: 'day1', has_local_media: false },
]

test('all media includes individual takes, edited films and saved partial analysis', () => {
  assert.deepEqual(resolveMatchCutScope({ mode: 'all', mediaIds: [], folderIds: [] }, media, 'source'), ['film', 'loose', 'source', 'take2'])
})
test('selected media and multiple folders combine without duplicate recordings', () => {
  const scope = { mode: 'selected', mediaIds: ['take2', 'film'], folderIds: ['day1', 'unfiled'] }
  assert.deepEqual(resolveMatchCutScope(scope, media, 'source'), ['film', 'loose', 'source', 'take2'])
  assert.deepEqual(resolveMatchCutScope({ mode: 'current', mediaIds: [], folderIds: [] }, media, 'source'), ['source'])
})
test('folder scopes follow imported and moved media without changing saved selection', () => {
  const scope = { mode: 'selected', mediaIds: [], folderIds: ['day2'] }
  assert.deepEqual(resolveMatchCutScope(scope, media, 'source'), ['film'])
  assert.deepEqual(resolveMatchCutScope(scope, [...media, { id: 'added', status: 'complete', shot_count: 1, folder_id: 'day2' }], 'source'), ['added', 'film'])
  assert.deepEqual(resolveMatchCutScope(scope, media.map(item => ({ ...item, folder_id: null })), 'source'), [])
})
test('more than 20 sources produce one global ranking instead of a truncated library', async () => {
  const ids = Array.from({ length: 45 }, (_, index) => `media-${index}`)
  const calls = [], progress = []
  const result = await searchAcrossMedia([...ids, ids[0]], async batch => {
    calls.push(batch)
    return { matches: batch.map(id => ({ job_id: id, shot_number: 1, score: Number(id.split('-')[1]) / 100 })), indexes: batch.map(job_id => ({ job_id })), score_basis: 'test' }
  }, undefined, (_, count) => progress.push(count))
  assert.deepEqual(calls.map(batch => batch.length), [20, 20, 5])
  assert.deepEqual(progress, [20, 40, 45])
  assert.equal(result.indexes.length, 45)
  assert.equal(result.matches.length, 12)
  assert.equal(result.matches[0].job_id, 'media-44')
})
test('canceling a large search prevents subsequent requests and publishing late results', async () => {
  const controller = new AbortController()
  let calls = 0, updates = 0
  await assert.rejects(searchAcrossMedia(Array.from({ length: 21 }, (_, i) => String(i)), async () => {
    calls++; controller.abort(); return { matches: [], indexes: [] }
  }, controller.signal, () => updates++), { name: 'AbortError' })
  assert.equal(calls, 1); assert.equal(updates, 0)
})
