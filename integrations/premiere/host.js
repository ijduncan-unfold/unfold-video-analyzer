"use strict";

// Public Adobe UXP contracts (no CEP/QE):
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

const PROJECT_META_NS = "http://ns.adobe.com/premierePrivateProjectMetaData/1.0/";
const DM_NS = "http://ns.adobe.com/xmp/1.0/DynamicMedia/";
const DC_NS = "http://purl.org/dc/elements/1.1/";
const XMP_FIELDS = {
  description: [DC_NS, "description", "alt"], keywords: [DC_NS, "subject", "bag"],
  notes: [DM_NS, "logComment"], scene: [DM_NS, "scene"], shot: [DM_NS, "shotNumber"],
  take: [DM_NS, "takeNumber", "integer"], camera_roll: [DM_NS, "tapeName"], camera: [DM_NS, "cameraLabel"],
  production_title: [DM_NS, "projectName"], director: [DM_NS, "director"], cinematographer: [DM_NS, "directorPhotography"],
  shot_type: [DM_NS, "shotSize"], camera_movement: [DM_NS, "cameraMove"], location: [DM_NS, "shotLocation"],
};
const METADATA_COLUMNS = new Set(["Column.PropertyText.Description", "Column.Intrinsic.LogNote", "Column.PropertyText.Scene", "Column.PropertyText.Shot", "Column.Intrinsic.TapeName",
  "VideoAnalyzerKeywords", "VideoAnalyzerTake", "VideoAnalyzerCamera", "VideoAnalyzerProduction", "VideoAnalyzerDirector", "VideoAnalyzerCinematographer", "VideoAnalyzerShotType", "VideoAnalyzerCameraMovement", "VideoAnalyzerLocation", "VideoAnalyzerColors", "VideoAnalyzerSubjects", "VideoAnalyzerDialogue",
  "VideoAnalyzerSceneDescription", "VideoAnalyzerCosplayDescription", "VideoAnalyzerCosplayCharacters", "VideoAnalyzerCosplayFranchises", "VideoAnalyzerCosplayVariants", "VideoAnalyzerCosplayIdentification",
  "VideoAnalyzerClipDescriptions", "VideoAnalyzerDetails", "VideoAnalyzerSlates", "VideoAnalyzerTechnical"]);

