import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

// A thread pin and a Jev marker on the same criterion never overlap: the point at the marker's
// center, and its tap pad, belong to the marker at every width (jev-suggestions #markers-mobile).
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
const start = runtime.indexOf('function clearJevMarkers(');
const end = runtime.indexOf('\n}\n', start) + 3;
assert.ok(start >= 0, 'runtime exposes clearJevMarkers');
assert.match(runtime, /holder\.appendChild\(pin\);\n\s*clearJevMarkers\(pin, holder\);/, 'every placed pin steps clear of its block markers');
assert.match(runtime, /forEach\(placeJevMarker\); renderPins\(\);/, 'on resize markers are placed before pins');

const box = (x, y, w, h) => ({ left: x, top: y, right: x + w, bottom: y + h, width: w, height: h });
const center = r => [r.left + r.width / 2, r.top + r.height / 2];
const inside = ([x, y], r) => x >= r.left && x < r.right && y >= r.top && y < r.bottom;

function run({ holderLeft, pinLeft, pinSize, marker, pad }) {
  const pin = { style: { left: pinLeft + 'px' }, getBoundingClientRect() { return box(holderLeft + parseFloat(this.style.left), 397, pinSize, pinSize); } };
  const markerEl = { getBoundingClientRect: () => marker, pad };
  const holder = { querySelectorAll: sel => (assert.equal(sel, '.hx-jev-marker'), [markerEl]) };
  const getComputedStyle = (el, pseudo) => (assert.equal(pseudo, '::before'), el.pad);
  new Function('getComputedStyle', runtime.slice(start, end) + 'return clearJevMarkers;')(getComputedStyle)(pin, holder);
  return pin.getBoundingClientRect();
}

// Measured QA geometry: 375 px phone and 560 px split pane (44 px pins), 1280 px desktop (24 px pins).
const mobilePad = { left: '-28px', top: '-14px', bottom: '-14px' };
for (const [w, holderLeft, pinLeft, pinSize, marker, pad] of [
  [375, 10, 311, 44, box(351, 399, 14, 14), mobilePad],
  [560, 10, 496, 44, box(536, 399, 14, 14), mobilePad],
  [1280, 0, 1200, 24, box(1240, 399, 14, 14), { left: '-16px', top: '-10px', bottom: '-10px' }],
]) {
  const pin = run({ holderLeft, pinLeft, pinSize, marker, pad });
  const tapPad = box(marker.left + parseFloat(pad.left), marker.top + parseFloat(pad.top), marker.right - marker.left - parseFloat(pad.left), marker.height - 2 * parseFloat(pad.top));
  assert.ok(pin.right <= tapPad.left || pin.bottom <= tapPad.top || pin.top >= tapPad.bottom, w + ' px: pin and marker tap pad are disjoint');
  assert.ok(!inside(center(marker), pin), w + ' px: the marker center is the marker, not the pin');
  assert.equal(pin.width, pinSize, w + ' px: pin keeps its tap target');
  assert.ok(pin.left >= holderLeft, w + ' px: pin never moves right or out of its block');
}

// A pin already clear of the marker is left where it was placed.
const clear = run({ holderLeft: 0, pinLeft: 100, pinSize: 44, marker: box(351, 399, 14, 14), pad: mobilePad });
assert.equal(clear.left, 100, 'a non-overlapping pin keeps its place');

console.log('runtime pin marker tests passed');
