"use strict";

// Public Adobe UXP contracts (no CEP/QE or timeline mutation):
// https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/projectutils
// https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/sourcemonitor
// https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/markers
// https://github.com/adobe/eslint-plugin-premierepro
const MIN_PREMIERE_VERSION = "26.3"; // SourceMonitor.setPosition was added in 26.3.
const MAX_MARKERS = 500;
const MAX_PROJECT_ITEMS = 20000;
const MAX_BIN_DEPTH = 64;

class HostError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "HostError";
    this.code = code;
  }
}

function fail(code, message) { throw new HostError(code, message); }
function requiredMethod(owner, name, explanation) {
  if (!owner || typeof owner[name] !== "function") {
    fail("UNSUPPORTED_API", explanation || `Premiere does not expose ${name}. Update to Premiere ${MIN_PREMIERE_VERSION} or later.`);
  }
}
function guidString(value) {
  const result = typeof value === "string" ? value : value && typeof value.toString === "function" ? value.toString() : "";
  if (!result || result === "[object Object]") fail("INVALID_PROJECT", "Premiere did not return a stable project identifier.");
  return result;
}
function isWindowsPath(path) { return /^[a-z]:[\\/]/i.test(path) || path.startsWith("\\\\"); }
function normalizedPath(path, windowsSource = false) {
  if (typeof path !== "string" || !path.trim()) return "";
  // POSIX paths are case-sensitive and may contain literal backslashes. Only
  // Windows drive/UNC paths use interchangeable separators and case folding.
  return windowsSource || isWindowsPath(path) ? path.replace(/\\/g, "/").toLowerCase() : path;
}
function finiteSeconds(value, label) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 864000) {
    fail("INVALID_TIME", `${label} must be a finite number of seconds between 0 and 864000.`);
  }
  return value;
}
function boundedText(value, label, maximum, fallback) {
  if (value === undefined || value === null) return fallback;
  if (typeof value !== "string" || value.length > maximum) {
    fail("INVALID_CUE", `${label} must be text of at most ${maximum} characters.`);
  }
  return value;
}
function cueValue(cue, keys) {
  for (const key of keys) if (cue[key] !== undefined) return cue[key];
  return undefined;
}
function markerKey(name, start, duration, comments) {
  return JSON.stringify([name, Math.round(start * 1000000), Math.round(duration * 1000000), comments]);
}

