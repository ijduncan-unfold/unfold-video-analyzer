/* UXP filesystem API: private same-user discovery, with no credential UI. */
const MAX_BYTES = 8192;
const UNAVAILABLE = "Start Video Analyzer, then click Connect. The panel finds it automatically.";

function discoveryPath(platform, home) {
  const root = String(home || "").replace(/\\/g, "/").replace(/\/$/, "");
  if (platform === "win32" && /^[a-z]:\//i.test(root)) return root + "/.video-analyzer/editor-service.json";
  if (platform === "darwin" && root.startsWith("/") && !root.startsWith("//")) return root + "/Library/Application Support/VideoAnalyzer/editor-service.json";
  throw new Error(UNAVAILABLE);
}

function parseDescriptor(text, platform) {
  let value;
  try { value = JSON.parse(text); } catch (_) { throw new Error(UNAVAILABLE); }
  if (!value || value.version !== 1 || typeof value.token !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(value.token) ||
      typeof value.instance_id !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value.instance_id)) throw new Error(UNAVAILABLE);
  if (!/^(http:\/\/(localhost|127\.0\.0\.1):8000|https:\/\/(localhost|127\.0\.0\.1):8443)\/?$/.test(value.server)) throw new Error(UNAVAILABLE);
  if (platform === "darwin" && !value.server.startsWith("https://")) throw new Error("Premiere on Mac requires HTTPS. Complete certificate setup and start Video Analyzer on https://localhost:8443.");
  // UXP rejects IP-literal permission entries. Use its supported localhost name.
  return { server: value.server.replace(/\/$/, "").replace("://127.0.0.1:", "://localhost:"), token: value.token, instance_id: value.instance_id };
}

function symbolicLink(info) {
  if (typeof info.isSymbolicLink === "function") return info.isSymbolicLink();
  // UXP omits some Node Stats methods; lstat still returns the native mode.
  if (Number.isInteger(info.mode) && (info.mode & 0o170000)) return (info.mode & 0o170000) === 0o120000;
  throw new Error(UNAVAILABLE);
}

async function readConnection(options = {}) {
  const os = options.os || require("os");
  const fs = options.fs || require("fs");
  const platform = os.platform(), file = discoveryPath(platform, os.homedir());
  let fd;
  let stage = "stat";
  try {
    async function inspect() {
      const pieces = file.split("/");
      let current = pieces.shift();
      for (const piece of pieces) {
        current += "/" + piece;
        stage = "stat ancestor " + pieces.indexOf(piece);
        const info = await fs.lstat(current);
        stage = "symlink check";
        if (symbolicLink(info)) throw new Error(UNAVAILABLE);
        stage = "stat";
      }
      const info = await fs.lstat(file);
      if (!info.isFile() || info.size < 1 || info.size > MAX_BYTES ||
          platform === "darwin" && (info.mode & 0o077)) throw new Error(UNAVAILABLE);
      return info;
    }
    const before = await inspect();
    stage = "open";
    fd = await fs.open(file, "r");
    const buffer = new ArrayBuffer(MAX_BYTES + 1);
    let total = 0;
    while (total < buffer.byteLength) {
      stage = "read";
      const result = await fs.read(fd, buffer, total, buffer.byteLength - total, total);
      if (!result.bytesRead) break;
      total += result.bytesRead;
    }
    const after = await inspect();
    stage = "file consistency";
    if (!total || total > MAX_BYTES || total !== before.size || before.dev !== after.dev ||
        before.ino !== after.ino || before.size !== after.size || before.mtimeMs !== after.mtimeMs) throw new Error(UNAVAILABLE);
    // Connection descriptors contain only ASCII endpoints and credentials.
    const bytes = new Uint8Array(buffer, 0, total);
    if (bytes.some(value => value > 127)) throw new Error(UNAVAILABLE);
    stage = "descriptor validation";
    return parseDescriptor(Array.from(bytes, value => String.fromCharCode(value)).join(""), platform);
  } catch (error) {
    console.warn("Video Analyzer discovery failed at " + stage + " (" + error.name + "; " + (/^[A-Z0-9_]+$/.test(error.code) ? error.code : "no code") + ")");
    if (error.message && error.message.startsWith("Premiere on Mac requires HTTPS")) throw error;
    throw new Error(UNAVAILABLE);
  } finally { if (fd !== undefined) await fs.close(fd).catch(() => {}); }
}

module.exports = { readConnection, discoveryPath, parseDescriptor, symbolicLink, MAX_BYTES, UNAVAILABLE };
