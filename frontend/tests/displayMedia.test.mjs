import assert from 'node:assert/strict'
import test from 'node:test'
import { duration } from '../src/components/library/format.ts'
import { isVideoMedia } from '../src/api/nativeMedia.ts'

test('short camera samples retain a visible duration', () => {
  assert.equal(duration(0.5), '0.5s')
  assert.equal(duration(12 / 24), '0.5s')
  assert.equal(duration(61.5), '1:01')
})

test('Mac resource forks are not selectable video media', () => {
  assert.equal(isVideoMedia('movie.R3D'), true)
  assert.equal(isVideoMedia('._movie.R3D'), false)
  assert.equal(isVideoMedia('__MACOSX/movie.MOV'), false)
  assert.equal(isVideoMedia('card\\._movie.MXF'), false)
})
