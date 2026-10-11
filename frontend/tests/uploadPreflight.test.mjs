import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'

function loadUpload() {
  const source = readFileSync(new URL('../src/api/upload.ts', import.meta.url), 'utf8')
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
  let transports = 0
  const context = { exports: {}, require: () => ({ getApiKey: () => '' }), XMLHttpRequest: function () { transports++; throw new Error('Unexpected transfer') } }
  vm.runInNewContext(compiled, context)
  return { upload: context.exports.uploadVideo, transfers: () => transports }
}

test('rejects a 4.2 GB camera file before creating any upload transport', async () => {
  const api = loadUpload()
  await assert.rejects(api.upload({ name: 'camera.mxf', size: 4.2 * 1024 ** 3 }), /Link original/)
  assert.equal(api.transfers(), 0)
})

test('RAW originals use linking even when smaller than the copy limit', async () => {
  const api = loadUpload()
  for (const extension of ['braw', 'r3d', 'ari', 'arx', 'crm']) {
    await assert.rejects(api.upload({ name: `camera.${extension}`, size: 100 }), /Link original/)
  }
  assert.equal(api.transfers(), 0)
})
