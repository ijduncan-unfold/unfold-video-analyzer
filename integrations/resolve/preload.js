"use strict";
// Sandboxed preload can require only Electron; no Node modules or native SDK.
const { contextBridge, ipcRenderer } = require("electron");
const channel = "video-analyzer:resolve:action";
const api = {};
for (const name of ["bootstrap", "connect", "disconnect", "refresh", "loadSelection", "selectAsset", "setView", "analyze", "selectClip", "copyTimes", "addMarkers", "exportShots", "syncMetadata", "exportSubtitles", "openAnalyzer"]) {
  api[name] = payload => ipcRenderer.invoke(channel, name, payload);
}
contextBridge.exposeInMainWorld("videoAnalyzer", Object.freeze(api));
