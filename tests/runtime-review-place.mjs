import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { createServer } from 'node:net';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

// Marks through agent edits on the page (review-state #anchoring-states, #acceptance-anchor-moved,
// -changed, -gone, -stale-page): the full runtime on the QA fixture's review-state collections
// (tests/fixtures/qa reset.sh + spools.py), served by the fixture's serve.py, in headless Chromium.
// Needs PLAYWRIGHT_CORE=<path to playwright-core>.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
assert.equal(readFileSync(resolve(root, 'docs/specs/.viz/runtime.js'), 'utf8'), runtime, 'runtime copies are identical');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_CORE || 'playwright-core');

const fixture = resolve(root, 'tests/fixtures/qa');
const qaRoot = mkdtempSync(join(tmpdir(), 'review-place-'));
const env = { ...process.env, QA_ROOT: qaRoot, QA_FIXTURE: fixture, QA_REPO_SPEC_CHAT: root };
execFileSync('sh', [join(fixture, 'reset.sh')], { env, stdio: 'inherit' });
const port = await new Promise(res => { const s = createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); });
const server = spawn('python3', [join(fixture, 'serve.py'), join(qaRoot, 'serve/registry.toml'), String(port)], {
  stdio: 'ignore', detached: true,
  env: { ...env, HOME: join(qaRoot, 'home'), XDG_STATE_HOME: join(qaRoot, 'state'), XDG_CONFIG_HOME: join(qaRoot, 'config'), HERDR_SOCKET_PATH: '' },
});
const url = collection => `http://127.0.0.1:${port}/${collection}/docs/specs/report.spec.html`;
for (let i = 0; ; i++) {
  try { if ((await fetch(url('anchor-moved'))).ok) break; } catch {}
  if (i > 150) throw new Error('qa serve did not start');
  await new Promise(r => setTimeout(r, 100));
}

const SENTENCE = 'The export button downloads a CSV of the current table.';
const EXPORT = 'p[data-anchor="report-export"]';
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
const errors = [];
const open = async collection => {
  const page = await context.newPage();
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto(url(collection));
  await page.waitForFunction(() => /connected/.test(document.getElementById('hx-status')?.textContent || ''));
  await page.waitForSelector('#hx-threads .hx-thread', { state: 'attached' });
  return page;
};
// The one thread's mark as the page shows it, beside the service's place for it.
const shown = page => page.evaluate(async () => {
  const dir = location.pathname.replace(/^\//, '') + '.review';
  const events = await (await fetch('/api/events?dir=' + encodeURIComponent(dir))).json();
  const root = events.find(e => e.body.event === 'comment');
  const pins = [...document.querySelectorAll('.hx-pin')];
  const pin = pins[0];
  const holder = pin && pin.parentElement.closest('[data-anchor]');
  const textRect = (el, needle) => {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    let text = '', node; const parts = [];
    while ((node = walker.nextNode())) { if (node.parentElement.closest('.hx-pin')) continue; parts.push({ node, start: text.length }); text += node.data; }
    const start = text.indexOf(needle);
    if (start < 0) return null;
    const at = i => { const p = parts.filter(p => p.start <= i).pop(); return [p.node, i - p.start]; };
    const range = document.createRange();
    range.setStart(...at(start));
    range.setEnd(...at(start + needle.length - 1));
    range.setEnd(range.endContainer, range.endOffset + 1);
    const r = range.getBoundingClientRect();
    return { top: r.top, bottom: r.bottom };
  };
  const pr = pin && pin.getBoundingClientRect();
  const tr = holder && root.place.quote ? textRect(holder, root.place.quote) : null;
  const notice = document.querySelector('#hx-threads .hx-thread .hx-place-notice');
  return {
    place: root.place, pins: pins.length, holder: holder && holder.dataset.anchor,
    pinOnText: Boolean(pr && tr && pr.top < tr.bottom && pr.bottom > tr.top),
    notice: notice ? notice.textContent : null,
    noticeQuote: notice ? notice.querySelector('.hx-quote')?.textContent : null,
  };
});

try {
  // #acceptance-anchor-moved: a paragraph inserted above; same sentence, no notice.
  let page = await open('anchor-moved');
  let s = await shown(page);
  assert.equal(s.place.state, 'kept');
  assert.equal(s.pins, 1);
  assert.equal(s.holder, 'report-export', 'pin on the same sentence block');
  assert.equal(s.pinOnText, true, 'pin beside the sentence text');
  assert.equal(s.notice, null, 'no change notice');
  await page.close();

  // #acceptance-anchor-changed: part rewritten; pin on the remaining text, changed notice with the original quote.
  page = await open('anchor-changed');
  s = await shown(page);
  assert.equal(s.place.state, 'changed');
  assert.equal(s.holder, s.place.anchorId);
  assert.equal(s.pinOnText, true, 'pin beside the remaining text');
  assert.match(s.notice, /^Text changed since this comment/);
  assert.equal(s.noticeQuote, '“' + SENTENCE + '”', 'original quote');
  await page.close();

  // #acceptance-anchor-gone: sentence deleted; removed notice with the original quote, pin on the nearest surviving block.
  page = await open('anchor-gone');
  s = await shown(page);
  assert.equal(s.place.state, 'gone');
  assert.ok(s.place.anchorId && s.place.anchorId !== 'report-export');
  assert.equal(await page.locator(EXPORT).count(), 0, 'the sentence is gone');
  assert.equal(s.pins, 1);
  assert.equal(s.holder, s.place.anchorId, 'pin on the nearest surviving anchored block');
  assert.match(s.notice, /^Text removed/);
  assert.equal(s.noticeQuote, '“' + SENTENCE + '”', 'original quote');
  await page.close();

  // #acceptance-anchor-stale-page: page loaded before the agent's insertion above; the comment saves after it lands.
  page = await context.newPage();
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto(url('stale-page'));
  await page.waitForFunction(() => /connected/.test(document.getElementById('hx-status')?.textContent || ''));
  await page.click('#hx-mode');
  await page.evaluate(sel => {
    const p = document.querySelector(sel);
    const range = document.createRange();
    range.selectNodeContents(p);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    p.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
  }, EXPORT);
  await page.fill('.hx-composer textarea', 'Saved from a stale page.');
  await page.click('.hx-composer [data-act=save]');
  await page.waitForFunction(() => localStorage.getItem('spec-chat:outbox:stale-page/docs/specs/report.spec.html.review') === '[]', null, { timeout: 5000 });
  await page.close();
  page = await open('stale-page');
  assert.equal(await page.locator('p[data-anchor="report-scope"]').count(), 1, 'the agent edit landed');
  s = await shown(page);
  assert.equal(s.place.state, 'kept');
  assert.equal(s.holder, 'report-export', 'pin on that same sentence');
  assert.equal(s.pinOnText, true);
  assert.equal(s.notice, null);
  await page.close();

  assert.deepEqual(errors, [], 'no page errors');
} finally {
  await browser.close();
  try { process.kill(-server.pid); } catch {}
  rmSync(qaRoot, { recursive: true, force: true });
}

console.log('runtime review place tests passed');
