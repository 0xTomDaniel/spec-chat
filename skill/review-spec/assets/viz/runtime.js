// spec-chat runtime v0.1 — hydrates semantic islands and mounts the annotation layer.
// Transports: FSA (file://, primary) | HTTP review-serve (http(s)://, secondary).
// Same spools, same event schema either way. See DESIGN.md.

const SPEC_FILE = decodeURIComponent(location.pathname.split('/').pop());
const REVIEW_DIRNAME = SPEC_FILE + '.review';
const VENDOR = { echarts: new URL('./vendor/echarts-5.5.1.min.js', import.meta.url).href };

/* ---------------- error overlay (headless-debuggable) ---------------- */
window.addEventListener('error', e => overlay('error', e.message + ' @ ' + (e.filename || '').split('/').pop() + ':' + e.lineno));
window.addEventListener('unhandledrejection', e => overlay('rejection', String(e.reason)));
function overlay(kind, msg) {
  let el = document.getElementById('hx-errors');
  if (!el) {
    el = document.createElement('div');
    el.id = 'hx-errors';
    el.style.cssText = 'position:fixed;bottom:0;left:0;right:0;background:#8b1a1a;color:#fff;font:12px monospace;padding:6px 10px;z-index:9999;white-space:pre-wrap;';
    document.body.appendChild(el);
  }
  el.textContent += kind + ': ' + msg + '\n';
}

/* ---------------- state ---------------- */
const state = {
  transport: null,       // {mode, ready, listEvents, postEvent, specModified, label}
  events: [],            // [{actor, name, body}] sorted by name
  seenNames: new Set(),
  threads: new Map(),    // commentId -> {ev, replies:[], status, draft}
  commentMode: false,
  activeThread: null,
  composer: null,        // {anchorId, target, quote, holder}
  charts: new Map(),     // sectionAnchor -> {chart, config, el}
  specMtime: null,
};

