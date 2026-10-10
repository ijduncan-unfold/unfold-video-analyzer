/* UXP uses CommonJS. This panel deliberately has no build step or webview. */
const DEFAULT_SERVER = "http://127.0.0.1:8000";
const MAC_SERVER = "https://localhost:8443";
const TOKEN_KEY = "video-analyzer-premiere-token";
const PREFS_FILE = "premiere-panel.json";
const MAX_RESULTS = 500;
const PAGE_SIZE = 100;
const ACTIVE_STATUSES = ["pending", "queued", "uploading", "processing", "analyzing", "running"];

function normalizeServer(value) {
  const match = /^(http|https):\/\/(127\.0\.0\.1|localhost):(8000|8443)\/?$/i.exec(String(value || "").trim());
  if (!match || match[1].toLowerCase() === "http" && match[3] !== "8000" || match[1].toLowerCase() === "https" && match[3] !== "8443") {
    throw new Error("Use HTTP port 8000 on Windows or HTTPS port 8443 on Mac, with localhost or 127.0.0.1. This panel connects only to the local analyzer.");
  }
  return match[1].toLowerCase() + "://" + match[2].toLowerCase() + ":" + match[3];
}

function parseSeconds(value) {
  if (typeof value === "number") return Number.isFinite(value) && value >= 0 ? value : null;
  if (typeof value !== "string" || !/^\d+(?::\d{1,2}){0,2}(?:[.,]\d+)?$/.test(value.trim())) return null;
  const parts = value.trim().replace(",", ".").split(":").map(Number);
  if (parts.length > 1 && parts[parts.length - 1] >= 60 || parts.length === 3 && parts[1] >= 60) return null;
  const seconds = parts.reduce((total, part) => total * 60 + part, 0);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}

function timingValue(entry, side) {
  for (const key of [side + "_seconds", side + "_time", side]) {
    if (Object.prototype.hasOwnProperty.call(entry, key)) return entry[key];
  }
  return undefined;
}

function validRange(entry, duration) {
  const start = parseSeconds(timingValue(entry, "start"));
  const end = parseSeconds(timingValue(entry, "end"));
  if (start === null || end === null || end <= start || Number.isFinite(duration) && end > duration) return null;
  return { start, end };
}

function transcriptCues(entries, duration) {
  return (Array.isArray(entries) ? entries : []).map((entry, index) => {
    if (!entry || typeof entry.text !== "string" || !entry.text.trim()) return null;
    const range = validRange(entry, duration);
    if (!range) return null;
    return {
      start: range.start, end: range.end, name: "Dialogue " + (index + 1),
      text: entry.text.trim(), speaker: typeof entry.speaker === "string" ? entry.speaker.trim() : "",
    };
  }).filter(Boolean).sort((a, b) => a.start - b.start || a.end - b.end);
}

function cueTime(seconds) {
  const ms = Math.round(seconds * 1000);
  const whole = Math.floor(ms / 1000);
  const hours = Math.floor(whole / 3600);
  return (hours ? hours + ":" : "") + String(Math.floor(whole % 3600 / 60)).padStart(2, "0") + ":" +
    String(whole % 60).padStart(2, "0") + "." + String(ms % 1000).padStart(3, "0");
}

function searchTerms(query) {
  return String(query || "").trim().split(/\s+/).filter(Boolean).slice(0, 20);
}

function includesQuery(text, query) {
  const value = String(text || "").toLowerCase();
  return searchTerms(query).every(term => value.indexOf(term.toLowerCase()) !== -1);
}

