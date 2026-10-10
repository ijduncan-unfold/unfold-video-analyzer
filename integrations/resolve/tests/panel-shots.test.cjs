"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const helpers = require("../controller");
function fixture(count = 2) {
  const all = [], elements = new Map(), calls = [], timers = [];
  function element(tag = "div") {
    const value = { tag, value: "", children: [], events: {}, disabled: false, checked: false, className: "", classList: { toggle() {} }, setAttribute() {}, appendChild(child) { this.children.push(child); }, addEventListener(name, fn) { this.events[name] = fn; } };
    Object.defineProperty(value, "textContent", { get() { return this.text || ""; }, set(text) { this.text = text; this.children = []; } }); all.push(value); return value;
  }
  const html = fs.readFileSync(path.join(__dirname, "../index.html"), "utf8");
  for (const match of html.matchAll(/id="([^"]+)"/g)) elements.set(match[1], element());
  const document = { getElementById: id => elements.get(id), createElement: element, createTextNode: text => ({ text }), querySelectorAll(selector) {
    const live = []; function walk(node) { live.push(node); for (const child of node.children || []) walk(child); }
    walk(elements.get("results")); walk(elements.get("asset-list"));
    return live.filter(node => selector === ".shot-checkbox" ? node.className === "shot-checkbox" : node.tag === "button");
  } };
  let state = { connected: true, assets: [], asset: { job_id: "a", analysis_config: { started_at: "run" }, status: "completed" }, project: { id: "p", name: "Film" }, results: Array.from({ length: count }, (_, i) => ({ shot_number: i + 1, start: i * 2, end: i * 2 + 2, name: "Shot " + (i + 1), text: "Shot description" })), queries: { shots: "", transcript: "" }, tab: "shots", mode: "flash_only", revision: 1, total: count, message: "Connected" };
  const bridge = Object.fromEntries(["bootstrap", "refresh", "exportShots", "setView"].map(name => [name, async payload => { calls.push([name, payload]); if (name === "setView") state = { ...state, tab: payload.tab }; return { ok: true, state: structuredClone(state) }; }]));
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../panel.js"), "utf8"), { document, window: { videoAnalyzer: bridge, ResolvePanelHelpers: helpers, addEventListener() {} }, setTimeout(callback) { const timer = { callback }; timers.push(timer); return timer; }, clearTimeout() {} });
  return { elements, calls, timers, checkboxes: () => document.querySelectorAll(".shot-checkbox"), setState(value) { state = { ...state, ...value }; } };
}
const tick = () => new Promise(resolve => setImmediate(resolve));
test("selected shots survive polling and export current revision and indices, without renderer metadata", async () => {
  const f = fixture(); await tick(); const first = f.checkboxes()[0]; first.checked = true; first.events.change();
  assert.equal(f.elements.get("selection-count").textContent, "1 selected");
  f.setState({ revision: 2 }); await f.timers.at(-1).callback(); assert.equal(f.checkboxes()[0].checked, true);
  await f.elements.get("append-shots").events.click();
  assert.deepEqual(JSON.parse(JSON.stringify(f.calls.at(-1))), ["exportShots", { revision: 2, indices: [0], destination: "current" }]);
  f.setState({ asset: { job_id: "a", analysis_config: { started_at: "new-analysis" } } }); await f.timers.at(-1).callback();
  assert.equal(f.elements.get("selection-count").textContent, "0 selected"); assert.equal(f.elements.get("append-shots").disabled, true);
});
test("select shown respects the fifty-shot limit and dialogue hides shot export controls", async () => {
  const f = fixture(51); await tick(); const select = f.elements.get("select-shown"); select.checked = true; select.events.change();
  assert.equal(f.elements.get("selection-count").textContent, "50 selected");
  f.elements.get("show-more").events.click(); assert.equal(select.disabled, true); assert.equal(f.checkboxes()[50].disabled, true); assert.equal(f.checkboxes()[0].disabled, false);
  await f.elements.get("selects-timeline").events.click(); assert.equal(f.calls.at(-1)[1].indices.length, 50); assert.equal(f.calls.at(-1)[1].destination, "new");
  await f.elements.get("transcript-tab").events.click(); assert.equal(f.elements.get("shot-tools").hidden, true); assert.equal(f.elements.get("selection-count").textContent, "0 selected");
});
