"use strict";
const helpers = require("./controller");
const DEFAULT_SERVER = "http://127.0.0.1:8000";
const MAC_SERVER = "https://localhost:8443";
const ACTIONS = Object.freeze(["bootstrap", "connect", "disconnect", "refresh", "reloadSaved", "loadSelection", "selectAsset", "setView", "analyze", "selectClip", "showTimelineClip", "copyTimes", "addMarkers", "exportShots", "syncMetadata", "exportSubtitles", "openAnalyzer"]);
const PAYLOAD_FIELDS = Object.freeze({ loadSelection: ["source"], selectAsset: ["id"], setView: ["tab", "mode", "query"], analyze: ["revision"], selectClip: ["revision", "index"], showTimelineClip: ["revision", "index"], copyTimes: ["revision", "index"], addMarkers: ["revision", "count"], exportShots: ["revision", "indices", "destination"], syncMetadata: ["revision"], exportSubtitles: ["revision"] });
const OFFLINE = "Start Video Analyzer. This panel connects automatically.";
function normalizeServer(value) {
  const match = /^(http|https):\/\/(127\.0\.0\.1|localhost):(8000|8443)\/?$/i.exec(String(value || "").trim());
  if (!match || match[1].toLowerCase() === "http" && match[3] !== "8000" || match[1].toLowerCase() === "https" && match[3] !== "8443") throw new Error("Use localhost or 127.0.0.1, with HTTP port 8000 or HTTPS port 8443. This plugin connects only to the local analyzer.");
  return match[1].toLowerCase() + "://" + match[2].toLowerCase() + ":" + match[3];
}
function plain(value) { return value && typeof value === "object" && !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype; }
function sanitizePreferences(input, defaultServer) {
  const clean = { projects: {} };
  if (!plain(input)) return clean;
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
  const { host, fetch, preferences, saveSubtitle, clipboard, openBrowser, readConnection } = dependencies;
  const defaultServer = dependencies.platform === "darwin" ? MAC_SERVER : DEFAULT_SERVER;
  const state = { connected: false, server: defaultServer, project: null, assets: [], asset: null, shots: [], total: 0, tab: "shots", mode: "flash_only", queries: { shots: "", transcript: "" }, revision: 0, message: OFFLINE };
  let token = "", activeConnection = null, connectionEpoch = 0, selectionEpoch = 0, nativeBusy = false, connecting = null, destroyed = false, saved = sanitizePreferences(null, defaultServer), writeQueue = Promise.resolve();
  function view() { const { server, ...publicState } = state; return JSON.parse(JSON.stringify({ ...publicState, results: helpers.cues(state.asset, state.shots, state.tab, state.queries[state.tab]) })); }
  function invalidate() { state.revision++; }
  function remember() {
    if (state.project) saved.projects[state.project.id] = { assetId: state.asset && state.asset.job_id || "", tab: state.tab, mode: state.mode, queries: { ...state.queries } };
    saved = sanitizePreferences(saved, defaultServer);
    const value = JSON.stringify(saved);
    writeQueue = writeQueue.then(() => preferences.write(value)).catch(() => { /* A read-only preference folder must not block analysis. */ });
    return writeQueue;
  }
  function offline(message = OFFLINE) {
    token = ""; activeConnection = null; connectionEpoch++; selectionEpoch++; invalidate();
    Object.assign(state, { connected: false, project: null, assets: [], asset: null, shots: [], total: 0, message });
  }
  function assertConnected() { if (!state.connected || !token) throw new Error(OFFLINE); }
  async function discovery() {
    let descriptor;
    try { descriptor = await readConnection(); } catch (_) { throw new Error(OFFLINE); }
    if (!plain(descriptor) || typeof descriptor.token !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(descriptor.token) || typeof descriptor.instance_id !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(descriptor.instance_id)) throw new Error(OFFLINE);
    try { return { ...descriptor, server: normalizeServer(descriptor.server) }; } catch (_) { throw new Error(OFFLINE); }
  }
  function checkHealth(health, descriptor) {
    if (!health || health.host !== "resolve" || health.protocol_version !== 1) throw new Error("Update Video Analyzer to connect this Resolve panel.");
    if (health.instance_id !== descriptor.instance_id) throw new Error(OFFLINE);
  }
  async function validateConnection() {
    assertConnected();
    const epoch = connectionEpoch, current = activeConnection;
    try {
      const descriptor = await discovery();
      if (destroyed || epoch !== connectionEpoch) throw new Error("The connection changed. Try again.");
      if (!current || descriptor.token !== current.token || descriptor.instance_id !== current.instance_id || descriptor.server !== current.server) throw new Error("The local service restarted. Reconnecting automatically.");
      checkHealth(await request("/health"), current);
      if (destroyed || epoch !== connectionEpoch) throw new Error("The connection changed. Try again.");
    } catch (error) {
      if (!destroyed && epoch === connectionEpoch) offline(error.message);
      throw error;
    }
  }
  async function request(path, options = {}, text = false) {
    const epoch = connectionEpoch, currentToken = token, endpoint = state.server;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), path === "/register" ? 180000 : 30000);
    let response;
    try { response = await fetch(endpoint + "/api/editor/resolve" + path, { ...options, redirect: "error", signal: controller.signal,
      headers: { Authorization: "Bearer " + currentToken, ...(options.body ? { "Content-Type": "application/json" } : {}) } }); }
    catch (_) { if (epoch === connectionEpoch) offline(); throw new Error(OFFLINE); }
    finally { if (!response) clearTimeout(timer); }
    let body;
    try { body = await response.text(); } finally { clearTimeout(timer); }
    if (epoch !== connectionEpoch) throw new Error("The connection changed. Try again.");
    if (body.length > 16 * 1024 * 1024) throw new Error("The local service response was too large. Search a smaller set of results.");
    let data;
    if (!text || !response.ok) { try { data = JSON.parse(body); } catch (_) { if (response.ok) throw new Error("The local service returned an invalid response."); } }
    if (!response.ok) {
      const unauthorized = response.status === 401 || response.status === 403;
      if (unauthorized) offline();
      const detail = typeof (data && data.detail) === "string" ? data.detail.replaceAll(currentToken, "[credential]") : "Local service request failed (" + response.status + ").";
      const error = new Error(unauthorized ? "The local service restarted. Reconnecting automatically." : detail);
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
    const shots = Array.isArray(data.shots) ? data.shots : [], total = Number.isFinite(data.total) ? data.total : shots.length;
    if (JSON.stringify(shots) !== JSON.stringify(state.shots) || total !== state.total) invalidate();
    state.shots = shots; state.total = total;
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
    if (!state.connected) return connect();
    if (nativeBusy) return view();
    try { await validateConnection(); }
    catch (error) { if (!state.connected) return connect(); throw error; }
    const changed = await verifyProject();
    if (changed) await projectAssets();
    else if (state.asset) {
      const epoch = selectionEpoch, id = state.asset.job_id, projectId = state.project.id;
      const asset = await request("/assets/" + encodeURIComponent(id));
      await host.getProjectIdentity(projectId);
      if (fresh(epoch, projectId, id)) {
        if (!sourceBinding(asset, projectId)) throw new Error("This asset no longer belongs to the active Resolve project.");
        if (session(asset) !== session(state.asset) || JSON.stringify(asset.editor_sources) !== JSON.stringify(state.asset.editor_sources) || JSON.stringify(asset.technical) !== JSON.stringify(state.asset.technical) || JSON.stringify(asset.transcript) !== JSON.stringify(state.asset.transcript)) invalidate();
        state.asset = asset; const index = state.assets.findIndex(item => item.job_id === id); if (index >= 0) state.assets[index] = asset;
        await readShots(epoch, id);
      }
    }
    return view();
  }
  async function connect() {
    if (connecting) return connecting;
    if (destroyed) return view();
    if (state.connected) return refresh();
    const epoch = connectionEpoch;
    connecting = (async () => {
      let descriptor;
      try {
        descriptor = await discovery();
        if (destroyed || epoch !== connectionEpoch) return view();
        state.server = descriptor.server; token = descriptor.token;
        const health = await request("/health");
        checkHealth(health, descriptor);
        if (destroyed || epoch !== connectionEpoch) return view();
        activeConnection = descriptor; state.connected = true; state.message = health.google_configured === false ? "Connected. Configure Gemini in Video Analyzer before analyzing." : "Connected.";
        await verifyProject(); await projectAssets(); return view();
      } catch (error) {
        if (!destroyed && epoch === connectionEpoch) offline(error.message === "Update Video Analyzer to connect this Resolve panel." ? error.message : OFFLINE);
        return view();
      }
    })();
    try { return await connecting; } finally { connecting = null; }
  }
  async function loadSelection(payload = {}) {
    const source = payload.source || "pool";
    if (!["pool", "timeline"].includes(source)) throw new Error("Choose the Media Pool or the clip under the timeline playhead.");
    await validateConnection(); const selection = await host.getSelection(source);
    if (!state.project || state.project.id !== selection.project_id) clearProject({ id: selection.project_id, name: selection.project_name });
    const epoch = selectionEpoch, projectId = selection.project_id, warnings = selection.warnings.slice();
    let loaded = 0, preferredId = null;
    for (const clip of selection.clips) {
      await validateConnection();
      await host.getProjectIdentity(projectId);
      if (!fresh(epoch, projectId)) throw new Error("The project changed while loading clips. Load the current selection again.");
      try { const registered = await request("/register", { method: "POST", body: JSON.stringify({ project_id: projectId, item_id: clip.item_id, name: clip.name, media_path: clip.media_path, source_fps: clip.source_fps,
        ...(clip.viewing_path ? { viewing_path: clip.viewing_path, proxy_alignment_confirmed: clip.proxy_alignment_confirmed === true } : {}) }) }); loaded++; if (!preferredId) preferredId = registered.job_id; }
      catch (error) { if (error.status === 401 || error.status === 403 || !fresh(epoch, projectId)) throw error; warnings.push(clip.name + ": " + error.message); }
    }
    if (!fresh(epoch, projectId)) throw new Error("The project changed while loading clips.");
    await projectAssets();
    if (preferredId && state.assets.some(asset => asset.job_id === preferredId) && (!state.asset || state.asset.job_id !== preferredId)) await selectAsset(preferredId);
    state.message = loaded ? loaded + " source shot" + (loaded === 1 ? "" : "s") + " loaded" + (state.asset && state.asset.shot_count ? "; saved analysis restored." : ". Choose an analysis type and click Analyze.") : source === "timeline" ? "Place the timeline playhead over a video clip, then load it here." : "Select original video shots in Resolve's Media Pool, then load them here.";
    if (warnings.length) state.message += " " + warnings.join(" ");
    return view();
  }
  async function nativeContext(payload) {
    await validateConnection();
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
    async bootstrap() { try { saved = sanitizePreferences(JSON.parse(await preferences.read()), defaultServer); } catch (_) { saved = sanitizePreferences(null, defaultServer); } return connect(); },
    connect,
    async disconnect() { offline(); return view(); },
    refresh, loadSelection,
    async reloadSaved() {
      await validateConnection(); await verifyProject(); await projectAssets();
      state.message = state.asset && state.asset.shot_count ? "Saved analysis restored: " + state.asset.shot_count + (state.asset.shot_count === 1 ? " clip." : " clips.") : "No saved clips for this source yet. Load a source shot and analyze it.";
      return view();
    },
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
    async selectClip(payload) { const { binding } = await nativeContext(payload); await host.selectClip(binding); state.message = "Opened the source bin and selected this shot in Resolve's Media Pool."; return view(); },
    async showTimelineClip(payload) { const { binding } = await nativeContext(payload); const result = await host.showTimelineClip(binding, currentCue(payload)); state.message = "Timeline playhead moved to " + (result.range_matched ? "this analyzed clip" : "the first source occurrence covering this range") + " at " + result.timecode + "."; return view(); },
    async copyTimes(payload) { await nativeContext(payload); const cue = currentCue(payload); clipboard.writeText(helpers.cueTime(cue.start) + " – " + helpers.cueTime(cue.end)); state.message = "Copied source-media times."; return view(); },
    async addMarkers(payload) {
      const { binding, asset } = await nativeContext(payload); const results = helpers.cues(asset, state.shots, state.tab, state.queries[state.tab]);
      if (!results.length) throw new Error("No current results to export.");
      if (!Number.isInteger(payload.count) || payload.count < 1 || payload.count > results.length) throw new Error("Choose the visible result count before adding markers.");
      const summary = await host.addMarkers(binding, asset.technical, results.slice(0, payload.count));
      state.message = summary.added + " source markers added; " + summary.duplicates + " duplicates and " + summary.collisions + " occupied frames skipped."; return view();
    },
    async exportShots(payload) {
      const { binding, asset, revision } = await nativeContext(payload);
      if (state.tab !== "shots") throw new Error("Choose analyzed shots before adding clips to Resolve.");
      if (!Array.isArray(payload.indices) || !payload.indices.length || payload.indices.length > 50 || new Set(payload.indices).size !== payload.indices.length) throw new Error("Choose between 1 and 50 distinct shots.");
      if (!["pool", "current", "new"].includes(payload.destination)) throw new Error("Choose Media Pool, current timeline, or a new selects timeline.");
      const cues = payload.indices.map(index => currentCue({ index }));
      if (cues.some(cue => !Number.isInteger(cue.shot_number) || cue.shot_number < 1)) throw new Error("Refresh the analyzed shots before exporting.");
      const pack = await request("/assets/" + encodeURIComponent(asset.job_id) + "/shot-package", { method: "POST", body: JSON.stringify({
        analysis_session: session(asset), shots: cues.map(cue => ({ shot_number: cue.shot_number, start: cue.start, end: cue.end })) }) });
      if (revision !== state.revision || pack.asset_id !== asset.job_id || pack.analysis_session !== session(asset)) throw new Error("The selected analysis changed. Refresh the shots before exporting.");
      await validateConnection();
      const summary = await host.exportShots(binding, pack, payload.destination);
      await verifyProject(binding.project_id);
      state.message = summary.created + " clips saved; " + summary.reused + " existing clips reused in Video Analyzer shots." +
        (summary.added ? " " + summary.added + " added to " + (payload.destination === "new" ? "a new selects timeline." : "the end of the current timeline.") : " Drag them from the Media Pool into your edit.") + " Metadata saved in Keywords, Description, Dialog Notes, and Comments.";
      return view();
    },
    async exportSubtitles(payload) { const { asset } = await nativeContext(payload); if (!helpers.cues(asset, [], "transcript", "").length) throw new Error("No timed dialogue to export yet."); const content = await request("/assets/" + encodeURIComponent(asset.job_id) + "/subtitles", {}, true); const result = await saveSubtitle(helpers.subtitleName(asset), content); state.message = result ? "Subtitles exported." : "Export cancelled."; return view(); },
    async syncMetadata(payload) {
      const { binding, asset, revision } = await nativeContext(payload);
      if (helpers.ACTIVE_STATUSES.includes(asset.status)) throw new Error("Wait for analysis to finish before syncing metadata.");
      const mapping = await request("/assets/" + encodeURIComponent(asset.job_id) + "/metadata?analysis_session=" + encodeURIComponent(session(asset)));
      if (revision !== state.revision || !mapping.record || mapping.record.asset.id !== asset.job_id) throw new Error("The selected analysis changed. Refresh before syncing metadata.");
      await validateConnection();
      const result = await host.syncMetadata(binding, mapping);
      state.message = result.updated + " metadata fields synced. Complete analysis and timed dialogue retained." + (result.preserved ? " " + result.preserved + " editor fields preserved." : "");
      return view();
    },
    async openAnalyzer() { assertConnected(); await openBrowser(state.server + "/"); return view(); },
  };
  async function invoke(action, payload) {
    if (!ACTIONS.includes(action)) throw new Error("Unsupported plugin action.");
    if (payload !== undefined && (!plain(payload) || JSON.stringify(payload).length > 8192)) throw new Error("Invalid plugin action payload.");
    const allowed = PAYLOAD_FIELDS[action] || [];
    if (payload && Object.keys(payload).some(key => !allowed.includes(key))) throw new Error("Unsupported plugin action field.");
    if (PAYLOAD_FIELDS[action] && action !== "loadSelection" && payload === undefined) throw new Error("This plugin action requires its current context.");
    if (nativeBusy && !["refresh", "bootstrap"].includes(action)) throw new Error("Wait for the current native action to finish.");
    const locked = ["connect", "disconnect", "reloadSaved", "loadSelection", "selectAsset", "analyze", "selectClip", "showTimelineClip", "copyTimes", "addMarkers", "exportShots", "syncMetadata", "exportSubtitles"].includes(action);
    if (locked) nativeBusy = true;
    try { return await actions[action](payload); } finally { if (locked) nativeBusy = false; }
  }
  return { invoke, view, destroy() { destroyed = true; offline(); } };
}
module.exports = { createService, normalizeServer, sourceBinding, sanitizePreferences, ACTIONS, DEFAULT_SERVER, MAC_SERVER };
