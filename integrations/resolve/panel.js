"use strict";
(function () {
  const bridge = window.videoAnalyzer, helpers = window.ResolvePanelHelpers;
  const el = id => document.getElementById(id);
  let state = { connected: false, assets: [], results: [], queries: { shots: "", transcript: "" }, tab: "shots", mode: "flash_only", revision: 0 };
  let busy = false, shown = 100, pollTimer = null, searchTimer = null, requestNumber = 0, appliedNumber = 0, disposed = false;
  function message(value, error = false) { el("message").textContent = value; el("message").classList.toggle("error", error); }
  function controls() {
    el("workspace").hidden = !state.connected;
    el("retry").hidden = state.connected; el("retry").disabled = busy;
    el("connection-status").textContent = state.connected ? "Connected" : "Waiting for app";
    for (const id of ["load-selection", "asset-toggle", "mode", "shots-tab", "transcript-tab", "search"]) el(id).disabled = busy;
    const analyzing = state.asset && helpers.ACTIVE_STATUSES.includes(state.asset.status);
    el("analyze").disabled = busy || !state.asset || analyzing; el("mode").disabled = busy || analyzing;
    el("analyze").textContent = analyzing ? "Analyzing…" : state.asset && state.asset.shot_count ? "Analyze again" : "Analyze";
    el("add-markers").disabled = busy || !state.asset || !state.results.length;
    const markerCount = Math.min(shown, state.results.length);
    el("add-markers").textContent = "Add " + markerCount + " source marker" + (markerCount === 1 ? "" : "s");
    el("export-subtitles").hidden = state.tab !== "transcript";
    el("export-subtitles").disabled = busy || !state.asset || !helpers.cues(state.asset, [], "transcript", "").length;
    el("open-analyzer").disabled = busy;
    for (const button of document.querySelectorAll(".result button,.asset-option")) button.disabled = busy;
  }
  function renderPicker() {
    el("project-name").textContent = state.project ? state.project.name : "";
    const title = asset => asset.metadata && asset.metadata.title || asset.filename || "Untitled clip";
    el("asset-toggle").textContent = state.asset ? title(state.asset) : "Choose a clip";
    const list = el("asset-list"); list.textContent = "";
    const query = el("asset-search").value;
    const assets = state.assets.filter(asset => helpers.includesQuery(title(asset), query));
    for (const asset of assets) {
      const button = document.createElement("button"); button.type = "button"; button.className = "asset-option" + (state.asset && state.asset.job_id === asset.job_id ? " selected" : ""); button.setAttribute("role", "listitem");
      const name = document.createElement("span"); helpers.appendHighlight(document, name, title(asset), query); button.appendChild(name);
      const status = document.createElement("span"); status.className = "asset-status"; status.textContent = (asset.shot_count || 0) + " shots · " + (asset.status || "imported"); button.appendChild(status);
      button.addEventListener("click", () => action("selectAsset", { id: asset.job_id }).then(() => { el("asset-picker").hidden = true; el("asset-toggle").setAttribute("aria-expanded", "false"); })); list.appendChild(button);
    }
    if (!assets.length) { const empty = document.createElement("p"); empty.className = "hint"; empty.textContent = query ? "No matching clips." : "Select original footage in Resolve's Media Pool, then load it here."; list.appendChild(empty); }
  }
  function renderResults() {
    const list = el("results"); list.textContent = "";
    const query = state.queries[state.tab], results = state.results.slice(0, shown), revision = state.revision;
    el("result-count").textContent = results.length + (state.tab === "shots" && state.total > results.length ? " of " + state.total : state.results.length > results.length ? " of " + state.results.length : "") + (state.tab === "shots" ? " shots" : results.length === 1 ? " line" : " lines");
    el("shots-tab").setAttribute("aria-selected", String(state.tab === "shots")); el("transcript-tab").setAttribute("aria-selected", String(state.tab === "transcript"));
    list.setAttribute("aria-labelledby", state.tab + "-tab");
    el("search").setAttribute("placeholder", state.tab === "shots" ? "Search shots, tags, composition…" : "Search dialogue…"); el("search").setAttribute("aria-label", state.tab === "shots" ? "Search shots" : "Search dialogue");
    for (let index = 0; index < results.length; index++) {
      const result = results[index], article = document.createElement("article"); article.className = "result";
      const time = document.createElement("span"); time.className = "result-time"; time.textContent = helpers.cueTime(result.start) + " – " + helpers.cueTime(result.end); article.appendChild(time);
      const title = document.createElement("h2"); helpers.appendHighlight(document, title, result.speaker || result.name, query); article.appendChild(title);
      const description = document.createElement("p"); helpers.appendHighlight(document, description, result.text, query); article.appendChild(description);
      for (const text of [result.detail, query && result.context]) if (text) { const detail = document.createElement("p"); detail.className = "result-detail"; helpers.appendHighlight(document, detail, text, query); article.appendChild(detail); }
      const tools = document.createElement("div"); tools.className = "row";
      for (const [label, name] of [["Select clip in Resolve", "selectClip"], ["Copy times", "copyTimes"]]) { const button = document.createElement("button"); button.type = "button"; button.textContent = label; button.addEventListener("click", () => action(name, { revision, index })); tools.appendChild(button); }
      article.appendChild(tools); list.appendChild(article);
    }
    if (!results.length) { const empty = document.createElement("p"); empty.className = "hint"; empty.textContent = !state.asset ? "Load a clip to begin." : query ? "No matches. Try another search." : state.tab === "transcript" ? "No timed dialogue yet. Analyze footage with audio to populate this tab." : helpers.ACTIVE_STATUSES.includes(state.asset.status) ? "Shots appear as sections finish." : "Analyze this clip to discover shots."; list.appendChild(empty); }
    el("show-more").hidden = shown >= state.results.length;
  }
  function render(previous) {
    const projectChanged = !previous || (previous.project && previous.project.id) !== (state.project && state.project.id);
    if (projectChanged || previous.asset && state.asset && previous.asset.job_id !== state.asset.job_id || previous.tab !== state.tab) shown = 100;
    if (projectChanged || previous.tab !== state.tab) el("search").value = state.queries[state.tab];
    el("mode").value = state.mode; el("progress").textContent = helpers.progressText(state.asset);
    renderPicker(); renderResults(); controls();
  }
  function stopPoll() { if (pollTimer !== null) clearTimeout(pollTimer); pollTimer = null; }
  function schedulePoll() { stopPoll(); if (!disposed) pollTimer = setTimeout(poll, state.connected && state.asset && helpers.ACTIVE_STATUSES.includes(state.asset.status) ? 2500 : 5000); }
  async function call(name, payload) {
    const number = ++requestNumber;
    const reply = await bridge[name](payload);
    if (reply && reply.state && !disposed && number >= appliedNumber) { appliedNumber = number; const previous = state; state = reply.state; render(previous); }
    if (!reply || !reply.ok) throw new Error(reply && reply.error && reply.error.message || "The plugin action failed.");
    return reply.state;
  }
  async function action(name, payload) {
    if (busy || disposed) return;
    busy = true; stopPoll(); controls();
    try { const result = await call(name, payload); if (result.message) message(result.message); return result; }
    catch (error) { message(error.message, true); }
    finally { busy = false; controls(); schedulePoll(); }
  }
  async function poll() {
    pollTimer = null; if (busy || disposed) return schedulePoll();
    try { const previousConnected = state.connected; const result = await call("refresh"); if (!result.connected || !previousConnected) message(result.message); }
    catch (error) { message(error.message, true); }
    finally { schedulePoll(); }
  }
  function on(id, event, callback) { el(id).addEventListener(event, callback); }
  on("retry", "click", () => action("connect"));
  on("load-selection", "click", () => action("loadSelection"));
  on("asset-toggle", "click", () => { el("asset-picker").hidden = !el("asset-picker").hidden; el("asset-toggle").setAttribute("aria-expanded", String(!el("asset-picker").hidden)); if (!el("asset-picker").hidden) el("asset-search").focus(); });
  on("asset-search", "input", renderPicker);
  on("analyze", "click", () => action("analyze", { revision: state.revision }));
  on("mode", "change", () => action("setView", { mode: el("mode").value }));
  on("shots-tab", "click", async () => { await action("setView", { tab: "shots" }); el("search").value = state.queries.shots; });
  on("transcript-tab", "click", async () => { await action("setView", { tab: "transcript" }); el("search").value = state.queries.transcript; });
  on("search", "input", () => { if (searchTimer !== null) clearTimeout(searchTimer); searchTimer = setTimeout(() => { searchTimer = null; shown = 100; action("setView", { query: el("search").value }); }, 250); });
  on("add-markers", "click", () => action("addMarkers", { revision: state.revision, count: Math.min(shown, state.results.length) }));
  on("export-subtitles", "click", () => action("exportSubtitles", { revision: state.revision }));
  on("open-analyzer", "click", () => action("openAnalyzer"));
  on("show-more", "click", () => { shown = Math.min(500, shown + 100); renderResults(); controls(); });
  window.addEventListener("beforeunload", () => { disposed = true; stopPoll(); if (searchTimer !== null) clearTimeout(searchTimer); });
  if (!bridge) { message("Open this integration from Resolve Studio's Workspace menu.", true); controls(); return; }
  call("bootstrap").then(result => { el("search").value = state.queries[state.tab]; message(result.message); }).catch(error => message(error.message, true)).finally(schedulePoll);
})();
