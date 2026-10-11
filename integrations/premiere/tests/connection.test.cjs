const test = require('node:test');
const assert = require('node:assert/strict');
const { readConnection, discoveryPath, parseDescriptor, MAX_BYTES } = require('../connection');
const descriptor = { version: 1, server: 'http://127.0.0.1:8000', token: 't'.repeat(43), instance_id: '3cbdf73c-344e-4a7f-8988-869876b413e7' };

function filesystem(text, options = {}) {
  let closes = 0;
  const data = Buffer.from(text);
  return {
    fs: {
      async lstat(file) { return { isFile: () => true, isSymbolicLink: () => options.linked === file,
        size: options.size || data.length, ino: 1, mode: options.mode || 0o100600, mtimeMs: 1 }; },
      async open() { return 7; },
      async read(fd, buffer, offset, length, position) {
        assert.equal(fd, 7);
        const count = Math.min(length, data.length - position, 23);
        new Uint8Array(buffer, offset, count).set(data.subarray(position, position + count));
        return { bytesRead: count, buffer };
      },
      async close() { closes++; },
    },
    get closes() { return closes; },
  };
}

test('private discovery matches Windows and Mac companion locations', () => {
  assert.equal(discoveryPath('win32','C:\\Users\\User'), 'C:/Users/User/.video-analyzer/editor-service.json');
  assert.equal(discoveryPath('darwin','/Users/User'), '/Users/User/Library/Application Support/VideoAnalyzer/editor-service.json');
  assert.throws(() => discoveryPath('win32','\\\\network\\profile'));
});

test('discovery reads bounded chunks and closes the handle without persisting a credential', async () => {
  const mock = filesystem(JSON.stringify(descriptor));
  const result = await readConnection({ fs: mock.fs, os: { platform: () => 'win32', homedir: () => 'C:/Users/User' } });
  assert.equal(result.token, descriptor.token);
  assert.equal(result.server, 'http://localhost:8000');
  assert.equal(mock.closes, 1);
});

test('remote, malformed and insecure Mac descriptors never provide credentials', () => {
  for (const server of ['https://evil.test', 'http://localhost.evil.test:8000', 'http://localhost:8000/path', 'http://localhost:8000?token=leak']) {
    assert.throws(() => parseDescriptor(JSON.stringify({ ...descriptor, server }), 'win32'));
  }
  assert.throws(() => parseDescriptor(JSON.stringify(descriptor), 'darwin'), /requires HTTPS/);
  assert.throws(() => parseDescriptor(JSON.stringify({ ...descriptor, token: 'invalid' }), 'win32'));
});

test('oversized and symlink-backed discovery is rejected before opening', async () => {
  for (const options of [{ size: MAX_BYTES + 1 }, { linked: 'C:/Users/User/.video-analyzer' }]) {
    const mock = filesystem(JSON.stringify(descriptor), options);
    await assert.rejects(readConnection({ fs: mock.fs, os: { platform: () => 'win32', homedir: () => 'C:/Users/User' } }), /Start Video Analyzer/);
    assert.equal(mock.closes, 0);
  }
});

test('Mac refuses public-readable discovery files', async () => {
  const mock = filesystem(JSON.stringify({ ...descriptor, server: 'https://localhost:8443' }), { mode: 0o100644 });
  await assert.rejects(readConnection({ fs: mock.fs, os: { platform: () => 'darwin', homedir: () => '/Users/User' } }), /Start Video Analyzer/);
});

test('UXP stats without isSymbolicLink use native mode and still reject links', async () => {
  for (const mode of [0o100600, 0o120600, undefined]) {
    const mock = filesystem(JSON.stringify(descriptor));
    const stat = mock.fs.lstat;
    mock.fs.lstat = async file => { const info = await stat(file); delete info.isSymbolicLink; info.mode = mode; return info; };
    const operation = readConnection({ fs: mock.fs, os: { platform: () => 'win32', homedir: () => 'C:/Users/User' } });
    if (mode === 0o100600) assert.equal((await operation).server, 'http://localhost:8000');
    else await assert.rejects(operation, /Start Video Analyzer/);
  }
});

test('a descriptor replaced during reading is discarded and the handle is closed', async () => {
  const mock = filesystem(JSON.stringify(descriptor));
  let opened = false;
  const originalOpen = mock.fs.open;
  const originalStat = mock.fs.lstat;
  mock.fs.open = async () => { opened = true; return originalOpen(); };
  mock.fs.lstat = async file => ({ ...await originalStat(file), ino: opened ? 2 : 1 });
  await assert.rejects(readConnection({ fs: mock.fs, os: { platform: () => 'win32', homedir: () => 'C:/Users/User' } }), /Start Video Analyzer/);
  assert.equal(mock.closes, 1);
});