/* ---------------- transports ---------------- */
function httpTransport() {
  const dir = location.pathname.replace(/^\//, '') + '.review';
  return {
    mode: 'http', label: 'review-serve',
    ready: Promise.resolve(true),
    async listEvents() {
      const r = await fetch('/api/events?dir=' + encodeURIComponent(dir));
      return r.json();
    },
    async postEvent(body) {
      await fetch('/api/events?dir=' + encodeURIComponent(dir) + '&actor=human', { method: 'POST', body: JSON.stringify(body) });
    },
    async specModified() {
      const r = await fetch(location.pathname, { method: 'HEAD' });
      return new Date(r.headers.get('Last-Modified') || 0).getTime();
    },
  };
}

function fsaTransport() {
  let root = null; // directory handle of the folder containing the spec
  const idb = () => new Promise((res, rej) => {
    const q = indexedDB.open('spec-chat', 1);
    q.onupgradeneeded = () => q.result.createObjectStore('handles');
    q.onsuccess = () => res(q.result);
    q.onerror = () => rej(q.error);
  });
  const store = async (mode, fn) => {
    const db = await idb();
    return new Promise((res, rej) => {
      const tx = db.transaction('handles', mode);
      const rq = fn(tx.objectStore('handles'));
      rq.onsuccess = () => res(rq.result);
      rq.onerror = () => rej(rq.error);
    });
  };
  const t = {
    mode: 'fsa', label: 'local folder', connected: false,
    async tryRestore() {
      try {
        const h = await store('readonly', s => s.get(location.href));
        if (!h) return 'none';
        const p = await h.queryPermission({ mode: 'readwrite' });
        if (p === 'granted') { root = h; t.connected = true; return 'granted'; }
        root = h;
        return 'prompt'; // needs a user gesture to re-request
      } catch { return 'none'; }
    },
    async connect() { // user gesture required
      if (root && !t.connected) {
        if (await root.requestPermission({ mode: 'readwrite' }) === 'granted') { t.connected = true; return; }
        root = null;
      }
      root = await window.showDirectoryPicker({ mode: 'readwrite' });
      await store('readwrite', s => s.put(root, location.href));
      t.connected = true;
    },
    async _dir(actor, create) {
      const rev = await root.getDirectoryHandle(REVIEW_DIRNAME, { create: true });
      return rev.getDirectoryHandle(actor, { create: !!create });
    },
    async listEvents() {
      if (!t.connected) return [];
      const out = [];
      for (const actor of ['human', 'agent']) {
        let d;
        try { d = await t._dir(actor); } catch { continue; }
        for await (const [name, h] of d.entries()) {
          if (h.kind !== 'file') continue;
          try { out.push({ actor, name, body: JSON.parse(await (await h.getFile()).text()) }); } catch {}
        }
      }
      out.sort((a, b) => a.name < b.name ? -1 : 1);
      return out;
    },
    async postEvent(body) {
      const d = await t._dir('human', true);
      const name = String(Date.now() * 1e6 + Math.floor(Math.random() * 1e6)) + '-' + (body.event || 'event') + '-' + (body.id || 'x') + '.json';
      const fh = await d.getFileHandle(name, { create: true });
      const w = await fh.createWritable();
      await w.write(JSON.stringify(body));
      await w.close();
    },
    async specModified() {
      if (!t.connected) return null;
      try { return (await (await root.getFileHandle(SPEC_FILE)).getFile()).lastModified; } catch { return null; }
    },
  };
  return t;
}

/* ---------------- islands ---------------- */
async function hydrateIslands() {
  const islands = [...document.querySelectorAll('script[type="application/spec+json"]')];
  if (!islands.length) return;
  if (islands.some(s => s.dataset.lib === 'echarts')) await loadScript(VENDOR.echarts);
  for (const s of islands) {
    const target = s.parentElement.querySelector('[data-render-target]');
    if (!target) continue;
    let config;
    try { config = JSON.parse(s.textContent); } catch (e) { overlay('island', 'bad JSON in ' + holderOf(s)?.dataset.anchor); continue; }
    if (s.dataset.lib === 'echarts') {
      target.style.minHeight = target.style.minHeight || '300px';
      if (config.animation === undefined) config.animation = false; // deterministic renders: screenshots, diffs, headless review
      const chart = window.echarts.init(target);
      // clickable axes/labels for universal anchoring
      for (const ax of ['xAxis', 'yAxis']) if (config[ax]) config[ax] = Object.assign({ triggerEvent: true }, config[ax]);
      chart.setOption(config);
      const anchor = holderOf(s)?.dataset.anchor;
      state.charts.set(anchor, { chart, config, el: target });
      chart.on('click', params => onChartClick(anchor, params));
      // blank canvas (no mark under cursor) anchors to the figure itself
      const holderEl = holderOf(s);
      chart.getZr().on('click', ev => {
        if (ev.target || !state.commentMode) return;
        const peers = [...holderEl.querySelectorAll('[data-render-target]')];
        openComposer(anchor, { type: 'element', key: 'figure[' + (peers.indexOf(target) + 1) + ']' }, 'figure: chart');
      });
      // hover ring for canvas marks CSS can't reach (axis labels, ticks)
      chart.on('mouseover', p => {
        if (!state.commentMode || p.componentType === 'series') return; // series get emphasis borders
        let r = null;
        try {
          r = p.event.target.getBoundingRect().clone();
          if (p.event.target.transform) r.applyTransform(p.event.target.transform);
        } catch { return; }
        let ring = target.querySelector('.hx-ring') || target.appendChild(Object.assign(document.createElement('div'), { className: 'hx-ring' }));
        ring.style.cssText += ';left:' + (r.x - 4) + 'px;top:' + (r.y - 4) + 'px;width:' + (r.width + 8) + 'px;height:' + (r.height + 8) + 'px;display:block';
      });
      chart.on('mouseout', () => { const ring = target.querySelector('.hx-ring'); if (ring) ring.style.display = 'none'; });
      new ResizeObserver(() => { chart.resize(); renderPins(); }).observe(target);
    }
  }
}
function loadScript(src) {
  return new Promise((res, rej) => {
    const el = document.createElement('script');
    el.src = src; el.onload = res; el.onerror = () => rej(new Error('failed to load ' + src));
    document.head.appendChild(el);
  });
}
const holderOf = el => el && el.closest('[data-anchor]');

/* ---------------- anchoring cascade ---------------- */
function onChartClick(anchor, params) {
  if (!state.commentMode) return;
  let target, quote;
  if (params.componentType === 'series') {
    target = { type: 'datum', key: String(params.name) };
    quote = 'bar: ' + params.name + ' · ' + params.value;
  } else if (params.componentType === 'xAxis') {
    target = { type: 'axis-x', key: String(params.value) };
    quote = 'x-axis label: ' + params.value;
  } else if (params.componentType === 'yAxis') {
    target = { type: 'axis-y', key: String(params.value) };
    quote = 'y-axis tick: ' + params.value;
  } else if (params.componentType === 'markLine') {
    target = { type: 'target', key: String(params.value ?? params.name ?? '') };
    quote = 'target line: ' + (params.value ?? params.name);
  } else return;
  openComposer(anchor, target, quote);
}

function elementDescriptor(el, holder) {
  let node = el;
  while (node && node !== holder &&
         !node.matches('h1, h2, p, li, ul, ol, table, tr, td, th, blockquote, pre, code, nav, [data-render-target]')) {
    node = node.parentElement;
  }
  if (!node || node === holder) return null;
  const isFig = node.hasAttribute('data-render-target');
  const sel = isFig ? '[data-render-target]' : node.tagName.toLowerCase();
  const peers = [...holder.querySelectorAll(sel)];
  const name = isFig ? 'figure' : sel === 'h1' ? 'title' : sel === 'nav' ? 'breadcrumbs' : sel;
  const quote = (node.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 60);
  return { key: name + '[' + (peers.indexOf(node) + 1) + ']', quote };
}

function resolveElement(holder, key) { // 'p[2]' -> element, for pin positioning
  const m = /^([a-z0-9]+|title|breadcrumbs|figure)\[(\d+)\]$/.exec(key);
  if (!m) return null;
  const sel = m[1] === 'title' ? 'h1' : m[1] === 'breadcrumbs' ? 'nav' : m[1] === 'figure' ? '[data-render-target]' : m[1];
  return [...holder.querySelectorAll(sel)][+m[2] - 1] || null;
}

function onDocClick(e) {
  if (!state.commentMode || e.target.closest('.hx-pin,.hx-panel,.hx-toolbar,#hx-errors')) return;
  const holder = holderOf(e.target);
  if (!holder) return;
  if (e.target.tagName === 'CANVAS') return; // canvas clicks are the chart's business: marks via chart events, blanks via zrender
  const sel = window.getSelection();
  const selTxt = sel ? String(sel).trim() : '';
  if (selTxt) {
    openComposer(holder.dataset.anchor, { type: 'text', key: selTxt.slice(0, 40) }, selTxt);
    sel.removeAllRanges();
    return;
  }
  const desc = elementDescriptor(e.target, holder);
  if (desc) openComposer(holder.dataset.anchor, { type: 'element', key: desc.key }, desc.quote);
  else openComposer(holder.dataset.anchor, null, null);
}

/* ---------------- events -> threads ---------------- */
function ingest(events) {
  let changed = false;
  for (const e of events) {
    if (state.seenNames.has(e.actor + '/' + e.name)) continue;
    state.seenNames.add(e.actor + '/' + e.name);
    state.events.push(e);
    changed = true;
  }
  if (!changed) return;
  state.events.sort((a, b) => a.name < b.name ? -1 : 1);
  state.threads.clear();
  let lastHandoff = '';
  for (const e of state.events) if (e.body.event === 'handoff') lastHandoff = e.name;
  for (const e of state.events) {
    const b = e.body;
    if (b.event === 'comment') state.threads.set(b.id, { ev: e, replies: [], status: e.name > lastHandoff ? 'draft' : 'pending' });
    else if ((b.event === 'reply' || b.event === 'status') && state.threads.has(b.respondsTo)) {
      const th = state.threads.get(b.respondsTo);
      if (b.event === 'reply') { th.replies.push(e); th.status = b.status || 'acknowledged'; }
      else th.status = b.status;
    }
  }
  renderPanel();
  renderPins();
  renderBadges();
}

/* ---------------- UI ---------------- */
const CSS = `
/* document presentation — the spec file stays lean; the dialect's look lives here */
body{margin:0;background:#faf9f6;color:#22242a;padding-bottom:100px}
article.spec{max-width:720px;margin:0 auto;padding:40px 24px;font:16.5px/1.65 "Iowan Old Style","Palatino Linotype",Georgia,serif}
article.spec header{border-bottom:1px solid #e2e0d8;padding-bottom:16px;margin-bottom:28px}
article.spec h1{font-size:29px;line-height:1.2;margin:0 0 8px;letter-spacing:-.01em}
article.spec h2{font-size:20px;margin:26px 0 10px}
article.spec nav{font:12px system-ui;color:#8b8e98}
article.spec p{margin:0 0 10px;max-width:62ch}
article.spec a{color:#12897c}
[data-render-target]{border:1px solid #e2e0d8;border-radius:8px;background:#fff;margin:6px 0 10px}
@media(prefers-color-scheme:dark){
body{background:#17191d;color:#e8e7e2}
article.spec header{border-color:#33363c}
article.spec nav{color:#74767e}
article.spec a{color:#34a899}
[data-render-target]{border-color:#33363c;background:#1d2024}
}
.hx-toolbar{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);display:flex;gap:4px;align-items:center;background:#fff;border:1px solid #ddd;border-radius:12px;box-shadow:0 8px 28px rgba(30,30,40,.14);padding:6px;z-index:900;font:13px system-ui}
.hx-toolbar button{font:600 12.5px system-ui;border:none;background:transparent;border-radius:8px;padding:8px 14px;cursor:pointer}
.hx-toolbar button[aria-pressed=true]{background:#fbf3e2;color:#b47308}
.hx-toolbar .hx-status{color:#888;font-size:11.5px;padding:0 10px}
.hx-panel{position:fixed;top:0;right:0;width:330px;height:100vh;background:#f4f3ef;border-left:1px solid #ddd;z-index:800;display:flex;flex-direction:column;font:13px system-ui;transform:translateX(100%);transition:transform .2s}
.hx-panel.open{transform:none}
body.hx-panel-open{padding-right:330px}
.hx-panel-head{padding:14px 16px;border-bottom:1px solid #ddd;font-weight:650}
.hx-panel-head .hx-sub{font-weight:400;font-size:11px;color:#888}
.hx-threads{flex:1;overflow-y:auto;padding:10px 12px;display:flex;flex-direction:column;gap:8px}
.hx-thread{background:#fff;border:1px solid #ddd;border-radius:8px;padding:10px 12px;cursor:pointer}
.hx-thread.active{border-color:#d98e04;box-shadow:0 0 0 1px #d98e04}
.hx-anchor{font-family:ui-monospace,monospace;font-size:10.5px;color:#999;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hx-pill{font-size:9.5px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;border-radius:4px;padding:2px 6px;float:right}
.hx-pill[data-s=draft],.hx-pill[data-s=pending]{color:#b47308;background:#fbf3e2}
.hx-pill[data-s=acknowledged]{color:#0e7264;background:#e3f2f0}
.hx-pill[data-s=resolved]{color:#3d8c40;background:#e8f2e8}
.hx-msg{margin-top:7px;font-size:12.5px;line-height:1.45}
.hx-who{font-size:10px;font-weight:700;color:#999;text-transform:uppercase}
.hx-quote{display:block;border-left:2px solid #ddd;padding-left:7px;color:#999;font-style:italic;font-size:11.5px;margin:2px 0}
.hx-composer textarea{width:100%;min-height:56px;font:12.5px system-ui;border:1px solid #ccc;border-radius:6px;padding:6px 8px;box-sizing:border-box;margin-top:6px}
.hx-btn{font:600 12px system-ui;border:1px solid #ccc;background:#fff;border-radius:6px;padding:5px 12px;cursor:pointer;margin:6px 6px 0 0}
.hx-btn.pri{background:#22242a;color:#fff;border-color:#22242a}
.hx-handoff{border-top:1px solid #ddd;padding:12px 16px;display:flex;justify-content:space-between;align-items:center}
.hx-handoff .hx-note{font-size:11.5px;color:#888}
.hx-pin{position:absolute;z-index:700;width:24px;height:24px;border-radius:50% 50% 50% 4px;border:none;cursor:pointer;font:600 11.5px system-ui;color:#fff;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 8px rgba(20,20,30,.3)}
.hx-pin[data-s=draft],.hx-pin[data-s=pending]{background:#d98e04}
.hx-pin[data-s=acknowledged]{background:#12897c}
.hx-pin[data-s=resolved]{background:#fff;color:#3d8c40;border:2px solid #3d8c40}
[data-anchor]{position:relative}
body.hx-comment [data-anchor]{cursor:copy}
body.hx-comment [data-anchor]:hover:not(:has(:is(h1,h2,p,li,ul,ol,table,tr,td,th,blockquote,pre,code,nav,[data-render-target]):hover)){outline:2px dashed #d98e04;outline-offset:6px}
body.hx-comment [data-anchor] :is(h1,h2,p,li,td,th,blockquote,pre,code,nav):hover{outline:1.5px dashed #d98e04;outline-offset:4px;border-radius:2px}
[data-render-target]{position:relative}
.hx-ring{position:absolute;border:2px dashed #d98e04;border-radius:4px;pointer-events:none;z-index:650}
body.hx-comment [data-render-target]:hover{border:1.5px dashed #d98e04}
body.hx-comment [data-render-target] canvas{cursor:copy!important}
.hx-badge{font:600 9.5px system-ui;text-transform:uppercase;letter-spacing:.04em;color:#0e7264;background:#e3f2f0;border-radius:4px;padding:2px 7px;margin-left:8px;vertical-align:middle}
.hx-banner{position:fixed;top:0;left:0;right:0;background:#12897c;color:#fff;font:600 13px system-ui;padding:8px 16px;z-index:950;display:flex;gap:14px;align-items:center;justify-content:center}
.hx-toast{position:fixed;bottom:76px;left:50%;transform:translateX(-50%);background:#22242a;color:#faf9f6;font:600 12.5px system-ui;border-radius:8px;padding:9px 16px;box-shadow:0 8px 28px rgba(30,30,40,.3);z-index:960;opacity:0;transition:opacity .25s;pointer-events:none}
.hx-toast.show{opacity:1}
.hx-banner button{font:inherit;border:1px solid #fff;background:transparent;color:#fff;border-radius:6px;padding:3px 12px;cursor:pointer}
@media(prefers-color-scheme:dark){
.hx-toolbar,.hx-thread{background:#24272c;border-color:#3a3d42;color:#e8e7e2}
.hx-toolbar button{color:#e8e7e2}
.hx-panel{background:#1d2024;border-color:#3a3d42;color:#e8e7e2}
.hx-panel-head{border-color:#3a3d42}
.hx-handoff{border-color:#3a3d42}
.hx-btn{background:#24272c;color:#e8e7e2;border-color:#4a4d52}
.hx-btn.pri{background:#e8e7e2;color:#1d2024}
.hx-composer textarea{background:#17191d;color:#e8e7e2;border-color:#4a4d52}
}`;

function mountUI() {
  const style = document.createElement('style');
  style.textContent = CSS;
  document.head.appendChild(style);

  const bar = document.createElement('div');
  bar.className = 'hx-toolbar';
  bar.innerHTML = '<button id="hx-mode" aria-pressed="false">✛ Comment (C)</button><button id="hx-connect" hidden>Connect review folder</button><span class="hx-status" id="hx-status">starting…</span>';
  document.body.appendChild(bar);

  const panel = document.createElement('aside');
  panel.className = 'hx-panel';
  panel.innerHTML = '<div class="hx-panel-head">Review <span class="hx-sub" id="hx-agent"></span></div><div class="hx-threads" id="hx-threads"></div><div class="hx-handoff"><span class="hx-note" id="hx-drafts">0 drafts</span><button class="hx-btn pri" id="hx-handoff">Hand off to agent →</button></div>';
  document.body.appendChild(panel);

  document.getElementById('hx-mode').addEventListener('click', () => setCommentMode(!state.commentMode));
  document.getElementById('hx-handoff').addEventListener('click', handoff);
  document.addEventListener('keydown', e => {
    if (e.key.toLowerCase() === 'c' && !/^(textarea|input)$/i.test(e.target.tagName)) setCommentMode(!state.commentMode);
    if (e.key === 'Escape') { state.composer = null; setCommentMode(false); renderPanel(); }
  });
  document.addEventListener('click', onDocClick);
}

function setCommentMode(on) {
  state.commentMode = on;
  document.body.classList.toggle('hx-comment', on);
  document.getElementById('hx-mode').setAttribute('aria-pressed', String(on));
  // amber hover highlight on chart marks (canvas can't take CSS outlines)
  for (const { chart } of state.charts.values()) {
    chart.setOption({ series: [{ emphasis: { itemStyle: on ? { borderColor: '#d98e04', borderWidth: 3 } : { borderWidth: 0 } } }] });
  }
  if (on) openPanel(true);
}
function openPanel(open) {
  document.querySelector('.hx-panel').classList.toggle('open', open);
  document.body.classList.toggle('hx-panel-open', open);
}
function status(msg) { document.getElementById('hx-status').textContent = msg; }

function openComposer(anchorId, target, quote) {
  state.composer = { anchorId, target, quote };
  setCommentMode(false);
  openPanel(true);
  renderPanel();
  setTimeout(() => document.querySelector('.hx-composer textarea')?.focus(), 0);
}

const label = (b) => '#' + b.anchorId + (b.target ? ' › ' + b.target.key : '');

function renderPanel() {
  const wrap = document.getElementById('hx-threads');
  if (!wrap) return;
  wrap.innerHTML = '';
  if (state.composer) {
    const c = state.composer;
    const d = document.createElement('div');
    d.className = 'hx-thread active';
    d.innerHTML = '<div class="hx-anchor">' + label({ anchorId: c.anchorId, target: c.target }) + '</div>' +
      (c.quote ? '<span class="hx-quote">“' + esc(c.quote) + '”</span>' : '') +
      '<div class="hx-composer"><textarea placeholder="Comment… (⌘⏎ to send)"></textarea>' +
      '<button class="hx-btn pri" data-act="save">Comment</button><button class="hx-btn" data-act="cancel">Cancel</button></div>';
    d.querySelector('textarea').addEventListener('keydown', e => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); d.querySelector('[data-act=save]').click(); }
    });
    d.querySelector('[data-act=save]').addEventListener('click', async () => {
      const text = d.querySelector('textarea').value.trim();
      if (!text) return;
      await state.transport.postEvent({ id: 'u' + Date.now().toString(36), event: 'comment', anchorId: c.anchorId, target: c.target, quote: c.quote, text, actor: 'human', createdAt: new Date().toISOString(), schemaVersion: 1 });
      state.composer = null;
      toast('Comment saved as draft — hand off when ready');
      refresh();
    });
    d.querySelector('[data-act=cancel]').addEventListener('click', () => { state.composer = null; renderPanel(); });
    wrap.appendChild(d);
  }
  const threads = [...state.threads.values()].reverse();
  for (const th of threads) {
    const b = th.ev.body;
    const d = document.createElement('div');
    d.className = 'hx-thread' + (state.activeThread === b.id ? ' active' : '');
    let html = '<span class="hx-pill" data-s="' + th.status + '">' + th.status + '</span><div class="hx-anchor">' + esc(label(b)) + '</div>' +
      '<div class="hx-msg"><span class="hx-who">You</span>' + (b.quote ? '<span class="hx-quote">“' + esc(b.quote) + '”</span>' : '') + esc(b.text) + '</div>';
    for (const r of th.replies) html += '<div class="hx-msg"><span class="hx-who">Agent</span>' + esc(r.body.text) + '</div>';
    if (th.status === 'acknowledged') html += '<button class="hx-btn" data-act="resolve">✓ Resolve</button>';
    d.innerHTML = html;
    d.addEventListener('click', () => { state.activeThread = b.id; renderPanel(); renderPins(); scrollToThread(b); });
    d.querySelector('[data-act=resolve]')?.addEventListener('click', async e => {
      e.stopPropagation();
      await state.transport.postEvent({ id: 's' + Date.now().toString(36), event: 'status', respondsTo: b.id, status: 'resolved', actor: 'human', createdAt: new Date().toISOString(), schemaVersion: 1 });
      toast('Resolved');
      refresh();
    });
    wrap.appendChild(d);
  }
  const drafts = [...state.threads.values()].filter(t => t.status === 'draft').length;
  document.getElementById('hx-drafts').textContent = drafts + ' draft' + (drafts === 1 ? '' : 's');
  document.getElementById('hx-handoff').disabled = !drafts;
}