/* Use text nodes exclusively. Metadata and dialogue must never become markup. */
function appendHighlight(document, parent, text, query) {
  const source = String(text || "");
  const terms = searchTerms(query).sort((a, b) => b.length - a.length);
  if (!terms.length) { parent.textContent = source; return; }
  const pattern = new RegExp(terms.map(term => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"), "gi");
  let previous = 0;
  let match;
  while ((match = pattern.exec(source)) !== null) {
    if (match.index > previous) {
      const normal = document.createElement("span");
      normal.textContent = source.slice(previous, match.index);
      parent.appendChild(normal);
    }
    const marked = document.createElement("span");
    marked.className = "highlight";
    marked.textContent = match[0];
    parent.appendChild(marked);
    previous = match.index + match[0].length;
  }
  if (previous < source.length) {
    const normal = document.createElement("span");
    normal.textContent = source.slice(previous);
    parent.appendChild(normal);
  }
}

function sourceBinding(asset, projectId) {
  const sources = asset && Array.isArray(asset.editor_sources) ? asset.editor_sources : [];
  return sources.find(source => source && source.host === "premiere" && source.project_id === projectId &&
    typeof source.item_id === "string" && typeof source.media_path === "string") || null;
}

function cleanSubtitleName(name) {
  let stem = String(name || "video").split(/[\\/]/).pop().replace(/\.[^.]+$/, "");
  stem = stem.replace(/[<>:"/\\|?*\u0000-\u001f\u007f\u202a-\u202e\u2066-\u2069]/g, "_").replace(/[. ]+$/, "").slice(0, 140);
  if (!stem || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(stem)) stem = "video_" + (stem || "subtitles");
  return stem + "_subtitles.srt";
}

function progressText(asset) {
  if (!asset) return "Load clips, then choose what to analyze.";
  const progress = asset.analysis_progress || {};
  const parts = [];
  if (progress.stage) parts.push(String(progress.stage).replace(/_/g, " "));
  if (progress.total_sections) parts.push((progress.completed_sections || 0) + "/" + progress.total_sections + " sections");
  if (Number.isFinite(progress.processed_seconds) && progress.total_seconds) parts.push(cueTime(progress.processed_seconds) + " / " + cueTime(progress.total_seconds));
  if (progress.completed_shots) parts.push(progress.completed_shots + " shots");
  if (asset.status === "error") return "Analysis stopped. Saved results remain available. " + String(asset.error || "Retry analysis to continue.");
  if (asset.status === "cancelled") return "Analysis cancelled. Saved results remain available.";
  if (asset.status === "complete" || asset.status === "completed") return "Analysis complete" + (asset.shot_count ? " · " + asset.shot_count + " shots" : "") + ".";
  return parts.join(" · ") || (ACTIVE_STATUSES.indexOf(asset.status) !== -1 ? "Preparing analysis… Results appear as sections finish." : "Ready to analyze.");
}

function createPanel(dependencies) {
  const document = dependencies.document;
  const uxp = dependencies.uxp;
  const host = dependencies.host;
  const fetch = dependencies.fetch;
  const schedule = dependencies.setTimeout || setTimeout;
  const unschedule = dependencies.clearTimeout || clearTimeout;
  const defaultServer = dependencies.platform === "darwin" ? MAC_SERVER : DEFAULT_SERVER;
  const el = id => document.getElementById(id);
  const state = {
    connected: false, token: "", server: defaultServer, project: null, assets: [], asset: null,
    tab: "shots", queries: { shots: "", transcript: "" }, shots: [], shotTotal: 0, shown: PAGE_SIZE,
    scope: 0, resultRevision: 0, resultsPending: false, nativeBlocked: true, polling: false, timer: null, searchTimer: null, initialized: false,
    busy: {}, preferences: { projects: {}, mode: "flash_only", server: defaultServer },
  };
  let initialization;
  let preferenceWrites = Promise.resolve();
  let assetRevision = 0;

  function message(text, error) {
    el("message").textContent = text;
    el("message").className = "message" + (error ? " error" : "");
  }
  function show(id, visible) { el(id).classList.toggle("hidden", !visible); }
  function isAnalyzing() { return state.asset && ACTIVE_STATUSES.indexOf(state.asset.status) !== -1; }
  function binding() { return sourceBinding(state.asset, state.project && state.project.id); }
  function controls() {
    const active = !!state.asset;
    const mutation = !!(state.busy.selection || state.busy.analyze || state.busy.markers);
    el("connect").disabled = !!state.busy.connect;
    el("disconnect").disabled = !state.connected || !!state.busy.connect;
    el("load-selection").disabled = !state.connected || mutation;
    el("analyze").disabled = !state.connected || !active || mutation || !!isAnalyzing();
    el("mode").disabled = mutation || !!isAnalyzing();
    el("analyze").textContent = isAnalyzing() ? "Analyzing…" : active && (state.asset.shot_count || state.asset.flash) ? "Analyze again" : "Analyze";
    el("add-markers").disabled = !state.connected || !binding() || !visibleResults().length || mutation || state.resultsPending || state.nativeBlocked;
    el("add-markers").textContent = state.busy.markers ? "Adding…" : "Add " + visibleResults().length + " markers";
    el("export-subtitles").disabled = !state.connected || !active || !transcriptCues(state.asset.transcript, state.asset.technical && state.asset.technical.duration_seconds).length || !!state.busy.export;
    show("workspace", state.connected);
    el("progress").textContent = progressText(state.asset);
  }

  function remember() {
    if (state.project) {
      state.preferences.projects[state.project.id] = {
        assetId: state.asset && state.asset.job_id, tab: state.tab, queries: state.queries,
      };
      const ids = Object.keys(state.preferences.projects);
      if (ids.length > 50) delete state.preferences.projects[ids[0]];
    }
    state.preferences.server = state.server;
    state.preferences.mode = el("mode").value;
    const serialized = JSON.stringify(state.preferences);
    preferenceWrites = preferenceWrites.then(async () => {
      const folder = await uxp.storage.localFileSystem.getDataFolder();
      const file = await folder.createFile(PREFS_FILE, { overwrite: true });
      await file.write(serialized);
    }).catch(() => { /* Preferences are useful; persistence must never block analysis. */ });
    return preferenceWrites;
  }

  async function request(path, init, textResponse) {
    const endpoint = state.server;
    const token = state.token;
    const headers = { Authorization: "Bearer " + token };
    const options = Object.assign({}, init || {}, { headers, redirect: "error" });
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    let timer;
    let controller;
    if (typeof AbortController !== "undefined") {
      controller = new AbortController();
      options.signal = controller.signal;
    }
    const response = await Promise.race([
      fetch(endpoint + "/api/editor/premiere" + path, options),
      new Promise((resolve, reject) => {
        timer = schedule(() => {
          if (controller) controller.abort();
          reject(new Error("The local service did not respond. Check that it is running, then reconnect."));
        }, 30000);
      }),
    ]).catch(error => {
      if (endpoint.indexOf("https://") === 0 && error && error.name === "TypeError") {
        throw new Error("Could not reach the local HTTPS service. Complete the Mac certificate setup, trust its local certificate, and start the companion app on port 8443.");
      }
      throw error;
    }).finally(() => { if (timer) unschedule(timer); });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      const reason = body && typeof body.detail === "string" ? body.detail : "Local service request failed (" + response.status + ").";
      if (response.status === 401 || response.status === 403) throw new Error("Panel token rejected. Run Premiere setup and reconnect with the current token.");
      const error = new Error(reason);
      error.status = response.status;
      throw error;
    }
    return textResponse ? response.text() : response.json();
  }

  function stopPoll() {
    if (state.timer !== null) unschedule(state.timer);
    state.timer = null;
  }
  function startPoll(delay) {
    stopPoll();
    if (state.connected && state.asset) state.timer = schedule(poll, delay === undefined ? 2500 : delay);
  }
  async function poll() {
    state.timer = null;
    if (!state.connected || !state.asset || state.polling) return;
    const scope = state.scope;
    const id = state.asset.job_id;
    const revision = assetRevision;
    state.polling = true;
    try {
      const asset = await request("/assets/" + encodeURIComponent(id));
      if (scope !== state.scope || revision !== assetRevision) return;
      state.asset = asset;
      const index = state.assets.findIndex(item => item.job_id === id);
      if (index !== -1) state.assets[index] = asset;
      renderPicker();
      if (state.tab === "shots") await refreshShots();
      else renderResults();
      controls();
    } catch (error) {
      if (scope === state.scope && revision === assetRevision) {
        if (error.status === 409) blockNative(error.message);
        else message(error.message, true);
      }
    } finally {
      state.polling = false;
      if (state.connected) startPoll(isAnalyzing() ? 2500 : 6000);
    }
  }

  function renderPicker() {
    el("project-name").textContent = state.project ? state.project.name : "Open a Premiere project.";
    el("asset-toggle").textContent = state.asset ? assetName(state.asset) + " ▾" : "Choose a clip ▾";
    const list = el("asset-list");
    list.textContent = "";
    const q = el("asset-search").value;
    const assets = state.assets.filter(asset => includesQuery(assetName(asset), q));
    assets.forEach(asset => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "asset-option" + (state.asset && state.asset.job_id === asset.job_id ? " selected" : "");
      item.setAttribute("role", "listitem");
      item.setAttribute("aria-label", "Choose " + assetName(asset));
      const title = document.createElement("span");
      appendHighlight(document, title, assetName(asset), q);
      const status = document.createElement("span");
      status.className = "asset-status";
      status.textContent = (asset.shot_count || 0) + " shots · " + (asset.status || "imported");
      item.appendChild(title);
      item.appendChild(status);
      item.addEventListener("click", () => run(() => selectAsset(asset.job_id)));
      list.appendChild(item);
    });
    if (!assets.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = q ? "No clips match that search." : "Select footage in Premiere and load it here.";
      list.appendChild(empty);
    }
  }
  function assetName(asset) { return asset.metadata && asset.metadata.title || asset.filename || "Untitled clip"; }

  function analysisSession(asset) {
    return String(asset && asset.analysis_config && asset.analysis_config.started_at || "");
  }
  function nativeContext(source) {
    return {
      scope: state.scope, revision: assetRevision, resultRevision: state.resultRevision,
      id: state.asset && state.asset.job_id, project: state.project && state.project.id,
      session: analysisSession(state.asset), tab: state.tab, source: Object.assign({}, source),
    };
  }
  function contextIsCurrent(context) {
    return state.connected && state.asset && state.asset.job_id === context.id && state.scope === context.scope &&
      assetRevision === context.revision && state.resultRevision === context.resultRevision &&
      state.project && state.project.id === context.project && state.tab === context.tab && analysisSession(state.asset) === context.session;
  }
  function blockNative(reason) {
    state.nativeBlocked = true;
    for (const button of el("results").children) button.disabled = true;
    message(reason + " Load the Premiere selection again before using native results.", true);
    controls();
  }
  async function verifyNativeContext(context) {
    if (!contextIsCurrent(context) || state.nativeBlocked) throw new Error("The selected results changed. Refresh the clip and try again.");
    let current;
    try { current = await request("/assets/" + encodeURIComponent(context.id)); }
    catch (error) {
      if (error.status === 409 && contextIsCurrent(context)) blockNative(error.message);
      throw error;
    }
    if (!contextIsCurrent(context)) throw new Error("The selected results changed. Refresh the clip and try again.");
    const source = sourceBinding(current, context.project);
    if (!source || source.item_id !== context.source.item_id || source.media_path !== context.source.media_path ||
        analysisSession(current) !== context.session) {
      blockNative("The source binding or analysis changed.");
      throw new Error("The source binding or analysis changed. Load the Premiere selection again before using these results.");
    }
    return source;
  }

  function allResults() {
    if (!state.asset) return [];
    const duration = state.asset.technical && state.asset.technical.duration_seconds;
    if (state.tab === "transcript") {
      return transcriptCues(state.asset.transcript, duration).filter(cue => includesQuery(cue.text + " " + cue.speaker, state.queries.transcript));
    }
    return state.shots.map(shot => {
      const range = validRange(shot, duration);
      if (!range) return null;
      const tags = Array.isArray(shot.tags) ? shot.tags.filter(tag => typeof tag === "string") : [];
      return {
        start: range.start, end: range.end, name: "Shot " + String(shot.shot_number || "").padStart(2, "0"),
        text: String(shot.visual_description || ""),
        detail: [shot.shot_type, shot.camera_movement, shot.mood, tags.join(", ")].filter(Boolean).join(" · "),
        context: typeof shot.match_context === "string" ? shot.match_context : "",
      };
    }).filter(Boolean);
  }
  function visibleResults() { return allResults().slice(0, Math.min(state.shown, MAX_RESULTS)); }
  function renderResults() {
    const list = el("results");
    list.textContent = "";
    const tab = state.tab;
    const q = state.queries[tab];
    const results = visibleResults();
    const total = tab === "shots" ? state.shotTotal : allResults().length;
    el("result-count").textContent = results.length + (total > results.length ? " of " + total : "") + (tab === "shots" ? " shots" : " lines");
    el("shots-tab").setAttribute("aria-selected", String(tab === "shots"));
    el("transcript-tab").setAttribute("aria-selected", String(tab === "transcript"));
    el("results").setAttribute("aria-labelledby", tab + "-tab");
    el("search").setAttribute("aria-label", tab === "shots" ? "Search shots" : "Search dialogue");
    el("search").setAttribute("placeholder", tab === "shots" ? "Search shots, tags, composition…" : "Search dialogue…");
    const bound = binding();
    results.forEach(result => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "result";
      button.disabled = !bound || state.nativeBlocked;
      button.setAttribute("aria-label", "Open " + result.name + " at " + cueTime(result.start) + " in the Source Monitor");
      const time = document.createElement("span");
      time.className = "result-time";
      time.textContent = cueTime(result.start) + " – " + cueTime(result.end);
      button.appendChild(time);
      const title = document.createElement("span");
      title.className = "result-title";
      appendHighlight(document, title, tab === "transcript" && result.speaker ? result.speaker : result.name, q);
      button.appendChild(title);
      const description = document.createElement("span");
      description.className = "result-description";
      appendHighlight(document, description, result.text, q);
      button.appendChild(description);
      for (const value of [result.detail, q && result.context]) {
        if (!value) continue;
        const detail = document.createElement("span");
        detail.className = "result-detail";
        appendHighlight(document, detail, value, q);
        button.appendChild(detail);
      }
      const source = bound && Object.assign({}, bound);
      const context = source && nativeContext(source);
      button.addEventListener("click", () => run(async () => {
        if (!source || state.busy.navigate || state.nativeBlocked) return;
        state.busy.navigate = true;
        try {
          const verified = await verifyNativeContext(context);
          await host.openSource(verified, result.start);
          if (contextIsCurrent(context)) message("Opened " + cueTime(result.start) + " in Premiere’s Source Monitor.");
        }
        finally { state.busy.navigate = false; }
      }));
      list.appendChild(button);
    });
    if (!results.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = !state.asset ? "Load a clip to begin." : q ? "No matches. Try another search." :
        tab === "transcript" ? "No timed dialogue yet. Analyze footage with audio to populate this tab." :
        isAnalyzing() ? "Shots will appear as each section finishes." : "Analyze this clip to discover shots.";
      list.appendChild(empty);
    }
    show("show-more", allResults().length > results.length && state.shown < MAX_RESULTS);
    show("export-subtitles", tab === "transcript");
    controls();
  }

  async function refreshShots() {
    if (!state.asset || !state.connected) return;
    const scope = state.scope;
    const id = state.asset.job_id;
    const revision = ++state.resultRevision;
    const q = state.queries.shots;
    state.resultsPending = true;
    controls();
    try {
      const data = await request("/assets/" + encodeURIComponent(id) + "/shots?q=" + encodeURIComponent(q) + "&limit=" + MAX_RESULTS);
      if (scope !== state.scope || revision !== state.resultRevision || !state.asset || state.asset.job_id !== id || q !== state.queries.shots) return;
      state.shots = Array.isArray(data.shots) ? data.shots : [];
      state.shotTotal = Number.isFinite(data.total) ? data.total : state.shots.length;
      if (state.tab === "shots") renderResults();
    } catch (error) {
      if (error.status === 409 && scope === state.scope && state.asset && state.asset.job_id === id) blockNative(error.message);
      throw error;
    } finally {
      if (scope === state.scope && revision === state.resultRevision) state.resultsPending = false;
      controls();
    }
  }

  async function refreshProject(selection) {
    const scope = state.scope;
    const selected = selection || await host.getSelection();
    if (scope !== state.scope) return;
    const project = { id: selected.project_id, name: selected.project_name || "Premiere project", path: selected.project_path || "" };
    if (!project.id) throw new Error("Open a saved Premiere project, then load selected clips.");
    const changed = !state.project || state.project.id !== project.id;
    if (changed) {
      ++state.scope;
      ++assetRevision;
      ++state.resultRevision;
      stopPoll();
      state.asset = null;
      state.shots = [];
      state.shotTotal = 0;
      state.resultsPending = false;
      state.nativeBlocked = true;
      const saved = state.preferences.projects[project.id] || {};
      state.tab = saved.tab === "transcript" ? "transcript" : "shots";
      state.queries = { shots: String(saved.queries && saved.queries.shots || "").slice(0, 300), transcript: String(saved.queries && saved.queries.transcript || "").slice(0, 300) };
      el("search").value = state.queries[state.tab];
    }
    state.project = project;
    const currentScope = state.scope;
    const data = await request("/assets?project_id=" + encodeURIComponent(project.id));
    if (currentScope !== state.scope) return;
    state.assets = Array.isArray(data.assets) ? data.assets : [];
    const saved = state.preferences.projects[project.id] || {};
    const chosen = state.assets.find(asset => asset.job_id === (state.asset && state.asset.job_id || saved.assetId)) || state.assets[0];
    renderPicker();
    if (chosen) await selectAsset(chosen.job_id);
    else { renderResults(); show("asset-picker", true); el("asset-toggle").setAttribute("aria-expanded", "true"); }
    return selected;
  }

  async function selectAsset(id) {
    if (!state.connected || state.busy.analyze || state.busy.markers) return;
    const scope = state.scope;
    const revision = ++assetRevision;
    ++state.resultRevision;
    stopPoll();
    state.shots = [];
    state.shotTotal = 0;
    state.resultsPending = false;
    state.nativeBlocked = true;
    state.shown = PAGE_SIZE;
    state.asset = state.assets.find(item => item.job_id === id) || null;
    renderPicker();
    renderResults();
    const asset = await request("/assets/" + encodeURIComponent(id));
    if (scope !== state.scope || revision !== assetRevision) return;
    state.asset = asset;
    state.nativeBlocked = false;
    show("asset-picker", false);
    el("asset-toggle").setAttribute("aria-expanded", "false");
    await remember();
    if (state.tab === "shots") await refreshShots();
    else renderResults();
    controls();
    startPoll();
  }

  async function connect() {
    if (state.busy.connect) return;
    const server = normalizeServer(el("server").value);
    if (dependencies.platform === "darwin" && server.indexOf("http://") === 0) {
      throw new Error("Premiere on Mac requires HTTPS. Use https://localhost:8443 after completing the local certificate setup.");
    }
    const token = String(el("token").value || state.token).trim();
    if (!token || !/^[\x21-\x7e]{16,512}$/.test(token)) throw new Error("Paste a valid panel token from the Premiere setup command.");
    state.busy.connect = true;
    ++state.scope;
    stopPoll();
    state.connected = false;
    state.server = server;
    state.token = token;
    controls();
    const scope = state.scope;
    try {
      const health = await request("/health");
      if (scope !== state.scope) return;
      state.connected = true;
      let saved = false;
      if (uxp.storage.secureStorage) {
        try { await uxp.storage.secureStorage.setItem(TOKEN_KEY, token); saved = true; }
        catch (error) { /* Leave the token in memory only if OS secure storage is unavailable. */ }
      }
      el("token").value = "";
      show("connection", false);
      el("connection-toggle").setAttribute("aria-expanded", "false");
      message("Connected." + (!saved ? " Token is kept for this session only." : "") +
        (health.google_configured === false ? " Configure Gemini in the local service before analyzing." : ""));
      await remember();
      try { await refreshProject(); }
      catch (error) { message("Connected. " + error.message, true); }
    } finally {
      state.busy.connect = false;
      controls();
    }
  }

  async function disconnect() {
    ++state.scope;
    ++assetRevision;
    ++state.resultRevision;
    stopPoll();
    state.connected = false;
    state.token = "";
    state.asset = null;
    state.project = null;
    state.assets = [];
    state.shots = [];
    state.resultsPending = false;
    state.nativeBlocked = true;
    el("token").value = "";
    if (uxp.storage.secureStorage) await uxp.storage.secureStorage.removeItem(TOKEN_KEY).catch(() => {});
    show("connection", true);
    el("connection-toggle").setAttribute("aria-expanded", "true");
    message("Disconnected. Analysis already running in the local service continues.");
    renderPicker();
    renderResults();
    controls();
  }

  async function loadSelection() {
    if (!state.connected || state.busy.selection || state.busy.analyze || state.busy.markers) return;
    state.busy.selection = true;
    controls();
    const scope = state.scope;
    try {
      const selection = await host.getSelection();
      if (scope !== state.scope) return;
      if (!selection.clips || !selection.clips.length) throw new Error("Select media clips in Premiere’s Project panel first.");
      if (selection.clips.length > 100) throw new Error("Load up to 100 selected clips at a time.");
      let firstId;
      let failed = 0;
      const errors = [];
      for (let index = 0; index < selection.clips.length; index++) {
        if (scope !== state.scope) return;
        const clip = selection.clips[index];
        message("Loading clip " + (index + 1) + " of " + selection.clips.length + "…");
        try {
          const asset = await request("/register", {
            method: "POST", body: JSON.stringify({ project_id: selection.project_id, item_id: clip.item_id, media_path: clip.media_path, name: clip.name, source_fps: clip.source_fps }),
          });
          if (!firstId) firstId = asset.job_id;
        } catch (error) { failed++; errors.push(error.message); }
      }
      if (scope !== state.scope) return;
      await refreshProject(selection);
      if (firstId) await selectAsset(firstId);
      const warnings = Array.isArray(selection.warnings) ? selection.warnings : [];
      message((selection.clips.length - failed) + " clips loaded. Choose an analysis type and click Analyze." +
        (failed ? " " + failed + " could not be loaded: " + errors[0] : "") + (warnings.length ? " " + warnings.join(" ") : ""), failed > 0);
    } finally { state.busy.selection = false; controls(); }
  }

  async function analyze() {
    if (!state.connected || !state.asset || state.busy.analyze || isAnalyzing()) return;
    state.busy.analyze = true;
    controls();
    const scope = state.scope;
    const id = state.asset.job_id;
    try {
      const mode = el("mode").value;
      if (mode !== "flash_only" && mode !== "flash_pro") throw new Error("Choose Shots and tags or Full analysis.");
      ++assetRevision;
      ++state.resultRevision;
      await request("/assets/" + encodeURIComponent(id) + "/analyze", { method: "POST", body: JSON.stringify({ mode }) });
      if (scope !== state.scope || !state.asset || state.asset.job_id !== id) return;
      state.asset.status = "analyzing";
      state.asset.error = null;
      state.asset.analysis_progress = { stage: "preparing" };
      message("Analysis started. You can keep editing; results update here as sections finish.");
      await remember();
      startPoll(1000);
    } finally { state.busy.analyze = false; controls(); }
  }

  async function setTab(tab) {
    if (tab !== "shots" && tab !== "transcript") return;
    state.tab = tab;
    state.resultsPending = false;
    state.shown = PAGE_SIZE;
    el("search").value = state.queries[tab];
    await remember();
    renderResults();
    if (tab === "shots" && state.asset) await refreshShots();
  }

  async function search(query) {
    state.queries[state.tab] = String(query || "").slice(0, 300);
    if (state.tab === "transcript") state.resultsPending = false;
    state.shown = PAGE_SIZE;
    ++state.resultRevision;
    await remember();
    if (state.tab === "shots") await refreshShots();
    else renderResults();
  }

  async function addMarkers() {
    const source = binding();
    const results = visibleResults();
    if (!source || !results.length || state.busy.markers || state.resultsPending || state.nativeBlocked) return;
    const context = nativeContext(source);
    state.busy.markers = true;
    controls();
    const scope = state.scope;
    const id = state.asset.job_id;
    try {
      const cues = results.slice(0, MAX_RESULTS).map(result => ({
        start: result.start, end: result.end, name: result.name,
        comment: [result.speaker, result.text, result.detail, result.context].filter(Boolean).join("\n"),
      }));
      const verified = await verifyNativeContext(context);
      const result = await host.addMarkers(verified, cues);
      if (scope === state.scope && state.asset && state.asset.job_id === id) {
        message(result.added + " source markers added." + (result.skipped ? " " + result.skipped + " existing markers skipped." : ""));
      }
    } finally { state.busy.markers = false; controls(); }
  }

  async function exportSubtitles() {
    if (!state.connected || !state.asset || state.busy.export) return;
    state.busy.export = true;
    controls();
    const id = state.asset.job_id;
    const filename = cleanSubtitleName(assetName(state.asset));
    try {
      const content = await request("/assets/" + encodeURIComponent(id) + "/subtitles", undefined, true);
      const file = await uxp.storage.localFileSystem.getFileForSaving(filename, { types: ["srt"] });
      if (!file) return;
      await file.write(content);
      message("Subtitles exported. The file includes all saved dialogue, regardless of your search.");
    } finally { state.busy.export = false; controls(); }
  }

  async function run(action) {
    try { await action(); }
    catch (error) { message(error && error.message || "Something went wrong. Please try again.", true); controls(); }
  }
  function on(id, event, action) { el(id).addEventListener(event, () => run(action)); }

  async function init() {
    if (initialization) return initialization;
    initialization = (async () => {
      on("connect", "click", connect);
      on("disconnect", "click", disconnect);
      on("connection-toggle", "click", () => {
        const expanded = el("connection-toggle").getAttribute("aria-expanded") !== "true";
        show("connection", expanded);
        el("connection-toggle").setAttribute("aria-expanded", String(expanded));
      });
      on("load-selection", "click", loadSelection);
      on("analyze", "click", analyze);
      on("asset-toggle", "click", () => {
        const expanded = el("asset-toggle").getAttribute("aria-expanded") !== "true";
        show("asset-picker", expanded);
        el("asset-toggle").setAttribute("aria-expanded", String(expanded));
        if (expanded) el("asset-search").focus();
      });
      on("asset-search", "input", renderPicker);
      on("shots-tab", "click", () => setTab("shots"));
      on("transcript-tab", "click", () => setTab("transcript"));
      on("mode", "change", remember);
      on("show-more", "click", () => { state.shown = Math.min(state.shown + PAGE_SIZE, MAX_RESULTS); renderResults(); });
      on("add-markers", "click", addMarkers);
      on("export-subtitles", "click", exportSubtitles);
      on("open-library", "click", async () => {
        const error = await uxp.shell.openExternal(normalizeServer(state.server) + "/");
        if (typeof error === "string" && error) throw new Error(error);
      });
      el("search").addEventListener("input", () => {
        if (state.searchTimer !== null) unschedule(state.searchTimer);
        state.queries[state.tab] = String(el("search").value || "").slice(0, 300);
        ++state.resultRevision;
        state.resultsPending = state.tab === "shots";
        controls();
        state.searchTimer = schedule(() => { state.searchTimer = null; run(() => search(el("search").value)); }, 250);
      });
      for (const id of ["shots-tab", "transcript-tab"]) {
        el(id).addEventListener("keydown", event => {
          if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
            event.preventDefault();
            const next = id === "shots-tab" ? "transcript" : "shots";
            el(next + "-tab").focus();
            run(() => setTab(next));
          }
        });
      }
      for (const id of ["server", "token"]) el(id).addEventListener("keydown", event => { if (event.key === "Enter") run(connect); });
      el("server").value = state.server;
      if (dependencies.platform === "darwin") {
        el("connection-hint").textContent = "Mac uses local HTTPS on port 8443. Complete certificate setup, open the companion app, then copy the Premiere pairing code from Settings.";
      }
      try {
        const folder = await uxp.storage.localFileSystem.getDataFolder();
        const file = await folder.getEntry(PREFS_FILE);
        const preferences = JSON.parse(await file.read());
        if (preferences && typeof preferences === "object") {
          state.preferences = { server: normalizeServer(preferences.server || defaultServer), mode: preferences.mode === "flash_pro" ? "flash_pro" : "flash_only", projects: preferences.projects && typeof preferences.projects === "object" ? preferences.projects : {} };
          state.server = state.preferences.server;
          el("server").value = state.server;
          el("mode").value = state.preferences.mode;
          el("mode").setAttribute("value", state.preferences.mode);
        }
      } catch (error) { /* Missing or obsolete preferences should use defaults. */ }
      if (uxp.storage.secureStorage) {
        try {
          const stored = await uxp.storage.secureStorage.getItem(TOKEN_KEY);
          state.token = Array.from(stored, value => String.fromCharCode(value)).join("");
        } catch (error) { /* Secure storage is a cache; users can reconnect when cleared. */ }
      }
      state.initialized = true;
      controls();
      if (state.token) await run(connect);
    })();
    return initialization;
  }

  function destroy() {
    ++state.scope;
    state.connected = false;
    stopPoll();
    if (state.searchTimer !== null) unschedule(state.searchTimer);
    state.searchTimer = null;
    state.token = "";
  }
  return { init, connect, disconnect, loadSelection, refreshProject, selectAsset, analyze, setTab, search, addMarkers, exportSubtitles, destroy, getState: () => state };
}

module.exports = { createPanel, normalizeServer, parseSeconds, validRange, transcriptCues, cueTime, searchTerms, includesQuery, appendHighlight, sourceBinding, cleanSubtitleName, progressText };

if (typeof document !== "undefined") {
  const uxp = require("uxp");
  const host = require("./host");
  let platform;
  try { platform = require("os").platform(); }
  catch (error) { /* The editable endpoint remains available if host OS detection fails. */ }
  const panel = createPanel({ document, uxp, host, fetch, platform });
  uxp.entrypoints.setup({
    plugin: { destroy() { panel.destroy(); } },
    panels: {
      videoAnalyzerPanel: {
        async create() { await panel.init(); },
        async show() {
          await panel.init();
          if (panel.getState().connected) {
            try { await panel.refreshProject(); }
            catch (error) { document.getElementById("message").textContent = error.message; }
          }
        },
      },
    },
  });
}
