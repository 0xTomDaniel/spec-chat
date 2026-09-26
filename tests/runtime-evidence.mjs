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

// Minimal DOM: enough for renderJev, its markers, and the shared popover (as runtime-jev-render).
class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.parent = null; this.dataset = {}; this.attrs = {};
    this.ownText = ''; this.className = ''; this.listeners = {}; this.style = {}; this.hidden = false;
  }
  appendChild(child) { if (child.parent) child.remove(); child.parent = this; this.children.push(child); return child; }
  insertBefore(child, ref) { child.parent = this; const i = this.children.indexOf(ref); this.children.splice(i < 0 ? 0 : i, 0, child); return child; }
  append(...children) { for (const c of children) this.appendChild(c); }
  replaceChildren(...children) { for (const c of [...this.children]) c.remove(); this.append(...children); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  fire(type, extra = {}) {
    const event = { type, target: this, preventDefault() {}, stopPropagation() {}, ...extra };
    for (const fn of this.listeners[type] || []) fn(event);
    return event;
  }
  remove() { if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1); this.parent = null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  focus() { document.activeElement = this; }
  set textContent(v) { this.children = []; this.ownText = String(v); }
  get textContent() { return this.ownText + this.children.map(c => c.textContent).join(''); }
  get parentNode() { return this.parent; }
  get nodeType() { return 1; }
  get childNodes() { return [{ nodeType: 3, nodeValue: this.ownText }, ...this.children]; }
  get firstChild() { return this.children[0] || null; }
  get lastElementChild() { return this.children.at(-1) || null; }
  get isConnected() { let e = this; while (e.parent) e = e.parent; return e === body; }
  get offsetParent() { return null; }
  contains(other) { for (let e = other; e; e = e.parent) if (e === this) return true; return false; }
  closest(sel) { for (let e = this; e; e = e.parent) if (e.matches(sel)) return e; return null; }
  *walk() { for (const c of this.children) { yield c; yield* c.walk(); } }
  matches(sel) {
    return sel.split(',').some(s => {
      s = s.trim();
      if (s === ':hover') return false;
      if (s.startsWith('.')) return this.className.split(/\s+/).includes(s.slice(1));
      const attr = s.match(/^\[data-([a-z-]+)\]$/);
      if (attr) { const key = attr[1].replace(/-([a-z])/g, (_, c) => c.toUpperCase()); return key in this.dataset; }
      if (s === 'article.spec') return this.tagName === 'ARTICLE' && this.className === 'spec';
      return this.tagName === s.toUpperCase();
    });
  }
  querySelectorAll(sel) { return [...this.walk()].filter(e => e.matches(sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
const body = new El('body');
body.classList = { contains: () => false };
const article = body.appendChild(new El('article'));
article.className = 'spec';
const criterion = anchor => {
  const p = article.appendChild(new El('p'));
  p.dataset.anchor = anchor;
  p.dataset.acceptanceCriterion = '';
  p.textContent = 'Criterion ' + anchor;
  return p;
};
const criteria = ['passed', 'failed', 'reworded', 'failed-reworded', 'material', 'unsure', 'uncommitted', 'none', 'plain', 'lane'];
for (const a of criteria) criterion(a);
const story = article.appendChild(new El('p'));
story.dataset.anchor = 'story';
story.textContent = 'A story, not a criterion.';
const docListeners = {};
const document = { body, activeElement: body, querySelectorAll: s => body.querySelectorAll(s), querySelector: s => body.querySelector(s),
  createElement: t => new El(t), addEventListener: (type, fn) => (docListeners[type] ||= []).push(fn) };
const posted = [];
const windowListeners = {};
const parent = { postMessage: (data, origin) => posted.push([data, origin]) };
const window = { parent, addEventListener: (type, fn) => (windowListeners[type] ||= []).push(fn), matchMedia: () => ({ matches: true }) };
const composed = [];
const openComposer = (anchor, target, quote, text) => composed.push([anchor, text]);

const code = [
  slice('function findAnchor(', '\n\nfunction clearJev('),
  slice('function coveragePair(', '\n\n// Name the folder'),
  slice('function jevDisplayLabel(', '\n\nfunction goToJevTarget('),
  slice('/* ---------------- Jev markers', '\n\n/* ---------------- UI'),
  slice('async function requestEvidence(', '\n\nfunction markIssueFocus('),
  slice('function commitDate(', '\n\nfunction rangeBarText('),
].join('\n\n');
const state = { readingView: false, jev: { request: 0, status: 'idle', base: null, items: [] }, evidence: { criteria: null, watched: true, hostOrigin: null } };
const location = { search: '', pathname: '/specs/demo.spec.html' };
let answer = null;
const fetches = [];
const fetch = async url => { fetches.push(url); if (answer instanceof Error) throw answer; return answer; };
const { renderJev, requestEvidence, evidenceAge, evidenceDiff, listenEvidenceHost } = Function('document', 'window', 'state', 'location', 'URLSearchParams', 'EMBED_REVIEW_DIR', 'NodeFilter',
  'requestAnimationFrame', 'cancelAnimationFrame', 'getComputedStyle', 'innerWidth', 'innerHeight', 'fetch', 'openComposer',
  code + '; return { renderJev, requestEvidence, evidenceAge, evidenceDiff, listenEvidenceHost };')(document, window, state, location, URLSearchParams, null, {}, () => 0, () => {},
  () => ({}), 375, 812, fetch, openComposer);

const holder = anchor => article.querySelectorAll('[data-anchor]').find(e => e.dataset.anchor === anchor);
const markersOf = anchor => holder(anchor).querySelectorAll('.hx-jev-marker');
const marker = anchor => { const found = markersOf(anchor); assert.equal(found.length, 1, 'one marker on ' + anchor); return found[0]; };
const pop = () => body.querySelector('.hx-jev-pop');
const textBefore = article.children.map(e => e.textContent);
const json = value => ({ ok: true, json: async () => value });
const ago = ms => new Date(Date.now() - ms).toISOString();
const E = 'https://evidence.example';
const VIEW = E + '/bundles/b#criterion=' + encodeURIComponent('spec-chat::c 1');
const entry = (values = {}) => ({ match: true, verdict: 'pass', judgment: null, pr: 58, capturedAt: ago(9 * 86400000 + 5000),
  onMain: true, artifact: E + '/artifacts/a.png', bundle: E + '/bundles/b', view: VIEW, proven: 'Criterion old text', uncommitted: false, ...values });

// Age is minutes, hours, or days.
const now = Date.parse('2026-09-26T12:00:00Z');
assert.equal(evidenceAge('2026-09-26T11:55:00Z', now), '5 m');
assert.equal(evidenceAge('2026-09-26T09:00:00Z', now), '3 h');
assert.equal(evidenceAge('2026-09-24T11:00:00Z', now), '2 d');
assert.equal(evidenceAge('garbage', now), '');

// Down, erroring, non-JSON, or unconfigured: no note, no error, page unchanged. The browser names only the path.
for (const value of [new Error('down'), { ok: false, json: async () => ({}) }, { ok: true, json: async () => { throw new SyntaxError(); } },
  json({ criteria: null })]) {
  answer = value;
  await requestEvidence();
  assert.equal(body.querySelectorAll('.hx-jev-marker').length, 0);
  assert.equal(body.querySelectorAll('.hx-jev-note').length, 0);
}
assert.ok(fetches.every(url => url === '/api/evidence?path=specs%2Fdemo.spec.html'), fetches.join());

answer = json({ criteria: {
  passed: entry(),
  failed: entry({ verdict: 'fail', pr: 12, capturedAt: ago(3 * 3600000 + 5000) }),
  reworded: entry({ match: false, judgment: 'cosmetic' }),
  'failed-reworded': entry({ match: false, judgment: 'cosmetic', verdict: 'fail' }),
  material: entry({ match: false, judgment: 'material', onMain: false }),
  unsure: entry({ match: false, judgment: 'unsure' }),
  uncommitted: entry({ uncommitted: true }),
  plain: entry({ artifact: null, view: null, pr: null, capturedAt: ago(5 * 60000 + 5000) }),
  lane: entry({ onMain: false }),
  story: entry(),
} });
await requestEvidence();

const note = anchor => {
  marker(anchor).fire('click');
  const row = pop().querySelector('.hx-jev-pop-note');
  const label = row.querySelector('.hx-jev-pop-text');
  const meta = row.querySelector('.hx-jev-pop-meta');
  const bundle = row.querySelector('.hx-jev-pop-link');
  return { group: row.dataset.group, label: label.textContent, labelTag: label.tagName, artifact: label.href || null,
    target: label.target || null, context: meta ? meta.children.filter(c => c.tagName === 'SPAN').map(c => c.textContent).join('') : null,
    bundle: bundle ? [bundle.textContent, bundle.href, bundle.target] : null };
};
const linked = (label, context, extra = {}) => ({ group: 'evidence', label, labelTag: 'A', artifact: VIEW, target: '_blank',
  context, bundle: ['bundle', E + '/bundles/b', '_blank'], ...extra });
assert.deepEqual(note('passed'), linked('Passed', '#58 · 9 d'));
assert.deepEqual(note('failed'), linked('Failed', '#12 · 3 h'));
assert.deepEqual(note('reworded'), linked('Passed · reworded', '#58 · 9 d'));
assert.deepEqual(note('failed-reworded'), linked('Failed · reworded', '#58 · 9 d'));
assert.deepEqual(note('material'), linked('Stale', '#58 · 9 d · not on main'));
assert.deepEqual(note('unsure'), linked('Stale', '#58 · 9 d'));
assert.deepEqual(note('uncommitted'), linked('Stale', '#58 · 9 d'));
assert.deepEqual(note('lane'), linked('Passed', '#58 · 9 d · not on main'));
assert.deepEqual(note('plain'), linked('Passed', '5 m', { labelTag: 'SPAN', artifact: null, target: null }));
assert.deepEqual(note('none'), { group: 'evidence', label: 'Not yet', labelTag: 'SPAN', artifact: null, target: null, context: null, bundle: null });
// Failed and Stale get the colored !, Passed the green check, Not yet the gray dot; stories get no evidence.
const marked = key => body.querySelectorAll('.hx-jev-marker').filter(m => m.dataset[key] === 'true').map(m => m.closest('[data-anchor]').dataset.anchor);
assert.deepEqual(marked('attention'), ['failed', 'failed-reworded', 'material', 'unsure', 'uncommitted']);
assert.deepEqual(marked('passed'), ['passed', 'reworded', 'plain', 'lane']);
assert.equal(marker('none').dataset.attention + marker('none').dataset.passed, 'falsefalse');
assert.equal(markersOf('story').length, 0);
// Notes add no text to the spec and move nothing: each criterion keeps its text, plus one marker.
assert.deepEqual(article.children.map(e => e.textContent), textBefore);
for (const a of criteria) assert.deepEqual(holder(a).children.map(c => c.tagName), ['BUTTON']);

// Stale and reworded notes show one inline diff of the proven text against the text as read; matching notes show none.
const row = anchor => { marker(anchor).fire('click'); return pop().querySelector('.hx-jev-pop-note'); };
const diffOf = anchor => {
  const diff = row(anchor).querySelector('.hx-jev-pop-diff');
  return diff ? diff.children.filter(c => c.tagName !== 'SPAN' || c.textContent.trim()).map(c => [c.tagName, c.textContent]) : null;
};
for (const a of ['reworded', 'failed-reworded', 'material', 'unsure', 'uncommitted'])
  assert.deepEqual(diffOf(a), [['SPAN', 'Criterion'], ['DEL', 'old text'], ['INS', a]], a);
for (const a of ['passed', 'failed', 'lane', 'none']) assert.equal(diffOf(a), null, a);
assert.deepEqual(evidenceDiff('a b c d', 'a x c d e').map(p => p.op + ':' + p.text), ['same:a', 'del:b', 'ins:x', 'same:c d', 'ins:e']);
// Only Stale gets Ask for re-proof; it drafts the fixed text in the composer and writes nothing.
const actionsOf = anchor => row(anchor).querySelectorAll('.hx-btn').map(b => b.textContent);
for (const a of ['passed', 'failed', 'reworded', 'failed-reworded', 'lane', 'plain', 'none']) assert.deepEqual(actionsOf(a), [], a);
for (const a of ['material', 'unsure', 'uncommitted']) assert.deepEqual(actionsOf(a), ['Ask for re-proof'], a);
row('material').querySelector('.hx-btn').fire('click');
const captured = new Date(Date.now() - 9 * 86400000 - 5000).toISOString().slice(0, 10);
assert.deepEqual(composed, [['material', 'material changed since its evidence (#58, ' + captured + '): please recapture it.']]);
assert.equal(fetches.length, 5, 'drafting writes nothing');
// Watched: no warning. Unwatched: Stale says so and still drafts; other notes stay quiet.
assert.equal(row('material').querySelector('.hx-jev-pop-warn'), null);
state.evidence.watched = false;
renderJev();
assert.equal(row('material').querySelector('.hx-jev-pop-warn').textContent, 'No one is watching this spec; a re-proof request is saved as a note only.');
assert.deepEqual(actionsOf('material'), ['Ask for re-proof']);
assert.equal(row('passed').querySelector('.hx-jev-pop-warn'), null);
state.evidence.watched = true;
renderJev();

// Plugin bridge: links behave as today until the parent frame announces itself; then clicks post IDs to that origin.
const click = el => { let prevented = false; el.fire('click', { preventDefault() { prevented = true; } }); return prevented; };
const labelOf = anchor => row(anchor).querySelector('.hx-jev-pop-text');
const bundleOf = anchor => row(anchor).querySelector('.hx-jev-pop-link');
assert.equal(click(labelOf('passed')), false);
assert.equal(click(bundleOf('passed')), false);
listenEvidenceHost();
const announce = (data, source = parent, origin = 'https://bb.example') => windowListeners.message.forEach(fn => fn({ data, source, origin }));
announce({ type: 'spec-chat-host', opens: ['evidence'] }, {});
announce({ type: 'spec-chat-host', opens: [] });
announce({ type: 'other', opens: ['evidence'] });
assert.equal(click(labelOf('passed')), false, 'unannounced or foreign sender leaves links alone');
assert.deepEqual(posted, []);
announce({ type: 'spec-chat-host', opens: ['evidence'] });
assert.equal(click(labelOf('passed')), true);
assert.equal(click(bundleOf('passed')), true);
assert.equal(click(bundleOf('plain')), true);
assert.deepEqual(posted, [
  [{ type: 'spec-chat-open-evidence', bundle: 'b', criterion: 'spec-chat::c 1' }, 'https://bb.example'],
  [{ type: 'spec-chat-open-evidence', bundle: 'b' }, 'https://bb.example'],
  [{ type: 'spec-chat-open-evidence', bundle: 'b' }, 'https://bb.example'],
]);

// Evidence is listed first beside other Jev notes, and Jev rerenders keep it.
state.jev.status = 'on';
state.jev.items = [{ kind: 'coverage', id: 'story::passed', state: 'label', label: 'unrelated', target: null, record: 'r' }];
renderJev();
marker('passed').fire('click');
assert.deepEqual(pop().querySelectorAll('.hx-jev-pop-note').map(n => n.dataset.group), ['evidence', 'coverage']);

console.log('runtime evidence tests passed');
