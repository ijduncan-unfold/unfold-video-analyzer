"use strict";

// Native Resolve proxies stay in Electron's main process. Every call uses the
// Promise SDK from Resolve Studio 20.1+, including properties and list getters.
const MAX_ITEMS = 20000;
const MAX_MARKERS = 500;
const MARKER_NAMESPACE = "video-analyzer:resolve:v1:";
const NO_MARKER_RESPONSE = "GetMarkerCustomData: Error response (errorStr:No marker exists at the input frameId argument) (errorCode:2)";
const SHOT_BIN = "Video Analyzer shots";
const SHOT_NAMESPACE = "video-analyzer:shot:v1:";
const RECORD_START = "[Video Analyzer shot]\n", RECORD_END = "\n[/Video Analyzer shot]";
class HostError extends Error {
  constructor(code, message) { super(message); this.name = "HostError"; this.code = code; }
}
function fail(code, message) { throw new HostError(code, message); }
function method(owner, name) {
  if (!owner || typeof owner[name] !== "function") fail("UNSUPPORTED_API", "Resolve Studio 20.1 or later is required. This build does not expose " + name + ".");
}
function identity(value, label) {
  if (typeof value !== "string" || !value.trim() || value.length > 500) fail("INVALID_IDENTITY", "Resolve did not return a stable " + label + " identifier.");
  return value;
}
function list(value, label) {
  if (Array.isArray(value)) return value;
  if (value && typeof value === "object") return Object.values(value);
  fail("INVALID_LIST", "Resolve could not read " + label + ".");
}
function number(value) {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string" || !/^\d+(?:\.\d+)?$/.test(value.trim())) return null;
  return Number(value.trim());
}
function pathKey(value) {
  if (typeof value !== "string" || !value.trim() || value.includes("\0") || value.length > 4096) return "";
  return /^[a-z]:[\\/]/i.test(value) || value.startsWith("\\\\") ? value.replace(/\\/g, "/").toLowerCase() : value;
}
function absolutePath(value) { return !!pathKey(value) && /^(?:[a-z]:[\\/]|\\\\|\/)/i.test(value); }
function seconds(value, label) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 864000) fail("INVALID_CUE", label + " must be a finite source-media time.");
  return value;
}
function text(value, maximum, label) {
  if (typeof value !== "string" || value.length > maximum) fail("INVALID_CUE", label + " is invalid.");
  return value;
}
function timebase(descriptor, technical) {
  const fps = number(technical && technical.nominal_frame_rate_fraction && (() => {
    const pieces = technical.nominal_frame_rate_fraction.split("/").map(Number);
    return pieces.length === 2 && pieces[1] > 0 ? pieces[0] / pieces[1] : null;
  })());
  const frames = number(technical && technical.frame_rate_verified_frames);
  if (!technical || technical.nominal_frame_rate_verified !== true || !fps || fps > 1000 || !Number.isInteger(frames) || frames < 2 ||
      !descriptor.source_fps || !Number.isInteger(descriptor.source_frames) || descriptor.source_start !== 0) {
    fail("UNVERIFIED_TIMEBASE", "Resolve exports require a verified constant frame rate and complete original clip. Analyze the original media; variable-rate or indeterminate sources cannot be exported yet.");
  }
  if (Math.abs(fps - descriptor.source_fps) > 0.001 || frames !== descriptor.source_frames) {
    fail("SOURCE_TIMEBASE_CHANGED", "Resolve's clip frame rate or frame count differs from the analyzed original. Select the full original clip and restore its original frame rate before exporting.");
  }
  return { fps, frames };
}
function prepareCues(cues, timing) {
  if (!Array.isArray(cues) || cues.length > MAX_MARKERS) fail("MARKER_LIMIT", "Choose at most " + MAX_MARKERS + " markers at a time.");
  return cues.map((cue, index) => {
    if (!cue || typeof cue !== "object" || Array.isArray(cue)) fail("INVALID_CUE", "Marker " + (index + 1) + " is invalid.");
    const start = seconds(cue.start, "Marker start");
    const end = seconds(cue.end, "Marker end");
    if (end <= start || end > timing.frames / timing.fps + 0.001) fail("INVALID_CUE", "Marker timing falls outside the original clip.");
    const frame = Math.round(start * timing.fps);
    const duration = Math.max(1, Math.round(end * timing.fps) - frame);
    if (frame < 0 || frame >= timing.frames || frame + duration > timing.frames) fail("INVALID_CUE", "Marker frame range falls outside the original clip.");
    const name = text(cue.name || "Video Analyzer " + (index + 1), 250, "Marker name");
    const note = text(cue.text || "", 20000, "Marker text");
    // Identifies our own writes for duplicate checks and careful rollback only.
    const key = MARKER_NAMESPACE + require("node:crypto").createHash("sha256").update(JSON.stringify([frame, duration, name, note])).digest("hex");
    return { frame, duration, name, note, key };
  });
}
function markerMap(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail("MARKER_READ_FAILED", "Resolve could not read source markers.");
  const result = new Map();
  // Resolve's native Promise SDK can return shifted dictionary keys, including
  // -1 for a marker at source frame zero. These keys identify records only;
  // GetMarkerCustomData at the requested frame determines actual occupancy.
  for (const [key, marker] of Object.entries(value)) {
    const frame = /^-?(?:\d+(?:\.\d*)?|\.\d+)$/.test(key) ? Number(key) : NaN;
    if (!Number.isFinite(frame) || result.has(frame) || !marker || typeof marker !== "object" || Array.isArray(marker)) {
      fail("MARKER_READ_FAILED", "Resolve returned an ambiguous source marker dictionary. Existing markers were preserved.");
    }
    result.set(frame, marker);
  }
  return result;
}
function shotRecord(comments) {
  if (typeof comments !== "string") return null;
  const start = comments.indexOf(RECORD_START), end = comments.indexOf(RECORD_END, start + RECORD_START.length);
  if (start < 0 || end < 0) return null;
  try { const record = JSON.parse(comments.slice(start + RECORD_START.length, end)); return record && typeof record.key === "string" && record.key.startsWith(SHOT_NAMESPACE) ? { record, start, end: end + RECORD_END.length } : null; }
  catch (_) { return null; }
}
function keywordValues(value) {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(keywordValues);
  if (value && typeof value === "object") return Object.values(value).flatMap(keywordValues);
  return [];
}
function prepareShots(binding, pack, timing) {
  if (!pack || typeof pack.asset_id !== "string" || !Array.isArray(pack.shots) || !pack.shots.length || pack.shots.length > 50) fail("SHOT_LIMIT", "Choose between 1 and 50 analyzed shots.");
  const seen = new Set();
  return pack.shots.map(shot => {
    if (!shot || !Number.isInteger(shot.shot_number) || shot.shot_number < 1 || seen.has(shot.shot_number)) fail("INVALID_SHOT", "Choose distinct analyzed shots.");
    seen.add(shot.shot_number);
    const start = seconds(shot.start_seconds, "Shot start"), end = seconds(shot.end_seconds, "Shot end");
    if (end <= start || end > timing.frames / timing.fps + 0.001) fail("INVALID_SHOT", "Shot timing falls outside the original clip.");
    // Gemini intervals are [start, end); Resolve's subclip end is inclusive.
    const startFrame = Math.round(start * timing.fps), endFrame = Math.round(end * timing.fps) - 1;
    if (startFrame < 0 || endFrame < startFrame || endFrame >= timing.frames) fail("INVALID_SHOT", "This shot has no valid source frame range.");
    const key = SHOT_NAMESPACE + require("node:crypto").createHash("sha256").update(JSON.stringify([pack.asset_id, binding.item_id, pathKey(binding.media_path), startFrame, endFrame])).digest("hex");
    const name = (String(pack.filename || "Source").replace(/\.[^.]+$/, "") + " · Shot " + String(shot.shot_number).padStart(2, "0")).slice(0, 200);
    const description = typeof shot.visual_description === "string" ? shot.visual_description : "";
    const keywordFields = [shot.tags, shot.human_tags, shot.shot_type, shot.camera_movement, shot.mood, shot.subjects, shot.actions, shot.dominant_colors, shot.location, shot.project_metadata && shot.project_metadata.tags];
    for (const slate of shot.slates || []) {
      for (const field of ['production_title', 'scene', 'take', 'slate_shot', 'camera_roll', 'camera', 'director', 'cinematographer', 'date', 'sound_roll']) {
        if (typeof slate[field] === 'string' && slate[field].trim()) keywordFields.push(field.replaceAll('_', ' ') + ': ' + slate[field].trim());
      }
    }
    const keywords = [...new Set(keywordFields.flatMap(keywordValues).map(v => v.trim()).filter(Boolean))];
    // shot-package contains public shot facts plus explicitly scoped section,
    // asset and dialogue context, never provider credentials or raw API usage.
    const record = { key, asset_id: pack.asset_id, analysis_session: pack.analysis_session, source_file: pack.filename,
      source_range: { start_seconds: start, end_seconds: end, start_frame: startFrame, end_frame_inclusive: endFrame, fps: timing.fps }, description, shot };
    const json = JSON.stringify(record);
    if (json.length > 200000) fail("METADATA_LIMIT", "This shot's metadata exceeds Resolve's preview export limit. Export its JSON from the full analyzer instead.");
    return { key, name, description, keywords, record, json, startFrame, endFrame };
  }).sort((a, b) => a.startFrame - b.startFrame || a.endFrame - b.endFrame);
}
function createHost(resolve) {
  async function readMarkers(item) { return markerMap(await item.GetMarkers()); }
  async function markerAtFrame(item, frame) {
    let customData;
    try { customData = await item.GetMarkerCustomData(frame); }
    catch (error) {
      // An existing user marker may have empty customData. Only this precise
      // native no-marker response establishes absence; all other errors escape.
      if ((typeof error === "string" ? error : error && error.message) === NO_MARKER_RESPONSE) return { exists: false };
      throw error;
    }
    if (typeof customData !== "string") fail("MARKER_READ_FAILED", "Resolve could not confirm whether this source frame already has a marker. Existing markers were preserved.");
    return { exists: true, customData };
  }
  function ownedRecords(markers, cue) { return Array.from(markers.values()).filter(marker => marker.customData === cue.key); }
  function unchanged(marker, cue) { return marker.customData === cue.key && marker.color === "Blue" && marker.name === cue.name && marker.note === cue.note && number(marker.duration) === cue.duration; }
  async function verifyCue(item, markers, cue) {
    const records = ownedRecords(markers, cue), position = await markerAtFrame(item, cue.frame);
    if (records.length !== 1 || !unchanged(records[0], cue) || !position.exists || position.customData !== cue.key) fail("MARKER_VERIFY_FAILED", "Resolve could not confirm every marker at its requested source frame.");
  }
  async function active(expected) {
    method(resolve, "GetProjectManager");
    const manager = await resolve.GetProjectManager();
    method(manager, "GetCurrentProject");
    const project = await manager.GetCurrentProject();
    if (!project) fail("NO_PROJECT", "Open a Resolve project and select original clips in the Media Pool.");
    method(project, "GetUniqueId");
    const id = identity(await project.GetUniqueId(), "project");
    if (expected !== undefined && expected !== id) fail("PROJECT_CHANGED", "The active Resolve project changed. Load its selection before using these results.");
    return { project, id };
  }
  async function projectIdentity(expected) {
    const { project, id } = await active(expected);
    method(project, "GetName");
    return { id, name: String(await project.GetName() || "Resolve project") };
  }
  async function pool(project) { method(project, "GetMediaPool"); const result = await project.GetMediaPool(); if (!result) fail("NO_MEDIA_POOL", "Open the Media Pool in Resolve."); return result; }
  async function describe(item) {
    method(item, "GetUniqueId"); method(item, "GetClipProperty");
    const id = identity(await item.GetUniqueId(), "clip");
    const properties = await item.GetClipProperty();
    if (!properties || typeof properties !== "object") fail("INVALID_CLIP", "Resolve could not read this clip's properties.");
    const name = String(properties["Clip Name"] || properties["File Name"] || "Source clip").slice(0, 250);
    const path = properties["File Path"];
    if (!absolutePath(path)) return { warning: name + ": select accessible original video media, rather than timelines, generators, or compound clips." };
    const fps = number(properties.FPS);
    const frames = number(properties.Frames);
    const proxy = properties["Proxy Media Path"];
    return { item, descriptor: { item_id: id, name, media_path: path, source_fps: fps && fps <= 1000 ? fps : null, source_frames: Number.isInteger(frames) && frames > 0 ? frames : null, source_start: number(properties.Start),
      ...(absolutePath(proxy) ? { viewing_path: proxy, proxy_alignment_confirmed: true } : {}) } };
  }
  async function getSelection() {
    const { project, id } = await active();
    const media = await pool(project);
    method(media, "GetSelectedClips");
    const selected = list(await media.GetSelectedClips(), "the Media Pool selection");
    if (selected.length > 500) fail("SELECTION_LIMIT", "Select at most 500 original clips at a time.");
    const clips = [], warnings = [];
    for (const item of selected) { const result = await describe(item); if (result.warning) warnings.push(result.warning); else clips.push(result.descriptor); }
    await active(id);
    method(project, "GetName");
    return { project_id: id, project_name: String(await project.GetName() || "Resolve project"), clips, warnings,
      capabilities: { source_select: typeof media.SetSelectedClip === "function", source_seek: false, source_markers: true, max_marker_batch: MAX_MARKERS } };
  }
  async function locate(media, id) {
    method(media, "GetRootFolder"); const root = await media.GetRootFolder();
    const queue = [{ folder: root, depth: 0 }], visited = new Set();
    let count = 0;
    for (let position = 0; position < queue.length; position++) {
      const { folder, depth } = queue[position];
      if (!folder || visited.has(folder)) continue;
      visited.add(folder);
      if (++count > MAX_ITEMS || depth > 64) fail("PROJECT_LIMIT", "This Media Pool is too large for the preview integration.");
      method(folder, "GetClipList"); method(folder, "GetSubFolderList");
      for (const item of list(await folder.GetClipList(), "Media Pool clips")) {
        if (++count > MAX_ITEMS) fail("PROJECT_LIMIT", "This Media Pool is too large for the preview integration.");
        method(item, "GetUniqueId");
        if (await item.GetUniqueId() === id) return describe(item);
      }
      for (const child of list(await folder.GetSubFolderList(), "Media Pool bins")) queue.push({ folder: child, depth: depth + 1 });
    }
    fail("SOURCE_UNAVAILABLE", "This clip is no longer in the Resolve project. Load selected clips again.");
  }
  async function resolveBinding(binding) {
    if (!binding || binding.host !== "resolve" || !pathKey(binding.media_path)) fail("INVALID_BINDING", "Load selected Resolve clips before using native results.");
    identity(binding.project_id, "project"); identity(binding.item_id, "clip");
    const { project } = await active(binding.project_id);
    const media = await pool(project);
    const result = await locate(media, binding.item_id);
    if (!result.descriptor) fail("SOURCE_UNAVAILABLE", result.warning);
    if (pathKey(result.descriptor.media_path) !== pathKey(binding.media_path)) fail("MEDIA_CHANGED", "This Resolve clip was relinked. Load and analyze its new original media.");
    const sourceRate = number(binding.source_fps);
    if (!sourceRate || !result.descriptor.source_fps || Math.abs(sourceRate - result.descriptor.source_fps) > 0.001) fail("SOURCE_TIMEBASE_CHANGED", "Resolve's clip frame rate differs from the analyzed original, or could not be verified. Load the full original clip at its original frame rate.");
    await active(binding.project_id);
    return { project, media, item: result.item, descriptor: result.descriptor };
  }
  async function selectClip(binding) {
    const { media, item } = await resolveBinding(binding);
    method(media, "SetSelectedClip");
    if (await media.SetSelectedClip(item) !== true) fail("SELECT_FAILED", "Resolve could not select this clip in the Media Pool.");
    method(media, "GetSelectedClips");
    let confirmed = false;
    for (const selected of list(await media.GetSelectedClips(), "the Media Pool selection")) {
      method(selected, "GetUniqueId");
      if (await selected.GetUniqueId() === binding.item_id) confirmed = true;
    }
    if (!confirmed) fail("SELECT_VERIFY_FAILED", "Resolve did not confirm the requested Media Pool selection.");
    await active(binding.project_id);
    return { selected: true, source_seek: false };
  }
  async function exportShots(binding, pack, destination = "pool") {
    if (!["pool", "current", "new"].includes(destination)) fail("INVALID_DESTINATION", "Choose Media Pool, current timeline, or a new selects timeline.");
    const { project, media, item: original, descriptor } = await resolveBinding(binding);
    const timing = timebase(descriptor, pack && pack.technical), plans = prepareShots(binding, pack, timing);
    method(resolve, "GetMediaStorage"); method(media, "GetCurrentFolder"); method(media, "SetCurrentFolder"); method(media, "AddSubFolder");
    const storage = await resolve.GetMediaStorage(); method(storage, "AddItemListToMediaPool");
    let target = null, targetId = null;
    if (destination === "current") {
      method(project, "GetCurrentTimeline"); method(media, "AppendToTimeline");
      target = await project.GetCurrentTimeline();
      if (!target) fail("NO_TIMELINE", "Open a timeline in Resolve, or choose New selects timeline.");
      method(target, "GetUniqueId"); targetId = identity(await target.GetUniqueId(), "timeline");
    } else if (destination === "new") { method(media, "CreateTimelineFromClips"); }
    async function checkTarget() {
      const current = await active(binding.project_id), source = await describe(original);
      if (!source.descriptor || source.descriptor.item_id !== binding.item_id || pathKey(source.descriptor.media_path) !== pathKey(binding.media_path)) fail("MEDIA_CHANGED", "The original clip changed during export. Refresh the Resolve selection.");
      timebase(source.descriptor, pack.technical);
      if (targetId) {
        const timeline = await current.project.GetCurrentTimeline();
        if (!timeline || await timeline.GetUniqueId() !== targetId) fail("TIMELINE_CHANGED", "The active timeline changed. Saved shots remain in the Media Pool; select the intended timeline before adding them.");
      }
    }
    await checkTarget();
    const priorFolder = await media.GetCurrentFolder(), root = await media.GetRootFolder();
    method(root, "GetSubFolderList");
    let bin = null;
    for (const folder of list(await root.GetSubFolderList(), "Media Pool bins")) {
      method(folder, "GetName"); if (await folder.GetName() === SHOT_BIN) { bin = folder; break; }
    }
    if (!bin) { await checkTarget(); bin = await media.AddSubFolder(root, SHOT_BIN); }
    if (!bin) fail("BIN_CREATE_FAILED", "Resolve could not create the Video Analyzer shots bin.");
    method(bin, "GetClipList");
    const items = [], summary = { created: 0, reused: 0, added: 0, destination, metadata: true };
    let changedFolder = false, timelineWriteAttempted = false;
    try {
      if (await media.SetCurrentFolder(bin) !== true) fail("BIN_SELECT_FAILED", "Resolve could not select the shots bin.");
      changedFolder = true;
      const existing = new Map();
      for (const candidate of list(await bin.GetClipList(), "saved shots")) {
        method(candidate, "GetMetadata");
        const metadata = await candidate.GetMetadata(), receipt = shotRecord(metadata && metadata.Comments);
        if (receipt) {
          if (existing.has(receipt.record.key)) fail("AMBIGUOUS_SHOT", "Multiple saved shots have the same Video Analyzer identity. Resolve was left unchanged; inspect the shots bin before retrying.");
          existing.set(receipt.record.key, candidate);
        }
      }
      for (const plan of plans) {
        await checkTarget();
        let item = existing.get(plan.key), created = false;
        if (!item) {
          // This documented MediaStorage overload creates a real subclip that
          // references the original file; it never renders or copies media.
          const imported = list(await storage.AddItemListToMediaPool([{ media: binding.media_path, startFrame: plan.startFrame, endFrame: plan.endFrame }]), "imported shot");
          if (imported.length !== 1) fail("SUBCLIP_CREATE_FAILED", "Resolve did not return one trimmed shot. Inspect the shots bin before retrying.");
          item = imported[0]; created = true;
        }
        method(item, "GetUniqueId"); method(item, "GetClipProperty"); method(item, "GetMetadata"); method(item, "SetMetadata");
        const id = identity(await item.GetUniqueId(), "shot"), properties = await item.GetClipProperty();
        if (id === binding.item_id || !properties || pathKey(properties["File Path"]) !== pathKey(binding.media_path) ||
            number(properties.Start) !== plan.startFrame || number(properties.End) !== plan.endFrame ||
            number(properties.Frames) !== plan.endFrame - plan.startFrame + 1 || !number(properties.FPS) || Math.abs(number(properties.FPS) - timing.fps) > 0.001) {
          fail("SUBCLIP_VERIFY_FAILED", "Resolve did not confirm a separate subclip with the requested source range. No original metadata was changed. Inspect the shots bin before retrying.");
        }
        await checkTarget();
        if (created) summary.created++; else summary.reused++;
        const before = await item.GetMetadata();
        if (!before || typeof before !== "object") fail("METADATA_READ_FAILED", "Resolve could not read this shot's metadata.");
        const receipt = shotRecord(before.Comments);
        if (!created && (!receipt || receipt.record.key !== plan.key)) fail("SHOT_CHANGED", "This saved shot's identity changed. Inspect the Media Pool before retrying.");
        let humanComments = receipt && receipt.record.key === plan.key ? (String(before.Comments || "").slice(0, receipt.start) + String(before.Comments || "").slice(receipt.end)).trim() : String(before.Comments || "").trim();
        if (created && before.Description && before.Description !== plan.description) humanComments += (humanComments ? "\n\n" : "") + "Source description: " + before.Description;
        const keywords = [...new Set(String(before.Keywords || "").split(",").concat(plan.keywords).map(v => v.trim()).filter(Boolean))].join(", ");
        const metadata = { Keywords: keywords, Comments: (humanComments ? humanComments + "\n\n" : "") + RECORD_START + plan.json + RECORD_END,
          Description: !created && receipt && before.Description !== receipt.record.description ? String(before.Description || "") : plan.description };
        if (created) {
          if (typeof item.SetName === "function") { if (await item.SetName(plan.name) !== true) fail("SHOT_NAME_FAILED", "Resolve could not name the saved shot."); }
          else { method(item, "SetClipProperty"); if (await item.SetClipProperty("Clip Name", plan.name) !== true) fail("SHOT_NAME_FAILED", "Resolve could not name the saved shot."); }
        }
        if (await item.SetMetadata(metadata) !== true) fail("METADATA_WRITE_FAILED", "Resolve could not save searchable shot metadata. The shot remains in the Media Pool; nothing was added to the timeline.");
        const after = await item.GetMetadata();
        if (!after || Object.entries(metadata).some(([key, value]) => after[key] !== value)) fail("METADATA_VERIFY_FAILED", "Resolve did not confirm the shot metadata. Inspect the saved shot before retrying.");
        // Standard fields provide native text search. This optional newer SDK
        // field also retains a structured copy for third-party metadata tools.
        if (typeof item.SetThirdPartyMetadata === "function") {
          if (await item.SetThirdPartyMetadata({ "VideoAnalyzer.Shot": plan.json }) !== true) fail("METADATA_WRITE_FAILED", "Searchable metadata was saved, but Resolve rejected the structured copy. Nothing was added to the timeline.");
        }
        items.push(item); existing.set(plan.key, item);
      }
      await checkTarget();
      // Recheck Media Pool membership once at the end, rather than scanning a
      // large project for every individual metadata field written above.
      await resolveBinding(binding);
      if (destination === "current") {
        let appended;
        timelineWriteAttempted = true;
        try { appended = await media.AppendToTimeline(items); }
        catch (_) { fail("TIMELINE_WRITE_UNCERTAIN", "Resolve did not acknowledge the timeline edit. Inspect the timeline before retrying; saved shots and metadata remain in the Media Pool."); }
        const returned = list(appended, "appended timeline shots");
        const expected = new Set(); for (const item of items) expected.add(await item.GetUniqueId());
        const confirmed = new Set();
        for (const timelineItem of returned) {
          method(timelineItem, "GetMediaPoolItem"); const source = await timelineItem.GetMediaPoolItem();
          if (source) { const id = await source.GetUniqueId(); if (expected.has(id)) confirmed.add(id); }
        }
        if (confirmed.size !== expected.size) fail("TIMELINE_WRITE_UNCERTAIN", "Resolve did not confirm every appended shot. Inspect the timeline before retrying; saved shots remain in the Media Pool.");
        await checkTarget(); summary.added = items.length;
      } else if (destination === "new") {
        const name = (String(pack.filename || "Footage").replace(/\.[^.]+$/, "") + " · Analyzer selects " + new Date().toISOString().replace(/[:.]/g, "-")).slice(0, 200);
        let timeline;
        timelineWriteAttempted = true;
        try { timeline = await media.CreateTimelineFromClips(name, items); }
        catch (_) { fail("TIMELINE_WRITE_UNCERTAIN", "Resolve did not acknowledge the selects timeline. Inspect the Media Pool before retrying; saved shots and metadata remain available."); }
        if (!timeline) fail("TIMELINE_CREATE_FAILED", "Resolve could not create the selects timeline. Saved shots remain in the Media Pool.");
        method(timeline, "GetItemListInTrack");
        const videoItems = list(await timeline.GetItemListInTrack("video", 1), "selects timeline shots");
        const expected = new Set(); for (const item of items) expected.add(await item.GetUniqueId());
        const confirmed = new Set();
        for (const videoItem of videoItems) {
          method(videoItem, "GetMediaPoolItem"); const source = await videoItem.GetMediaPoolItem();
          if (source && expected.has(await source.GetUniqueId())) confirmed.add(await source.GetUniqueId());
        }
        if (videoItems.length !== items.length || confirmed.size !== expected.size) fail("TIMELINE_WRITE_UNCERTAIN", "Resolve did not confirm every shot in the new selects timeline. Inspect it before retrying.");
        await active(binding.project_id); summary.added = items.length;
      }
      return summary;
    } catch (error) {
      // Resolve has no documented transaction for subclips + timeline edits.
      // Never delete user clips or perform a generic Undo on uncertain writes.
      const failure = error instanceof Error ? error : new Error(String(error || "Resolve export failed."));
      failure.message += " " + summary.created + " shots created and " + summary.reused + " reused in the Media Pool during this action.";
      if (timelineWriteAttempted) failure.message += " A timeline edit was attempted; inspect the timeline before repeating this action.";
      throw failure;
    } finally {
      // Restore the previous bin only if the user has not selected another one.
      if (changedFolder && priorFolder) {
        try { await active(binding.project_id); const current = await media.GetCurrentFolder();
          if (current === bin || current && typeof current.GetUniqueId === "function" && typeof bin.GetUniqueId === "function" && await current.GetUniqueId() === await bin.GetUniqueId()) await media.SetCurrentFolder(priorFolder);
        } catch (_) { /* Keep an unavailable/changed project untouched. */ }
      }
    }
  }
  async function addMarkers(binding, technical, cues) {
    const { item, descriptor } = await resolveBinding(binding);
    const prepared = prepareCues(cues, timebase(descriptor, technical));
    method(item, "GetMarkers"); method(item, "GetMarkerCustomData"); method(item, "AddMarker"); method(item, "DeleteMarkerByCustomData");
    await readMarkers(item);
    const added = [], attempted = [];
    let duplicates = 0, collisions = 0;
    try {
      for (const cue of prepared) {
        await active(binding.project_id);
        const currentSource = await describe(item);
        if (!currentSource.descriptor || currentSource.descriptor.item_id !== descriptor.item_id ||
            pathKey(currentSource.descriptor.media_path) !== pathKey(descriptor.media_path)) {
          fail("MEDIA_CHANGED", "This clip was relinked during marker export. Load and analyze its new original media.");
        }
        timebase(currentSource.descriptor, technical);
        // Another editor action might add a marker after our initial read.
        const now = await readMarkers(item);
        const records = ownedRecords(now, cue), position = await markerAtFrame(item, cue.frame);
        if (records.length > 1) fail("MARKER_READ_FAILED", "Resolve returned multiple markers with the same Video Analyzer identity. Existing markers were preserved.");
        if (records.length === 1) {
          if (!position.exists || position.customData !== cue.key) fail("MARKER_VERIFY_FAILED", "An existing Video Analyzer marker was moved or could not be confirmed at its source frame. Existing markers were preserved.");
          duplicates++; continue;
        }
        if (position.exists) { collisions++; continue; }
        await active(binding.project_id);
        attempted.push(cue);
        if (await item.AddMarker(cue.frame, "Blue", cue.name, cue.note, cue.duration, cue.key) !== true) fail("MARKER_WRITE_FAILED", "Resolve rejected a source marker.");
        added.push(cue);
      }
      const verified = await readMarkers(item);
      for (const cue of added) await verifyCue(item, verified, cue);
      await active(binding.project_id);
    } catch (error) {
      let remaining = 0;
      // No generic Undo/DeleteMarkersByColor: only undo confirmed writes bearing
      // our exact customData, preserving pre-existing and concurrently edited data.
      for (const cue of attempted.reverse()) {
        try {
          const records = ownedRecords(await readMarkers(item), cue);
          if (records.length) {
            const position = await markerAtFrame(item, cue.frame);
            if (records.length !== 1 || !unchanged(records[0], cue) || !position.exists || position.customData !== cue.key || await item.DeleteMarkerByCustomData(cue.key) !== true) remaining++;
            else {
              const afterDelete = await readMarkers(item);
              if (ownedRecords(afterDelete, cue).length) remaining++;
            }
          }
        }
        catch (_) { remaining++; }
      }
      if (remaining) fail("MARKER_ROLLBACK_INCOMPLETE", "Marker export stopped and " + remaining + " Video Analyzer marker(s) could not be removed. Check this source clip before retrying.");
      throw error;
    }
    return { added: added.length, duplicates, collisions };
  }
  return { getProjectIdentity: projectIdentity, getSelection, resolveBinding, selectClip, addMarkers, exportShots };
}
module.exports = { createHost, HostError, MAX_MARKERS, MARKER_NAMESPACE, pathKey, timebase, prepareCues, markerMap, prepareShots, shotRecord, SHOT_BIN };
