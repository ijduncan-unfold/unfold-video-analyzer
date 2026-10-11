const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { createPanel, normalizeServer } = require("../panel.js");

const TOKEN = "t".repeat(43);
const clone = value => JSON.parse(JSON.stringify(value));
const flush = () => new Promise(resolve => setImmediate(resolve));

class Element {
  constructor(tagName = "div", id = "") {
    this.tagName = tagName;
    this.id = id;
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this.className = "";
    this.value = "";
    this.disabled = false;
    this._text = "";
    this.classList = {
      toggle: (name, force) => {
        const classes = new Set(this.className.split(/\s+/).filter(Boolean));
        const enabled = force === undefined ? !classes.has(name) : force;
        if (enabled) classes.add(name); else classes.delete(name);
        this.className = Array.from(classes).join(" ");
        return enabled;
      },
    };
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
  set innerHTML(value) { throw new Error("Untrusted content must not use innerHTML: " + value); }
  appendChild(child) { this.children.push(child); return child; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] === undefined ? null : this.attributes[name]; }
  addEventListener(event, callback) { (this.listeners[event] || (this.listeners[event] = [])).push(callback); }
  focus() { this.focused = true; }
  async fire(event, values = {}) {
    if (event === "click" && this.disabled) return;
    const payload = Object.assign({ target: this, key: "", preventDefault() {} }, values);
    await Promise.all((this.listeners[event] || []).map(callback => callback(payload)));
    await flush();
  }
}

function mockDocument() {
  const html = fs.readFileSync(path.join(__dirname, "../index.html"), "utf8");
  const elements = {};
  for (const match of html.matchAll(/<([\w-]+)\b([^>]*?)\bid="([^"]+)"([^>]*)>/g)) {
    const element = new Element(match[1], match[3]);
    const attributes = match[2] + match[4];
    for (const attr of attributes.matchAll(/([\w-]+)="([^"]*)"/g)) {
      element.setAttribute(attr[1], attr[2]);
      if (attr[1] === "class") element.className = attr[2];
      if (attr[1] === "value") element.value = attr[2];
    }
    if (/\bdisabled\b/.test(attributes)) element.disabled = true;
    elements[element.id] = element;
  }
  elements.mode.value = "flash_only";
  return {
    elements,
    getElementById(id) { assert.ok(elements[id], "Unknown UI element " + id); return elements[id]; },
    createElement(tag) { return new Element(tag); },
  };
}

function shot(number, text, extra = {}) {
  return Object.assign({ shot_number: number, start_seconds: (number - 1) * 3, end_seconds: number * 3,
    visual_description: text, shot_type: "close-up", camera_movement: "static", mood: "quiet", tags: [] }, extra);
}

function asset(id = "clip-a", project = "project-a", item = "item-a", name = "Desert footage") {
  return {
    job_id: id, filename: "desert.mp4", metadata: { title: name }, status: "complete", shot_count: 3,
    technical: { duration_seconds: 1000, frame_rate: 24, has_audio: true }, flash: {},
    editor_sources: [{ host: "premiere", project_id: project, item_id: item, media_path: "C:\\footage\\" + id + ".mp4", name }],
    transcript: [
      { start_seconds: 2, end_seconds: 4, text: "Look at that circle.", speaker: "Alex" },
      { start_time: "00:10.250", end_time: "00:12.500", text: "Meet me in the center.", speaker: "Rae" },
      { start_seconds: 18, end_seconds: 20, text: "Keep moving.", speaker: "Alex" },
    ],
  };
}

function persistentStorage() {
  const files = new Map();
  const secrets = new Map();
  const saves = [];
  const file = name => ({
    async read() { if (!files.has(name)) throw new Error("Missing file"); return files.get(name); },
    async write(content) { files.set(name, content); },
  });
  const storage = {
    secureStorage: {
      async getItem(key) { if (!secrets.has(key)) throw new Error("Missing token"); return secrets.get(key); },
      async setItem(key, value) { secrets.set(key, Uint8Array.from(Buffer.from(value))); },
      async removeItem(key) { secrets.delete(key); },
    },
    localFileSystem: {
      async getDataFolder() { return {
        async getEntry(name) { if (!files.has(name)) throw new Error("Missing file"); return file(name); },
        async createFile(name) { return file(name); },
      }; },
      async getFileForSaving(filename, options) {
        const saved = { filename, options, content: null };
        saves.push(saved);
        return { async write(content) { saved.content = content; } };
      },
    },
  };
  return { files, secrets, saves, storage };
}

