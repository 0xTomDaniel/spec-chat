import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

// Hovering an unsure note shows its sentence inside the box the popover was placed with, so the
// popover stays inside the viewport, even for a near-bottom marker, never covers its marker, and
// never widens the page (jev-suggestions
// #neutral-questions-rule, #markers-open, #markers-mobile, #acceptance-neutral). Runs the real
// runtime CSS and popover code in headless Chromium. Needs PLAYWRIGHT_CORE=<path to playwright-core>.
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
const code = ['mountJevMarker', 'placeJevMarker', 'jevPopoverKeeps', 'scheduleJevPopoverClose', 'jevPopoverElement', 'renderJevNote', 'openJevPopover', 'closeJevPopover', 'placeJevPopover']
  .map(name => slice(`function ${name}(`, '\n}\n')).join('\n');
const spec = readFileSync(resolve(root, 'docs/specs/jev-suggestions.spec.html'), 'utf8')
  .replace(/<script[^>]*runtime\.js[^>]*><\/script>/, '')
  .replace('./.style/spec.css', 'file://' + resolve(root, 'docs/specs/.style/spec.css'));
const sentence = 'Jev could not tell which story this criterion verifies, so it asks instead of guessing';

function mount({ css, code, sentence, nearBottom }) {
  document.head.insertAdjacentHTML('beforeend', '<style>' + css + '</style>');
  document.body.style.paddingBottom = '100vh';
  const holder = document.querySelector('[data-acceptance-criterion]');
  const jevPopoverState = { element: null, marker: null, closeTimer: 0, wired: false };
  const api = new Function('jevPopoverState', 'wireJevPopover', code + 'return { mountJevMarker };')(jevPopoverState, () => {});
  api.mountJevMarker(holder, [{ text: 'story?', group: 'neutral', state: 'unsure', sentence }]);
  holder.scrollIntoView({ block: 'center' });
  // Near-bottom: the marker sits just above the viewport's bottom edge, so a popover below it cannot fit.
  if (nearBottom) scrollBy(0, document.querySelector('.hx-jev-marker').getBoundingClientRect().bottom - (innerHeight - 40));
}

function measure() {
  const pop = document.querySelector('.hx-jev-pop').getBoundingClientRect();
  const marker = document.querySelector('.hx-jev-marker').getBoundingClientRect();
  const shown = document.querySelector('.hx-jev-pop-sentence');
  return { left: pop.left, right: pop.right, top: pop.top, bottom: pop.bottom, width: pop.width, shown: getComputedStyle(shown).display !== 'none',
    inside: pop.left >= 0 && pop.right <= innerWidth && pop.top >= 0 && pop.bottom <= innerHeight,
    coversMarker: pop.left < marker.right && pop.right > marker.left && pop.top < marker.bottom && pop.bottom > marker.top,
    pageWidth: document.documentElement.scrollWidth, viewport: document.documentElement.clientWidth };
}

const browser = await chromium.launch();
try {
  for (const width of [375, 1280, 1440]) {
    for (const nearBottom of [false, true]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      await page.goto('file://' + resolve(root, 'docs/specs/jev-suggestions.spec.html'));
      await page.setContent(spec, { waitUntil: 'load' });
      await page.evaluate(mount, { css, code, sentence, nearBottom });
      await page.hover('.hx-jev-marker');
      const before = await page.evaluate(measure);
      const at = `${width} px${nearBottom ? ', near-bottom marker' : ''}: `;
      assert.ok(!before.shown, at + 'sentence hidden before hovering the note');
      await page.hover('.hx-jev-pop-text');
      const after = await page.evaluate(measure);
      assert.ok(after.shown, at + 'hover shows the sentence');
      assert.ok(after.inside, at + `popover stays in the viewport (${after.left}..${after.right}, ${after.top}..${after.bottom})`);
      assert.ok(after.pageWidth <= after.viewport, at + `page does not widen (${after.pageWidth} > ${after.viewport})`);
      if (width > 640) {
        assert.ok(!after.coversMarker, at + 'popover never covers its marker');
        assert.deepEqual([after.left, after.top, after.width], [before.left, before.top, before.width], at + 'popover keeps its placed box');
      }
      await page.close();
    }
  }
} finally {
  await browser.close();
}

console.log('runtime jev popover tests passed');