function createHost(injectedApi) {
  let api = injectedApi;
  function getApi() {
    if (!api) {
      try { api = require("premierepro"); }
      catch (_) { fail("HOST_UNAVAILABLE", "Open this panel inside Premiere Pro. The native Premiere API is unavailable here."); }
    }
    return api;
  }
  async function activeProject(expectedId) {
    const ppro = getApi();
    requiredMethod(ppro.Project, "getActiveProject");
    const project = await ppro.Project.getActiveProject();
    if (!project) fail("NO_PROJECT", "Open a Premiere project, then select clips in its Project panel.");
    if (expectedId !== undefined && guidString(project.guid) !== expectedId) {
      fail("PROJECT_CHANGED", "The active Premiere project changed. Refresh the selection before opening results or adding markers.");
    }
    return project;
  }
  function identity(project) {
    return { id: guidString(project.guid), name: project.name || "Untitled project", path: project.path || "" };
  }
  function capabilities(project) {
    const ppro = getApi();
    return {
      minimum_version: MIN_PREMIERE_VERSION,
      selection: Boolean(ppro.ProjectUtils && typeof ppro.ProjectUtils.getSelection === "function"),
      source_open: Boolean(ppro.SourceMonitor && typeof ppro.SourceMonitor.openProjectItem === "function"),
      source_seek: Boolean(ppro.SourceMonitor && typeof ppro.SourceMonitor.setPosition === "function" && typeof ppro.SourceMonitor.getPosition === "function"),
      source_markers: Boolean(ppro.Markers && typeof ppro.Markers.getMarkers === "function" && typeof project.executeTransaction === "function" && typeof project.lockedAccess === "function"),
      timeline_selection: false,
      max_marker_batch: MAX_MARKERS,
    };
  }
  async function itemId(item) {
    requiredMethod(item, "getId", "Premiere did not expose a stable identifier for this project item.");
    const id = await item.getId();
    if (typeof id !== "string" || !id) fail("INVALID_ITEM", "Premiere returned an invalid project item identifier.");
    return id;
  }
  function asClip(item) {
    const ppro = getApi();
    requiredMethod(ppro.ClipProjectItem, "cast");
    return ppro.ClipProjectItem.cast(item);
  }
  function asFolder(item) {
    const ppro = getApi();
    return ppro.FolderItem && typeof ppro.FolderItem.cast === "function" ? ppro.FolderItem.cast(item) : null;
  }
  async function walkItems(initial, visit) {
    if (!Array.isArray(initial)) fail("INVALID_SELECTION", "Premiere returned an invalid project selection.");
    const queue = initial.map(item => ({ item, depth: 0 }));
    const seen = new Set();
    for (let cursor = 0; cursor < queue.length; cursor += 1) {
      if (cursor >= MAX_PROJECT_ITEMS || queue.length > MAX_PROJECT_ITEMS) {
        fail("PROJECT_TOO_LARGE", "This selection contains too many project items. Select a smaller bin or individual clips.");
      }
      const { item, depth } = queue[cursor];
      const id = await itemId(item);
      if (seen.has(id)) continue;
      seen.add(id);
      if (await visit(item, id)) return;
      const folder = asFolder(item);
      if (folder) {
        if (depth >= MAX_BIN_DEPTH) fail("BIN_DEPTH_LIMIT", "The selected bin nesting is too deep. Select the source clips directly.");
        const children = await folder.getItems();
        if (!Array.isArray(children)) fail("INVALID_SELECTION", "Premiere returned invalid bin contents.");
        for (const child of children) queue.push({ item: child, depth: depth + 1 });
      }
    }
  }
  async function describeClip(item, id) {
    const ppro = getApi();
    const clip = asClip(item);
    if (!clip) return { skipped: true };
    if (typeof clip.isSequence !== "function" || typeof clip.isOffline !== "function") {
      fail("UNSUPPORTED_API", "Premiere cannot verify this item is online source media. Update Premiere before analyzing it.");
    }
    if (await clip.isSequence()) return { warning: `${item.name || "Selection"}: select source clips instead of nested sequences.` };
    if (await clip.isOffline()) return { warning: `${item.name || "Selection"}: media is offline. Relink it in Premiere first.` };
    if ((typeof clip.isMergedClip === "function" && await clip.isMergedClip()) ||
        (typeof clip.isMulticamClip === "function" && await clip.isMulticamClip())) {
      return { warning: `${item.name || "Selection"}: select the original media instead of a merged or multicam clip.` };
    }
    if (ppro.Constants && ppro.Constants.ContentType && typeof clip.getContentType === "function" &&
        await clip.getContentType() !== ppro.Constants.ContentType.MEDIA) {
      return { warning: `${item.name || "Selection"}: this item is not original media.` };
    }
    requiredMethod(clip, "getMediaFilePath");
    const path = await clip.getMediaFilePath();
    if (typeof path !== "string" || !path || !(/^([a-z]:[\\/]|\\\\|\/)/i.test(path))) {
      return { warning: `${item.name || "Selection"}: no local source media path is available.` };
    }
    let fps;
    if (typeof clip.getFootageInterpretation === "function") {
      const interpretation = await clip.getFootageInterpretation();
      if (interpretation && typeof interpretation.getFrameRate === "function") {
        const value = interpretation.getFrameRate();
        if (typeof value === "number" && Number.isFinite(value) && value > 0) fps = value;
      }
    }
    return { clip, descriptor: { item_id: id, name: item.name || clip.name || "Source clip", media_path: path, source_fps: fps } };
  }
  async function getProjectIdentity() { return identity(await activeProject()); }
  async function getSelection() {
    const ppro = getApi();
    const project = await activeProject();
    const projectIdentity = identity(project);
    requiredMethod(ppro.ProjectUtils, "getSelection");
    const selection = await ppro.ProjectUtils.getSelection(project);
    requiredMethod(selection, "getItems");
    const clips = [];
    const warnings = [];
    await walkItems(await selection.getItems(), async (item, id) => {
      const result = await describeClip(item, id);
      if (result.warning) warnings.push(result.warning);
      if (result.descriptor) clips.push(result.descriptor);
      return false;
    });
    await activeProject(projectIdentity.id);
    if (!clips.length && !warnings.length) warnings.push("Select original clips or a bin in Premiere's Project panel. Timeline selections are not supported yet.");
    return { project_id: projectIdentity.id, project_name: projectIdentity.name, project_path: projectIdentity.path, clips, warnings, capabilities: capabilities(project) };
  }
  async function resolveBinding(binding) {
    if (!binding || typeof binding.project_id !== "string" || !binding.project_id ||
        typeof binding.item_id !== "string" || !binding.item_id || !normalizedPath(binding.media_path) ||
        (binding.source_fps != null && (typeof binding.source_fps !== "number" || !Number.isFinite(binding.source_fps) || binding.source_fps <= 0))) {
      fail("INVALID_BINDING", "Refresh the Premiere selection to restore this source clip's project and media identity.");
    }
    const project = await activeProject(binding.project_id);
    requiredMethod(project, "getRootItem");
    const root = await project.getRootItem();
    requiredMethod(root, "getItems");
    let found;
    await walkItems(await root.getItems(), async (item, id) => {
      if (id !== binding.item_id) return false;
      found = await describeClip(item, id);
      return true;
    });
    if (!found || !found.descriptor) fail("SOURCE_UNAVAILABLE", found && found.warning || "This source clip is no longer available in the Premiere project. Refresh the selection.");
    const windowsSource = isWindowsPath(found.descriptor.media_path);
    if (normalizedPath(found.descriptor.media_path, windowsSource) !== normalizedPath(binding.media_path, windowsSource)) {
      fail("MEDIA_CHANGED", "This clip now points to different media. Refresh and analyze the new source before opening results or adding markers.");
    }
    if (binding.source_fps != null) {
      if (!found.descriptor.source_fps) {
        fail("UNSUPPORTED_API", "Premiere cannot verify this source clip's frame rate. Update Premiere before opening results or adding markers.");
      }
      // Accept rounded fractional rates, but reject footage reinterpreted at another rate.
      if (Math.abs(found.descriptor.source_fps - binding.source_fps) > 0.001) {
        fail("FOOTAGE_INTERPRETATION_CHANGED", "This clip's interpreted frame rate differs from the analyzed media. Restore its original frame rate in Premiere before opening results or adding markers.");
      }
    }
    await activeProject(binding.project_id);
    return { project, clip: found.clip, descriptor: found.descriptor };
  }
  async function openSource(binding, seconds) {
    finiteSeconds(seconds, "Source position");
    const ppro = getApi();
    requiredMethod(ppro.SourceMonitor, "setPosition", "Jumping to a source moment requires Premiere 26.3 or later. Update Premiere and reload the panel.");
    requiredMethod(ppro.SourceMonitor, "getPosition");
    requiredMethod(ppro.SourceMonitor, "getProjectItem");
    requiredMethod(ppro.SourceMonitor, "openProjectItem");
    requiredMethod(ppro.TickTime, "createWithSeconds");
    const resolved = await resolveBinding(binding);
    if (await ppro.SourceMonitor.openProjectItem(resolved.clip) !== true) {
      fail("SOURCE_OPEN_FAILED", "Premiere could not open this clip in the Source Monitor.");
    }
    if (await ppro.SourceMonitor.setPosition(ppro.TickTime.createWithSeconds(seconds)) !== true) {
      fail("SOURCE_SEEK_FAILED", "Premiere opened the clip but could not seek to the requested moment.");
    }
    const currentItem = await ppro.SourceMonitor.getProjectItem();
    const position = await ppro.SourceMonitor.getPosition();
    const tolerance = 1 / (resolved.descriptor.source_fps || 24) + 0.001;
    if (!currentItem || await itemId(currentItem) !== binding.item_id || !position ||
        typeof position.seconds !== "number" || !Number.isFinite(position.seconds) || Math.abs(position.seconds - seconds) > tolerance) {
      fail("SOURCE_VERIFY_FAILED", "Premiere did not confirm the requested source moment. Check the Source Monitor and refresh the selection.");
    }
    await activeProject(binding.project_id);
    return { opened: true, seconds: position.seconds, item_id: binding.item_id, project_id: binding.project_id };
  }
  function prepareCues(cues) {
    if (!Array.isArray(cues) || cues.length > MAX_MARKERS) {
      fail("MARKER_LIMIT", `Choose at most ${MAX_MARKERS} markers per export.`);
    }
    return cues.map((cue, index) => {
      if (!cue || typeof cue !== "object" || Array.isArray(cue)) fail("INVALID_CUE", `Marker ${index + 1} is invalid.`);
      const start = finiteSeconds(cueValue(cue, ["start", "start_seconds", "start_time_seconds"]), `Marker ${index + 1} start`);
      const endValue = cueValue(cue, ["end", "end_seconds", "end_time_seconds"]);
      const end = endValue === undefined || endValue === null ? start : finiteSeconds(endValue, `Marker ${index + 1} end`);
      if (end < start) fail("INVALID_CUE", `Marker ${index + 1} ends before it starts.`);
      const name = boundedText(cue.name, "Marker name", 4096, `Video Analyzer ${index + 1}`);
      const comment = boundedText(cueValue(cue, ["comment", "text", "description"]), "Marker comment", 20000, "");
      const comments = comment ? `Video Analyzer\n${comment}` : "Video Analyzer";
      return { name, start, duration: end - start, comments, key: markerKey(name, start, end - start, comments) };
    });
  }
  function existingMarkerKeys(owner) {
    const markers = owner.getMarkers();
    if (!Array.isArray(markers)) fail("MARKER_READ_FAILED", "Premiere did not return the source marker list.");
    const keys = new Set();
    for (const marker of markers) {
      const comments = marker.getComments();
      if (comments !== "Video Analyzer" && !(typeof comments === "string" && comments.startsWith("Video Analyzer\n"))) continue;
      const start = marker.getStart();
      const duration = marker.getDuration();
      if (!start || !duration || typeof start.seconds !== "number" || typeof duration.seconds !== "number") {
        fail("MARKER_READ_FAILED", "Premiere could not read an existing Video Analyzer marker's time.");
      }
      keys.add(markerKey(marker.getName(), start.seconds, duration.seconds, comments));
    }
    return keys;
  }
  async function addMarkers(binding, cues) {
    const prepared = prepareCues(cues);
    const ppro = getApi();
    const { project, clip } = await resolveBinding(binding);
    if (!capabilities(project).source_markers) fail("UNSUPPORTED_API", "This Premiere build does not expose undoable source markers.");
    requiredMethod(ppro.TickTime, "createWithSeconds");
    const owner = await ppro.Markers.getMarkers(clip);
    requiredMethod(owner, "getMarkers");
    requiredMethod(owner, "createAddMarkerAction");
    await activeProject(binding.project_id);
    let added = 0;
    let skipped = 0;
    let committed = false;
    // Native actions cannot escape a lock/transaction scope. Both callbacks must be synchronous.
    project.lockedAccess(() => {
      const keys = existingMarkerKeys(owner);
      const pending = [];
      for (const cue of prepared) {
        if (keys.has(cue.key)) { skipped += 1; continue; }
        keys.add(cue.key);
        pending.push(cue);
      }
      if (!pending.length) { committed = true; return; }
      committed = project.executeTransaction(compound => {
        for (const cue of pending) {
          const action = owner.createAddMarkerAction(cue.name, ppro.Marker && ppro.Marker.MARKER_TYPE_COMMENT || "Comment",
            ppro.TickTime.createWithSeconds(cue.start), ppro.TickTime.createWithSeconds(cue.duration), cue.comments);
          if (!action) fail("MARKER_ACTION_FAILED", "Premiere could not create a source marker action.");
          if (compound.addAction(action) === false) fail("MARKER_ACTION_FAILED", "Premiere rejected a source marker action.");
        }
      }, "Add Video Analyzer source markers");
      added = pending.length;
    });
    if (committed !== true) fail("MARKER_TRANSACTION_FAILED", "Premiere did not commit the source markers. No successful export was confirmed.");
    const verifiedKeys = existingMarkerKeys(owner);
    if (prepared.some(cue => !verifiedKeys.has(cue.key))) {
      fail("MARKER_VERIFY_FAILED", "Premiere committed the transaction but could not confirm every source marker. Check the clip markers before retrying.");
    }
    return { added, skipped };
  }
  async function getSelectedMedia() {
    const result = await getSelection();
    return { project: { id: result.project_id, name: result.project_name, path: result.project_path },
      items: result.clips.map(clip => ({ id: clip.item_id, itemId: clip.item_id, name: clip.name, mediaPath: clip.media_path, sourceFps: clip.source_fps, selectionSource: "project" })),
      warnings: result.warnings, capabilities: result.capabilities };
  }
  async function bindingForId(id, expectedProjectId) {
    const selection = await getSelection();
    if (expectedProjectId && expectedProjectId !== selection.project_id) fail("PROJECT_CHANGED", "The active Premiere project changed. Refresh the selection.");
    const clip = selection.clips.find(item => item.item_id === id);
    if (!clip) fail("SOURCE_UNAVAILABLE", "Select this original clip in the Project panel before opening it or adding markers.");
    return { project_id: selection.project_id, ...clip };
  }
  return {
    getProjectIdentity, getSelection, getSelectedMedia, openSource, addMarkers,
    openSourceAt: async (id, seconds, projectId) => openSource(await bindingForId(id, projectId), seconds),
    addSourceMarkers: async (id, cues, projectId) => addMarkers(await bindingForId(id, projectId), cues),
  };
}

let defaultHost;
function defaultCall(name, args) {
  if (!defaultHost) defaultHost = createHost();
  return defaultHost[name](...args);
}
module.exports = {
  createHost, HostError, MIN_PREMIERE_VERSION, MAX_MARKERS,
  getProjectIdentity: (...args) => defaultCall("getProjectIdentity", args),
  getSelection: (...args) => defaultCall("getSelection", args),
  getSelectedMedia: (...args) => defaultCall("getSelectedMedia", args),
  openSource: (...args) => defaultCall("openSource", args),
  openSourceAt: (...args) => defaultCall("openSourceAt", args),
  addMarkers: (...args) => defaultCall("addMarkers", args),
  addSourceMarkers: (...args) => defaultCall("addSourceMarkers", args),
};
