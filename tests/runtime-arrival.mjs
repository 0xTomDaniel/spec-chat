import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

// Arrival at an anchor (a Jev link on this or another spec, or Go to) gets the open-TBD highlight.
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
  slice('function reviewHandoffState(', '\n\nfunction handoffObservation('),
  slice('function addressPlace(', '\n\nfunction keepPlace('),
  slice('function scrollToJevAnchor(', '\n\nfunction orphanHintElement('),
].join('\n\n');

const block = (anchor, tbd = null) => {
  const classes = new Set();
  const el = { dataset: { anchor }, scrolled: [], classList: { add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c) },
    scrollIntoView: options => el.scrolled.push(options.block) };
  el.tbd = tbd && { getAttribute: () => tbd, closest: () => el };
  return el;
};
const blocks = [block('intro'), block('rule'), block('open', 'q'), block('other')];
const document = { querySelectorAll: sel => sel === '[data-anchor]' ? blocks
  : sel === '[data-spec-tbd]' ? blocks.filter(b => b.tbd).map(b => b.tbd)
  : sel === '.hx-tbd-open' ? blocks.filter(b => b.classList.contains('hx-tbd-open')) : [] };
const location = { hash: '' };
const draft = new Map([['d', { status: 'draft' }]]);
const state = { threads: draft };
const { renderHighlight, scrollToJevAnchor } = Function('document', 'location', 'state', code + '; return { renderHighlight, scrollToJevAnchor };')(document, location, state);
const lit = () => blocks.filter(b => b.classList.contains('hx-tbd-open')).map(b => b.dataset.anchor);

// A page arrived at #rule (another spec's Jev link) highlights rule only; open TBDs stay gated as before.
location.hash = '#rule';
renderHighlight();
assert.deepEqual(lit(), ['rule']);
// With TBD open as the action, both show the same highlight.
state.threads = new Map();
renderHighlight();
assert.deepEqual(lit(), ['rule', 'open']);
state.threads = draft;

// Go to on this spec moves the address; the hashchange arrival scrolls and moves the highlight.
scrollToJevAnchor('other');
assert.equal(location.hash, 'other');
location.hash = '#other';
scrollToJevAnchor('other');
assert.deepEqual(lit(), ['other']);
assert.deepEqual(blocks[3].scrolled, ['center']);
// An address naming no anchor highlights nothing.
location.hash = '#hxdebug';
renderHighlight();
assert.deepEqual(lit(), []);

// One highlight style: the Jev flash is gone, and boot listens for arrivals.
assert.doesNotMatch(runtime, /hx-jev-target-flash|hx-jev-flash/);
const bootAt = runtime.indexOf('(async function boot()');
assert.match(runtime.slice(bootAt), /renderHighlight\(\);\n\s+window\.addEventListener\('hashchange', arriveAtAddress\);/);

console.log('runtime arrival tests passed');
