import assert from 'node:assert/strict'
import { test } from 'node:test'
import { queueBatchAnalysis } from '../src/api/batchAnalysis.ts'

for (const mode of ['flash_only', 'flash_pro', 'cosplay']) {
  test(`queues each selected clip once with ${mode}, without waiting for analysis to finish`, async () => {
    const submitted = [], notified = []
    const results = await queueBatchAnalysis(['a', 'b', 'a', 'c'], mode, async (id, selectedMode) => {
      submitted.push([id, selectedMode])
      return { job_id: id, status: 'queued' }
    }, result => notified.push(result))
    assert.deepEqual(submitted, [['a', mode], ['b', mode], ['c', mode]])
    assert.deepEqual(results.map(result => result.id), ['a', 'b', 'c'])
    assert.ok(results.every(result => result.accepted))
    assert.deepEqual(notified, results)
  })
}

test('reports a rejected clip and continues queueing remaining selections', async () => {
  const selected = new Set(['missing', 'busy', 'ok']), errors = []
  await queueBatchAnalysis([...selected], 'flash_pro', async id => {
    if (id === 'missing') throw new Error('Asset not found')
    if (id === 'busy') throw new Error('This asset is already queued or analyzing')
  }, result => {
    if (result.accepted) selected.delete(result.id)
    else errors.push(result.error)
  })
  assert.deepEqual([...selected], ['missing', 'busy'])
  assert.deepEqual(errors, ['Asset not found', 'This asset is already queued or analyzing'])
})

test('emits each accepted clip immediately while the next request is still pending', async () => {
  let release
  const pending = new Promise(resolve => { release = resolve })
  const progress = []
  const done = queueBatchAnalysis(['first', 'second'], 'flash_only', async id => {
    if (id === 'second') await pending
  }, result => progress.push(result.id))
  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual(progress, ['first'])
  release()
  await done
  assert.deepEqual(progress, ['first', 'second'])
})
