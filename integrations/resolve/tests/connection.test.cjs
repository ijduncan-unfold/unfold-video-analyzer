"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { readConnection, discoveryPath, parseDescriptor, MAX_BYTES } = require("../connection");
const descriptor = { version: 1, server: "http://127.0.0.1:8000", token: "a".repeat(43), instance_id: "b6408716-238a-4ea1-bc23-82f35c4953d3" };
async function fixture(callback) {
  const folder = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), "video-analyzer-discovery-")));
  const file = path.join(folder, "editor-service.json");
  try { await fs.writeFile(file, JSON.stringify(descriptor), { mode: 0o600 }); await callback(file, folder); }
  finally { await fs.rm(folder, { recursive: true, force: true }); }
}
test("production paths are fixed per user and ignore development overrides", () => {
  const env = { LOCALAPPDATA: "C:\\Users\\Editor\\AppData\\Local", EDITOR_DISCOVERY_PATH: "C:\\elsewhere\\secret.json" };
  assert.equal(discoveryPath({ platform: "win32", env, development: false }), "C:\\Users\\Editor\\AppData\\Local\\VideoAnalyzer\\editor-service.json");
  assert.equal(discoveryPath({ platform: "darwin", env, home: "/Users/editor", development: false }), "/Users/editor/Library/Application Support/VideoAnalyzer/editor-service.json");
  assert.equal(discoveryPath({ env: { EDITOR_DISCOVERY_PATH: path.resolve("test-only.json") }, development: true }), path.resolve("test-only.json"));
});
test("descriptor requires version, permitted loopback endpoint, credential and instance identity", () => {
  assert.deepEqual(parseDescriptor(JSON.stringify(descriptor)), { server: descriptor.server, token: descriptor.token, instance_id: descriptor.instance_id });
  for (const patch of [{ version: 2 }, { token: "short" }, { token: "a".repeat(42) + "+" }, { server: "https://evil.example:8443" }, { server: "http://user@localhost:8000" }, { instance_id: "wrong" }]) assert.throws(() => parseDescriptor(JSON.stringify({ ...descriptor, ...patch })), /Start Video Analyzer/);
});
test("private regular descriptor is read only by the main-process discovery dependency", async () => fixture(async file => {
  const result = await readConnection({ file }); assert.equal(result.token, descriptor.token); assert.equal(result.instance_id, descriptor.instance_id);
}));
test("missing, malformed, directory and oversized discovery files produce no paths or credentials", async () => fixture(async (file, folder) => {
  for (const target of [file + ".missing", folder]) await assert.rejects(readConnection({ file: target }), error => !error.message.includes(folder) && !error.message.includes(descriptor.token));
  await fs.writeFile(file, JSON.stringify({ ...descriptor, token: descriptor.token, version: 0 }));
  await assert.rejects(readConnection({ file }), error => !error.message.includes(descriptor.token));
  await fs.writeFile(file, "x".repeat(MAX_BYTES + 1)); await assert.rejects(readConnection({ file }), /Start Video Analyzer/);
  await fs.writeFile(file, "{broken"); await assert.rejects(readConnection({ file }), /Start Video Analyzer/);
}));
test("directory symlinks and Windows junctions cannot redirect discovery", async () => fixture(async (file, folder) => {
  const actual = path.join(folder, "actual"), alias = path.join(folder, "alias"); await fs.mkdir(actual); await fs.writeFile(path.join(actual, "editor-service.json"), JSON.stringify(descriptor), { mode: 0o600 });
  await fs.symlink(actual, alias, process.platform === "win32" ? "junction" : "dir");
  await assert.rejects(readConnection({ file: path.join(alias, "editor-service.json") }), /Start Video Analyzer/);
}));
test("group-readable or world-readable POSIX credential files are refused", { skip: process.platform === "win32" }, async () => fixture(async file => {
  await fs.chmod(file, 0o644); await assert.rejects(readConnection({ file }), /Start Video Analyzer/); await fs.chmod(file, 0o600); assert.equal((await readConnection({ file })).token, descriptor.token);
}));
test("Windows virtualized realpath spelling is accepted only for the same regular file identity", async () => fixture(async (file, folder) => {
  const alias = path.join(folder, "virtualized-editor-service.json"); await fs.link(file, alias);
  const module = { exports: {} };
  vm.runInNewContext(await fs.readFile(path.join(__dirname, "../connection.js"), "utf8"), { module, Buffer, process,
    require(name) { if (name === "node:fs/promises") return { ...fs, async realpath(value) { return value === file ? alias : fs.realpath(value); } }; if (name === "./service") return require("../service"); return require(name); } });
  assert.equal((await module.exports.readConnection({ file, platform: "win32" })).token, descriptor.token);
  await fs.unlink(alias); await fs.writeFile(alias, JSON.stringify(descriptor), { mode: 0o600 });
  await assert.rejects(module.exports.readConnection({ file, platform: "win32" }), /Start Video Analyzer/);
}));
