import test from 'node:test'
import assert from 'node:assert/strict'
import { playbackError } from '../src/components/library/playbackError.ts'

test('keeps the service explanation when the NAS or proxy is unavailable', () => {
  const message = 'Original media is offline and no cached playback proxy is available.'
  assert.equal(playbackError({ available: false, message }), message)
})

test('distinguishes browser decode failure from unavailable source media', () => {
  assert.match(playbackError({ available: true, kind: 'original', message: '' }), /original is available/)
  const proxyError = playbackError({ available: true, kind: 'cached_proxy', original_offline: true, message: '' })
  assert.match(proxyError, /proxy could not be played/)
  assert.match(proxyError, /original media is also offline/)
})
