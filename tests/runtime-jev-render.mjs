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

// Minimal DOM: enough for renderJev and its markers.
class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.parent = null; this.dataset = {}; this.attrs = {}; this.textContent = ''; this.className = ''; }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  insertBefore(child, ref) { child.parent = this; const i = this.children.indexOf(ref); this.children.splice(i < 0 ? 0 : i, 0, child); return child; }
  append(...children) { for (const c of children) this.appendChild(c); }
  addEventListener() {}
  remove() { if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1); this.parent = null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  get parentNode() { return this.parent; }
  get firstChild() { return this.children[0] || null; }
  *walk() { for (const c of this.children) { yield c; yield* c.walk(); } }
  matches(sel) {
    return sel.split(',').some(s => {
      s = s.trim();
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
const section = anchor => {
  const s = article.appendChild(new El('section'));
  s.dataset.anchor = anchor;
  s.appendChild(new El('h3'));
  return s;
};
for (const a of ['story-gap', 'criterion-gap', 'story-unsure', 'criterion-unsure', 'story-down', 'criterion-down']) section(a);
const document = { body, querySelectorAll: s => body.querySelectorAll(s), querySelector: s => body.querySelector(s),
  createElement: t => new El(t) };

const code = [
  slice('function findAnchor(', '\n\nfunction clearJev('),
  slice('function coveragePair(', '\n\n// Name the folder'),
  slice('function jevDisplayLabel(', '\n\nfunction goToJevTarget('),
  slice('function renderJev(', '\n\n/* ---------------- UI'),
].join('\n\n');
// The real runtime state shape, as requestJev leaves it after a successful fetch.
const state = { readingView: false, jev: { request: 1, status: 'on', base: 'b', items: [
  { kind: 'coverage', id: 'story-gap::criterion-gap', state: 'label', label: 'unrelated', target: null, record: 'r1' },
  { kind: 'coverage', id: 'story-unsure::criterion-unsure', state: 'unsure', label: null, target: null, record: 'r2' },
  { kind: 'coverage', id: 'story-down::criterion-down', state: 'unavailable', label: null, target: null, record: null },
] } };
const renderJev = Function('document', 'state', 'location', 'URLSearchParams', 'EMBED_REVIEW_DIR', 'JEV_TYPE_LABELS',
  code + '; return renderJev;')(document, state, { search: '' }, URLSearchParams, null, {});
renderJev();

const markers = anchor => body.querySelectorAll('.hx-jev-coverage')
  .filter(m => m.parent.parent.dataset.anchor === anchor).map(m => [m.dataset.state, m.textContent]);
assert.deepEqual(markers('story-gap'), [['gap', 'No criterion covers this']]);
assert.deepEqual(markers('criterion-gap'), [['gap', 'No story backs this']]);
assert.deepEqual(markers('story-unsure'), [['unsure', 'unsure']]);
assert.deepEqual(markers('criterion-unsure'), [['unsure', 'unsure']]);
assert.deepEqual(markers('story-down'), [['unavailable', 'Jev unavailable']]);
assert.deepEqual(markers('criterion-down'), [['unavailable', 'Jev unavailable']]);

state.jev.status = 'off';
state.jev.items = [];
renderJev();
assert.equal(body.querySelectorAll('.hx-jev-coverage').length, 0, 'Jev off clears coverage markers');

// After Move comment here resolves the orphaned thread, its card offers no second move,
// even while the old orphan suggestion is still in state until the next Jev fetch.
const orphanHintElement = Function('document', 'state', 'goToJevTarget', 'moveOrphan',
  slice('function orphanHintElement(', '\n\nasync function moveOrphan(') + '; return orphanHintElement;',
)(document, { movingOrphans: new Set() }, () => {}, () => {});
const suggestion = { kind: 'orphan', id: 'thread-1', state: 'label', label: 'Moved section', target: 'moved-section', record: 'r9' };
const acts = el => el.querySelectorAll('button').map(b => b.dataset.act);
assert.deepEqual(acts(orphanHintElement({ id: 'thread-1', status: 'pending' }, suggestion)), ['orphan-go', 'orphan-move']);
assert.deepEqual(acts(orphanHintElement({ id: 'thread-1', status: 'resolved' }, suggestion)), ['orphan-go']);
assert.equal(orphanHintElement({ id: 'thread-1', status: 'pending' }, null), null);

console.log('runtime Jev render tests passed');
