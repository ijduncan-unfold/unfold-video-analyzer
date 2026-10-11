"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { createService, normalizeServer, sanitizePreferences, sourceBinding } = require("../service");
const token = "a".repeat(43);
const instanceId = "b6408716-238a-4ea1-bc23-82f35c4953d3";
const source = { host: "resolve", project_id: "project-1", item_id: "clip-1", media_path: "C:\\original.mp4", source_fps: 24 };
const original = { job_id: "asset-1", filename: "original.mp4", metadata: { title: "Original" }, status: "complete", technical: { duration_seconds: 10 }, editor_sources: [source], analysis_config: { started_at: "session-1" }, transcript: [{ start: 1, end: 2, text: "Hello world", speaker: "Guard" }] };
function fixture(options = {}) {
  let project = { id: "project-1", name: "Film" }, asset = structuredClone(original), content = options.preferences || "{}";
  const requests = [], native = [], writes = [], exports = [], copies = [], browser = [], discoveries = [];
  let connection = { token, instance_id: instanceId, server: options.platform === "darwin" ? "https://localhost:8443" : "http://127.0.0.1:8000", ...options.connection };
  const host = { async getProjectIdentity(expected) { if (expected && project.id !== expected) throw new Error("Project changed"); return { ...project }; },
    async getSelection() { return { project_id: project.id, project_name: project.name, clips: [{ item_id: "clip-1", name: "Original", media_path: source.media_path, source_fps: 24, source_frames: 240 }], warnings: [] }; },
    async resolveBinding(binding) { await host.getProjectIdentity(binding.project_id); if (options.relinked) throw new Error("Media changed"); return {}; },
    async selectClip(binding) { native.push(["select", binding]); return { selected: true }; }, async addMarkers(binding, technical, cues) { native.push(["markers", binding, cues]); return { added: cues.length, duplicates: 0, collisions: 0 }; },
    async exportShots(binding, pack, destination) { native.push(["shots", binding, pack, destination]); if (options.exportShots) return options.exportShots(binding, pack, destination); return { created: pack.shots.length, reused: 0, added: destination === "pool" ? 0 : pack.shots.length }; },
    async syncMetadata(binding, mapping) { native.push(['metadata', binding, mapping]); return { updated: 3, preserved: 1 }; },
  };
  const service = createService({ platform: options.platform || "win32", host,
    async readConnection() { discoveries.push(true); if (options.discoveryError) throw new Error(options.discoveryError); return connection; },
    preferences: { async read() { return content; }, async write(value) { content = value; writes.push(value); } }, clipboard: { writeText(value) { copies.push(value); } },
    async saveSubtitle(name, value) { exports.push([name, value]); return true; }, async openBrowser(value) { browser.push(value); },
    async fetch(url, init) {
      requests.push([url, init]); if (options.onFetch) { const response = await options.onFetch(url, init); if (response) return response; }
      const endpoint = new URL(url), apiPath = endpoint.pathname.replace("/api/editor/resolve", ""); let value;
      if (apiPath === "/health") value = { host: options.healthHost || "resolve", protocol_version: options.protocolVersion || 1, instance_id: options.healthInstance || connection.instance_id, google_configured: true };
      else if (apiPath === "/assets") value = { assets: !options.emptyLibrary && endpoint.searchParams.get("project_id") === "project-1" ? [asset] : [] };
      else if (apiPath === "/register") { value = asset; }
      else if (apiPath === "/assets/asset-1") value = asset;
      else if (apiPath === "/assets/asset-1/shots") value = { shots: [{ shot_number: 1, start: 0, end: 2, visual_description: "A centered circle", tags: ["circle"], match_context: "Centered composition" }], total: 1 };
      else if (apiPath === "/assets/asset-1/analyze") value = { status: "queued" };
      else if (apiPath === '/assets/asset-1/metadata') value = { schema_version: '1.0', fields: [], record: { asset: { id: asset.job_id }, transcript: asset.transcript } };
      else if (apiPath === "/assets/asset-1/shot-package") value = { asset_id: asset.job_id, analysis_session: asset.analysis_config.started_at, technical: asset.technical,
        shots: JSON.parse(init.body).shots.map(cue => ({ shot_number: cue.shot_number, start_seconds: cue.start, end_seconds: cue.end, visual_description: "Authoritative public metadata", section_analysis: { composition: "Centered circle" } })) };
      else if (apiPath === "/assets/asset-1/subtitles") return { ok: true, status: 200, text: async () => "1\n00:00:01,000 --> 00:00:02,000\nHello world\n" };
      else throw new Error("Unexpected path " + apiPath);
      return { ok: true, status: 200, text: async () => JSON.stringify(value) };
    },
  });
  return { service, requests, native, writes, exports, copies, browser, discoveries, host, async connect() { return service.invoke("bootstrap"); }, setConnection(value) { connection = value; }, setProject(id) { project = { id, name: id }; }, updateAsset(patch) { Object.assign(asset, patch); }, getAsset() { return asset; } };
}
test("endpoint allowlist rejects remote hosts, credentials, alternate ports and suffixes", () => { for (const value of ["http://localhost:8000.evil", "https://localhost:8000", "http://127.0.0.1:8443", "http://localhost:8000/path", "http://user@localhost:8000", "http://localhost:8000?x=1", "file:///secret", "https://example.com:8443"]) assert.throws(() => normalizeServer(value)); assert.equal(normalizeServer(" HTTPS://LOCALHOST:8443/ "), "https://localhost:8443"); });
test("Mac defaults to HTTPS, Windows defaults to loopback HTTP", async () => { const mac = fixture({ platform: "darwin" }); await mac.connect(); assert.match(mac.requests[0][0], /^https:\/\/localhost:8443/); const win = fixture(); await win.connect(); assert.match(win.requests[0][0], /^http:\/\/127\.0\.0\.1:8000/); });
test("connection uses main-owned bearer and prohibits redirects", async () => { const m = fixture(); const state = await m.connect(); assert.equal(state.connected, true); assert.equal(m.requests[0][1].headers.Authorization, "Bearer " + token); assert.equal(m.requests[0][1].redirect, "error"); assert.equal(JSON.stringify(state).includes(token), false); });
test("valid connection restores project clips without starting analysis", async () => { const m = fixture(); const state = await m.connect(); assert.equal(state.asset.job_id, "asset-1"); assert.equal(state.results[0].name, "Shot 01"); assert.equal(m.requests.some(([url]) => url.endsWith("/analyze")), false); });
test("the Premiere protocol is rejected for a Resolve connection", async () => { const m = fixture({ healthHost: "premiere" }); const state = await m.connect(); assert.match(state.message, /Update Video Analyzer/); assert.equal(state.connected, false); assert.equal(m.requests.length, 1); });
test("invalid discovered credential never reaches a local service", async () => { const m = fixture({ connection: { token: "bad" } }); assert.equal((await m.connect()).connected, false); assert.equal(m.requests.length, 0); });
test("disallowed discovered endpoint never receives a credential", async () => { const m = fixture({ connection: { server: "https://example.org" } }); assert.equal((await m.connect()).connected, false); assert.equal(m.requests.length, 0); });
test("renderer cannot provide a token, endpoint or discovery path", async () => { const m = fixture(); for (const payload of [{ token }, { server: "http://localhost:8000" }, { file: "C:\\private" }]) await assert.rejects(m.service.invoke("connect", payload), /Unsupported.*field/); assert.equal(m.discoveries.length, 0); });
test("preferences retain per-project clip, mode, tab and queries, with no raw token", async () => { const m = fixture(); await m.connect(); await m.service.invoke("setView", { tab: "transcript", query: "world", mode: "flash_pro" }); const saved = JSON.parse(m.writes.at(-1)); assert.equal(saved.projects["project-1"].assetId, "asset-1"); assert.equal(saved.projects["project-1"].queries.transcript, "world"); assert.equal(saved.projects["project-1"].mode, "flash_pro"); assert.equal(m.writes.join("").includes(token), false); });
test("saved transcript search and full analysis mode are restored", async () => { const m = fixture({ preferences: JSON.stringify({ projects: { "project-1": { assetId: "asset-1", tab: "transcript", mode: "flash_pro", queries: { transcript: "hello" } } } }) }); const state = await m.connect(); assert.equal(state.tab, "transcript"); assert.equal(state.mode, "flash_pro"); assert.equal(state.queries.transcript, "hello"); assert.equal(state.results.length, 1); });
test("unknown fields and prototype-bearing preference data are discarded", () => { const clean = sanitizePreferences(JSON.parse('{"token":"secret","projects":{"__proto__":{"tab":"transcript"},"p":{"queries":{"shots":"circle"},"token":"secret"}}}'), "http://127.0.0.1:8000"); assert.equal(clean.token, undefined); assert.equal(Object.hasOwn(clean.projects, "__proto__"), false); assert.equal(clean.projects.p.token, undefined); });
test("search query is encoded and never promoted to an arbitrary URL", async () => { const m = fixture(); await m.connect(); await m.service.invoke("setView", { query: "circle & center" }); assert.ok(m.requests.some(([url]) => url.includes("q=circle%20%26%20center"))); });
test("load selection registers only native-selected original descriptors", async () => { const m = fixture(); await m.connect(); await m.service.invoke("loadSelection"); const registration = m.requests.find(([url]) => url.endsWith("/register")); const body = JSON.parse(registration[1].body); assert.deepEqual(Object.keys(body).sort(), ["item_id", "media_path", "name", "project_id", "source_fps"]); assert.equal(body.media_path, source.media_path); assert.equal(m.requests.some(([url]) => url.endsWith("/analyze")), false); });
test("full analysis is explicit and posts the chosen mode", async () => { const m = fixture(); await m.connect(); let state = await m.service.invoke("setView", { mode: "flash_pro" }); state = await m.service.invoke("analyze", { revision: state.revision }); const call = m.requests.find(([url]) => url.endsWith("/analyze")); assert.deepEqual(JSON.parse(call[1].body), { mode: "flash_pro" }); assert.equal(state.asset.status, "analyzing"); });
test("progressive refresh exposes newly finished shots and dialogue", async () => { const m = fixture(); await m.connect(); m.updateAsset({ status: "analyzing", shot_count: 10, analysis_progress: { completed_sections: 2, total_sections: 4 }, transcript: [{ start: 3, end: 4, text: "New dialogue" }] }); let state = await m.service.invoke("refresh"); assert.equal(state.asset.shot_count, 10); assert.equal(state.asset.analysis_progress.completed_sections, 2); state = await m.service.invoke("setView", { tab: "transcript" }); assert.equal(state.results[0].text, "New dialogue"); });
test("stale result revisions cannot select sources or write markers", async () => { const m = fixture(); const state = await m.connect(); await m.service.invoke("setView", { query: "circle" }); await assert.rejects(m.service.invoke("selectClip", { revision: state.revision, index: 0 }), /results changed/); await assert.rejects(m.service.invoke("addMarkers", { revision: state.revision, count: 1 }), /results changed/); assert.equal(m.native.length, 0); });
test("server rebind blocks native selection and markers", async () => { const m = fixture(); const state = await m.connect(); m.updateAsset({ editor_sources: [{ ...source, item_id: "different" }] }); await assert.rejects(m.service.invoke("selectClip", { revision: state.revision }), /source binding/); assert.equal(m.native.length, 0); });
test("new analysis session invalidates prior result actions", async () => { const m = fixture(); const state = await m.connect(); m.updateAsset({ analysis_config: { started_at: "session-2" } }); await assert.rejects(m.service.invoke("copyTimes", { revision: state.revision, index: 0 }), /analysis changed/); assert.equal(m.copies.length, 0); });
test("native relink check also blocks analysis and subtitle export", async () => { const m = fixture({ relinked: true }); const state = await m.connect(); await assert.rejects(m.service.invoke("analyze", { revision: state.revision }), /Media changed/); await assert.rejects(m.service.invoke("exportSubtitles", { revision: state.revision }), /Media changed/); assert.equal(m.exports.length, 0); assert.equal(m.requests.some(([url]) => url.endsWith("/analyze")), false); });
test("project switch removes previous project's results", async () => { const m = fixture(); await m.connect(); m.setProject("other"); const state = await m.service.invoke("refresh"); assert.equal(state.project.id, "other"); assert.equal(state.asset, null); assert.equal(state.results.length, 0); });
test("project switch before native action prevents writes", async () => { const m = fixture(); const state = await m.connect(); m.setProject("other"); await assert.rejects(m.service.invoke("selectClip", { revision: state.revision }), /Project changed/); assert.equal(m.native.length, 0); });
test("foreign host or project bindings are never adopted", async () => { const m = fixture(); m.updateAsset({ editor_sources: [{ ...source, host: "premiere" }] }); const state = await m.connect(); assert.equal(state.asset, null); assert.equal(state.assets.length, 0); assert.equal(sourceBinding({ editor_sources: [source] }, "other"), null); });
test("copy range derives text from current server result, never renderer text", async () => { const m = fixture(); const state = await m.connect(); await m.service.invoke("copyTimes", { revision: state.revision, index: 0 }); assert.equal(m.copies[0], "00:00.000 – 00:02.000"); await assert.rejects(m.service.invoke("copyTimes", { revision: state.revision, index: 0, text: "arbitrary" }), /Unsupported.*field/); });
test("source marker export derives cues from current results, with visible count", async () => { const m = fixture(); const state = await m.connect(); await m.service.invoke("addMarkers", { revision: state.revision, count: 1 }); assert.equal(m.native[0][0], "markers"); assert.equal(m.native[0][2][0].name, "Shot 01"); await assert.rejects(m.service.invoke("addMarkers", { revision: state.revision, count: 1, cues: [] }), /Unsupported.*field/); });
test("SRT export uses main-controlled filename, dialog, and backend subtitle content", async () => { const m = fixture(); const state = await m.connect(); await m.service.invoke("exportSubtitles", { revision: state.revision }); assert.equal(m.exports[0][0], "original_subtitles.srt"); assert.match(m.exports[0][1], /Hello world/); await assert.rejects(m.service.invoke("exportSubtitles", { revision: state.revision, filePath: "C:\\secret" }), /Unsupported.*field/); });
test("opening full analyzer allows only the discovered loopback root", async () => { const m = fixture(); await m.connect(); await m.service.invoke("openAnalyzer"); assert.deepEqual(m.browser, ["http://127.0.0.1:8000/"]); await assert.rejects(m.service.invoke("openAnalyzer", { url: "https://evil" }), /Unsupported.*field/); });
test("shot export sends only current identities and ranges, and native writes use the fresh server package", async () => {
  const m = fixture(); const state = await m.connect(); await m.service.invoke("exportShots", { revision: state.revision, indices: [0], destination: "current" });
  const request = m.requests.find(([url]) => url.endsWith("/shot-package")); assert.deepEqual(JSON.parse(request[1].body), { analysis_session: "session-1", shots: [{ shot_number: 1, start: 0, end: 2 }] });
  assert.equal(m.native[0][0], "shots"); assert.equal(m.native[0][3], "current"); assert.equal(m.native[0][2].shots[0].section_analysis.composition, "Centered circle");
  assert.match(m.service.view().message, /end of the current timeline/); assert.equal(m.requests.some(([url]) => url.endsWith("/analyze")), false);
});
test('metadata sync uses a fresh authenticated server record, not renderer metadata', async () => {
  const m = fixture(), state = await m.connect();
  await m.service.invoke('syncMetadata', { revision: state.revision });
  assert.equal(m.native[0][0], 'metadata'); assert.equal(m.native[0][2].record.transcript[0].text, 'Hello world');
  assert.ok(m.requests.some(([url]) => url.endsWith('/metadata?analysis_session=session-1')));
  await assert.rejects(m.service.invoke('syncMetadata', { revision: state.revision, fields: [] }), /Unsupported.*field/);
  m.updateAsset({ analysis_config: { started_at: 'new' } });
  await assert.rejects(m.service.invoke('syncMetadata', { revision: state.revision }), /analysis changed/);
  assert.equal(m.native.length, 1);
});
test("save shots and selects timeline use explicit destinations", async () => {
  for (const destination of ["pool", "new"]) { const m = fixture(); const state = await m.connect(); await m.service.invoke("exportShots", { revision: state.revision, indices: [0], destination }); assert.equal(m.native[0][3], destination); }
});
test("renderer cannot inject metadata, paths or native clip IDs into shot export", async () => {
  const m = fixture(); const state = await m.connect(); for (const extra of [{ metadata: {} }, { media_path: "C:\\other.mp4" }, { item_id: "other" }, { shots: [] }]) await assert.rejects(m.service.invoke("exportShots", { revision: state.revision, indices: [0], destination: "pool", ...extra }), /Unsupported.*field/); assert.equal(m.native.length, 0);
});
test("shot export rejects dialogue, stale revisions, invalid selections and foreign destinations", async () => {
  const m = fixture(); const state = await m.connect();
  for (const fields of [{ indices: [] }, { indices: [0, 0] }, { indices: [-1] }, { indices: [500] }, { indices: [true] }, { indices: Array.from({ length: 51 }, (_, i) => i) }, { destination: "external" }]) await assert.rejects(m.service.invoke("exportShots", { revision: state.revision, indices: [0], destination: "pool", ...fields }));
  const transcript = await m.service.invoke("setView", { tab: "transcript" }); await assert.rejects(m.service.invoke("exportShots", { revision: state.revision, indices: [0], destination: "pool" }), /results changed/); await assert.rejects(m.service.invoke("exportShots", { revision: transcript.revision, indices: [0], destination: "pool" }), /Choose analyzed shots/); assert.equal(m.native.length, 0);
});
test("server snapshot conflicts prevent all native shot writes", async () => {
  const m = fixture({ onFetch(url) { if (url.endsWith("/shot-package")) return { ok: false, status: 409, text: async () => JSON.stringify({ detail: "Shot timing changed" }) }; } }); const state = await m.connect(); await assert.rejects(m.service.invoke("exportShots", { revision: state.revision, indices: [0], destination: "pool" }), /timing changed/); assert.equal(m.native.length, 0);
});
test("a native shot write locks polling and competing actions until acknowledged", async () => {
  let release, started; const barrier = new Promise(resolve => { release = resolve; }), began = new Promise(resolve => { started = resolve; });
  const m = fixture({ async exportShots() { started(); await barrier; return { created: 1, reused: 0, added: 1 }; } }); const state = await m.connect();
  const pending = m.service.invoke("exportShots", { revision: state.revision, indices: [0], destination: "current" }); await began;
  const before = m.requests.length; await m.service.invoke("refresh"); assert.equal(m.requests.length, before); await assert.rejects(m.service.invoke("setView", { query: "person" }), /Wait for.*native action/); release(); await pending; assert.equal(m.native.length, 1);
});
test("disconnect clears context and native actions before discovery reconnects", async () => { const m = fixture(); const prior = await m.connect(); await m.service.invoke("disconnect"); const calls = m.requests.length; await assert.rejects(m.service.invoke("addMarkers", { revision: prior.revision, count: 1 }), /Start Video Analyzer/); assert.equal(m.requests.length, calls); assert.equal(m.service.view().connected, false); assert.equal(m.service.view().asset, null); assert.equal((await m.service.invoke("refresh")).connected, true); assert.equal(m.discoveries.length, 2); });
test("a delayed stale asset reply cannot repopulate a switched project", async () => {
  let waiting = false, release;
  const barrier = new Promise(resolve => { release = resolve; });
  const m = fixture({ async onFetch(url) { if (waiting && url.endsWith("/assets/asset-1")) await barrier; } }); await m.connect(); waiting = true;
  const pending = m.service.invoke("refresh"); await new Promise(resolve => setImmediate(resolve)); m.setProject("other"); release(); await assert.rejects(pending, /Project changed/);
  const current = await m.service.invoke("refresh"); assert.equal(current.project.id, "other"); assert.equal(current.asset, null);
});
test("missing discovery stays offline without exposing its error and retries on the next refresh", async () => {
  const options = { discoveryError: "private path and credential " + token }; const m = fixture(options);
  let state = await m.connect(); assert.equal(state.connected, false); assert.match(state.message, /Start Video Analyzer/); assert.equal(JSON.stringify(state).includes(token), false); assert.equal(m.requests.length, 0);
  delete options.discoveryError; state = await m.service.invoke("refresh"); assert.equal(state.connected, true); assert.equal(m.discoveries.length, 2); assert.equal(m.native.length, 0);
});
test("a stale descriptor or mismatched service instance cannot load project data", async () => {
  const m = fixture({ healthInstance: "579458c7-0aac-43de-8239-dd2a2de3da57" }); const state = await m.connect();
  assert.equal(state.connected, false); assert.equal(state.asset, null); assert.equal(m.requests.length, 1); assert.equal(JSON.stringify(state).includes(instanceId), false);
});
test("unsupported protocol remains offline before any project or native action", async () => { const m = fixture({ protocolVersion: 2 }); assert.equal((await m.connect()).connected, false); assert.equal(m.requests.length, 1); assert.equal(m.native.length, 0); });
test("credentials and discovery fields never enter renderer state or preferences", async () => {
  const m = fixture({ preferences: JSON.stringify({ token, instance_id: instanceId, server: "http://localhost:8000", projects: {} }) }); const state = await m.connect();
  assert.equal(state.server, undefined); assert.equal(state.token, undefined); assert.equal(state.instance_id, undefined); assert.equal(JSON.stringify(state).includes(token), false);
  const value = JSON.parse(m.writes.at(-1)); assert.deepEqual(Object.keys(value), ["projects"]); assert.equal(JSON.stringify(value).includes(token), false); assert.equal(JSON.stringify(value).includes(instanceId), false);
});
test("credential rotation invalidates current results and reconnects without replaying analysis", async () => {
  let rejectAnalysis = true;
  const m = fixture({ async onFetch(url) { if (url.endsWith("/analyze") && rejectAnalysis) return { ok: false, status: 401, text: async () => JSON.stringify({ detail: token }) }; } });
  const prior = await m.connect(); await assert.rejects(m.service.invoke("analyze", { revision: prior.revision }), error => !error.message.includes(token) && /Reconnecting/.test(error.message));
  const offline = m.service.view(); assert.equal(offline.connected, false); assert.equal(offline.asset, null); assert.notEqual(offline.revision, prior.revision);
  const nextToken = "b".repeat(43); m.setConnection({ token: nextToken, server: "http://localhost:8000", instance_id: instanceId }); rejectAnalysis = false;
  const restored = await m.service.invoke("refresh"); assert.equal(restored.connected, true); assert.equal(m.requests.at(-1)[1].headers.Authorization, "Bearer " + nextToken);
  assert.equal(m.requests.filter(([url]) => url.endsWith("/analyze")).length, 1); assert.equal(m.native.length, 0); assert.equal(m.writes.join("").includes(nextToken), false);
});
test("a forbidden authorization response clears native context and does not replay marker export", async () => {
  let blocked = false;
  const m = fixture({ async onFetch(url) { if (blocked && url.endsWith("/assets/asset-1")) return { ok: false, status: 403, text: async () => "{}" }; } });
  const state = await m.connect(); blocked = true; await assert.rejects(m.service.invoke("addMarkers", { revision: state.revision, count: 1 }), /Reconnecting/); assert.equal(m.service.view().asset, null);
  blocked = false; await m.service.invoke("refresh"); assert.equal(m.native.length, 0);
});
test("closing and reopening restores project clip, searches and view through automatic discovery", async () => {
  const first = fixture(); await first.connect(); await first.service.invoke("setView", { tab: "transcript", mode: "flash_pro", query: "world" });
  const preferences = first.writes.at(-1); first.service.destroy(); const second = fixture({ preferences }); const state = await second.connect();
  assert.equal(state.asset.job_id, "asset-1"); assert.equal(state.tab, "transcript"); assert.equal(state.mode, "flash_pro"); assert.equal(state.queries.transcript, "world"); assert.equal(state.results.length, 1); assert.equal(second.discoveries.length, 1); assert.equal(second.native.length, 0);
});
test("destroy during pending discovery cannot reconnect a closed plugin", async () => {
  let release; const pending = new Promise(resolve => { release = resolve; }); const m = fixture();
  const service = createService({ host: m.host, preferences: { read: async () => "{}", write: async () => {} }, fetch: async () => { throw new Error("Closed plugin must not request"); }, readConnection: () => pending });
  const connection = service.invoke("bootstrap"); await new Promise(resolve => setImmediate(resolve)); service.destroy(); release({ token, server: "http://127.0.0.1:8000", instance_id: instanceId });
  assert.equal((await connection).connected, false);
});
test("empty library polls still verify descriptor and authenticated service health", async () => {
  const m = fixture({ emptyLibrary: true }); const connected = await m.connect(); assert.equal(connected.connected, true); assert.equal(connected.asset, null);
  const calls = m.requests.length, reads = m.discoveries.length; const refreshed = await m.service.invoke("refresh");
  assert.equal(refreshed.connected, true); assert.equal(m.discoveries.length, reads + 1); assert.equal(m.requests.length, calls + 1); assert.ok(m.requests.at(-1)[0].endsWith("/health"));
});
test("empty library detects a stopped companion and reconnects when it returns", async () => {
  let running = true; const m = fixture({ emptyLibrary: true, async onFetch() { if (!running) throw new Error("Service stopped"); } }); await m.connect();
  running = false; const stopped = await m.service.invoke("refresh"); assert.equal(stopped.connected, false); assert.equal(stopped.project, null); assert.match(stopped.message, /Start Video Analyzer/);
  running = true; m.setConnection({ token: "b".repeat(43), server: "http://127.0.0.1:8000", instance_id: "579458c7-0aac-43de-8239-dd2a2de3da57" });
  const restored = await m.service.invoke("refresh"); assert.equal(restored.connected, true); assert.equal(restored.asset, null); assert.equal(m.native.length, 0);
});
test("descriptor removal clears an empty project's context without an asset request", async () => {
  const options = { emptyLibrary: true }, m = fixture(options); await m.connect(); options.discoveryError = "private file missing " + token;
  const calls = m.requests.length, state = await m.service.invoke("refresh"); assert.equal(state.connected, false); assert.equal(state.project, null); assert.equal(m.requests.length, calls); assert.equal(JSON.stringify(state).includes(token), false);
});
test("connected polling invalidates old results and reconnects when descriptor identity, token or server changes", async () => {
  for (const patch of [{ instance_id: "579458c7-0aac-43de-8239-dd2a2de3da57" }, { token: "b".repeat(43) }, { server: "http://localhost:8000" }]) {
    const m = fixture(); const prior = await m.connect(); m.setConnection({ token, server: "http://127.0.0.1:8000", instance_id: instanceId, ...patch });
    const refreshed = await m.service.invoke("refresh"); assert.equal(refreshed.connected, true); assert.ok(refreshed.revision > prior.revision);
    await assert.rejects(m.service.invoke("addMarkers", { revision: prior.revision, count: 1 }), /results changed/); assert.equal(m.native.length, 0);
  }
});
test("native mutation refuses a changed descriptor instead of reconnecting or replaying", async () => {
  for (const action of ["analyze", "addMarkers", "selectClip", "copyTimes", "exportSubtitles"]) {
    const m = fixture(); const prior = await m.connect(), calls = m.requests.length;
    m.setConnection({ token: "b".repeat(43), server: "http://localhost:8000", instance_id: "579458c7-0aac-43de-8239-dd2a2de3da57" });
    const payload = { revision: prior.revision, ...(["selectClip", "copyTimes"].includes(action) ? { index: 0 } : action === "addMarkers" ? { count: 1 } : {}) };
    await assert.rejects(m.service.invoke(action, payload), /Reconnecting/); assert.equal(m.service.view().connected, false); assert.equal(m.service.view().asset, null); assert.equal(m.requests.length, calls); assert.equal(m.native.length, 0); assert.equal(m.copies.length, 0); assert.equal(m.exports.length, 0);
    await m.service.invoke("refresh"); assert.equal(m.service.view().connected, true); assert.equal(m.native.length, 0); assert.equal(m.requests.filter(([url]) => url.endsWith("/analyze")).length, 0);
  }
});
test("selection registration validates the active instance before any source registration", async () => {
  const m = fixture(); await m.connect(); m.setConnection({ token, server: "http://127.0.0.1:8000", instance_id: "579458c7-0aac-43de-8239-dd2a2de3da57" });
  await assert.rejects(m.service.invoke("loadSelection"), /Reconnecting/); assert.equal(m.requests.some(([url]) => url.endsWith("/register")), false);
});
test("empty library rejects changed authenticated health even if descriptor has not changed", async () => {
  const options = { emptyLibrary: true }, m = fixture(options); await m.connect(); options.healthInstance = "579458c7-0aac-43de-8239-dd2a2de3da57";
  const state = await m.service.invoke("refresh"); assert.equal(state.connected, false); assert.equal(state.project, null); assert.equal(m.native.length, 0);
});
