import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
const slice = (from, to) => {
  const start = runtime.indexOf(from);
  const end = runtime.indexOf(to, start);
  assert.ok(start >= 0 && end > start, 'runtime exposes ' + from);
  return runtime.slice(start, end);
};
const code = [
  slice('function findAnchor(', '\n\nfunction clearJev('),
  slice("const PLACE_KEY", '\n\n/* ---------------- boot'),
].join('\n\n');

// Boot order: keepPlace() before render, its step after render.
const bootAt = runtime.indexOf('(async function boot()');
const boot = runtime.slice(bootAt, runtime.indexOf('\n})();', bootAt));
assert.ok(boot.indexOf('keepPlace()') < boot.indexOf('await hydrateIslands()'), 'place listeners arm before render');
assert.ok(boot.indexOf('restorePlace()') > boot.indexOf('adoptForeignCharts()'), 'place restores after render');

// Page: sections at document offsets; rect.top follows scrollY.
function page({ storage, hash = '', embed = null, anchors = ['intro', 'place', 'place-save', 'later'] } = {}) {
  const tops = { intro: 0, place: 1000, 'place-save': 1200, later: 3000, hidden: 500 };
  const win = { scrollY: 0, listeners: {}, timers: [], scrolls: [] };
  win.addEventListener = (t, fn) => (win.listeners[t] ||= []).push(fn);
  win.removeEventListener = (t, fn) => { win.listeners[t] = (win.listeners[t] || []).filter(f => f !== fn); };
  win.fire = t => (win.listeners[t] || []).forEach(fn => fn({ type: t }));
  win.scrollTo = (_, y) => { win.scrolls.push(y); win.scrollY = y; };
  Object.defineProperty(win, 'localStorage', { get() { if (storage instanceof Error) throw storage; return storage; } });
  const els = anchors.map(a => ({ dataset: { anchor: a },
    getBoundingClientRect: () => ({ top: tops[a] - win.scrollY, height: a === 'hidden' ? 0 : 150 }) }));
  const doc = { querySelectorAll: () => els };
  const setTimeout = fn => win.timers.push(fn);
  const clearTimeout = () => {};
  const location = { pathname: '/specs/demo.spec.html', hash };
  const { keepPlace } = Function('window', 'document', 'location', 'EMBED_REVIEW_DIR', 'setTimeout', 'clearTimeout',
    code + '; return { keepPlace };')(win, doc, location, embed, setTimeout, clearTimeout);
  return { win, keepPlace };
}
const memory = () => { const m = new Map(); return { m, getItem: k => m.has(k) ? m.get(k) : null,
  setItem: (k, v) => m.set(k, String(v)), removeItem: k => m.delete(k) }; };
const KEY = 'hx-place:/specs/demo.spec.html';

// Save on scroll (throttled) and pagehide: nearest section at top plus offset.
const store = memory();
let p = page({ storage: store });
p.keepPlace()();
assert.deepEqual(p.win.scrolls, [], 'no record leaves the page at the top');
p.win.scrollY = 1240;
p.win.fire('scroll'); p.win.fire('scroll'); p.win.fire('scroll');
assert.equal(p.win.timers.length, 1, 'scroll saves are throttled');
p.win.timers.shift()();
assert.deepEqual(JSON.parse(store.m.get(KEY)), { anchor: 'place-save', offset: 40 });
p.win.scrollY = 3010;
p.win.fire('pagehide');
assert.deepEqual(JSON.parse(store.m.get(KEY)), { anchor: 'later', offset: 10 }, 'pagehide saves');

// Reload: returns to the section at the same offset, after render.
p = page({ storage: store });
const after = p.keepPlace();
assert.deepEqual(p.win.scrolls, [], 'nothing scrolls before render');
after();
assert.deepEqual(p.win.scrolls, [3010]);

// Back at the top clears the record.
p.win.scrollY = 0;
p.win.fire('pagehide');
assert.equal(store.m.has(KEY), false);

