// spec-page-design #error-overlay, #acceptance-error-repeat: the error overlay keeps one line per distinct error, an error
// that comes again counting its times on its own line, so an error on a timer never grows it. Its place under the top
// line, on a desktop and a phone, and at the top of a page with none, is runtime-spec-surface.mjs.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
const from = 'const errorLines = new Map();', to = '\n\n/* ---------------- state';
const start = runtime.indexOf(from), end = runtime.indexOf(to, start);
assert.ok(start >= 0 && end > start, 'runtime exposes the error overlay');
const code = runtime.slice(start, end);

// A page body holding elements by id; the overlay goes in at the top, after the top line when the page shows one.
function page({ topLine = false } = {}) {
  const element = tag => ({ tagName: tag.toUpperCase(), id: '', className: '', textContent: '', children: [],
    appendChild(child) { this.children.push(child); return child; } });
  const body = { children: [],
    get firstChild() { return this.children[0] || null; },
    insertBefore(child, ref) { const i = ref ? this.children.indexOf(ref) : -1; this.children.splice(i < 0 ? this.children.length : i, 0, child); } };
  if (topLine) body.children.push(Object.assign(element('div'), { id: 'hx-top-line' }), Object.assign(element('main'), { id: 'doc' }));
  else body.children.push(Object.assign(element('main'), { id: 'doc' }));
  body.children.forEach(child => Object.defineProperty(child, 'nextSibling', { get: () => body.children[body.children.indexOf(child) + 1] || null }));
  const document = { body, createElement: element, getElementById: id => body.children.find(child => child.id === id) || null };
  let measured = 0;
  const measureTop = () => { measured += 1; };
  const measureTopNext = () => {}; // the runtime's: measures in the next frame, never inside the observation
  const observed = [];
  class ResizeObserver { constructor(callback) { this.callback = callback; } observe(el) { observed.push([el.id, this.callback]); } }
  const overlay = Function('document', 'measureTop', 'measureTopNext', 'ResizeObserver', code + '; return overlay;')(
    document, measureTop, measureTopNext, ResizeObserver);
  const shown = () => document.getElementById('hx-errors')?.children.map(line => line.textContent) || [];
  return { overlay, shown, body, measured: () => measured, observed: () => observed.map(([id, callback]) => [id, callback === measureTopNext]) };
}

// The same error three times: one line, counting 3.
{
  const p = page({ topLine: true });
  for (let i = 0; i < 3; i++) p.overlay('rejection', 'TypeError: loadedEvents().concat is not a function');
  assert.deepEqual(p.shown(), ['rejection: TypeError: loadedEvents().concat is not a function (×3)'], 'one line counting 3');
  assert.deepEqual(p.body.children.map(child => child.id), ['hx-top-line', 'hx-errors', 'doc'], 'under the top line, above the page');
  assert.equal(p.measured(), 3, 'the page is measured under it at once, every time it changes');
  assert.deepEqual(p.observed(), [['hx-errors', true]], 'and again in the next frame as its height alone changes, as the screen\'s does');
}

// Two distinct errors: two lines, each counting its own times; a first error shows no count.
{
  const p = page();
  p.overlay('rejection', 'TypeError: a is not a function');
  p.overlay('error', 'b is undefined @ runtime.js:12');
  p.overlay('rejection', 'TypeError: a is not a function');
  assert.deepEqual(p.shown(), ['rejection: TypeError: a is not a function (×2)', 'error: b is undefined @ runtime.js:12'],
    'two lines, in the order they first came');
  assert.equal(p.body.children[0].id, 'hx-errors', 'at the top of the page while no top line shows');
}

// The same message of another kind is another error.
{
  const p = page();
  p.overlay('error', 'x'); p.overlay('island', 'x');
  assert.deepEqual(p.shown(), ['error: x', 'island: x'], 'kind and message make the error');
}

console.log('ok runtime error overlay');