function analysisFields(record) {
  const shots = Array.isArray(record.shots) ? record.shots : [];
  const timed = shot => String(shot.start_time || shot.start_seconds || "0") + "–" + String(shot.end_time || shot.end_seconds || "");
  return [
    ["VideoAnalyzerClipDescriptions", "Clip descriptions", shots.map(shot => timed(shot) + " · Clip " + shot.shot_number + "\n" + (shot.visual_description || "")).join("\n\n")],
    ["VideoAnalyzerDetails", "Detailed analysis", JSON.stringify({ summary: record.summary, analysis: record.analysis,
      clips: shots.map(shot => ({ ...shot, thumbnail_url: undefined, thumbnail: undefined })) }, null, 2)],
    ["VideoAnalyzerSlates", "Slate readings", shots.filter(shot => shot.slates && shot.slates.length).map(shot => timed(shot) + "\n" + JSON.stringify(shot.slates, null, 2)).join("\n\n")],
    ["VideoAnalyzerTechnical", "Source inspection", JSON.stringify(record.technical || {}, null, 2)],
  ].filter(([, , value]) => value && value !== "{}").map(([premiere, label, value]) => ({ premiere, label, value }));
}
function createHost(injectedApi, injectedXmp) {
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
    return { projectItem: item, clip, descriptor: { item_id: id, name: item.name || clip.name || "Source clip", media_path: path, source_fps: fps } };
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
    return { project, projectItem: found.projectItem, clip: found.clip, descriptor: found.descriptor };
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
    if (await ppro.SourceMonitor.openProjectItem(resolved.projectItem) !== true) {
      fail("SOURCE_OPEN_FAILED", "Premiere could not open this clip in the Source Monitor.");
    }
    // SourceMonitor positions are recording-relative, even when the UI displays
    // embedded camera timecode. A seek already at the target can return false;
    // verify the actual item/position before deciding whether it failed.
    const seekResult = await ppro.SourceMonitor.setPosition(ppro.TickTime.createWithSeconds(seconds));
    const currentItem = await ppro.SourceMonitor.getProjectItem();
    const position = await ppro.SourceMonitor.getPosition();
    const tolerance = 1 / (resolved.descriptor.source_fps || 24) + 0.001;
    if (!currentItem || await itemId(currentItem) !== binding.item_id || !position ||
        typeof position.seconds !== "number" || !Number.isFinite(position.seconds) || Math.abs(position.seconds - seconds) > tolerance) {
      if (seekResult === false) fail("SOURCE_SEEK_FAILED", "Premiere opened the clip but could not seek to the requested moment.");
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
  async function syncMetadata(binding, mapping) {
    const { project, projectItem } = await resolveBinding(binding);
    const ppro = getApi(), metadata = ppro.Metadata;
    for (const name of ["getProjectMetadata", "createSetProjectMetadataAction", "addPropertyToProjectMetadataSchema", "getXMPMetadata", "createSetXMPMetadataAction"]) requiredMethod(metadata, name);
    requiredMethod(project, "lockedAccess"); requiredMethod(project, "executeTransaction");
    let toolkit = injectedXmp;
    if (!toolkit) { try { toolkit = require("uxp").xmp; } catch (_) { /* capability error below */ } }
    if (!toolkit || typeof toolkit.XMPMeta !== "function") fail("UNSUPPORTED_API", "This Premiere build does not expose the XMP toolkit for project metadata.");
    if (!mapping || mapping.schema_version !== "1.0" || !Array.isArray(mapping.fields) || !mapping.record || mapping.record.schema !== "video-analyzer.library") fail("INVALID_METADATA", "Refresh the analysis before syncing metadata.");
    const record = JSON.stringify(mapping.record);
    if (record.length > 4000000) fail("METADATA_LIMIT", "This analysis exceeds the panel's 4 MB metadata limit. Export the complete JSON and XMP from the analyzer instead; nothing was truncated.");
    toolkit.XMPMeta.registerNamespace(PROJECT_META_NS, "premierePrivateProjectMetaData");
    const packet = await metadata.getProjectMetadata(projectItem);
    if (typeof packet !== "string") fail("METADATA_READ_FAILED", "Premiere could not read this clip's project metadata.");
    const xmp = new toolkit.XMPMeta(packet);
    const filePacket = await metadata.getXMPMetadata(projectItem);
    if (typeof filePacket !== "string") fail("METADATA_READ_FAILED", "Premiere could not read this clip's XMP metadata.");
    const fileXmp = new toolkit.XMPMeta(filePacket);
    toolkit.XMPMeta.registerNamespace(DM_NS, "xmpDM");
    toolkit.XMPMeta.registerNamespace(DC_NS, "dc");
    const read = (document, key) => { const value = document.getProperty(PROJECT_META_NS, key); return value == null ? "" : String(value.value === undefined ? value : value.value); };
    const propertyValue = value => value == null ? "" : String(value.value === undefined ? value : value.value);
    const readNative = (document, key) => {
      const [ns, name, type] = XMP_FIELDS[key];
      // Native UXP's array accessors throw on a property that does not exist.
      if (document.getProperty(ns, name) == null) return type === "bag" ? [] : "";
      // This UXP build rejects an empty generic language despite the XMP docs.
      if (type === "alt") { requiredMethod(document, "getLocalizedText"); return propertyValue(document.getLocalizedText(ns, name, "en", "x-default")); }
      if (type === "bag") {
        requiredMethod(document, "countArrayItems"); requiredMethod(document, "getArrayItem");
        return Array.from({ length: document.countArrayItems(ns, name) }, (_, index) => propertyValue(document.getArrayItem(ns, name, index + 1)));
      }
      return propertyValue(document.getProperty(ns, name));
    };
    let prior = {};
    try { prior = JSON.parse(read(xmp, "VideoAnalyzerReceipt") || "{}"); } catch (_) { /* unrecognized prior ownership: preserve all existing values */ }
    if (!prior || typeof prior !== "object" || Array.isArray(prior)) prior = {};
    let priorXmp = {};
    try { priorXmp = JSON.parse(read(xmp, "VideoAnalyzerXMPReceipt") || "{}"); } catch (_) { /* preserve unowned native values */ }
    if (!priorXmp || typeof priorXmp !== "object" || Array.isArray(priorXmp)) priorXmp = {};
    const proposed = {}, labels = {}, preserved = [];
    const nativeProposed = {}, nativeOwned = {}, nativeChanges = {};
    for (const field of mapping.fields) {
      if (!field || !Object.prototype.hasOwnProperty.call(XMP_FIELDS, field.key) || typeof field.value !== "string" || !field.value) continue;
      const type = XMP_FIELDS[field.key][2];
      if (type === "integer" && (!/^\d+$/.test(field.value) || !Number.isSafeInteger(Number(field.value)))) continue;
      nativeProposed[field.key] = type === "bag" ? (Array.isArray(field.xmp_value) ? field.xmp_value.filter(value => typeof value === "string" && value) : field.value.split(/,\s*/).filter(Boolean)) :
        type === "integer" ? String(Number(field.value)) : field.value;
    }
    for (const key of new Set([...Object.keys(nativeProposed), ...Object.keys(priorXmp)])) {
      if (!Object.prototype.hasOwnProperty.call(XMP_FIELDS, key)) continue;
      const current = readNative(fileXmp, key);
      const empty = Array.isArray(current) ? current.length === 0 : !current;
      if (!empty && (!Object.prototype.hasOwnProperty.call(priorXmp, key) || JSON.stringify(current) !== JSON.stringify(priorXmp[key]))) { preserved.push("XMP:" + key); continue; }
      const value = nativeProposed[key] === undefined ? (Array.isArray(current) ? [] : "") : nativeProposed[key];
      nativeOwned[key] = value;
      if (JSON.stringify(current) === JSON.stringify(value)) continue;
      nativeChanges[key] = value;
      const [ns, name, type] = XMP_FIELDS[key];
      if (type === "bag") {
        requiredMethod(fileXmp, "deleteProperty"); requiredMethod(fileXmp, "appendArrayItem");
        if (!toolkit.XMPConst || toolkit.XMPConst.PROP_IS_ARRAY === undefined) fail("UNSUPPORTED_API", "Premiere did not expose XMP keyword arrays.");
        fileXmp.deleteProperty(ns, name);
        value.forEach(item => fileXmp.appendArrayItem(ns, name, item, 0, toolkit.XMPConst.PROP_IS_ARRAY));
      } else if (type === "alt") { requiredMethod(fileXmp, "setLocalizedText"); fileXmp.setLocalizedText(ns, name, "en", "x-default", value); }
      else if (type === "integer" && !value) { requiredMethod(fileXmp, "deleteProperty"); fileXmp.deleteProperty(ns, name); }
      else fileXmp.setProperty(ns, name, type === "integer" ? Number(value) : value);
    }
    for (const field of [...mapping.fields, ...analysisFields(mapping.record)]) {
      if (!field || !METADATA_COLUMNS.has(field.premiere) || typeof field.value !== "string" || !field.value) continue;
      proposed[field.premiere] = field.value; labels[field.premiere] = "Video Analyzer · " + String(field.label || field.key);
    }
    const owned = {}, changes = {};
    for (const key of new Set([...Object.keys(proposed), ...Object.keys(prior)])) {
      if (!METADATA_COLUMNS.has(key)) continue;
      const current = read(xmp, key);
      if (current && (!Object.prototype.hasOwnProperty.call(prior, key) || current !== prior[key])) { preserved.push(key); continue; }
      changes[key] = proposed[key] || ""; owned[key] = changes[key];
    }
    changes.VideoAnalyzerAnalysis = record;
    changes.VideoAnalyzerReceipt = JSON.stringify(owned);
    changes.VideoAnalyzerXMPReceipt = JSON.stringify(nativeOwned);
    labels.VideoAnalyzerAnalysis = "Video Analyzer · Complete analysis JSON";
    labels.VideoAnalyzerReceipt = "Video Analyzer · Sync record";
    labels.VideoAnalyzerXMPReceipt = "Video Analyzer · XMP sync record";
    for (const key of Object.keys(changes)) {
      if (key.startsWith("VideoAnalyzer")) {
        if (!ppro.Constants || !ppro.Constants.MetadataType || ppro.Constants.MetadataType.TEXT === undefined) fail("UNSUPPORTED_API", "Premiere did not expose text metadata fields.");
        // False may mean a schema property already exists. Readback below,
        // rather than schema creation alone, confirms the actual write.
        await metadata.addPropertyToProjectMetadataSchema(key, labels[key] || "Video Analyzer · " + key, ppro.Constants.MetadataType.TEXT);
      }
      xmp.setProperty(PROJECT_META_NS, key, changes[key]);
    }
    await resolveBinding(binding);
    if (await metadata.getProjectMetadata(projectItem) !== packet) fail("METADATA_CHANGED", "Clip metadata changed during sync. Try again; your edits were preserved.");
    if (await metadata.getXMPMetadata(projectItem) !== filePacket) fail("METADATA_CHANGED", "Clip XMP changed during sync. Try again; your edits were preserved.");
    let committed = false;
    // Premiere owns XMP persistence (embedded metadata or sidecars). Never open
    // or rewrite the source ourselves; preserve all unrelated packet properties.
    project.lockedAccess(() => {
      committed = project.executeTransaction(compound => {
        if (Object.keys(nativeChanges).length && compound.addAction(metadata.createSetXMPMetadataAction(projectItem, fileXmp.serialize())) === false) fail("METADATA_WRITE_FAILED", "Premiere rejected the XMP metadata action.");
        if (compound.addAction(metadata.createSetProjectMetadataAction(projectItem, xmp.serialize(), Object.keys(changes))) === false) fail("METADATA_WRITE_FAILED", "Premiere rejected the project metadata action.");
      }, "Sync Video Analyzer metadata");
    });
    if (committed !== true) fail("METADATA_WRITE_FAILED", "Premiere did not confirm the metadata transaction.");
    let unconfirmed = [], nativeUnconfirmed = [];
    // Premiere can return the old packet briefly after a successful transaction.
    // Verify both packets with a bounded wait rather than repeating the write.
    for (let attempt = 0; attempt < 6; attempt++) {
      const after = new toolkit.XMPMeta(await metadata.getProjectMetadata(projectItem));
      unconfirmed = Object.entries(changes).filter(([key, value]) => read(after, key) !== value).map(([key]) => key);
      const nativeAfter = new toolkit.XMPMeta(await metadata.getXMPMetadata(projectItem));
      nativeUnconfirmed = Object.entries(nativeChanges).filter(([key, value]) => JSON.stringify(readNative(nativeAfter, key)) !== JSON.stringify(value)).map(([key]) => key);
      if (!unconfirmed.length && !nativeUnconfirmed.length) break;
      if (attempt < 5) await new Promise(resolve => setTimeout(resolve, 100 * (attempt + 1)));
    }
    if (unconfirmed.length) fail("METADATA_VERIFY_FAILED", "Premiere did not confirm: " + unconfirmed.map(key => labels[key] || key).join(", ") + ". Inspect project metadata before retrying.");
    if (nativeUnconfirmed.length) fail("METADATA_VERIFY_FAILED", "Premiere did not confirm every XMP field. Inspect file metadata before retrying.");
    await resolveBinding(binding);
    return { updated: Object.keys(changes).length - 3 + Object.keys(nativeChanges).length, preserved: preserved.length, complete_record: true };
  }
  async function bindingForId(id, expectedProjectId) {
    const selection = await getSelection();
    if (expectedProjectId && expectedProjectId !== selection.project_id) fail("PROJECT_CHANGED", "The active Premiere project changed. Refresh the selection.");
    const clip = selection.clips.find(item => item.item_id === id);
    if (!clip) fail("SOURCE_UNAVAILABLE", "Select this original clip in the Project panel before opening it or adding markers.");
    return { project_id: selection.project_id, ...clip };
  }
  function commit(project, label, build) {
    requiredMethod(project, "lockedAccess"); requiredMethod(project, "executeTransaction");
    let committed = false;
    project.lockedAccess(() => { committed = project.executeTransaction(compound => {
      const action = build();
      if (!action || compound.addAction(action) === false) fail("EDIT_ACTION_FAILED", "Premiere rejected the edit action.");
    }, label); });
    if (committed !== true) fail("EDIT_TRANSACTION_FAILED", "Premiere did not confirm this edit. Inspect the Project panel and timeline before retrying.");
  }
  async function importSelection(plan, expectedProjectId, loadMetadata, progress = () => {}) {
    if (!plan || plan.schema !== "unfold.premiere-selection" || !/^[a-f0-9-]{36}$/.test(plan.id || "") ||
        typeof plan.name !== "string" || !plan.name.trim() || plan.name.length > 160 ||
        !Array.isArray(plan.items) || !plan.items.length || plan.items.length > 1000 || typeof loadMetadata !== "function") {
      fail("INVALID_SELECTION", "Refresh the saved analyzer selection before importing.");
    }
    const seen = new Set();
    for (const item of plan.items) {
      if (!item || typeof item.id !== "string" || !item.id || seen.has(item.id) ||
          typeof item.media_path !== "string" || !/^([a-z]:[\\/]|\\\\|\/)/i.test(item.media_path)) {
        fail("INVALID_SELECTION", "Selection contains invalid or duplicate media references.");
      }
      seen.add(item.id);
    }
    const project = await activeProject(expectedProjectId);
    const projectId = identity(project).id;
    requiredMethod(project, "importFiles");
    const root = await project.getRootItem();
    requiredMethod(root, "getItems"); requiredMethod(root, "createBinAction");
    const binName = plan.name + " · " + plan.id.slice(0, 8);
    const findBin = async () => (await root.getItems()).filter(item => item.name === binName && asFolder(item));
    let candidates = await findBin();
    if (candidates.length > 1) fail("AMBIGUOUS_BIN", "More than one bin has this selection name. Rename the duplicate before retrying.");
    if (!candidates.length) {
      await activeProject(projectId);
      commit(project, "Create Unfold selection bin", () => root.createBinAction(binName, false));
      candidates = await findBin();
    }
    if (candidates.length !== 1) fail("BIN_VERIFY_FAILED", "Premiere did not confirm the selection bin. Inspect the Project panel before retrying.");
    const target = candidates[0], folder = asFolder(target);
    const summary = { imported: 0, reused: 0, synced: 0, preserved: 0, bin: binName };
    const findClip = async path => {
      const matches = [];
      for (const item of await folder.getItems()) {
        const result = await describeClip(item, await itemId(item));
        if (result.descriptor && normalizedPath(result.descriptor.media_path, isWindowsPath(path)) === normalizedPath(path, isWindowsPath(path))) matches.push(result.descriptor);
      }
      if (matches.length > 1) fail("AMBIGUOUS_SOURCE", "Duplicate source clips exist in the selection bin. Resolve them before retrying.");
      return matches[0];
    };
    try {
      for (const item of plan.items) {
        // Fetch current corrections and recheck original identity immediately before import.
        const current = await loadMetadata(item.id);
        if (!current || current.media_path !== item.media_path || !current.mapping ||
            !current.mapping.record || !current.mapping.record.asset || current.mapping.record.asset.id !== item.id) {
          fail("SOURCE_CHANGED", "The saved source changed. Reload this selection before importing.");
        }
        await activeProject(projectId);
        let descriptor = await findClip(item.media_path);
        if (!descriptor) {
          const result = await project.importFiles([item.media_path], true, target, false);
          await activeProject(projectId);
          descriptor = await findClip(item.media_path);
          if (result !== true || !descriptor) fail("IMPORT_VERIFY_FAILED", "Premiere did not confirm this original in the selection bin. Inspect it before retrying.");
          summary.imported++;
        } else summary.reused++;
        const result = await syncMetadata({ project_id: projectId, ...descriptor }, current.mapping);
        summary.synced++; summary.preserved += result.preserved || 0;
        progress({ ...summary, total: plan.items.length });
      }
      await activeProject(projectId);
      return summary;
    } catch (error) {
      error.message += " " + summary.imported + " originals imported, " + summary.reused + " reused, " + summary.synced + " metadata records synced. Completed clips remain in the selection bin; retry reuses them.";
      throw error;
    }
  }
  async function prepareSubclip(binding, range) {
    const { project, clip, descriptor } = await resolveBinding(binding);
    const ppro = getApi();
    const start = finiteSeconds(range && range.start, "Clip start");
    const end = finiteSeconds(range && range.end, "Clip end");
    const duration = finiteSeconds(range && range.duration, "Source duration");
    if (end <= start || end > duration) fail("INVALID_TIME", "The clip must fit inside the original recording.");
    requiredMethod(clip, "createSubClipAction");
    // Source seconds are relative to the recording, not its embedded timecode.
    const fps = descriptor.source_fps;
    const frameStart = fps ? Math.round(start * fps) / fps : start;
    const frameEnd = fps ? Math.round(end * fps) / fps : end;
    if (frameEnd <= frameStart) fail("INVALID_TIME", "This result is shorter than one source frame.");
    const key = JSON.stringify([binding.item_id, binding.media_path, frameStart, frameEnd, String(range.session || "")]);
    let hash = 2166136261;
    for (let i = 0; i < key.length; i++) hash = Math.imul(hash ^ key.charCodeAt(i), 16777619) >>> 0;
    // Premiere 26.5's subclip action replaces non-ASCII separators in the
    // resulting native name. Use an ASCII display label so readback/reuse is
    // stable; the original source name and path are never changed.
    const label = (value, fallback, maximum) => String(value || fallback).normalize("NFKD")
      .replace(/[^\x20-\x7e]/g, "").trim().slice(0, maximum) || fallback;
    const name = label(descriptor.name, "Source", 70) + " - " + label(range.name, "Clip", 35) + " - VA " + hash.toString(16);
    let rangeReadback = "No matching native item was returned.";
    const find = async () => {
      let found;
      const root = await project.getRootItem();
      await walkItems(await root.getItems(), async item => {
        if (item.name !== name) return false;
        const candidate = asClip(item);
        if (!candidate || await candidate.isOffline() || await candidate.isSequence()) return false;
        const windowsSource = isWindowsPath(binding.media_path);
        if (normalizedPath(await candidate.getMediaFilePath(), windowsSource) !== normalizedPath(binding.media_path, windowsSource)) return false;
        requiredMethod(candidate, "getInPoint"); requiredMethod(candidate, "getOutPoint");
        const mediaType = ppro.Constants.MediaType.VIDEO;
        let a = await candidate.getInPoint(mediaType), b = await candidate.getOutPoint(mediaType);
        // New native subclips have hard media boundaries but no user marks.
        // Premiere returns -400000 seconds for each unset mark. Verify the
        // actual media bounds relative to the original's embedded start.
        if (a && b && a.seconds === -400000 && b.seconds === -400000) {
          requiredMethod(candidate, "getMedia"); requiredMethod(clip, "getMedia");
          const media = await candidate.getMedia(), original = await clip.getMedia();
          requiredMethod(media, "getStart"); requiredMethod(media, "getDuration"); requiredMethod(original, "getStart");
          const mediaStart = await media.getStart(), originalStart = await original.getStart(), mediaDuration = await media.getDuration();
          a = { seconds: mediaStart && originalStart ? mediaStart.seconds - originalStart.seconds : NaN };
          b = { seconds: mediaDuration ? a.seconds + mediaDuration.seconds : NaN };
        }
        rangeReadback = `Expected ${frameStart.toFixed(6)}–${frameEnd.toFixed(6)}s; Premiere returned ${a && a.seconds}–${b && b.seconds}s.`;
        if (!a || !b || !Number.isFinite(a.seconds) || !Number.isFinite(b.seconds)) return false;
        const tolerance = 0.5 / (fps || 24) + 0.000001;
        if (Math.abs(a.seconds - frameStart) > tolerance || Math.abs(b.seconds - frameEnd) > tolerance) return false;
        found = item; return true;
      });
      return found;
    };
    let subclip = await find();
    let created = false;
    await resolveBinding(binding);
    if (!subclip) {
      commit(project, "Create Video Analyzer subclip", () => clip.createSubClipAction(name,
        ppro.TickTime.createWithSeconds(frameStart), ppro.TickTime.createWithSeconds(frameEnd), true,
        { takeVideo: true, takeAudio: true }));
      created = true;
      subclip = await find();
      if (!subclip) fail("SUBCLIP_VERIFY_FAILED", "Premiere did not confirm the new subclip's source range. " + rangeReadback + " Inspect the Project panel before retrying.");
    }
    await activeProject(binding.project_id);
    return { project, subclip, name, created, start: frameStart, end: frameEnd };
  }
  async function makeSubclip(binding, range) {
    const result = await prepareSubclip(binding, range);
    requiredMethod(getApi().SourceMonitor, "openProjectItem");
    if (await getApi().SourceMonitor.openProjectItem(result.subclip) === false) fail("SOURCE_OPEN_FAILED", "The subclip was created but Premiere could not open it. Find it in the Project panel.");
    return { name: result.name, created: result.created };
  }
  async function insertClip(binding, range) {
    const project = await activeProject(binding.project_id);
    requiredMethod(project, "getActiveSequence");
    const sequence = await project.getActiveSequence();
    if (!sequence) fail("NO_SEQUENCE", "Open a timeline in Premiere before inserting this clip.");
    const sequenceId = guidString(sequence.guid);
    requiredMethod(sequence, "getPlayerPosition");
    const position = await sequence.getPlayerPosition();
    finiteSeconds(position.seconds, "Timeline playhead");
    requiredMethod(getApi().SequenceEditor, "getEditor");
    const editor = getApi().SequenceEditor.getEditor(sequence);
    requiredMethod(editor, "createInsertProjectItemAction");
    requiredMethod(sequence, "getVideoTrack");
    // Require readback support before editing so a rejected/partial native
    // action cannot be reported as an inserted clip.
    const videoTrack = await sequence.getVideoTrack(0);
    requiredMethod(videoTrack, "getTrackItems");
    const result = await prepareSubclip(binding, range);
    await resolveBinding(binding);
    const current = await project.getActiveSequence();
    if (!current || guidString(current.guid) !== sequenceId || Math.abs((await current.getPlayerPosition()).seconds - position.seconds) > 0.00001) {
      fail("TIMELINE_CHANGED", "The active timeline or playhead moved. Try inserting again at the new position.");
    }
    commit(project, "Insert Video Analyzer clip", () => editor.createInsertProjectItemAction(result.subclip, position, 0, 0, false));
    const subclipId = await itemId(result.subclip);
    const items = await videoTrack.getTrackItems(getApi().Constants.TrackItemType.CLIP, false);
    const tolerance = 1 / (binding.source_fps || 24) + 0.00001;
    let confirmed = false;
    for (const item of items) {
      if (await itemId(await item.getProjectItem()) !== subclipId) continue;
      const start = await item.getStartTime(), duration = await item.getDuration();
      if (Math.abs(start.seconds - position.seconds) <= tolerance && Math.abs(duration.seconds - (result.end - result.start)) <= tolerance) {
        confirmed = true; break;
      }
    }
    if (!confirmed) fail("TIMELINE_VERIFY_FAILED", "Premiere did not confirm the inserted clip's position and duration. Inspect the timeline or Undo before retrying.");
    return { inserted: true, name: result.name, position: position.seconds, start: result.start, end: result.end };
  }
  return {
    getProjectIdentity, getSelection, getSelectedMedia, resolveBinding, openSource, addMarkers, syncMetadata, importSelection, makeSubclip, insertClip,
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
  resolveBinding: (...args) => defaultCall("resolveBinding", args),
  openSource: (...args) => defaultCall("openSource", args),
  openSourceAt: (...args) => defaultCall("openSourceAt", args),
  addMarkers: (...args) => defaultCall("addMarkers", args),
  addSourceMarkers: (...args) => defaultCall("addSourceMarkers", args),
  importSelection: (...args) => defaultCall("importSelection", args),
  syncMetadata: (...args) => defaultCall("syncMetadata", args),
  makeSubclip: (...args) => defaultCall("makeSubclip", args),
  insertClip: (...args) => defaultCall("insertClip", args),
};