function harness(options = {}) {
  const document = mockDocument();
  const stored = options.stored || persistentStorage();
  const connection = options.connection || { server: options.platform === 'darwin' ? 'https://localhost:8443' : 'http://127.0.0.1:8000', token: TOKEN, instance_id: '3cbdf73c-344e-4a7f-8988-869876b413e7' };
  const service = options.service || {
    assets: [asset()],
    shots: [shot(1, "A circular scope in a dark frame.", { tags: ["circle"] }),
      shot(2, "A person stands in the center.", { tags: ["center"] }),
      shot(3, "A desert dam beneath a blue sky.", { tags: ["dam"] })],
  };
  const current = { project: "project-a", clips: [{ item_id: "item-a", media_path: "C:\\footage\\clip-a.mp4", name: "Desert footage", source_fps: 24 }] };
  const requests = [];
  const opens = [];
  const markers = [];
  const metadataSyncs = [];
  const browser = [];
  const timers = new Map();
  let nextTimer = 0;
  let interceptor;
  const host = {
    async getSelection() {
      return { project_id: current.project, project_name: current.project === "project-a" ? "Film A" : "Film B",
        project_path: "C:\\edits\\film.prproj", clips: clone(current.clips), warnings: [], capabilities: { source_seek: true, source_markers: true } };
    },
    async openSource(binding, seconds) {
      if (binding.project_id !== current.project) throw new Error("The active Premiere project changed. Refresh the selection.");
      opens.push({ binding: clone(binding), seconds });
      return { opened: true };
    },
    async addMarkers(binding, cues) {
      if (binding.project_id !== current.project) throw new Error("The active Premiere project changed. Refresh the selection.");
      markers.push({ binding: clone(binding), cues: clone(cues) });
      return { added: cues.length, skipped: 0 };
    },
    async syncMetadata(binding, mapping) { metadataSyncs.push({ binding: clone(binding), mapping: clone(mapping) }); return { updated: 3, preserved: 1 }; },
  };
  function json(data, status = 200) { return { ok: status >= 200 && status < 300, status, async json() { return clone(data); }, async text() { return String(data); } }; }
  async function fetch(url, init) {
    const parsed = new URL(url);
    const record = { url, pathname: parsed.pathname, query: parsed.searchParams, init: clone(init), body: init.body ? JSON.parse(init.body) : undefined };
    requests.push(record);
    if (interceptor) {
      const override = await interceptor(record);
      if (override !== undefined) return override;
    }
    const pathname = parsed.pathname.replace("/api/editor/premiere", "");
    if (pathname === "/health") return json({ status: "ok", google_configured: true });
    if (pathname === "/assets") {
      const assets = service.assets.filter(item => item.editor_sources.some(source => source.project_id === parsed.searchParams.get("project_id")));
      return json({ assets, total: assets.length });
    }
    if (pathname === "/register") return json(service.assets.find(item => item.editor_sources.some(source => source.item_id === record.body.item_id && source.project_id === record.body.project_id)));
    const match = /^\/assets\/([^/]+)(\/[^/]+)?$/.exec(pathname);
    const item = match && service.assets.find(value => value.job_id === match[1]);
    if (!item) return json({ detail: "Unknown clip" }, 404);
    if (!match[2]) return json(item);
    if (match[2] === '/metadata') return json({ schema_version: '1.0', fields: [], record: { schema: 'video-analyzer.library', transcript: item.transcript } });
    if (match[2] === "/shots") {
      const q = (parsed.searchParams.get("q") || "").toLowerCase();
      const shots = service.shots.filter(value => JSON.stringify(value).toLowerCase().includes(q));
      return json({ shots: shots.slice(0, 500), total: shots.length });
    }
    if (match[2] === "/analyze") { item.status = "analyzing"; return json({ status: "analyzing", job_id: item.job_id }, 202); }
    if (match[2] === "/subtitles") return { ok: true, status: 200, async text() { return "1\n00:00:02,000 --> 00:00:04,000\nLook at that circle.\n\n2\n00:00:10,250 --> 00:00:12,500\nMeet me in the center.\n\n3\n00:00:18,000 --> 00:00:20,000\nKeep moving.\n"; } };
    throw new Error("Unmocked request " + url);
  }
  const panel = createPanel({ document, host, fetch, platform: options.platform,
    readConnection: options.readConnection || (async () => clone(connection)),
    uxp: { storage: stored.storage, shell: { async openExternal(url) { browser.push(url); return ""; } } },
    setTimeout(callback, delay) { const id = ++nextTimer; timers.set(id, { callback, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
  });
  return {
    panel, document, stored, service, current, requests, opens, markers, metadataSyncs, browser, timers, json, connection, host, fetch,
    el: id => document.getElementById(id),
    intercept(callback) { interceptor = callback; },
    async start() { await panel.init(); },
    async poll() {
      const entry = Array.from(timers).find(([, timer]) => timer.callback.name === "poll");
      assert.ok(entry, "There is a scheduled progress refresh");
      timers.delete(entry[0]);
      await entry[1].callback();
    },
  };
}

function descendants(element, predicate) {
  return element.children.flatMap(child => (predicate(child) ? [child] : []).concat(descendants(child, predicate)));
}

test('saved analysis refresh is free of analyze requests and uses full-app thumbnail URLs', async () => {
  const h = harness();
  h.service.shots[0].thumbnail_url = '/api/thumbnails/clip-a/1?v=run%3A1';
  h.service.shots[1].thumbnail_url = 'https://untrusted.example/thumbnail.jpg';
  await h.start();
  const images = descendants(h.el('results'), item => item.tagName === 'img');
  assert.equal(images.length, 1);
  assert.equal(images[0].getAttribute('src'), h.panel.getState().server + h.service.shots[0].thumbnail_url);
  h.service.shots[0].visual_description = 'Fresh saved description';
  await h.el('refresh-analysis').fire('click');
  assert.match(h.el('results').textContent, /Fresh saved description/);
  assert.equal(h.requests.filter(request => request.pathname.endsWith('/analyze')).length, 0);
});

test('timeline insertion preflights saved source and analysis; stale analysis blocks the host', async () => {
  const h = harness();
  const edits = [];
  h.host.insertClip = async (source, range) => { edits.push({ source, range }); return { position: 7 }; };
  h.host.makeSubclip = async () => ({ name: 'Native clip' });
  await h.start();
  const insert = () => descendants(h.el('results'), item => item.textContent === 'Add to timeline')[0];
  assert.equal(insert().disabled, false);
  await insert().fire('click');
  assert.equal(edits.length, 1);
  assert.equal(edits[0].source.item_id, 'item-a');
  assert.equal(edits[0].range.duration, 1000);
  assert.match(h.el('message').textContent, /Inserted/);
  h.service.assets[0].analysis_config = { started_at: 'changed-run' };
  await insert().fire('click');
  assert.equal(edits.length, 1);
  assert.match(h.el('message').textContent, /analysis changed/);
});

test('changed source restores readable saved results without enabling native edits', async () => {
  const old = asset('old');
  old.source_available = false;
  old.source_media_path = 'C:\\footage\\clip-a.mp4';
  old.editor_sources = [];
  const fresh = asset();
  fresh.source_media_path = old.source_media_path;
  fresh.status = 'ready'; fresh.shot_count = 0; fresh.transcript = [];
  const h = harness({ service: { assets: [fresh, old], shots: [shot(1, 'Saved description')] } });
  h.intercept(request => request.pathname.endsWith('/assets') && request.query.get('include_saved') === 'true'
    ? h.json({ assets: [fresh, old] }) : undefined);
  await h.start();
  assert.equal(h.panel.getState().asset.job_id, 'old');
  assert.match(h.el('results').textContent, /Saved description/);
  assert.equal(h.el('analyze').disabled, true);
  const actions = descendants(h.el('results'), item => item.className === 'clip-action');
  assert.equal(actions.every(item => item.disabled), true);
  await h.el('refresh-analysis').fire('click');
  assert.match(h.el('message').textContent, /timeline actions are disabled/);
  assert.equal(h.requests.some(request => request.pathname.endsWith('/analyze')), false);
});

test('empty results hide marker actions and reload explains that no results exist', async () => {
  const empty = asset();
  empty.shot_count = 0; empty.transcript = []; empty.status = 'ready'; empty.flash = null;
  const h = harness({ service: { assets: [empty], shots: [] } });
  await h.start();
  assert.match(h.el('add-markers').className, /hidden/);
  assert.equal(h.el('search').tagName, 'sp-textfield');
  assert.equal(h.el('search-label').textContent, 'Search clips');
  await h.el('refresh-analysis').fire('click');
  assert.match(h.el('message').textContent, /No saved clips/);
  assert.equal(h.requests.some(request => request.pathname.endsWith('/analyze')), false);
});

test('HTML script without a CommonJS module global keeps lifecycle callbacks synchronous', async t => {
  const vm = require('node:vm');
  const h = harness();
  let hooks;
  const timers = new Set();
  const modules = {
    uxp: { storage: h.stored.storage, entrypoints: { setup(value) {
      assert.equal(typeof value.plugin.create, 'function', 'Premiere requires plugin.create');
      hooks = value;
    } } },
    './host.js': h.host,
    './connection.js': { readConnection: async () => ({ ...h.connection }) },
    os: { platform: () => 'win32' },
  };
  const context = { document: h.document, fetch: h.fetch,
    require(name) { assert.ok(modules[name], 'Explicit UXP module: ' + name); return modules[name]; },
    setTimeout(fn, delay) { const id = setTimeout(fn, delay); timers.add(id); return id; },
    clearTimeout(id) { clearTimeout(id); timers.delete(id); },
  };
  t.after(() => { hooks?.plugin.destroy(); for (const id of timers) clearTimeout(id); });
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../panel.js'), 'utf8'), context);
  assert.ok(hooks);
  assert.equal(hooks.panels.videoAnalyzerPanel.create(), undefined);
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.match(h.el('message').textContent, /Connected automatically/);
  assert.equal(h.el('workspace').className.includes('hidden'), false);
  assert.equal(h.requests.filter(request => request.pathname.endsWith('/health')).length, 1);
  assert.equal(hooks.panels.videoAnalyzerPanel.show(), undefined);
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(h.requests.filter(request => request.pathname.endsWith('/health')).length, 1);
});

test("connects only to the authenticated local service and never analyzes while loading clips", async t => {
  const h = harness();
  h.stored.secrets.set('video-analyzer-premiere-token', Buffer.from('old-manual-token'));
  t.after(() => h.panel.destroy());
  await h.start();
  assert.equal(h.el("workspace").className.includes("hidden"), false);
  assert.equal(h.el("project-name").textContent, "Film A");
  assert.equal(h.el("results").children.length, 3);
  await h.panel.loadSelection();
  assert.ok(h.requests.some(request => request.pathname.endsWith("/register")));
  assert.equal(h.requests.filter(request => request.pathname.endsWith("/analyze")).length, 0);
  const registration = h.requests.find(request => request.pathname.endsWith("/register"));
  assert.equal(registration.body.media_path, "C:\\footage\\clip-a.mp4");
  assert.equal(registration.body.source_fps, 24);
  for (const request of h.requests) {
    assert.equal(request.init.headers.Authorization, "Bearer " + TOKEN);
    assert.equal(request.init.redirect, "error");
    assert.equal(new URL(request.url).host, "127.0.0.1:8000");
  }
  const serialized = Array.from(h.stored.files.values()).join("");
  assert.equal(serialized.includes(TOKEN), false);
  assert.equal(h.stored.secrets.size, 0);
  assert.equal(h.document.elements.token, undefined);
});

test('opening Premiere first retries discovery; disconnect stops retries and reconnection rereads credentials', async t => {
  let available = false;
  const connection = { server: 'http://127.0.0.1:8000', token: TOKEN };
  const h = harness({ readConnection: async () => { if (!available) throw new Error('Start Video Analyzer'); return { ...connection }; } });
  t.after(() => h.panel.destroy());
  await h.panel.init();
  assert.equal(h.panel.getState().connected, false);
  const retry = Array.from(h.timers).find(([, timer]) => timer.callback.name === 'reconnect');
  assert.ok(retry);
  available = true;
  h.timers.delete(retry[0]); // A fired native timeout is no longer scheduled.
  await retry[1].callback();
  assert.equal(h.panel.getState().connected, true);
  await h.panel.disconnect();
  assert.equal(h.timers.size, 0);
  connection.token = 'r'.repeat(43);
  await h.panel.connect();
  assert.equal(h.requests.at(-1).init.headers.Authorization, 'Bearer ' + connection.token);
  assert.equal(h.requests.filter(request => request.pathname.endsWith('/analyze')).length, 0);
});

test("rejects remote discovery and explains reconnection without leaking the credential", async t => {
  const h = harness();
  t.after(() => h.panel.destroy());
  h.connection.server = "http://127.0.0.1.evil.test:8000";
  await h.panel.init();
  await assert.rejects(h.panel.connect(), /only to the local analyzer/);
  assert.equal(h.requests.length, 0);
  h.connection.server = "http://127.0.0.1:8000";
  h.intercept(request => request.pathname.endsWith("/health") ? h.json({ detail: "Invalid token" }, 401) : undefined);
  await h.el("connect").fire("click");
  assert.match(h.el("message").textContent, /connection changed/i);
  assert.equal(h.el("message").textContent.includes(TOKEN), false);
  assert.equal(h.panel.getState().connected, false);
});

test('a stopped companion reconnects using fresh discovery without repeating analysis', async t => {
  const h = harness();
  t.after(() => h.panel.destroy());
  await h.start();
  h.intercept(request => { if (request.pathname.endsWith('/analyze')) throw new TypeError('Failed to fetch'); });
  await assert.rejects(h.panel.analyze(), /Failed to fetch/);
  assert.equal(h.panel.getState().connected, false);
  const retry = Array.from(h.timers).find(([, timer]) => timer.callback.name === 'reconnect');
  assert.ok(retry);
  h.connection.token = 'n'.repeat(43);
  h.intercept(() => undefined);
  h.timers.delete(retry[0]);
  await retry[1].callback();
  assert.equal(h.panel.getState().connected, true);
  assert.equal(h.requests.at(-1).init.headers.Authorization, 'Bearer ' + h.connection.token);
  assert.equal(h.requests.filter(request => request.pathname.endsWith('/analyze')).length, 1);
});

test('metadata button syncs a fresh complete server record after source preflight', async () => {
  const f = harness(); f.service.assets[0].analysis_config = { started_at: 'first' }; await f.start();
  assert.equal(f.el('sync-metadata').disabled, false);
  await f.panel.syncMetadata();
  assert.equal(f.metadataSyncs.length, 1);
  assert.equal(f.metadataSyncs[0].mapping.record.transcript[0].text, 'Look at that circle.');
  const request = f.requests.find(item => item.pathname.endsWith('/metadata'));
  assert.equal(request.query.get('analysis_session'), f.service.assets[0].analysis_config.started_at);
  assert.match(f.el('message').textContent, /editor fields preserved/);
  f.service.assets[0].analysis_config.started_at = 'replaced';
  await assert.rejects(f.panel.syncMetadata(), /analysis changed/);
  assert.equal(f.metadataSyncs.length, 1);
});
test("starts the explicitly chosen full analysis and receives incremental results", async t => {
  const h = harness();
  t.after(() => h.panel.destroy());
  await h.start();
  h.el("mode").value = "flash_pro";
  await h.panel.analyze();
  const request = h.requests.find(request => request.pathname.endsWith("/analyze"));
  assert.deepEqual(request.body, { mode: "flash_pro" });
  assert.equal(h.el("analyze").disabled, true);
  h.service.assets[0].analysis_progress = { stage: "analyzing_sections", completed_sections: 2, total_sections: 4, completed_shots: 4 };
  h.service.shots.push(shot(4, "Newly analyzed wheel at the center."));
  await h.poll();
  assert.equal(h.el("results").children.length, 4);
  assert.match(h.el("progress").textContent, /2\/4 sections/);
  assert.equal(h.requests.filter(request => request.pathname.endsWith("/analyze")).length, 1);
  h.service.assets[0].status = "complete";
  await h.poll();
  assert.equal(h.el("analyze").disabled, false);
  assert.match(h.el("progress").textContent, /complete/);
});

test("search reveals safe literal highlights and result clicks navigate native source time", async t => {
  const h = harness();
  t.after(() => h.panel.destroy());
  h.service.shots[1].visual_description = "A person centered near <img src=x onerror=bad()>";
  await h.start();
  await h.panel.search("center");
  assert.equal(h.el("results").children.length, 1);
  const result = h.el("results").children[0];
  assert.ok(result.textContent.includes("<img src=x onerror=bad()>"));
  const highlights = descendants(result, value => value.className === "highlight");
  assert.ok(highlights.some(value => value.textContent.toLowerCase() === "center"));
  assert.equal(descendants(result, value => value.tagName === "img").length, 0);
  await result.fire("click");
  assert.equal(h.opens.length, 1);
  assert.equal(h.opens[0].seconds, 3);
  assert.equal(h.opens[0].binding.item_id, "item-a");
  assert.equal(h.opens[0].binding.project_id, "project-a");
});

test("markers include only visible valid results and remain bounded for long films", async t => {
  const h = harness();
  t.after(() => h.panel.destroy());
  h.service.shots = Array.from({ length: 120 }, (_, i) => shot(i + 1, "Frame " + i));
  h.service.shots.push(shot(121, "Invalid end", { end_seconds: -1 }));
  await h.start();
  assert.equal(h.el("results").children.length, 100);
  await h.panel.addMarkers();
  assert.equal(h.markers[0].cues.length, 100);
  assert.equal(h.markers[0].cues[99].start, 297);
  await h.el("show-more").fire("click");
  await h.panel.addMarkers();
  assert.equal(h.markers[1].cues.length, 120);
  assert.equal(h.markers[1].cues.some(cue => cue.end < cue.start), false);
  await h.panel.setTab("transcript");
  await h.panel.search("center");
  await h.panel.addMarkers();
  assert.equal(h.markers[2].cues.length, 1);
  assert.equal(h.markers[2].cues[0].start, 10.25);
  assert.match(h.markers[2].cues[0].comment, /Meet me in the center/);
});

test("transcript navigation skips malformed timing and SRT exports all saved lines despite filtering", async t => {
  const h = harness();
  t.after(() => h.panel.destroy());
  h.service.assets[0].transcript.push({ start_seconds: "invalid", start_time: "00:02", end_seconds: 5, text: "Invalid cue" });
  h.service.assets[0].transcript.push({ start_seconds: 999, end_seconds: 1001, text: "Out of source bounds" });
  await h.start();
  await h.panel.setTab("transcript");
  assert.equal(h.el("results").children.length, 3);
  await h.panel.search("center");
  assert.equal(h.el("results").children.length, 1);
  await h.el("results").children[0].fire("click");
  assert.equal(h.opens[0].seconds, 10.25);
  await h.panel.exportSubtitles();
  assert.equal(h.stored.saves.length, 1);
  assert.equal(h.stored.saves[0].filename, "Desert footage_subtitles.srt");
  assert.match(h.stored.saves[0].content, /Look at that circle/);
  assert.match(h.stored.saves[0].content, /Keep moving/);
  const request = h.requests.find(value => value.pathname.endsWith("/subtitles"));
  assert.equal(request.query.get("q"), null);
});

test("an older search response cannot overwrite newer matches or create stale markers", async t => {
  const h = harness();
  t.after(() => h.panel.destroy());
  await h.start();
  let release;
  h.intercept(request => {
    if (request.pathname.endsWith("/shots") && request.query.get("q") === "circle") {
      return new Promise(resolve => { release = () => resolve(h.json({ shots: [h.service.shots[0]], total: 1 })); });
    }
  });
  const oldSearch = h.panel.search("circle");
  await flush();
  assert.equal(h.el("add-markers").disabled, true);
  await h.panel.addMarkers();
  assert.equal(h.markers.length, 0);
  await h.panel.search("center");
  release();
  await oldSearch;
  assert.equal(h.el("results").children.length, 1);
  assert.match(h.el("results").textContent, /person stands in the center/);
  await h.panel.addMarkers();
  assert.equal(h.markers[0].cues[0].start, 3);
});

test("switching projects discards stale responses and prevents markers against the wrong project", async t => {
  const h = harness();
  t.after(() => h.panel.destroy());
  h.service.assets.push(asset("clip-b", "project-b", "item-b", "Film B footage"));
  await h.start();
  let release;
  h.intercept(request => {
    if (request.pathname.endsWith("/assets") && request.query.get("project_id") === "project-a") {
      return new Promise(resolve => { release = () => resolve(h.json({ assets: [h.service.assets[0]], total: 1 })); });
    }
  });
  const oldRefresh = h.panel.refreshProject();
  await flush();
  h.current.project = "project-b";
  h.current.clips = [];
  await h.el("add-markers").fire("click");
  assert.equal(h.markers.length, 0);
  assert.match(h.el("message").textContent, /project changed/);
  await h.panel.refreshProject();
  release();
  await oldRefresh;
  assert.equal(h.el("project-name").textContent, "Film B");
  assert.match(h.el("asset-toggle").textContent, /Film B footage/);
  await h.panel.addMarkers();
  assert.equal(h.markers[0].binding.project_id, "project-b");
});

test("disconnect during a pending response cannot restore the clip or restart polling", async t => {
  const h = harness();
  t.after(() => h.panel.destroy());
  await h.start();
  let release;
  h.intercept(request => {
    if (request.pathname.endsWith("/assets/clip-a")) {
      return new Promise(resolve => { release = () => resolve(h.json(h.service.assets[0])); });
    }
  });
  const pending = h.panel.selectAsset("clip-a");
  await flush();
  await h.panel.disconnect();
  release();
  await pending;
  assert.equal(h.panel.getState().connected, false);
  assert.equal(h.panel.getState().asset, null);
  assert.equal(h.el("workspace").className.includes("hidden"), true);
  assert.equal(h.stored.secrets.size, 0);
  assert.equal(h.timers.size, 0);
});

test("reopening restores project clip, analysis mode, tab, and dialogue search without rerunning analysis", async t => {
  const stored = persistentStorage();
  const first = harness({ stored });
  t.after(() => first.panel.destroy());
  await first.start();
  first.el("mode").value = "flash_pro";
  await first.panel.setTab("transcript");
  await first.panel.search("center");
  first.panel.destroy();
  const reopened = harness({ stored, service: first.service });
  t.after(() => reopened.panel.destroy());
  await reopened.panel.init();
  assert.equal(reopened.panel.getState().connected, true);
  assert.equal(reopened.el("mode").value, "flash_pro");
  assert.equal(reopened.el("transcript-tab").getAttribute("aria-selected"), "true");
  assert.equal(reopened.el("search").value, "center");
  assert.equal(reopened.el("results").children.length, 1);
  assert.equal(reopened.requests.filter(request => request.pathname.endsWith("/analyze")).length, 0);
  assert.match(reopened.el("asset-toggle").textContent, /Desert footage/);
});

test("empty project stays usable and browser opening never puts the panel token in a URL", async t => {
  const h = harness({ service: { assets: [], shots: [] } });
  t.after(() => h.panel.destroy());
  await h.start();
  assert.equal(h.el("analyze").disabled, true);
  assert.equal(h.el("add-markers").disabled, true);
  assert.match(h.el("results").textContent, /Load a clip/);
  assert.equal(h.el("load-selection").disabled, false);
  await h.el("open-library").fire("click");
  assert.deepEqual(h.browser, ["http://127.0.0.1:8000/"]);
});

test("changed media blocks transcript navigation before any native action and stays blocked until reload", async t => {
  const h = harness();
  t.after(() => h.panel.destroy());
  await h.start();
  await h.panel.setTab("transcript");
  h.intercept(request => request.pathname.endsWith("/assets/clip-a") ? h.json({ detail: "The source file changed." }, 409) : undefined);
  await h.el("results").children[0].fire("click");
  assert.equal(h.opens.length, 0);
  assert.equal(h.el("add-markers").disabled, true);
  assert.ok(h.el("results").children.every(result => result.disabled));
  await h.panel.addMarkers();
  assert.equal(h.markers.length, 0);
  h.intercept(undefined);
  await h.poll();
  assert.equal(h.el("add-markers").disabled, true, "A successful poll alone must not silently clear the warning");
  await h.panel.selectAsset("clip-a");
  assert.equal(h.el("add-markers").disabled, false);
  await h.el("results").children[0].fire("click");
  assert.equal(h.opens.length, 1);
});

test("a source conflict discovered by progress polling disables cached native results", async t => {
  const h = harness();
  t.after(() => h.panel.destroy());
  await h.start();
  h.intercept(request => request.pathname.endsWith("/assets/clip-a") ? h.json({ detail: "The original media is offline." }, 409) : undefined);
  await h.poll();
  assert.equal(h.el("add-markers").disabled, true);
  assert.match(h.el("message").textContent, /offline/);
  assert.ok(h.el("results").children.every(result => result.disabled));
  await h.el("results").children[0].fire("click");
  assert.equal(h.opens.length, 0);
});

test("marker preflight rejects a replaced analysis session and relinked item binding", async t => {
  for (const changed of ["session", "binding"]) {
    const h = harness();
    t.after(() => h.panel.destroy());
    h.service.assets[0].analysis_config = { started_at: "2026-10-10T12:00:00Z" };
    await h.start();
    if (changed === "session") h.service.assets[0].analysis_config.started_at = "2026-10-10T12:30:00Z";
    else h.service.assets[0].editor_sources[0].media_path = "C:\\footage\\replacement.mp4";
    await assert.rejects(h.panel.addMarkers(), /source binding or analysis changed/);
    assert.equal(h.markers.length, 0);
    assert.equal(h.el("add-markers").disabled, true);
  }
});

test("disconnect or query change while native preflight is pending prevents host mutations", async t => {
  for (const change of ["disconnect", "query"]) {
    const h = harness();
    t.after(() => h.panel.destroy());
    await h.start();
    let release;
    h.intercept(request => {
      if (request.pathname.endsWith("/assets/clip-a")) return new Promise(resolve => { release = () => resolve(h.json(h.service.assets[0])); });
    });
    const adding = h.panel.addMarkers();
    const rejection = assert.rejects(adding, /selected results changed/);
    await flush();
    if (change === "disconnect") await h.panel.disconnect();
    else await h.panel.search("center");
    release();
    await rejection;
    assert.equal(h.markers.length, 0);
  }
});

test("new Mac profiles use authenticated local HTTPS and open the secure analyzer", async t => {
  const h = harness({ platform: "darwin" });
  t.after(() => h.panel.destroy());
  await h.start();
  assert.equal(h.el("server").value, "https://localhost:8443");
  assert.match(h.el("connection-hint").textContent, /certificate setup/);
  for (const request of h.requests) {
    assert.equal(new URL(request.url).origin, "https://localhost:8443");
    assert.equal(request.init.headers.Authorization, "Bearer " + TOKEN);
  }
  await h.el("open-library").fire("click");
  assert.deepEqual(h.browser, ["https://localhost:8443/"]);
});

test("Mac discovery overrides obsolete endpoint preferences on reopening", async t => {
  const stored = persistentStorage();
  const first = harness({ platform: "darwin", stored });
  t.after(() => first.panel.destroy());
  await first.panel.init();
  first.connection.server = "https://127.0.0.1:8443";
  await first.panel.connect();
  first.panel.destroy();
  const second = harness({ platform: "darwin", stored });
  t.after(() => second.panel.destroy());
  await second.panel.init();
  assert.equal(second.el("server").value, "https://localhost:8443");
  assert.equal(second.panel.getState().connected, true);
  assert.ok(second.requests.every(request => new URL(request.url).origin === "https://localhost:8443"));
});

test("an insecure Mac discovery cannot send credentials before HTTPS setup", async t => {
  const stored = persistentStorage();
  stored.files.set("premiere-panel.json", JSON.stringify({ server: "http://localhost:8000", mode: "flash_only", projects: {} }));
  const h = harness({ platform: "darwin", stored });
  h.connection.server = 'http://localhost:8000';
  t.after(() => h.panel.destroy());
  await h.panel.init();
  assert.equal(h.el("server").value, "http://localhost:8000");
  await h.el("connect").fire("click");
  assert.equal(h.requests.length, 0);
  assert.match(h.el("message").textContent, /Mac requires HTTPS/);
  assert.match(h.el("message").textContent, /localhost:8443/);
});

test("TLS failures give certificate guidance without disabling verification or falling back to HTTP", async t => {
  const h = harness({ platform: "darwin" });
  t.after(() => h.panel.destroy());
  h.intercept(() => { throw new TypeError("Certificate trust failure"); });
  await h.panel.init();
  h.requests.length = 0;
  await h.el("connect").fire("click");
  assert.equal(h.panel.getState().connected, false);
  assert.match(h.el("message").textContent, /certificate setup/);
  assert.equal(h.requests.length, 1);
  assert.equal(new URL(h.requests[0].url).origin, "https://localhost:8443");
  assert.equal(h.requests[0].init.rejectUnauthorized, undefined);
});

test("endpoint validation and manifest allow exactly the local HTTP and HTTPS transport pairs", () => {
  const endpoints = ["http://127.0.0.1:8000", "http://localhost:8000", "https://localhost:8443", "https://127.0.0.1:8443"];
  for (const endpoint of endpoints) assert.equal(normalizeServer(endpoint + "/"), endpoint);
  for (const endpoint of ["https://localhost:8000", "http://localhost:8443", "https://localhost:8444",
    "https://localhost.evil.test:8443", "https://localhost@evil.test:8443", "https://127.0.0.1:8443/redirect",
    "https://127.0.0.1:8443?token=leak", "https://[::1]:8443", "http://0.0.0.0:8000"]) {
    assert.throws(() => normalizeServer(endpoint), /only to the local analyzer/);
  }
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, "../manifest.json"), "utf8"));
  // Discovery canonicalizes IP loopback to localhost for UXP's permission matcher.
  assert.deepEqual(manifest.requiredPermissions.network.domains.sort(), ["http://localhost:8000", "https://localhost:8443"].sort());
  assert.deepEqual(manifest.requiredPermissions.launchProcess.schemes.sort(), ["http", "https"]);
});