function scrollToThread(b) {
  document.querySelector('[data-anchor="' + b.anchorId + '"]')?.scrollIntoView({ block: 'center', behavior: 'smooth' });
}

function pinPos(b, holder) {
  const t = b.target;
  if (!t) return { top: 4, left: holder.clientWidth - 30 };
  const info = state.charts.get(b.anchorId);
  if (info && ['datum', 'axis-x', 'axis-y'].includes(t.type)) {
    try {
      const hR = holder.getBoundingClientRect(), cR = info.el.getBoundingClientRect();
      if (t.type === 'datum') {
        const i = (info.config.xAxis.data || []).indexOf(t.key);
        const v = info.config.series[0].data[i];
        const [x, y] = [info.chart.convertToPixel({ xAxisIndex: 0 }, t.key), info.chart.convertToPixel({ yAxisIndex: 0 }, v)];
        return { top: cR.top - hR.top + y - 26, left: cR.left - hR.left + x - 12 };
      }
      const num = +String(t.key).replace(/[,\s]/g, ''); // axis keys can arrive locale-formatted ("1,000")
      const x = t.type === 'axis-x' ? info.chart.convertToPixel({ xAxisIndex: 0 }, t.key) : 6;
      const y = (t.type === 'axis-y' || t.type === 'target') ? info.chart.convertToPixel({ yAxisIndex: 0 }, num) : info.el.clientHeight - 24;
      return { top: cR.top - hR.top + y - 12, left: cR.left - hR.left + x - 12 };
    } catch { /* fall through */ }
  }
  if (t.type === 'element') {
    const el = resolveElement(holder, t.key);
    if (el) {
      const hR = holder.getBoundingClientRect(), eR = el.getBoundingClientRect();
      return { top: eR.top - hR.top - 4, left: Math.min(holder.clientWidth - 28, eR.right - hR.left - 12) };
    }
  }
  return { top: 4, left: holder.clientWidth - 30 }; // orphan/text fallback: block corner
}

