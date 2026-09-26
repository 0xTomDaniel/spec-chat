import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

// A criterion with a Jev marker and a thread pin: at every width the pin covers no text and no
// marker, stays inside its block, and each tap hits its own element (jev-suggestions
// #markers-pin-clear, #acceptance-pin-clear). Runs the real runtime CSS and placement code in
// headless Chromium. Needs playwright-core: PLAYWRIGHT_CORE=<path to playwright-core>.
// PIN_MARKER_SHOTS=<dir> also writes one screenshot per width.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
assert.equal(readFileSync(resolve(root, 'docs/specs/.viz/runtime.js'), 'utf8'), runtime, 'runtime copies are identical');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_CORE || 'playwright-core');

const slice = (from, to) => {
  const a = runtime.indexOf(from), b = runtime.indexOf(to, a + from.length);
  assert.ok(a >= 0 && b > a, from);
  return runtime.slice(a, b + to.length);
};
const css = ['FOCUS_CSS', 'CSS'].map(name => new Function('return ' + slice(`const ${name} = \``, '`;').replace(/^const \w+ = /, '').replace(/;$/, ''))()).join('\n');
assert.match(css, /\.hx-jev-marker\{position:absolute/, 'runtime overlay CSS extracted');
const code = ['mountJevMarker', 'placeJevMarker', 'pinPos', 'cornerPos', 'renderPins'].map(name => slice(`function ${name}(`, '\n}\n')).join('\n');
const spec = readFileSync(resolve(root, 'docs/specs/jev-suggestions.spec.html'), 'utf8')
  .replace(/<script[^>]*runtime\.js[^>]*><\/script>/, '')
  .replace('./.style/spec.css', 'file://' + resolve(root, 'docs/specs/.style/spec.css'));

function mount({ css, code, selector, order }) {
  document.head.insertAdjacentHTML('beforeend', '<style>' + css + '</style>');
  const holders = [...document.querySelectorAll(selector)];
  const state = { threads: new Map(), activeThread: null };
  const findAnchor = id => document.querySelector(`[data-anchor="${id}"]`);
  const noop = () => {};
  const stubs = { state, findAnchor, jevItem: () => null, label: () => 'thread', selectThread: noop, chartInfoFor: () => null, resolveElement: () => null,
    openJevPopover: noop, scheduleJevPopoverClose: noop, closeJevPopover: noop, jevPopoverKeeps: () => false, wireJevPopover: noop };
  const api = new Function(...Object.keys(stubs), code + 'return { mountJevMarker, placeJevMarker, renderPins };')(...Object.values(stubs));
  holders.forEach((holder, i) => state.threads.set('t' + i, { id: 't' + i, status: 'pending', ev: { body: { anchorId: holder.dataset.anchor, target: null } } }));
  // Real runtime order: renderJev mounts markers (mountJevMarker), renderPins runs on load, on
  // every change, every 2 s, and on resize (which also re-places markers).
  const markers = () => holders.forEach((holder, i) => api.mountJevMarker(holder, [{ text: 'note', attention: i % 3 === 0, group: 'evidence', passed: i % 3 === 1 }]));
  const pins = () => api.renderPins();
  (order === 'markers-first' ? [markers, pins] : [pins, markers, pins]).forEach(step => step());
  window.hxPlace = () => { document.querySelectorAll('.hx-jev-marker').forEach(api.placeJevMarker); api.renderPins(); };
  return holders.filter(h => h.querySelectorAll('.hx-jev-marker').length === 1 && h.querySelector(':scope > .hx-pin')).length;
}

function measure(selector) {
  const hit = (el, r) => { const e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return Boolean(e && (e === el || el.contains(e))); };
  const meet = (a, b) => a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5;
  const markers = [...document.querySelectorAll('.hx-jev-marker')];
  return [...document.querySelectorAll(selector)].map(holder => {
    const pin = holder.querySelector(':scope > .hx-pin'), marker = holder.querySelector('.hx-jev-marker');
    marker.scrollIntoView({ block: 'center', inline: 'nearest' }); // a wide table scrolls sideways to its marker
    const p = pin.getBoundingClientRect(), m = marker.getBoundingClientRect(), h = holder.getBoundingClientRect();
    const pad = getComputedStyle(marker, '::before');
    const tap = { left: m.left + parseFloat(pad.left), right: m.right - parseFloat(pad.right), top: m.top + parseFloat(pad.top), bottom: m.bottom - parseFloat(pad.bottom) };
    const lines = [];
    const walker = document.createTreeWalker(holder, NodeFilter.SHOW_TEXT);
    for (let t; (t = walker.nextNode());) {
      if (!t.nodeValue.trim() || t.parentElement.closest('.hx-pin,.hx-jev-marker')) continue;
      const range = document.createRange(); range.selectNodeContents(t);
      lines.push(...[...range.getClientRects()].filter(r => r.width > 0));
    }
    return {
      anchor: holder.dataset.anchor,
      pinOnText: lines.some(r => meet(p, r)),
      markerOnText: lines.some(r => meet(m, r)),
      pinOnMarker: markers.some(other => meet(p, other.getBoundingClientRect())) || meet(p, tap),
      pinInside: p.left >= h.left - 0.5 && p.right <= h.right + 0.5 && p.top >= h.top - 0.5 && p.bottom <= h.bottom + 0.5,
      pinTap: hit(pin, p),
      markerTap: hit(marker, m),
    };
  });
}

const shots = process.env.PIN_MARKER_SHOTS;
if (shots) mkdirSync(shots, { recursive: true });
const browser = await chromium.launch();
try {
  // Criteria paragraphs, and table rows, whose marker sits in the row's last cell.
  const cases = [
    { name: 'criterion', selector: '[data-acceptance-criterion]', count: 19, widths: [375, 560, 640, 800, 1280] },
    { name: 'row', selector: 'tr[data-anchor]', count: 23, widths: [375, 560, 1280] },
  ];
  for (const { name, selector, count, widths } of cases) for (const width of widths) for (const order of ['markers-first', 'pins-first']) {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    await page.goto('file://' + resolve(root, 'docs/specs/jev-suggestions.spec.html'));
    await page.setContent(spec, { waitUntil: 'load' });
    assert.equal(await page.evaluate(mount, { css, code, selector, order }), count, `${width} px ${order}: all ${count} ${name} blocks carry one marker and a pin`);
    // A second placement (the runtime re-renders pins every 2 s and on resize) lands on the same spots.
    const first = await page.evaluate(measure, selector);
    await page.evaluate(() => window.hxPlace());
    assert.deepEqual(await page.evaluate(measure, selector), first, `${width} px ${order}: re-render is stable`);
    if (name === 'row') assert.ok(first.some(r => r.anchor === 'note-reproof'), 'note-reproof row measured');
    for (const r of first) {
      const at = `${width} px ${order} ${name} ${r.anchor}: `;
      assert.ok(!r.pinOnText, at + 'pin covers no text');
      assert.ok(!r.markerOnText, at + 'marker covers no text');
      assert.ok(!r.pinOnMarker, at + 'pin covers no marker or marker tap pad');
      assert.ok(r.pinInside, at + 'pin stays inside its block');
      assert.ok(r.pinTap, at + 'a tap on the pin hits the pin');
      assert.ok(r.markerTap, at + 'a tap on the marker hits the marker');
    }
    if (shots && name === 'criterion' && order === 'pins-first') {
      await page.evaluate(() => document.querySelector('[data-anchor="acceptance-cache"]').scrollIntoView({ block: 'center' }));
      await page.screenshot({ path: resolve(shots, `pin-marker-${width}.png`) });
    }
    await page.close();
  }
} finally {
  await browser.close();
}

console.log('runtime pin marker tests passed');
