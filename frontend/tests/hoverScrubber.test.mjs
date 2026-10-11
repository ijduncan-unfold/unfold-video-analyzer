import assert from 'node:assert/strict'
import test from 'node:test'
import { createHoverScrubber } from '../src/components/library/hoverScrubber.ts'

class Media extends EventTarget {
  duration = 100; readyState = 2; seeking = false; at = 0; seeks = []
  get currentTime() { return this.at }
  set currentTime(value) { this.at = value; this.seeking = true; this.seeks.push(value) }
  land() { this.seeking = false; this.readyState = 2; this.dispatchEvent(new Event('seeked')) }
}
function frames(t) {
  const queue = new Map(); let id = 0
  const oldRequest = globalThis.requestAnimationFrame, oldCancel = globalThis.cancelAnimationFrame
  globalThis.requestAnimationFrame = callback => { queue.set(++id, callback); return id }
  globalThis.cancelAnimationFrame = id => queue.delete(id)
  t.after(() => { globalThis.requestAnimationFrame = oldRequest; globalThis.cancelAnimationFrame = oldCancel })
  return () => { const callbacks = [...queue.values()]; queue.clear(); callbacks.forEach(callback => callback()) }
}

test('rapid pointer reversal lands on the latest position without overlapping seeks', t => {
  const flush = frames(t), video = new Media(), shown = []
  const scrub = createHoverScrubber(video, 0, undefined, seconds => shown.push(seconds))
  scrub.seek(.2); scrub.seek(.8); flush()
  assert.deepEqual(video.seeks, [80])
  scrub.seek(.6); scrub.seek(.1); flush()
  assert.deepEqual(video.seeks, [80])
  video.land(); flush()
  assert.deepEqual(video.seeks, [80, 10])
  video.land(); flush(); flush()
  assert.deepEqual(shown, [80, 10])
  assert.deepEqual(video.seeks, [80, 10])
  scrub.dispose()
})

test('a position chosen during loading is applied to a half-second recording', t => {
  const flush = frames(t), video = new Media()
  video.duration = NaN; video.readyState = 0
  const scrub = createHoverScrubber(video, 0, undefined, () => {})
  scrub.seek(.8); flush(); assert.equal(video.seeks.length, 0)
  video.duration = .5; video.readyState = 1; video.dispatchEvent(new Event('loadedmetadata')); flush()
  assert.equal(video.seeks[0], .4)
  video.land(); scrub.seek(1); flush()
  assert.equal(video.seeks[1], .499)
  scrub.dispose()
})

test('clip previews remain inside their source in/out range', t => {
  const flush = frames(t), video = new Media()
  const scrub = createHoverScrubber(video, 10, 20, () => {})
  scrub.seek(.75); flush(); assert.equal(video.seeks[0], 17.5)
  video.land(); scrub.seek(-2); flush(); assert.equal(video.seeks[1], 10)
  video.land(); scrub.seek(3); flush(); assert.equal(video.seeks[2], 19.999)
  scrub.dispose()
})

test('leaving cancels pending work and prevents late frames from updating a card', t => {
  const flush = frames(t), video = new Media(), shown = []
  const scrub = createHoverScrubber(video, 0, undefined, seconds => shown.push(seconds))
  scrub.seek(.5); flush(); scrub.seek(.9); scrub.dispose(); video.land(); flush()
  assert.deepEqual(video.seeks, [50]); assert.deepEqual(shown, [])
})

test('rounded media timestamps do not cause endless repeated seeks', t => {
  const flush = frames(t), video = new Media()
  const scrub = createHoverScrubber(video, 0, undefined, () => {})
  scrub.seek(.33333); flush(); video.at = 33.33; video.land(); flush(); flush()
  assert.equal(video.seeks.length, 1)
  scrub.dispose()
})
