"use strict";

// Native Resolve proxies stay in Electron's main process. Every call uses the
// Promise SDK from Resolve Studio 20.1+, including properties and list getters.
const MAX_ITEMS = 20000;
const MAX_MARKERS = 500;
const MARKER_NAMESPACE = "video-analyzer:resolve:v1:";
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
    fail("UNVERIFIED_TIMEBASE", "Source markers require a verified constant frame rate and complete original clip. Analyze the original media; variable-rate or indeterminate sources cannot receive markers yet.");
  }
  if (Math.abs(fps - descriptor.source_fps) > 0.001 || frames !== descriptor.source_frames) {
    fail("SOURCE_TIMEBASE_CHANGED", "Resolve's clip frame rate or frame count differs from the analyzed original. Select the full original clip and restore its original frame rate before adding markers.");
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
function createHost(resolve) {
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
    return { item, descriptor: { item_id: id, name, media_path: path, source_fps: fps && fps <= 1000 ? fps : null, source_frames: Number.isInteger(frames) && frames > 0 ? frames : null, source_start: number(properties.Start) } };
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
  async function addMarkers(binding, technical, cues) {
    const { item, descriptor } = await resolveBinding(binding);
    const prepared = prepareCues(cues, timebase(descriptor, technical));
    method(item, "GetMarkers"); method(item, "AddMarker"); method(item, "DeleteMarkerAtFrame");
    const existing = await item.GetMarkers();
    if (!existing || typeof existing !== "object" || Array.isArray(existing)) fail("MARKER_READ_FAILED", "Resolve could not read existing source markers.");
    const occupied = new Set(Object.keys(existing).map(Number)), keys = new Set(Object.values(existing).map(marker => marker && marker.customData));
    const pending = [], added = [], attempted = [];
    let duplicates = 0, collisions = 0;
    for (const cue of prepared) {
      if (keys.has(cue.key)) { duplicates++; continue; }
      if (occupied.has(cue.frame)) { collisions++; continue; }
      occupied.add(cue.frame); keys.add(cue.key); pending.push(cue);
    }
    try {
      for (const cue of pending) {
        await active(binding.project_id);
        const currentSource = await describe(item);
        if (!currentSource.descriptor || currentSource.descriptor.item_id !== descriptor.item_id ||
            pathKey(currentSource.descriptor.media_path) !== pathKey(descriptor.media_path)) {
          fail("MEDIA_CHANGED", "This clip was relinked during marker export. Load and analyze its new original media.");
        }
        timebase(currentSource.descriptor, technical);
        // Another editor action might add a marker after our initial read.
        const now = await item.GetMarkers();
        if (!now || typeof now !== "object") fail("MARKER_READ_FAILED", "Resolve could not verify source markers before writing.");
        if (Object.prototype.hasOwnProperty.call(now, String(cue.frame))) { collisions++; continue; }
        await active(binding.project_id);
        attempted.push(cue);
        if (await item.AddMarker(cue.frame, "Blue", cue.name, cue.note, cue.duration, cue.key) !== true) fail("MARKER_WRITE_FAILED", "Resolve rejected a source marker.");
        added.push(cue);
      }
      const verified = await item.GetMarkers();
      if (!verified || added.some(cue => !verified[String(cue.frame)] || verified[String(cue.frame)].customData !== cue.key)) fail("MARKER_VERIFY_FAILED", "Resolve could not confirm every marker.");
      await active(binding.project_id);
    } catch (error) {
      let remaining = 0;
      // No generic Undo/DeleteMarkersByColor: only undo confirmed writes bearing
      // our exact customData, preserving pre-existing and concurrently edited data.
      for (const cue of attempted.reverse()) {
        try {
          const current = await item.GetMarkers(), marker = current && current[String(cue.frame)];
          if (!current || typeof current !== "object") { remaining++; continue; }
          if (marker && marker.customData === cue.key) {
            if (marker.name !== cue.name || marker.note !== cue.note || marker.duration !== cue.duration || await item.DeleteMarkerAtFrame(cue.frame) !== true) remaining++;
          }
        }
        catch (_) { remaining++; }
      }
      if (remaining) fail("MARKER_ROLLBACK_INCOMPLETE", "Marker export stopped and " + remaining + " Video Analyzer marker(s) could not be removed. Check this source clip before retrying.");
      throw error;
    }
    return { added: added.length, duplicates, collisions };
  }
  return { getProjectIdentity: projectIdentity, getSelection, resolveBinding, selectClip, addMarkers };
}
module.exports = { createHost, HostError, MAX_MARKERS, MARKER_NAMESPACE, pathKey, timebase, prepareCues };
