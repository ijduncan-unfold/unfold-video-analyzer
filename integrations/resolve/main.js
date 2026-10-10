"use strict";
const fs = require("node:fs/promises");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { createHost } = require("./host");
const { createService, ACTIONS } = require("./service");
const PLUGIN_ID = "com.ijduncan.videoanalyzer";
const IPC_CHANNEL = "video-analyzer:resolve:action";

// Exported pure checks allow verification without Electron or the licensed SDK.
function trustedSender(event, window, indexURL) {
  return !!(window && !window.isDestroyed() && event.sender === window.webContents && event.senderFrame === window.webContents.mainFrame && event.senderFrame.url === indexURL);
}
function supportedVersion(value) {
  return Array.isArray(value) && value.length >= 2 && Number.isInteger(value[0]) && Number.isInteger(value[1]) && (value[0] > 20 || value[0] === 20 && value[1] >= 1);
}
function windowOptions(preload) {
  return { width: 600, height: 880, minWidth: 360, minHeight: 480, title: "Video Analyzer", backgroundColor: "#111820", autoHideMenuBar: true,
    webPreferences: { preload, nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, allowRunningInsecureContent: false, webviewTag: false } };
}
async function start(electron, workflow) {
  const { app, BrowserWindow, ipcMain, net, clipboard, shell, dialog } = electron;
  let window, service, initialized = false, callbackRegistered = false, cleaned = false;
  function cleanup() {
    if (cleaned) return; cleaned = true;
    if (service) service.destroy();
    ipcMain.removeHandler(IPC_CHANNEL);
    if (initialized) {
      if (callbackRegistered && typeof workflow.DeregisterCallback === "function") { try { workflow.DeregisterCallback("ResolveQuit"); } catch (_) {} }
      try { workflow.CleanUp(); } catch (_) { /* Resolve may already be shutting down. */ }
    }
  }
  app.once("before-quit", cleanup);
  app.on("window-all-closed", () => app.quit());
  await app.whenReady();
  try {
    if (!workflow || typeof workflow.InitializePromise !== "function" || typeof workflow.GetResolvePromise !== "function" || typeof workflow.CleanUp !== "function") throw new Error("Install this plugin with the Workflow Integration SDK from Resolve Studio 20.1 or later.");
    if (typeof workflow.SetAPITimeout !== "function" || workflow.SetAPITimeout(15) !== true) throw new Error("Resolve's Workflow Integration SDK could not enable native API timeouts.");
    if (await workflow.InitializePromise(PLUGIN_ID) !== true) throw new Error("Resolve Studio could not initialize Video Analyzer. Close the plugin and reopen it from Workspace → Workflow Integrations.");
    initialized = true;
    const resolve = await workflow.GetResolvePromise();
    if (!resolve || typeof resolve.GetVersion !== "function" || !supportedVersion(await resolve.GetVersion())) throw new Error("Video Analyzer requires DaVinci Resolve Studio 20.1 or later.");
    if (typeof workflow.RegisterCallback !== "function" || workflow.RegisterCallback("ResolveQuit", () => app.quit()) !== true) throw new Error("Resolve could not register the plugin shutdown callback.");
    callbackRegistered = true;
    const folder = path.join(app.getPath("appData"), "VideoAnalyzer", "Resolve");
    const prefs = path.join(folder, "preferences.json");
    service = createService({ platform: process.platform, host: createHost(resolve), fetch: (...args) => net.fetch(...args), clipboard,
      preferences: { read: () => fs.readFile(prefs, "utf8"), async write(value) { await fs.mkdir(folder, { recursive: true, mode: 0o700 }); const temporary = prefs + ".tmp"; await fs.writeFile(temporary, value, { mode: 0o600 }); await fs.rename(temporary, prefs); } },
      async saveSubtitle(name, content) {
        // Filename is generated from the server asset. Renderer never supplies
        // a disk path or content; only the user-controlled save dialog selects it.
        const result = await dialog.showSaveDialog(window, { title: "Export subtitles", defaultPath: name, filters: [{ name: "SubRip subtitles", extensions: ["srt"] }] });
        if (result.canceled || !result.filePath) return false;
        await fs.writeFile(result.filePath, content, "utf8"); return true;
      },
      async openBrowser(url) { await shell.openExternal(url); },
    });
    const indexPath = path.join(__dirname, "index.html"), indexURL = pathToFileURL(indexPath).href;
    window = new BrowserWindow(windowOptions(path.join(__dirname, "preload.js")));
    window.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    window.webContents.session.setPermissionCheckHandler(() => false);
    window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    window.webContents.on("will-attach-webview", event => event.preventDefault());
    window.webContents.on("will-navigate", (event, url) => { if (url !== indexURL) event.preventDefault(); });
    ipcMain.handle(IPC_CHANNEL, async (event, action, payload) => {
      if (!trustedSender(event, window, indexURL) || !ACTIONS.includes(action)) return { ok: false, error: { code: "UNTRUSTED_ACTION", message: "This plugin action is not available." } };
      try { return { ok: true, state: await service.invoke(action, payload) }; }
      catch (error) { return { ok: false, error: { code: typeof error.code === "string" ? error.code : "ACTION_FAILED", message: error.message || "The plugin action failed." } }; }
    });
    await window.loadFile(indexPath);
    return { cleanup, window, service };
  } catch (error) {
    cleanup();
    await dialog.showMessageBox({ type: "error", title: "Video Analyzer", message: error.message || "The Resolve plugin could not start.", detail: "Use the installer helper to copy the SDK supplied by your local Resolve Studio installation. Do not use another Resolve version's native bridge." });
    app.quit(); return null;
  }
}
module.exports = { start, trustedSender, supportedVersion, windowOptions, PLUGIN_ID, IPC_CHANNEL };
if (require.main === module) {
  const electron = require("electron");
  let workflow;
  try { workflow = require(path.join(__dirname, "WorkflowIntegration.node")); }
  catch (_) { /* start presents an actionable SDK error without native paths. */ }
  start(electron, workflow).catch(() => electron.app.quit());
}