function renderPins() {
  document.querySelectorAll('.hx-pin').forEach(p => p.remove());
  let n = 0;
  for (const th of state.threads.values()) {
    n++;
    const b = th.ev.body;
    const holder = document.querySelector('[data-anchor="' + b.anchorId + '"]');
    if (!holder) continue;
    const pos = pinPos(b, holder);
    const pin = document.createElement('button');
    pin.className = 'hx-pin';
    pin.dataset.s = th.status;
    pin.textContent = n;
    pin.title = label(b);
    pin.style.top = pos.top + 'px';
    pin.style.left = pos.left + 'px';
    pin.addEventListener('click', e => { e.stopPropagation(); state.activeThread = b.id; openPanel(true); renderPanel(); });
    holder.appendChild(pin);
  }
}

function renderBadges() {
  document.querySelectorAll('.hx-badge').forEach(b => b.remove());
  const changed = new Set();
  for (const e of state.events) if (e.actor === 'agent' && e.body.change && e.body.anchorId) changed.add(e.body.anchorId);
  for (const a of changed) {
    const h = document.querySelector('[data-anchor="' + a + '"] h2, [data-anchor="' + a + '"] h1');
    if (!h || h.querySelector('.hx-badge')) continue;
    const s = document.createElement('span');
    s.className = 'hx-badge';
    s.textContent = 'updated by agent';
    h.appendChild(s);
  }
}

