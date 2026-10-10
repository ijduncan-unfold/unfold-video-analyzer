"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { start, trustedSender, supportedVersion, windowOptions, IPC_CHANNEL, PLUGIN_ID } = require("../main");
const helpers = require("../controller");
test("BrowserWindow isolates and sandboxes the renderer with no Node or webview", () => {
  const config = windowOptions("/plugin/preload.js");
  assert.equal(config.webPreferences.sandbox, true); assert.equal(config.webPreferences.contextIsolation, true); assert.equal(config.webPreferences.nodeIntegration, false);
  assert.equal(config.webPreferences.webSecurity, true); assert.equal(config.webPreferences.allowRunningInsecureContent, false); assert.equal(config.webPreferences.webviewTag, false);
});
test("IPC accepts only the plugin's own main frame at the exact local entry URL", () => {
  const mainFrame = { url: "file:///plugin/index.html" }, contents = { mainFrame }, window = { webContents: contents, isDestroyed: () => false };
  const event = { sender: contents, senderFrame: mainFrame };
  assert.equal(trustedSender(event, window, mainFrame.url), true);
  assert.equal(trustedSender({ ...event, sender: {} }, window, mainFrame.url), false);
  assert.equal(trustedSender({ ...event, senderFrame: { url: mainFrame.url } }, window, mainFrame.url), false);
  assert.equal(trustedSender(event, window, "file:///other.html"), false);
  assert.equal(trustedSender(event, { ...window, isDestroyed: () => true }, mainFrame.url), false);
});
test("version detection accepts Resolve Studio20.1+ only", () => { assert.equal(supportedVersion([20, 1, 0, 1, ""]), true); assert.equal(supportedVersion([21, 0, 0, 1, ""]), true); for (const version of [[20, 0], [19, 9], "20.1", null, ["20", "1"]]) assert.equal(supportedVersion(version), false); });
test("sandboxed preload exposes action methods, without SDK, IPC event or arbitrary request access", async () => {
  const invoked = [], exposed = {};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../preload.js"), "utf8"), { require(name) { assert.equal(name, "electron"); return { contextBridge: { exposeInMainWorld(name, api) { exposed[name] = api; } }, ipcRenderer: { invoke(channel, name, payload) { invoked.push([channel, name, payload]); return Promise.resolve({ ok: true }); } } }; } });
  assert.equal(Object.isFrozen(exposed.videoAnalyzer), true); assert.equal(exposed.videoAnalyzer.request, undefined); assert.equal(exposed.videoAnalyzer.resolve, undefined); assert.equal(exposed.videoAnalyzer.on, undefined);
  await exposed.videoAnalyzer.copyTimes({ revision: 1, index: 0 }); assert.deepEqual(invoked, [[IPC_CHANNEL, "copyTimes", { revision: 1, index: 0 }]]);
});
test("CSP blocks renderer network, external scripts, frames, forms and objects", () => {
  const html = fs.readFileSync(path.join(__dirname, "../index.html"), "utf8");
  for (const directive of ["default-src 'none'", "script-src 'self'", "connect-src 'none'", "frame-src 'none'", "form-action 'none'", "object-src 'none'"]) assert.ok(html.includes(directive));
  assert.equal(/<script[^>]+https?:/i.test(html), false); assert.equal(/\son\w+=/i.test(html), false);
});
test("main process lifecycle initializes Promise bridge, uses matching ID, and cleans once on quit", async () => {
  const events = {}, handlers = {}, calls = [];
  let instance;
  class Window {
    constructor(config) { calls.push(["window", config]); this.webContents = { mainFrame: { url: "" }, session: { setPermissionRequestHandler() {}, setPermissionCheckHandler() {} }, setWindowOpenHandler(fn) { this.open = fn; }, on() {} }; instance = this; }
    isDestroyed() { return false; }
    async loadFile(value) { this.webContents.mainFrame.url = require("node:url").pathToFileURL(value).href; calls.push(["load", value]); }
  }
  const electron = { app: { once(name, callback) { events[name] = callback; }, on(name, callback) { events[name] = callback; }, whenReady: async () => {}, getPath: () => "/user/appData", quit() { if (events["before-quit"]) events["before-quit"](); } }, BrowserWindow: Window,
    ipcMain: { handle(name, fn) { handlers[name] = fn; }, removeHandler(name) { delete handlers[name]; } }, net: { fetch() { throw new Error("Unexpected network"); } }, clipboard: {}, shell: {}, dialog: { async showMessageBox() { throw new Error("Unexpected startup error"); } } };
  let sdkInitialized = false;
  const workflow = { SetAPITimeout(value) { if (!sdkInitialized) throw new Error("SetAPITimeout: Not initialized, call Initialize(<pluginId>) first"); calls.push(["timeout", value]); return true; }, async InitializePromise(id) { calls.push(["init", id]); sdkInitialized = true; return true; }, async GetResolvePromise() { calls.push(["resolve"]); return { GetVersion: async () => [20, 1, 0, 1, ""] }; }, RegisterCallback(name, fn) { calls.push(["callback", name]); this.quit = fn; return true; }, DeregisterCallback(name) { calls.push(["deregister", name]); }, CleanUp() { calls.push(["cleanup"]); } };
  const result = await start(electron, workflow);
  assert.ok(result); assert.deepEqual(calls.find(call => call[0] === "init"), ["init", PLUGIN_ID]); assert.deepEqual(calls.find(call => call[0] === "timeout"), ["timeout", 15]);
  assert.ok(calls.findIndex(call => call[0] === "init") < calls.findIndex(call => call[0] === "timeout"));
  assert.deepEqual(instance.webContents.open(), { action: "deny" });
  const blocked = await handlers[IPC_CHANNEL]({ sender: {} }, "connect", { token: "secret" }); assert.equal(blocked.ok, false); assert.equal(blocked.error.code, "UNTRUSTED_ACTION");
  workflow.quit(); result.cleanup(); assert.equal(calls.filter(call => call[0] === "cleanup").length, 1); assert.equal(handlers[IPC_CHANNEL], undefined);
});
test("missing native SDK provides install guidance and never opens a renderer", async () => {
  const messages = []; let opened = false, quit = false;
  const app = { once() {}, on() {}, whenReady: async () => {}, quit() { quit = true; } };
  const result = await start({ app, BrowserWindow: class { constructor() { opened = true; } }, ipcMain: { removeHandler() {} }, dialog: { async showMessageBox(value) { messages.push(value); } } }, undefined);
  assert.equal(result, null); assert.equal(opened, false); assert.equal(quit, true); assert.match(messages[0].message, /Workflow Integration SDK/);
});
test("SDK timeout setup failures clean up an already initialized native interface", async () => {
  let initialized = false, cleaned = 0, timeoutCalled = false;
  const messages = [];
  const workflow = { async InitializePromise() { initialized = true; return true; }, GetResolvePromise() { throw new Error("Resolve API must not run after timeout setup failed"); },
    SetAPITimeout() { assert.equal(initialized, true); timeoutCalled = true; return false; }, CleanUp() { cleaned++; } };
  const app = { once() {}, on() {}, whenReady: async () => {}, quit() {} };
  const result = await start({ app, ipcMain: { removeHandler() {} }, dialog: { async showMessageBox(value) { messages.push(value); } } }, workflow);
  assert.equal(result, null); assert.equal(timeoutCalled, true); assert.equal(cleaned, 1); assert.match(messages[0].message, /could not enable native API timeouts/);
});
test("Electron's ESM-loaded CommonJS entry starts even when require.main is another module", () => {
  let started = 0, electronRequired = 0;
  const source = fs.readFileSync(path.join(__dirname, "../main.js"), "utf8");
  const module = { exports: {} };
  function fakeRequire(name) {
    if (name === "electron") {
      electronRequired++;
      return { app: { once() { started++; }, on() {}, whenReady() { return new Promise(() => {}); } } };
    }
    if (name.endsWith("WorkflowIntegration.node")) return {};
    if (name === "./host") return require("../host");
    if (name === "./service") return require("../service");
    return require(name);
  }
  fakeRequire.main = { exports: {} }; // Actual Electron default_app uses await import(entry).
  vm.runInNewContext(source, { module, require: fakeRequire, process: { versions: { electron: "40.0.0" }, type: "browser" }, __dirname: path.resolve(__dirname, "..") });
  assert.notEqual(fakeRequire.main, module);
  assert.equal(electronRequired, 1); assert.equal(started, 1);
});
test("regular Node imports and Electron renderer imports cannot start the native entry", () => {
  const source = fs.readFileSync(path.join(__dirname, "../main.js"), "utf8");
  for (const process of [{ versions: { node: "24.0.0" } }, { versions: { electron: "40.0.0" }, type: "renderer" }]) {
    const fakeRequire = name => { if (name === "electron" || name.endsWith("WorkflowIntegration.node")) throw new Error("Native entry must stay inert"); if (name === "./host") return require("../host"); if (name === "./service") return require("../service"); return require(name); };
    vm.runInNewContext(source, { module: { exports: {} }, require: fakeRequire, process, __dirname: path.resolve(__dirname, "..") });
  }
});
test("highlighting treats tags and dialogue as text, including markup-shaped queries", () => {
  const elements = [];
  const document = { createTextNode(value) { return { value }; }, createElement(name) { return { name, textContent: "" }; } };
  const parent = { appendChild(value) { elements.push(value); } };
  helpers.appendHighlight(document, parent, '<script>alert("center")</script> centered circle', "center");
  assert.equal(elements.filter(value => value.name === "mark").length, 2); assert.equal(elements.some(value => value.name === "script"), false);
  const literal = []; helpers.appendHighlight(document, { appendChild(value) { literal.push(value); } }, "[circle]", "[circle]"); assert.equal(literal[0].textContent, "[circle]");
});
test("transcript search includes speakers and filters invalid or out-of-duration cues", () => {
  const asset = { technical: { duration_seconds: 10 }, transcript: [{ start: "00:01.000", end: "00:02.000", text: "Hello", speaker: "Guard" }, { start: 9, end: 11, text: "Outside" }, { start: 4, end: 3, text: "Backwards" }] };
  const cues = helpers.cues(asset, [], "transcript", "guard hello"); assert.equal(cues.length, 1); assert.equal(cues[0].start, 1); assert.equal(cues[0].speaker, "Guard");
});
test("timing parser rejects malformed timestamps and nonfinite inputs", () => { for (const value of [NaN, Infinity, -1, "1:70", "00:60:00", "abc", "1;2"]) assert.equal(helpers.parseSeconds(value), null); assert.equal(helpers.parseSeconds("01:02:03.250"), 3723.25); });
test("SRT names cannot become file paths or Windows device names", () => { assert.equal(helpers.subtitleName({ filename: "../../CON.mp4" }).includes("/"), false); assert.equal(helpers.subtitleName({ filename: "CON.mp4" }), "video_subtitles.srt"); });
