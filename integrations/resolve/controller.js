(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ResolvePanelHelpers = api;
})(typeof globalThis === "object" ? globalThis : this, function () {
  "use strict";
  const ACTIVE_STATUSES = ["pending", "queued", "uploading", "processing", "analyzing", "running"];
  function parseSeconds(value) {
    if (typeof value === "number") return Number.isFinite(value) && value >= 0 ? value : null;
    if (typeof value !== "string" || !/^\d+(?::\d{1,2}){0,2}(?:[.,]\d+)?$/.test(value.trim())) return null;
    const parts = value.trim().replace(",", ".").split(":").map(Number);
    if (parts.length > 1 && parts[parts.length - 1] >= 60 || parts.length === 3 && parts[1] >= 60) return null;
    return parts.reduce((sum, part) => sum * 60 + part, 0);
  }
  function range(entry, duration) {
    const read = side => { for (const key of [side + "_seconds", side + "_time", side]) if (entry[key] !== undefined) return entry[key]; return undefined; };
    const start = parseSeconds(read("start")), end = parseSeconds(read("end"));
    return start !== null && end !== null && end > start && (!Number.isFinite(duration) || end <= duration) ? { start, end } : null;
  }
  function terms(query) { return String(query || "").trim().split(/\s+/).filter(Boolean).slice(0, 20); }
  function includesQuery(value, query) { const lower = String(value || "").toLowerCase(); return terms(query).every(term => lower.includes(term.toLowerCase())); }
  function cues(asset, shots, tab, query) {
    if (!asset) return [];
    const duration = asset.technical && asset.technical.duration_seconds;
    if (tab === "transcript") return (Array.isArray(asset.transcript) ? asset.transcript : []).map((entry, index) => {
      if (!entry || typeof entry.text !== "string" || !entry.text.trim()) return null;
      const timing = range(entry, duration);
      return timing ? { ...timing, name: "Dialogue " + (index + 1), text: entry.text.trim(), speaker: typeof entry.speaker === "string" ? entry.speaker : "", detail: "" } : null;
    }).filter(cue => cue && includesQuery(cue.text + " " + cue.speaker, query)).sort((a, b) => a.start - b.start || a.end - b.end).slice(0, 500);
    return (Array.isArray(shots) ? shots : []).map((shot, index) => {
      if (!shot || typeof shot !== "object") return null;
      const timing = range(shot, duration);
      const tags = Array.isArray(shot.tags) ? shot.tags.filter(tag => typeof tag === "string") : [];
      return timing ? { ...timing, name: "Shot " + String(shot.shot_number || index + 1).padStart(2, "0"), text: String(shot.visual_description || ""),
        detail: [shot.shot_type, shot.camera_movement, shot.mood, tags.join(", ")].filter(Boolean).join(" · "), context: typeof shot.match_context === "string" ? shot.match_context : "" } : null;
    }).filter(Boolean).slice(0, 500);
  }
  function cueTime(seconds) {
    const ms = Math.round(seconds * 1000), whole = Math.floor(ms / 1000), hours = Math.floor(whole / 3600);
    return (hours ? hours + ":" : "") + String(Math.floor(whole % 3600 / 60)).padStart(2, "0") + ":" + String(whole % 60).padStart(2, "0") + "." + String(ms % 1000).padStart(3, "0");
  }
  function appendHighlight(document, parent, value, query) {
    const source = String(value || ""), search = terms(query).sort((a, b) => b.length - a.length);
    if (!search.length) { parent.textContent = source; return; }
    const pattern = new RegExp(search.map(term => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"), "gi");
    let cursor = 0, match;
    while ((match = pattern.exec(source)) !== null) {
      if (match.index > cursor) parent.appendChild(document.createTextNode(source.slice(cursor, match.index)));
      const marked = document.createElement("mark"); marked.textContent = match[0]; parent.appendChild(marked);
      cursor = match.index + match[0].length;
    }
    if (cursor < source.length) parent.appendChild(document.createTextNode(source.slice(cursor)));
  }
  function progressText(asset) {
    if (!asset) return "Load selected clips, then choose an analysis type.";
    const progress = asset.analysis_progress || {};
    const parts = [];
    if (progress.stage) parts.push(String(progress.stage).replace(/_/g, " "));
    if (progress.completed_sections !== undefined && progress.total_sections) parts.push(progress.completed_sections + "/" + progress.total_sections + " sections");
    if (asset.shot_count) parts.push(asset.shot_count + " shots");
    if (asset.status === "error") return "Analysis stopped. Saved results remain available. " + String(asset.error || "Retry to continue.");
    if (ACTIVE_STATUSES.includes(asset.status)) return parts.join(" · ") || "Analyzing… Results appear as sections finish.";
    return parts.join(" · ") || "Ready to analyze.";
  }
  function subtitleName(asset) {
    let name = String(asset && asset.filename || "video").replace(/\.[^.]+$/, "").replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").replace(/[. ]+$/g, "").slice(0, 150);
    if (!name || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(name)) name = "video";
    return name + "_subtitles.srt";
  }
  return { ACTIVE_STATUSES, parseSeconds, range, terms, includesQuery, cues, cueTime, appendHighlight, progressText, subtitleName };
});