async function handoff() {
  const n = [...state.threads.values()].filter(t => t.status === 'draft').length;
  await state.transport.postEvent({ id: 'h' + Date.now().toString(36), event: 'handoff', anchorId: '', target: null, quote: null, text: 'batch from ' + state.transport.mode, actor: 'human', createdAt: new Date().toISOString(), schemaVersion: 1 });
  toast('Handed off ' + n + ' comment' + (n === 1 ? '' : 's') + ' — agent notified');
  refresh();
}

const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

let toastTimer;
function toast(msg) {
  let el = document.getElementById('hx-toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'hx-toast';
    el.className = 'hx-toast';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2600);
}

/* ---------------- loops ---------------- */
async function refresh() {
  try {
    ingest(await state.transport.listEvents());
    const agentEvents = state.events.filter(e => e.actor === 'agent');
    const last = agentEvents[agentEvents.length - 1];
    document.getElementById('hx-agent').textContent = last ? '· agent last event ' + new Date(last.body.createdAt).toLocaleTimeString() : '· no agent events yet';
    status('connected · ' + state.transport.label + ' · ' + state.threads.size + ' threads');
    if (location.hash.includes('hxdebug') && !state._beaconed) {
      state._beaconed = true;
      fetch('/hxdebug/threads=' + state.threads.size + '/pins=' + document.querySelectorAll('.hx-pin').length + '/charts=' + state.charts.size).catch(() => {});
    }
  } catch (e) { status('event sync failed: ' + e.message); }
}