// Zero-height anchors are skipped.
p = page({ storage: store, anchors: ['intro', 'hidden'] });
p.keepPlace()();
p.win.scrollY = 600; p.win.fire('pagehide');
assert.deepEqual(JSON.parse(store.m.get(KEY)), { anchor: 'intro', offset: 600 });

// Address anchor wins: specs have no ids, so the runtime scrolls to the [data-anchor] after render.
store.m.set(KEY, JSON.stringify({ anchor: 'later', offset: 10 }));
p = page({ storage: store, hash: '#place-save' });
const hashStep = p.keepPlace();
assert.deepEqual(p.win.scrolls, [], 'hash waits for render');
hashStep();
assert.deepEqual(p.win.scrolls, [1200], 'hash wins over the stored place');
p = page({ storage: store, hash: '#place%2Dsave' });
p.keepPlace()();
assert.deepEqual(p.win.scrolls, [1200], 'encoded hash decodes');
p = page({ storage: null, hash: '#place' });
p.keepPlace()();
assert.deepEqual(p.win.scrolls, [1000], 'hash wins without storage');
p = page({ storage: new Error('SecurityError'), hash: '#place' });
p.keepPlace()();
assert.deepEqual(p.win.scrolls, [1000], 'hash wins when storage throws');
// Unknown hash falls back to the stored place, then the top.
p = page({ storage: store, hash: '#nope' });
p.keepPlace()();
assert.deepEqual(p.win.scrolls, [3010], 'unknown hash: stored place');
p = page({ storage: memory(), hash: '#nope' });
p.keepPlace()();
assert.deepEqual(p.win.scrolls, [], 'unknown hash, no record: top');
p = page({ storage: store, hash: '#%E0%A4%A' });
p.keepPlace()();
assert.deepEqual(p.win.scrolls, [3010], 'malformed hash: stored place, no error');
// Reader moved before render: hash never re-scrolls either.
p = page({ storage: store, hash: '#place' });
const movedHash = p.keepPlace();
p.win.fire('keydown');
movedHash();
assert.deepEqual(p.win.scrolls, [], 'moved reader keeps position over hash');

// Reader moved before render: never re-scrolled.
p = page({ storage: store });
const step = p.keepPlace();
p.win.fire('wheel');
step();
assert.deepEqual(p.win.scrolls, []);
assert.equal((p.win.listeners.wheel || []).length, 0, 'intent listeners removed after render');

// Remembered section gone, or a corrupt record: top, no error.
p = page({ storage: store, anchors: ['intro', 'place'] });
p.keepPlace()();
assert.deepEqual(p.win.scrolls, []);
for (const bad of ['not json', '{"anchor":1,"offset":2}', '{"anchor":"later"}', 'null']) {
  store.m.set(KEY, bad);
  p = page({ storage: store });
  p.keepPlace()();
  assert.deepEqual(p.win.scrolls, [], bad);
}

// Storage throwing on getter, reads, and writes never breaks the page.
p = page({ storage: new Error('SecurityError') });
p.keepPlace()();
p.win.fire('pagehide');
assert.deepEqual(p.win.scrolls, []);
const boom = () => { throw new Error('QuotaExceededError'); };
p = page({ storage: { getItem: boom, setItem: boom, removeItem: boom } });
p.keepPlace()();
p.win.scrollY = 1240; p.win.fire('scroll'); p.win.timers.shift()();
p.win.scrollY = 0; p.win.fire('pagehide');
assert.deepEqual(p.win.scrolls, []);
p = page({ storage: null });
p.keepPlace()();

// Embed mode: the host owns scrolling.
store.m.set(KEY, JSON.stringify({ anchor: 'later', offset: 10 }));
p = page({ storage: store, embed: 'app.review' });
p.keepPlace()();
assert.deepEqual(p.win.scrolls, []);
assert.equal(p.win.listeners.scroll, undefined);

console.log('runtime place tests passed');
