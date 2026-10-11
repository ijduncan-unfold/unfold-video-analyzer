import assert from 'node:assert/strict'
import { test } from 'node:test'
import { chooseNativeFolders, discoverNativeMedia, getDroppedFolders, isVideoMedia, resolveBrowserFolderDrop } from '../src/api/nativeMedia.ts'

function fileEntry(name, contents = 'original', slices = []) {
  const file = new File([contents], name, { lastModified: 123456 })
  const slice = file.slice.bind(file)
  file.slice = (start, end) => { slices.push([start, end]); return slice(start, end) }
  return { name, isFile: true, isDirectory: false, file: success => success(file) }
}

function directory(name, batches) {
  return { name, isDirectory: true, isFile: false, createReader() {
    let index = 0
    return { readEntries: success => success(batches[index++] || []) }
  } }
}

function response(value, ok = true) { return { ok, json: async () => value } }

test('browser folder discovery crosses reader batches and preserves nested UNC roots with bounded reads', async t => {
  const slices = []
  const folder = directory('Card A', [[fileEntry('notes.xml')], [directory('Clip', [[directory('C001', [[fileEntry('C001.MXF', 'x'.repeat(300000), slices)]])]])]])
  const calls = []
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    calls.push([url, JSON.parse(init.body)])
    return response({ media_path: '\\\\server\\share\\Card A\\Clip\\C001\\C001.MXF' })
  })
  assert.equal(await resolveBrowserFolderDrop(folder), '\\\\server\\share\\Card A')
  assert.deepEqual(slices, [[0, 65536], [234464, undefined]])
  assert.equal(calls.length, 1)
  assert.equal(calls[0][0], '/api/local-media/resolve-drop')
  assert.equal(calls[0][1].name, 'C001.MXF')
  assert.match(calls[0][1].edge_sha256, /^[0-9a-f]{64}$/)
})

test('folder matching requires the complete relative path, not a repeated folder or filename', async t => {
  t.mock.method(globalThis, 'fetch', async () => response({ media_path: '/Volumes/Other Card/Clip/camera.mp4' }))
  assert.equal(await resolveBrowserFolderDrop(directory('Card', [[directory('Clip', [[fileEntry('camera.mp4')]])]])), null)
})

test('unknown, empty or unreadable browser folders fall back without copying video data', async t => {
  t.mock.method(globalThis, 'fetch', async () => response({ media_path: null }))
  assert.equal(await resolveBrowserFolderDrop(directory('Empty', [[fileEntry('notes.xml')]])), null)
  assert.equal(await resolveBrowserFolderDrop(directory('New Card', [[fileEntry('camera.mp4')]])), null)
  assert.equal(await resolveBrowserFolderDrop({ name: 'Offline', isDirectory: true, createReader() { throw new Error('unreadable') } }), null)
})

test('native folder picker and server discovery pass paths instead of file bytes', async t => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'window')
  Object.defineProperty(globalThis, 'window', { configurable: true, value: { pywebview: { api: { choose_folders: async () => ['C:\\Card A', '\\\\server\\Card B'] } } } })
  t.after(() => { if (previous) Object.defineProperty(globalThis, 'window', previous); else delete globalThis.window })
  assert.deepEqual(await chooseNativeFolders(), ['C:\\Card A', '\\\\server\\Card B'])
  const calls = []
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    calls.push([url, JSON.parse(init.body)])
    return response({ paths: ['/Volumes/Card/clip.mov'], warnings: [] })
  })
  assert.deepEqual(await discoverNativeMedia(['/Volumes/Card']), { paths: ['/Volumes/Card/clip.mov'], warnings: [] })
  assert.deepEqual(calls, [['/api/local-media/discover', { paths: ['/Volumes/Card'] }]])
})

test('drop captures directories while video filtering skips camera sidecars', () => {
  const folder = directory('Card', [])
  assert.deepEqual(getDroppedFolders({ items: [{ webkitGetAsEntry: () => folder }, { webkitGetAsEntry: () => fileEntry('clip.mov') }] }), [folder])
  for (const name of ['camera.MXF', 'part_001.R3D', 'frame.00001.ari', 'proxy.mp4']) assert.equal(isVideoMedia(name), true)
  for (const name of ['slate.jpg', 'sidecar.xml', 'sound.wav', 'notes.txt']) assert.equal(isVideoMedia(name), false)
})
