"use strict";
// This module runs only in Electron's main process. The renderer cannot choose
// the discovery path, inspect the descriptor, or access its bearer credential.
const fs = require("node:fs/promises");
const constants = require("node:fs").constants;
const path = require("node:path");
const os = require("node:os");
const { normalizeServer } = require("./service");
const MAX_BYTES = 8192;
const UNAVAILABLE = "Start Video Analyzer. This panel connects automatically.";
function discoveryPath({ platform = process.platform, env = process.env, home = os.homedir(), development = !process.versions.electron } = {}) {
  if (development && env.EDITOR_DISCOVERY_PATH) return path.resolve(env.EDITOR_DISCOVERY_PATH);
  if (platform === "win32" && env.LOCALAPPDATA) return path.win32.join(env.LOCALAPPDATA, "VideoAnalyzer", "editor-service.json");
  if (platform === "darwin") return path.posix.join(home, "Library", "Application Support", "VideoAnalyzer", "editor-service.json");
  throw new Error(UNAVAILABLE);
}
function parseDescriptor(text) {
  let value;
  try { value = JSON.parse(text); } catch (_) { throw new Error(UNAVAILABLE); }
  if (!value || Array.isArray(value) || value.version !== 1 || typeof value.token !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(value.token) ||
      typeof value.instance_id !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value.instance_id)) throw new Error(UNAVAILABLE);
  let server;
  try { server = normalizeServer(value.server); } catch (_) { throw new Error(UNAVAILABLE); }
  return { server, token: value.token, instance_id: value.instance_id };
}
async function readConnection(options = {}) {
  let handle;
  try {
    const file = options.file || discoveryPath(options), platform = options.platform || process.platform;
    // lstat every component so a Windows junction/reparse-backed alias or POSIX
    // symlink cannot redirect credential discovery into a different directory.
    async function checkAncestors(value) {
      const resolved = path.resolve(value), parsed = path.parse(resolved);
      let current = parsed.root;
      for (const component of path.relative(parsed.root, resolved).split(path.sep)) {
        current = path.join(current, component);
        const info = await fs.lstat(current);
        if (info.isSymbolicLink()) throw new Error(UNAVAILABLE);
      }
    }
    await checkAncestors(file);
    const before = await fs.lstat(file);
    if (!before.isFile() || before.size < 1 || before.size > MAX_BYTES || before.isSymbolicLink()) throw new Error(UNAVAILABLE);
    if (platform !== "win32" && ((before.mode & 0o077) !== 0 || typeof process.getuid === "function" && before.uid !== process.getuid())) throw new Error(UNAVAILABLE);
    const actual = await fs.realpath(file);
    await checkAncestors(actual);
    const canonical = await fs.lstat(actual);
    // Windows package filesystem virtualization can give the same regular file
    // a different realpath spelling without any junction or symlink. Compare
    // physical identity, retaining link checks on both directory chains.
    if (!canonical.isFile() || canonical.isSymbolicLink() || canonical.dev !== before.dev || canonical.ino !== before.ino || platform !== "win32" && actual !== path.resolve(file)) throw new Error(UNAVAILABLE);
    handle = await fs.open(file, constants.O_RDONLY | (constants.O_NOFOLLOW || 0));
    const opened = await handle.stat();
    if (!opened.isFile() || opened.dev !== before.dev || opened.ino !== before.ino || opened.size > MAX_BYTES || platform !== "win32" && ((opened.mode & 0o077) !== 0 || typeof process.getuid === "function" && opened.uid !== process.getuid())) throw new Error(UNAVAILABLE);
    const buffer = Buffer.alloc(MAX_BYTES + 1);
    const { bytesRead } = await handle.read(buffer, 0, buffer.length, 0);
    if (bytesRead < 1 || bytesRead > MAX_BYTES) throw new Error(UNAVAILABLE);
    const after = await fs.lstat(file);
    if (after.isSymbolicLink() || after.dev !== opened.dev || after.ino !== opened.ino) throw new Error(UNAVAILABLE);
    await checkAncestors(file); await checkAncestors(actual);
    return parseDescriptor(buffer.subarray(0, bytesRead).toString("utf8"));
  } catch (_) { throw new Error(UNAVAILABLE); }
  finally { if (handle) await handle.close().catch(() => {}); }
}
module.exports = { readConnection, discoveryPath, parseDescriptor, UNAVAILABLE, MAX_BYTES };
