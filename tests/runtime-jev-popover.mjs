import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

// Hovering an unsure note shows its sentence without widening the popover past where it was
// placed, so the popover stays inside the viewport and never widens the page (jev-suggestions
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

function mount({ css, code, sentence }) {
  document.head.insertAdjacentHTML('beforeend', '<style>' + css + '</style>');
  const holder = document.querySelector('[data-acceptance-criterion]');
  const jevPopoverState = { element: null, marker: null, closeTimer: 0, wired: false };
  const api = new Function('jevPopoverState', 'wireJevPopover', code + 'return { mountJevMarker };')(jevPopoverState, () => {});
  api.mountJevMarker(holder, [{ text: 'story?', group: 'neutral', state: 'unsure', sentence }]);
  holder.scrollIntoView({ block: 'center' });
}

function measure() {
  const pop = document.querySelector('.hx-jev-pop').getBoundingClientRect();
  const shown = document.querySelector('.hx-jev-pop-sentence');
  return { left: pop.left, right: pop.right, width: pop.width, shown: getComputedStyle(shown).display !== 'none',
    inside: pop.left >= 0 && pop.right <= innerWidth, pageWidth: document.documentElement.scrollWidth, viewport: document.documentElement.clientWidth };
}

const browser = await chromium.launch();
try {
  for (const width of [375, 1280, 1440]) {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    await page.goto('file://' + resolve(root, 'docs/specs/jev-suggestions.spec.html'));
    await page.setContent(spec, { waitUntil: 'load' });
    await page.evaluate(mount, { css, code, sentence });
    await page.hover('.hx-jev-marker');
    const before = await page.evaluate(measure);
    assert.ok(!before.shown, `${width} px: sentence hidden before hovering the note`);
    await page.hover('.hx-jev-pop-text');
    const after = await page.evaluate(measure);
    const at = `${width} px: `;
    assert.ok(after.shown, at + 'hover shows the sentence');
    assert.ok(after.inside, at + `popover stays in the viewport (${after.left}..${after.right})`);
    assert.ok(after.pageWidth <= after.viewport, at + `page does not widen (${after.pageWidth} > ${after.viewport})`);
    if (width > 640) assert.deepEqual([after.left, after.width], [before.left, before.width], at + 'popover keeps its placed box');
    await page.close();
  }
} finally {
  await browser.close();
}

console.log('runtime jev popover tests passed');