async function watchSpec() {
  const m = await state.transport.specModified();
  if (m && state.specMtime && m > state.specMtime && !document.getElementById('hx-banner')) {
    const b = document.createElement('div');
    b.className = 'hx-banner';
    b.id = 'hx-banner';
    b.innerHTML = 'Spec updated by agent <button>Reload</button>';
    b.querySelector('button').addEventListener('click', () => location.reload());
    document.body.appendChild(b);
  }
  if (m) state.specMtime = state.specMtime || m;
}

/* ---------------- boot ---------------- */
(async function boot() {
  mountUI();
  await hydrateIslands();
  state.transport = location.protocol === 'file:' ? fsaTransport() : httpTransport();

  if (state.transport.mode === 'fsa') {
    const btn = document.getElementById('hx-connect');
    const restored = await state.transport.tryRestore();
    if (restored !== 'granted') {
      btn.hidden = false;
      btn.textContent = restored === 'prompt' ? 'Resume review' : 'Connect review folder';
      status('view-only — connect to annotate');
      btn.addEventListener('click', async () => {
        try { await state.transport.connect(); btn.hidden = true; startLoops(); } catch (e) { status('connect failed: ' + e.message); }
      });
      return;
    }
  }
  startLoops();
})();

function startLoops() {
  status(state.transport.mode === 'fsa' ? 'connected · local folder' : 'connected · review-serve');
  refresh();
  watchSpec();
  setInterval(refresh, 2000);
  setInterval(watchSpec, 5000);
  window.addEventListener('resize', renderPins);
}
