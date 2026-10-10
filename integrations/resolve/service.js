"use strict";
const helpers = require("./controller");
const DEFAULT_SERVER = "http://127.0.0.1:8000";
const MAC_SERVER = "https://localhost:8443";
const ACTIONS = Object.freeze(["bootstrap", "connect", "disconnect", "refresh", "loadSelection", "selectAsset", "setView", "analyze", "selectClip", "copyTimes", "addMarkers", "exportSubtitles", "openAnalyzer"]);
const PAYLOAD_FIELDS = Object.freeze({ connect: ["server", "token"], selectAsset: ["id"], setView: ["tab", "mode", "query"], analyze: ["revision"], selectClip: ["revision", "index"], copyTimes: ["revision", "index"], addMarkers: ["revision", "count"], exportSubtitles: ["revision"] });
function normalizeServer(value) {
  const match = /^(http|https):\/\/(127\.0\.0\.1|localhost):(8000|8443)\/?$/i.exec(String(value || "").trim());
  if (!match || match[1].toLowerCase() === "http" && match[3] !== "8000" || match[1].toLowerCase() === "https" && match[3] !== "8443") throw new Error("Use localhost or 127.0.0.1, with HTTP port 8000 or HTTPS port 8443. This plugin connects only to the local analyzer.");
  return match[1].toLowerCase() + "://" + match[2].toLowerCase() + ":" + match[3];
}
function plain(value) { return value && typeof value === "object" && !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype; }
function sanitizePreferences(input, defaultServer) {
  const clean = { server: defaultServer, projects: {} };
  if (!plain(input)) return clean;
  try { clean.server = normalizeServer(input.server || defaultServer); } catch (_) { /* Old or invalid addresses never receive a token. */ }
  if (plain(input.projects)) for (const [id, saved] of Object.entries(input.projects).slice(-50)) {
    if (!id || id.length > 500 || !plain(saved) || ["__proto__", "constructor", "prototype"].includes(id)) continue;
    clean.projects[id] = { assetId: typeof saved.assetId === "string" ? saved.assetId.slice(0, 500) : "", tab: saved.tab === "transcript" ? "transcript" : "shots",
      mode: saved.mode === "flash_pro" ? "flash_pro" : "flash_only", queries: { shots: String(saved.queries && saved.queries.shots || "").slice(0, 500), transcript: String(saved.queries && saved.queries.transcript || "").slice(0, 500) } };
  }
  return clean;
}
function sourceBinding(asset, projectId) {
  return (Array.isArray(asset && asset.editor_sources) ? asset.editor_sources : []).find(source => source.host === "resolve" && source.project_id === projectId) || null;
}
function session(asset) { return String(asset && asset.analysis_config && asset.analysis_config.started_at || ""); }
function createService(dependencies) {
  const { host, fetch, preferences, saveSubtitle, clipboard, openBrowser } = dependencies;
  const defaultServer = dependencies.platform === "darwin" ? MAC_SERVER : DEFAULT_SERVER;
  const state = { connected: false, server: defaultServer, project: null, assets: [], asset: null, shots: [], total: 0, tab: "shots", mode: "flash_only", queries: { shots: "", transcript: "" }, revision: 0, message: "" };
  let token = "", connectionEpoch = 0, selectionEpoch = 0, nativeBusy = false, saved = sanitizePreferences(null, defaultServer), writeQueue = Promise.resolve();
  function view() { return JSON.parse(JSON.stringify({ ...state, results: helpers.cues(state.asset, state.shots, state.tab, state.queries[state.tab]) })); }
  function invalidate() { state.revision++; }
  function remember() {
    if (state.project) saved.projects[state.project.id] = { assetId: state.asset && state.asset.job_id || "", tab: state.tab, mode: state.mode, queries: { ...state.queries } };
    saved.server = state.server;
    saved = sanitizePreferences(saved, defaultServer);
    const value = JSON.stringify(saved);
    writeQueue = writeQueue.then(() => preferences.write(value)).catch(() => { /* A read-only preference folder must not block analysis. */ });
    return writeQueue;
  }
  function assertConnected() { if (!state.connected || !token) throw new Error("Connect to the local companion app first."); }
  async function request(path, options = {}, text = false) {
    const epoch = connectionEpoch, currentToken = token, endpoint = state.server;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), path === "/register" ? 180000 : 30000);
    let response;
    try { response = await fetch(endpoint + "/api/editor/resolve" + path, { ...options, redirect: "error", signal: controller.signal,
      headers: { Authorization: "Bearer " + currentToken, ...(options.body ? { "Content-Type": "application/json" } : {}) } }); }
    catch (_) { throw new Error(endpoint.startsWith("https:") ? "Cannot reach the local HTTPS service. Complete certificate setup and start the companion app on port 8443." : "Cannot reach the local service. Start the companion app on port 8000."); }
    finally { if (!response) clearTimeout(timer); }
    let body;
    try { body = await response.text(); } finally { clearTimeout(timer); }
    if (epoch !== connectionEpoch) throw new Error("The connection changed. Try again.");
    if (body.length > 16 * 1024 * 1024) throw new Error("The local service response was too large. Search a smaller set of results.");
    let data;
    if (!text || !response.ok) { try { data = JSON.parse(body); } catch (_) { if (response.ok) throw new Error("The local service returned an invalid response."); } }
    if (!response.ok) {
      const error = new Error(response.status === 401 || response.status === 403 ? "Pairing code rejected. Copy the current editor pairing code from the companion app and reconnect." : typeof (data && data.detail) === "string" ? data.detail : "Local service request failed (" + response.status + ").");
      error.status = response.status; throw error;
    }
    return text ? body : data;
  }
  function fresh(epoch, projectId, assetId) {
    return epoch === selectionEpoch && state.connected && state.project && state.project.id === projectId && (!assetId || state.asset && state.asset.job_id === assetId);
  }
  function clearProject(project) {
    selectionEpoch++; invalidate(); state.project = project; state.asset = null; state.shots = []; state.assets = []; state.total = 0;
    const settings = saved.projects[project.id] || {};
    state.tab = settings.tab === "transcript" ? "transcript" : "shots"; state.mode = settings.mode === "flash_pro" ? "flash_pro" : "flash_only";
    state.queries = { shots: String(settings.queries && settings.queries.shots || ""), transcript: String(settings.queries && settings.queries.transcript || "") };
  }
  async function verifyProject(expected) {
    const project = await host.getProjectIdentity(expected);
    if (!state.project || state.project.id !== project.id) { clearProject(project); return true; }
    state.project = project; return false;
  }
  async function readShots(epoch, id) {
    const query = state.queries.shots, projectId = state.project.id;
    const data = await request("/assets/" + encodeURIComponent(id) + "/shots?q=" + encodeURIComponent(query) + "&limit=500");
    if (!fresh(epoch, projectId, id) || query !== state.queries.shots) return;
    state.shots = Array.isArray(data.shots) ? data.shots : []; state.total = Number.isFinite(data.total) ? data.total : state.shots.length; invalidate();
  }
  async function selectAsset(id) {
    assertConnected();
    if (typeof id !== "string" || !state.assets.some(asset => asset.job_id === id)) throw new Error("Choose a clip loaded in this Resolve project.");
    const projectId = state.project.id, epoch = ++selectionEpoch;
    invalidate(); state.asset = null; state.shots = []; state.total = 0;
    const asset = await request("/assets/" + encodeURIComponent(id));
    if (!fresh(epoch, projectId) || !sourceBinding(asset, projectId)) throw new Error("The selected clip or project changed. Load the selection again.");
    await host.getProjectIdentity(projectId);
    if (!fresh(epoch, projectId)) return;
    state.asset = asset; invalidate(); await readShots(epoch, id); await remember();
  }
  async function projectAssets() {
    const epoch = selectionEpoch, projectId = state.project.id;
    const data = await request("/assets?project_id=" + encodeURIComponent(projectId));
    await host.getProjectIdentity(projectId);
    if (!fresh(epoch, projectId)) return;
    state.assets = (Array.isArray(data.assets) ? data.assets : []).filter(asset => sourceBinding(asset, projectId));
    const preferred = state.asset && state.asset.job_id || saved.projects[projectId] && saved.projects[projectId].assetId;
    const chosen = state.assets.find(asset => asset.job_id === preferred) || state.assets[0];
    if (chosen) await selectAsset(chosen.job_id);
  }
  async function refresh() {
    assertConnected(); if (nativeBusy) return view();
    const changed = await verifyProject();
    if (changed) await projectAssets();
    else if (state.asset) {
      const epoch = selectionEpoch, id = state.asset.job_id, projectId = state.project.id;
      const asset = await request("/assets/" + encodeURIComponent(id));
      await host.getProjectIdentity(projectId);
      if (fresh(epoch, projectId, id)) {
        if (!sourceBinding(asset, projectId)) throw new Error("This asset no longer belongs to the active Resolve project.");
        state.asset = asset; const index = state.assets.findIndex(item => item.job_id === id); if (index >= 0) state.assets[index] = asset;
        invalidate(); await readShots(epoch, id);
      }
    }
    return view();
  }
  async function loadSelection() {
    assertConnected(); const selection = await host.getSelection();
    if (!state.project || state.project.id !== selection.project_id) clearProject({ id: selection.project_id, name: selection.project_name });
    const epoch = selectionEpoch, projectId = selection.project_id, warnings = selection.warnings.slice();
    let loaded = 0;
    for (const clip of selection.clips) {
      await host.getProjectIdentity(projectId);
      if (!fresh(epoch, projectId)) throw new Error("The project changed while loading clips. Load the current selection again.");
      try { await request("/register", { method: "POST", body: JSON.stringify({ project_id: projectId, item_id: clip.item_id, name: clip.name, media_path: clip.media_path, source_fps: clip.source_fps }) }); loaded++; }
      catch (error) { if (error.status === 401 || error.status === 403 || !fresh(epoch, projectId)) throw error; warnings.push(clip.name + ": " + error.message); }
    }
    if (!fresh(epoch, projectId)) throw new Error("The project changed while loading clips.");
    await projectAssets(); state.message = loaded + " clips loaded. Choose an analysis type and click Analyze." + (warnings.length ? " " + warnings.join(" ") : "");
    return view();
  }
  async function nativeContext(payload) {
    assertConnected();
    if (!plain(payload) || payload.revision !== state.revision || !state.asset || !state.project) throw new Error("These results changed. Select the current clip and try again.");
    const revision = state.revision, epoch = selectionEpoch, id = state.asset.job_id, projectId = state.project.id, prior = sourceBinding(state.asset, projectId), priorSession = session(state.asset);
    if (!prior) throw new Error("Load selected Resolve clips before using native results.");
    const asset = await request("/assets/" + encodeURIComponent(id));
    const binding = sourceBinding(asset, projectId);
    if (!fresh(epoch, projectId, id) || revision !== state.revision || !binding || binding.item_id !== prior.item_id || binding.media_path !== prior.media_path || session(asset) !== priorSession) throw new Error("The source binding or analysis changed. Load the Resolve selection again.");
    await host.resolveBinding(binding);
    if (!fresh(epoch, projectId, id) || revision !== state.revision) throw new Error("The selected results changed. Try again.");
    return { asset, binding, revision };
  }
  function currentCue(payload) {
    const results = helpers.cues(state.asset, state.shots, state.tab, state.queries[state.tab]);
    if (!Number.isInteger(payload.index) || payload.index < 0 || payload.index >= results.length) throw new Error("Choose a current shot or dialogue line.");
    return results[payload.index];
  }
  const actions = {
    async bootstrap() { try { saved = sanitizePreferences(JSON.parse(await preferences.read()), defaultServer); } catch (_) { saved = sanitizePreferences(null, defaultServer); } state.server = saved.server; return view(); },
    async connect(payload) {
      if (!plain(payload) || typeof payload.token !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(payload.token.trim())) throw new Error("Paste the editor pairing code from the companion app.");
      const endpoint = normalizeServer(payload.server || defaultServer);
      state.connected = false; state.asset = null; state.project = null; state.assets = []; state.shots = []; state.total = 0; selectionEpoch++; connectionEpoch++; invalidate();
      state.server = endpoint; token = payload.token.trim();
      try { const health = await request("/health"); if (health.host !== "resolve" || health.protocol_version !== 1) throw new Error("This companion app does not support the Resolve protocol. Update Video Analyzer."); state.connected = true; state.message = "Connected. Pairing code is kept for this session only." + (health.google_configured === false ? " Configure Gemini in the companion app before analyzing." : ""); await remember(); await verifyProject(); await projectAssets(); return view(); }
      catch (error) { token = ""; state.connected = false; throw error; }
    },
    async disconnect() { token = ""; connectionEpoch++; selectionEpoch++; invalidate(); Object.assign(state, { connected: false, project: null, assets: [], asset: null, shots: [], total: 0, message: "Disconnected." }); return view(); },
    refresh, loadSelection,
    async selectAsset(payload) { await verifyProject(state.project && state.project.id); await selectAsset(payload && payload.id); return view(); },
    async setView(payload) {
      assertConnected(); if (!plain(payload)) throw new Error("Invalid view settings.");
      if (payload.tab !== undefined && !["shots", "transcript"].includes(payload.tab)) throw new Error("Choose Shots or Transcript.");
      if (payload.mode !== undefined && !["flash_only", "flash_pro"].includes(payload.mode)) throw new Error("Choose Shots and tags or Full analysis.");
      if (payload.query !== undefined && (typeof payload.query !== "string" || payload.query.length > 500)) throw new Error("Search is limited to 500 characters.");
      if (payload.tab) state.tab = payload.tab; if (payload.mode) state.mode = payload.mode; if (payload.query !== undefined) state.queries[state.tab] = payload.query;
      invalidate(); const epoch = selectionEpoch; if (state.asset && state.tab === "shots") await readShots(epoch, state.asset.job_id); await remember(); return view();
    },
    async analyze(payload) {
      const { asset } = await nativeContext(payload);
      if (helpers.ACTIVE_STATUSES.includes(asset.status)) throw new Error("This clip is already being analyzed.");
      await request("/assets/" + encodeURIComponent(asset.job_id) + "/analyze", { method: "POST", body: JSON.stringify({ mode: state.mode }) });
      state.asset = { ...asset, status: "analyzing", analysis_progress: { stage: "preparing" } }; invalidate(); state.message = "Analysis started. Shots and dialogue appear as sections finish."; return view();
    },
    async selectClip(payload) { const { binding } = await nativeContext(payload); await host.selectClip(binding); state.message = "Selected the source clip in Resolve's Media Pool. Use copied times for precise positioning."; return view(); },
    async copyTimes(payload) { await nativeContext(payload); const cue = currentCue(payload); clipboard.writeText(helpers.cueTime(cue.start) + " – " + helpers.cueTime(cue.end)); state.message = "Copied source-media times."; return view(); },
    async addMarkers(payload) {
      const { binding, asset } = await nativeContext(payload); const results = helpers.cues(asset, state.shots, state.tab, state.queries[state.tab]);
      if (!results.length) throw new Error("No current results to export.");
      if (!Number.isInteger(payload.count) || payload.count < 1 || payload.count > results.length) throw new Error("Choose the visible result count before adding markers.");
      const summary = await host.addMarkers(binding, asset.technical, results.slice(0, payload.count));
      state.message = summary.added + " source markers added; " + summary.duplicates + " duplicates and " + summary.collisions + " occupied frames skipped."; return view();
    },
    async exportSubtitles(payload) { const { asset } = await nativeContext(payload); if (!helpers.cues(asset, [], "transcript", "").length) throw new Error("No timed dialogue to export yet."); const content = await request("/assets/" + encodeURIComponent(asset.job_id) + "/subtitles", {}, true); const result = await saveSubtitle(helpers.subtitleName(asset), content); state.message = result ? "Subtitles exported." : "Export cancelled."; return view(); },
    async openAnalyzer() { assertConnected(); await openBrowser(state.server + "/"); return view(); },
  };
  async function invoke(action, payload) {
    if (!ACTIONS.includes(action)) throw new Error("Unsupported plugin action.");
    if (payload !== undefined && (!plain(payload) || JSON.stringify(payload).length > 8192)) throw new Error("Invalid plugin action payload.");
    const allowed = PAYLOAD_FIELDS[action] || [];
    if (payload && Object.keys(payload).some(key => !allowed.includes(key))) throw new Error("Unsupported plugin action field.");
    if (PAYLOAD_FIELDS[action] && payload === undefined) throw new Error("This plugin action requires its current context.");
    if (nativeBusy && !["refresh", "bootstrap"].includes(action)) throw new Error("Wait for the current native action to finish.");
    const locked = ["connect", "disconnect", "loadSelection", "selectAsset", "analyze", "selectClip", "copyTimes", "addMarkers", "exportSubtitles"].includes(action);
    if (locked) nativeBusy = true;
    try { return await actions[action](payload); } finally { if (locked) nativeBusy = false; }
  }
  return { invoke, view, destroy() { token = ""; connectionEpoch++; selectionEpoch++; state.connected = false; } };
}
module.exports = { createService, normalizeServer, sourceBinding, sanitizePreferences, ACTIONS, DEFAULT_SERVER, MAC_SERVER };
