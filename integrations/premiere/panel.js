/* UXP uses CommonJS. This panel deliberately has no build step or webview. */
const DEFAULT_SERVER = "http://127.0.0.1:8000";
const MAC_SERVER = "https://localhost:8443";
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
  if (progress.stage) parts.push(progress.stage === "preparing_proxy" || progress.stage === "preparing_part" ? "Preparing analysis proxy" : String(progress.stage).replace(/_/g, " "));
  if (progress.upload_part && progress.total_upload_parts && ["preparing_proxy", "preparing_part", "uploading", "processing"].indexOf(progress.stage) !== -1) parts.push("Part " + progress.upload_part + "/" + progress.total_upload_parts);
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
  const readConnection = dependencies.readConnection || require("./connection.js").readConnection;
  const defaultServer = dependencies.platform === "darwin" ? MAC_SERVER : DEFAULT_SERVER;
  const el = id => document.getElementById(id);
  const state = {
    connected: false, token: "", server: defaultServer, project: null, assets: [], asset: null,
    tab: "shots", queries: { shots: "", transcript: "" }, shots: [], shotTotal: 0, shown: PAGE_SIZE,
    scope: 0, resultRevision: 0, resultsPending: false, nativeBlocked: true, polling: false, timer: null, searchTimer: null, initialized: false,
    busy: {}, resultActions: [], actionFeedback: {}, preferences: { projects: {}, mode: "flash_only", server: defaultServer },
  };
  let initialization;
  let connectionTimer = null;
  let autoConnect = true;
  let preferenceWrites = Promise.resolve();
  let assetRevision = 0;

  function stopConnectionRetry() {
    if (connectionTimer !== null) unschedule(connectionTimer);
    connectionTimer = null;
  }
  function retryConnection() {
    stopConnectionRetry();
    if (autoConnect) connectionTimer = schedule(async function reconnect() {
      connectionTimer = null;
      if (autoConnect && !state.connected) await run(connect);
    }, 4000);
  }

  function message(text, error) {
    el("message").textContent = text;
    el("message").className = "message" + (error ? " error" : "");
  }
  function show(id, visible) { el(id).classList.toggle("hidden", !visible); }
  function isAnalyzing() { return state.asset && ACTIVE_STATUSES.indexOf(state.asset.status) !== -1; }
  function binding() { return state.asset && state.asset.source_available === false ? null : sourceBinding(state.asset, state.project && state.project.id); }
  function analysisBinding() {
    const projectId = state.project && state.project.id;
    const direct = sourceBinding(state.asset, projectId);
    if (direct) return direct;
    const key = path => typeof path === "string" && (/^[a-z]:[\\/]/i.test(path) || path.startsWith("\\\\")) ? path.replace(/\\/g, "/").toLowerCase() : path;
    const path = state.asset && state.asset.source_media_path;
    if (!path) return null;
    for (const asset of state.assets) {
      const source = sourceBinding(asset, projectId);
      if (source && key(source.media_path) === key(path)) return source;
    }
    return null;
  }
  function controls() {
    const active = !!state.asset;
    const mutation = !!(state.busy.selection || state.busy.analyze || state.busy.markers || state.busy.metadata || state.busy.timeline);
    el("connect").disabled = !!state.busy.connect;
    el("disconnect").disabled = !state.connected || !!state.busy.connect;
    el("load-selection").disabled = !state.connected || mutation;
    el("refresh-analysis").disabled = !state.connected || mutation;
    for (const button of state.resultActions) button.disabled = !state.connected || !binding() || mutation || state.resultsPending || state.nativeBlocked;
    el("analyze").disabled = !state.connected || !active || !analysisBinding() || mutation || !!isAnalyzing();
    el("mode").disabled = mutation || !!isAnalyzing();
    el("analyze").textContent = isAnalyzing() ? "Analyzing…" : active && state.asset.source_available === false ? "Analyze current source" : active && (state.asset.shot_count || state.asset.flash) ? "Analyze again" : "Analyze";
    el("add-markers").disabled = !state.connected || !binding() || !visibleResults().length || mutation || state.resultsPending || state.nativeBlocked;
    const markerCount = visibleResults().length;
    el("add-markers").textContent = state.busy.markers ? "Adding…" : "Add " + markerCount + " source " + (markerCount === 1 ? "marker" : "markers");
    show("add-markers", markerCount > 0);
    show("marker-help", markerCount > 0);
    el("sync-metadata").disabled = !state.connected || !binding() || !active || mutation || !!isAnalyzing() || state.nativeBlocked;
    el("sync-metadata").textContent = state.busy.metadata ? "Syncing metadata…" : "Sync metadata to Premiere";
    el("export-subtitles").disabled = !state.connected || !active || !transcriptCues(state.asset.transcript, state.asset.technical && state.asset.technical.duration_seconds).length || !!state.busy.export;
    show("workspace", state.connected);
    el("progress").textContent = state.asset && state.asset.source_available === false ? "Older saved results. Analyze current source to update them; timeline actions require results for the current file." : progressText(state.asset);
  }

  function selectedMode() {
    const index = el("mode").selectedIndex;
    if (index === 0) return "flash_only";
    if (index === 1) return "flash_pro";
    return el("mode").value;
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
    state.preferences.mode = selectedMode();
    if (dependencies.persistPreferences === false) return Promise.resolve();
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
      if (autoConnect && endpoint === state.server && token === state.token) {
        state.connected = false;
        state.token = "";
        stopPoll();
        retryConnection();
        controls();
      }
      if (endpoint.indexOf("https://") === 0 && error && error.name === "TypeError") {
        throw new Error("Could not reach the local HTTPS service. Complete the Mac certificate setup, trust its local certificate, and start the companion app on port 8443.");
      }
      throw error;
    }).finally(() => { if (timer) unschedule(timer); });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      const reason = body && typeof body.detail === "string" ? body.detail : "Local service request failed (" + response.status + ").";
      if ((response.status === 401 || response.status === 403) && endpoint === state.server && token === state.token) {
        state.connected = false; state.token = ""; stopPoll(); retryConnection(); controls();
        throw new Error("The local connection changed. Reconnecting to Video Analyzer…");
      }
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
    if (state.busy.timeline) { startPoll(); return; }
    if (!state.connected || !state.asset || state.polling) return;
    const scope = state.scope;
    const id = state.asset.job_id;
    const revision = assetRevision;
    state.polling = true;
    try {
      const asset = await request("/assets/" + encodeURIComponent(id) + "?saved=true");
      if (scope !== state.scope || revision !== assetRevision || state.busy.timeline || state.busy.markers || state.busy.metadata) return;
      state.asset = asset;
      const index = state.assets.findIndex(item => item.job_id === id);
      if (index !== -1) state.assets[index] = asset;
      renderPicker();
      if (state.tab === "shots") await refreshShots(true);
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
      const item = document.createElement("sp-button");
      item.type = "button";
      item.className = "asset-option" + (state.asset && state.asset.job_id === asset.job_id ? " selected" : "");
      item.setAttribute("variant", "secondary");
      item.setAttribute("role", "listitem");
      item.setAttribute("aria-label", "Choose " + assetName(asset));
      const title = document.createElement("span");
      appendHighlight(document, title, assetName(asset), q);
      const status = document.createElement("span");
      status.className = "asset-status";
      status.textContent = (asset.shot_count || 0) + " clips · " + (asset.source_available === false ? "saved results; source changed/offline" : asset.status || "imported");
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
        start: range.start, end: range.end, name: "Clip " + String(shot.shot_number || "").padStart(2, "0"),
        thumbnail: shot.thumbnail_url, number: shot.shot_number,
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
    state.resultActions = [];
    const tab = state.tab;
    const q = state.queries[tab];
    const results = visibleResults();
    const total = tab === "shots" ? state.shotTotal : allResults().length;
    el("result-count").textContent = results.length + (total > results.length ? " of " + total : "") + (tab === "shots" ? " clips" : " lines");
    el("shots-tab").setAttribute("aria-selected", String(tab === "shots"));
    el("transcript-tab").setAttribute("aria-selected", String(tab === "transcript"));
    el("shots-tab").setAttribute("variant", tab === "shots" ? "primary" : "secondary");
    el("transcript-tab").setAttribute("variant", tab === "transcript" ? "primary" : "secondary");
    el("results").setAttribute("aria-labelledby", tab + "-tab");
    el("search-label").textContent = tab === "shots" ? "Search clips" : "Search dialogue";
    el("search").setAttribute("aria-label", tab === "shots" ? "Search clips" : "Search dialogue");
    el("search").setAttribute("placeholder", tab === "shots" ? "Descriptions, tags…" : "Dialogue…");
    const bound = binding();
    results.forEach(result => {
      const button = document.createElement("div");
      button.setAttribute("role", "button");
      button.setAttribute("tabindex", "0");
      button.className = "result";
      button.disabled = !bound || state.nativeBlocked;
      button.setAttribute("aria-label", "Open " + result.name + " at " + cueTime(result.start) + " in the Source Monitor");
      const prefix = "/api/thumbnails/" + encodeURIComponent(state.asset.job_id) + "/" + result.number + "?v=";
      if (tab === "shots" && typeof result.thumbnail === "string" && result.thumbnail.startsWith(prefix) && !/[\r\n]/.test(result.thumbnail)) {
        const image = document.createElement("img");
        image.className = "result-thumbnail";
        image.setAttribute("src", state.server + result.thumbnail);
        image.setAttribute("alt", "");
        image.addEventListener("error", () => { image.classList.toggle("hidden", true); });
        button.appendChild(image);
      }
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
      const navigate = () => run(async () => {
        if (!source || state.busy.navigate || state.nativeBlocked) return;
        state.busy.navigate = true;
        try {
          const verified = await verifyNativeContext(context);
          await host.openSource(verified, result.start);
          if (contextIsCurrent(context)) message("Opened " + cueTime(result.start) + " in Premiere’s Source Monitor.");
        }
        finally { state.busy.navigate = false; }
      });
      button.addEventListener("click", event => {
        if (event.target && event.target.className === "clip-action") return;
        navigate();
      });
      button.addEventListener("keydown", event => {
        if ((event.key === "Enter" || event.key === " ") && event.target === button) { event.preventDefault(); navigate(); }
      });
      if (tab === "shots") {
        const actions = document.createElement("div"); actions.className = "row clip-actions";
        for (const operation of ["insertClip", "makeSubclip"]) {
          const action = document.createElement("sp-button");
          action.type = "button"; action.className = "clip-action";
          action.textContent = operation === "insertClip" ? "Add to timeline" : "Prepare to drag";
          action.setAttribute("variant", operation === "insertClip" ? "primary" : "secondary");
          action.disabled = button.disabled || typeof host[operation] !== "function" || !!state.busy.timeline || state.resultsPending;
          if (typeof host[operation] === "function") state.resultActions.push(action);
          action.addEventListener("click", event => {
            if (event.stopPropagation) event.stopPropagation();
            run(async () => {
              if (state.busy.timeline || state.busy.selection || state.busy.markers || state.busy.metadata || state.busy.analyze || !source || state.resultsPending || state.nativeBlocked) return;
              state.busy.timeline = true; stopPoll(); controls();
              const feedbackKey = state.asset.job_id + ":" + result.start + ":" + result.end;
              const feedback = text => { state.actionFeedback[feedbackKey] = text; status.textContent = text; };
              feedback(operation === "insertClip" ? "Adding clip to timeline…" : "Preparing clip for dragging…");
              try {
                const verified = await verifyNativeContext(context);
                const output = await host[operation](verified, { start: result.start, end: result.end,
                  name: result.name, duration: state.asset.technical.duration_seconds, session: context.session });
                if (contextIsCurrent(context)) { const text = operation === "insertClip" ?
                  "Inserted " + result.name + " at " + cueTime(output.position) + " on V1/A1. Undo in Premiere to remove the edit." :
                  "Ready. Drag the clip from Premiere’s Source Monitor or Project panel into the timeline.";
                  feedback(text); message(text); }
              } catch (error) { feedback(error.message); status.className = "action-status error"; throw error; }
              finally { state.busy.timeline = false; controls(); startPoll(); }
            });
          });
          actions.appendChild(action);
        }
        button.appendChild(actions);
        const status = document.createElement("span");
        status.className = "action-status";
        status.textContent = state.actionFeedback[state.asset.job_id + ":" + result.start + ":" + result.end] || "";
        button.appendChild(status);
      }
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

  async function refreshShots(background = false) {
    if (!state.asset || !state.connected) return;
    const scope = state.scope;
    const id = state.asset.job_id;
    const revision = background ? state.resultRevision : ++state.resultRevision;
    const q = state.queries.shots;
    state.resultsPending = true;
    controls();
    try {
      const data = await request("/assets/" + encodeURIComponent(id) + "/shots?saved=true&q=" + encodeURIComponent(q) + "&limit=" + MAX_RESULTS);
      if (scope !== state.scope || revision !== state.resultRevision || !state.asset || state.asset.job_id !== id || q !== state.queries.shots) return;
      if (background && state.busy.timeline) return;
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
    const data = await request("/assets?include_saved=true&project_id=" + encodeURIComponent(project.id));
    if (currentScope !== state.scope) return;
    state.assets = Array.isArray(data.assets) ? data.assets : [];
    const saved = state.preferences.projects[project.id] || {};
    let chosen = state.assets.find(asset => asset.job_id === (state.asset && state.asset.job_id || saved.assetId)) || state.assets[0];
    if (chosen && !chosen.shot_count && !(chosen.transcript || []).length && !ACTIVE_STATUSES.includes(chosen.status)) {
      chosen = state.assets.find(asset => asset.source_media_path && asset.source_media_path === chosen.source_media_path && (asset.shot_count || (asset.transcript || []).length)) || chosen;
    }
    renderPicker();
    if (chosen) await selectAsset(chosen.job_id);
    else { renderResults(); show("asset-picker", true); el("asset-toggle").setAttribute("aria-expanded", "true"); }
    return selected;
  }

  async function selectAsset(id) {
    if (!state.connected || state.busy.analyze || state.busy.markers || state.busy.metadata || state.busy.timeline) return;
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
    const asset = await request("/assets/" + encodeURIComponent(id) + "?saved=true");
    if (scope !== state.scope || revision !== assetRevision) return;
    state.asset = asset;
    state.nativeBlocked = asset.source_available === false;
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
    autoConnect = true;
    stopConnectionRetry();
    state.busy.connect = true;
    ++state.scope;
    stopPoll();
    state.connected = false;
    state.token = "";
    controls();
    const scope = state.scope;
    try {
      const connection = await readConnection();
      if (scope !== state.scope) return;
      const server = normalizeServer(connection.server);
      if (dependencies.platform === "darwin" && server.indexOf("http://") === 0) {
        throw new Error("Premiere on Mac requires HTTPS. Use https://localhost:8443 after completing the local certificate setup.");
      }
      state.server = server;
      state.token = connection.token;
      el("server").value = server;
      const health = await request("/health");
      if (scope !== state.scope) return;
      state.connected = true;
      show("connection", false);
      el("connection-toggle").setAttribute("aria-expanded", "false");
      message("Connected automatically." +
        (health.google_configured === false ? " Configure Gemini in the local service before analyzing." : ""));
      await remember();
      if (dependencies.deferHostUntilUser) {
        message("Connected automatically. Load Premiere selection or reload saved results when your project is ready.");
      } else {
        try { await refreshProject(); }
        catch (error) { message("Connected. " + error.message, true); }
      }
    } catch (error) {
      state.connected = false;
      state.token = "";
      throw error;
    } finally {
      state.busy.connect = false;
      controls();
      if (scope === state.scope && !state.connected) retryConnection();
    }
  }

  async function disconnect() {
    autoConnect = false;
    stopConnectionRetry();
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
    show("connection", true);
    el("connection-toggle").setAttribute("aria-expanded", "true");
    message("Disconnected. Analysis already running in the local service continues.");
    renderPicker();
    renderResults();
    controls();
  }

  async function loadSelection() {
    if (!state.connected || state.busy.selection || state.busy.analyze || state.busy.markers || state.busy.metadata) return;
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
      message((selection.clips.length - failed) + " clips loaded. " + (state.asset && state.asset.shot_count ? "Saved analysis restored. Click a result to preview or insert it." : "Choose an analysis type and click Analyze.") +
        (failed ? " " + failed + " could not be loaded: " + errors[0] : "") + (warnings.length ? " " + warnings.join(" ") : ""), failed > 0);
    } finally { state.busy.selection = false; controls(); }
  }

  async function analyze() {
    if (!state.connected || !state.asset || state.busy.selection || state.busy.timeline || state.busy.markers || state.busy.analyze || state.busy.metadata || isAnalyzing()) return;
    const source = analysisBinding();
    if (!source) throw new Error("Load the original clip from Premiere before analyzing its current version.");
    state.busy.analyze = true;
    stopPoll();
    controls();
    const scope = state.scope;
    try {
      const mode = selectedMode();
      if (mode !== "flash_only" && mode !== "flash_pro") throw new Error("Choose Shots and tags or Full analysis.");
      ++assetRevision;
      ++state.resultRevision;
      // Refresh the file identity independently of the saved results being read.
      // This never promotes old analysis into a changed source's timeline edits.
      await host.resolveBinding(source);
      if (scope !== state.scope) return;
      message("Checking current Premiere source…");
      const current = await request("/register", { method: "POST", body: JSON.stringify({
        project_id: source.project_id, item_id: source.item_id, media_path: source.media_path,
        name: assetName(state.asset), source_fps: source.source_fps,
      }) });
      if (scope !== state.scope) return;
      const currentSource = sourceBinding(current, source.project_id);
      if (!currentSource || currentSource.item_id !== source.item_id || currentSource.media_path !== source.media_path) throw new Error("The current source changed. Load the Premiere selection again.");
      await host.resolveBinding(currentSource);
      if (scope !== state.scope) return;
      const id = current.job_id;
      const index = state.assets.findIndex(asset => asset.job_id === id);
      if (index >= 0) state.assets[index] = current; else state.assets.unshift(current);
      const changed = state.asset.job_id !== id;
      state.asset = current;
      state.nativeBlocked = false;
      if (changed) { state.shots = []; state.shotTotal = 0; state.resultsPending = false; state.actionFeedback = {}; }
      renderPicker(); renderResults();
      if (ACTIVE_STATUSES.includes(current.status)) {
        message("The current source is already being analyzed. Results update as sections finish.");
        await remember(); return;
      }
      await request("/assets/" + encodeURIComponent(id) + "/analyze", { method: "POST", body: JSON.stringify({ mode }) });
      if (scope !== state.scope || !state.asset || state.asset.job_id !== id) return;
      state.asset.status = "analyzing";
      state.asset.error = null;
      state.asset.analysis_progress = { stage: "preparing" };
      state.shots = []; state.shotTotal = 0; state.resultsPending = false;
      renderResults();
      message("Analysis started. You can keep editing; results update here as sections finish.");
      await remember();
      startPoll(1000);
    } finally { state.busy.analyze = false; controls(); startPoll(); }
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
    if (!source || !results.length || state.busy.markers || state.busy.metadata || state.resultsPending || state.nativeBlocked) return;
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

  async function syncMetadata() {
    const source = binding();
    if (!source || state.busy.metadata || state.busy.markers || state.busy.analyze || state.busy.selection || isAnalyzing() || state.nativeBlocked) return;
    const context = nativeContext(source);
    state.busy.metadata = true; controls();
    try {
      const verified = await verifyNativeContext(context);
      const mapping = await request("/assets/" + encodeURIComponent(context.id) + "/metadata?analysis_session=" + encodeURIComponent(context.session));
      if (!contextIsCurrent(context)) throw new Error("The selected clip changed. Refresh before syncing metadata.");
      const result = await host.syncMetadata(verified, mapping);
      if (contextIsCurrent(context)) message(result.updated + " metadata fields synced. Complete analysis and timed dialogue retained." + (result.preserved ? " " + result.preserved + " editor fields preserved." : ""));
    } finally { state.busy.metadata = false; controls(); }
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
      on("refresh-analysis", "click", async () => {
        if (state.busy.timeline || state.busy.selection) return;
        state.busy.selection = true; stopPoll(); controls();
        try {
          const selected = await host.getSelection();
          const source = selected.project_id === (state.project && state.project.id) ? analysisBinding() : null;
          // Re-register the bound original to discover analysis saved by the
          // full app or another editor session, rather than rereading an empty job.
          if (source) await request("/register", { method: "POST", body: JSON.stringify({
            project_id: source.project_id, item_id: source.item_id, media_path: source.media_path,
            name: assetName(state.asset), source_fps: source.source_fps,
          }) });
          await refreshProject(selected);
          const count = state.asset && state.asset.shot_count || 0;
          message(state.asset && state.asset.source_available === false ?
            "Restored older saved results. Choose Analyze current source to update them. Timeline actions stay disabled for these older results." :
            count ? "Restored " + count + " saved " + (count === 1 ? "clip" : "clips") + ". No new analysis was started." :
            isAnalyzing() ? "Analysis is still running. Completed clips will appear here." :
            "No saved clips for this source version. Choose a source and Analyze, or select its saved results in the source picker.");
        } finally { state.busy.selection = false; controls(); startPoll(); }
      });
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
      on("sync-metadata", "click", syncMetadata);
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
      el("server").value = state.server;
      if (dependencies.platform === "darwin") {
        el("connection-hint").textContent = "Mac uses local HTTPS on port 8443. Complete certificate setup and start Video Analyzer. This panel connects automatically.";
      }
      try {
        if (dependencies.persistPreferences === false) throw new Error("Session preferences only");
        const folder = await uxp.storage.localFileSystem.getDataFolder();
        const file = await folder.getEntry(PREFS_FILE);
        const preferences = JSON.parse(await file.read());
        if (preferences && typeof preferences === "object") {
          state.preferences = { server: normalizeServer(preferences.server || defaultServer), mode: preferences.mode === "flash_pro" ? "flash_pro" : "flash_only", projects: preferences.projects && typeof preferences.projects === "object" ? preferences.projects : {} };
          state.server = state.preferences.server;
          el("server").value = state.server;
        }
      } catch (error) { /* Missing or obsolete preferences should use defaults. */ }
      el("mode").selectedIndex = state.preferences.mode === "flash_pro" ? 1 : 0;
      // Keep injected/non-Spectrum adapters consistent with the native picker.
      el("mode").value = state.preferences.mode;
      // Old pairing credentials are never read. Do not touch the native
      // credential vault during initialization; this connection uses discovery.
      state.initialized = true;
      controls();
      await run(connect);
    })();
    return initialization;
  }

  function destroy() {
    autoConnect = false;
    stopConnectionRetry();
    ++state.scope;
    state.connected = false;
    stopPoll();
    if (state.searchTimer !== null) unschedule(state.searchTimer);
    state.searchTimer = null;
    state.token = "";
  }
  return { init, connect, disconnect, loadSelection, refreshProject, selectAsset, analyze, setTab, search, addMarkers, syncMetadata, exportSubtitles, destroy, getState: () => state };
}

if (typeof module !== "undefined") module.exports = { createPanel, normalizeServer, parseSeconds, validRange, transcriptCues, cueTime, searchTerms, includesQuery, appendHighlight, sourceBinding, cleanSubtitleName, progressText };

if (typeof document !== "undefined") {
  try {
  const uxp = require("uxp");
  const host = require("./host.js");
  let platform;
  try { platform = require("os").platform(); }
  catch (error) { /* Discovery will report unavailable if host OS detection fails. */ }
  // Premiere can restore a docked panel while its project is still opening.
  // Calling ProjectUtils.getSelection at that point can block the native UI.
  // Connect to the companion now; read native clips only on an explicit action.
  const panel = createPanel({ document, uxp, host, fetch, platform, deferHostUntilUser: true, persistPreferences: false });
  function startupError(error) { document.getElementById("message").textContent = "Panel startup failed: " + error.message; }
  // UXP can create/show a restored dock while Premiere is still opening its
  // project. Even filesystem discovery during that phase can stall the host.
  // A deliberate Connect click starts discovery after the workspace is ready.
  document.getElementById("message").textContent = "Open your project, then click Connect. No pairing code is needed.";
  document.getElementById("connection-hint").textContent = "Start Video Analyzer, then connect to its local service. No pairing code is needed.";
  document.getElementById("connect").addEventListener("click", () => {
    setTimeout(() => panel.init().catch(startupError), 0);
  });
  uxp.entrypoints.setup({
    plugin: { create() {}, destroy() { panel.destroy(); } },
    panels: {
      videoAnalyzerPanel: {
        create() {},
        show() {},
      },
    },
  });
  } catch (error) {
    document.getElementById("message").textContent = "Panel startup failed: " + error.message;
  }
}
