import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { createServer } from 'node:net';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

// Pages served at another address (review-service #served-rule, #proof-served-seam; #acceptance-served-threads,
// -handoff, -index, -focus): the QA fixture's served-* collections (tests/fixtures/qa reset.sh + spools.py) on the
// fixture service (serve.py), public through the registry's public base address while on loopback, behind the
// fixture's test host (host.py), which serves each page with a base address into a frame with no origin.
// Headless Chromium. Needs PLAYWRIGHT_CORE=<path to playwright-core>.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
assert.equal(readFileSync(resolve(root, 'docs/specs/.viz/runtime.js'), 'utf8'), runtime, 'runtime copies are identical');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_CORE || 'playwright-core');

const fixture = resolve(root, 'tests/fixtures/qa');
const qaRoot = mkdtempSync(join(tmpdir(), 'served-host-'));
const env = { ...process.env, QA_ROOT: qaRoot, QA_FIXTURE: fixture, QA_REPO_SPEC_CHAT: root };
execFileSync('sh', [join(fixture, 'reset.sh')], { env, stdio: 'inherit' });
const freePort = () => new Promise(res => { const s = createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); });
const [port, hostPort] = [await freePort(), await freePort()];
const service = `http://127.0.0.1:${port}`;
const host = `http://127.0.0.1:${hostPort}`;
const start = argv => spawn('python3', argv, {
  stdio: 'ignore', detached: true,
  env: { ...env, HOME: join(qaRoot, 'home'), XDG_STATE_HOME: join(qaRoot, 'state'), XDG_CONFIG_HOME: join(qaRoot, 'config'), HERDR_SOCKET_PATH: '' },
});
const ready = async url => {
  for (let i = 0; ; i++) {
    try { if ((await fetch(url)).ok) return; } catch {}
    if (i > 150) throw new Error('not ready: ' + url);
    await new Promise(r => setTimeout(r, 100));
  }
};
const servers = [start([join(fixture, 'serve.py'), join(qaRoot, 'serve/registry.toml'), String(port)])];
// Stop the servers on any exit, also when a step throws before the finally below.
process.on('exit', () => { for (const server of servers) try { process.kill(-server.pid); } catch {} });
await ready(`${service}/qa-fixture/docs/specs/report.spec.html`);
servers.push(start([join(fixture, 'host.py'), service, String(hostPort)]));
await ready(`${host}/`);

const REPORT = 'docs/specs/report.spec.html';
const TOTALS = 'p[data-anchor="report-totals"]';
const human = collection => join(qaRoot, 'site', collection, REPORT + '.review/human');
const spoolEvents = collection => { try { return readdirSync(human(collection)).sort().map(n => JSON.parse(readFileSync(join(human(collection), n), 'utf8'))); } catch { return []; } };
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
const errors = [];

// The reviewer opens a collection's report spec in the host; returns the host page and its frame, once connected.
const open = async collection => {
  const page = await context.newPage();
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto(`${host}/${collection}/${REPORT}`);
  const frame = await (await page.waitForSelector('iframe')).contentFrame();
  await frame.waitForFunction(() => /connected/.test(document.getElementById('hx-status')?.textContent || ''));
  return { page, frame };
};
// Where the frame's document is: its origin, location, and own (base) address.
const where = frame => frame.evaluate(() => ({ origin: self.origin, path: location.pathname, page: new URL(location.href).searchParams.get('page'), base: document.baseURI }));
const served = async (frame, address) => {
  assert.deepEqual(await where(frame), { origin: 'null', path: '/frame', page: address, base: address }, 'served by the host with no origin');
};
const threads = frame => frame.evaluate(() => [...document.querySelectorAll('#hx-threads .hx-thread')].map(t => ({
  status: t.querySelector('.hx-pill')?.textContent,
  texts: [...t.querySelectorAll('.hx-msg .hx-text')].map(m => m.textContent),
})));

try {
  // #acceptance-served-threads: the spec with its comment thread, read from the spool its base address names.
  let { page, frame } = await open('served-threads');
  await served(frame, `${service}/served-threads/${REPORT}`);
  await frame.waitForSelector('#hx-threads .hx-thread', { state: 'attached' });
  assert.equal(await frame.textContent('h1'), 'Weekly report');
  assert.deepEqual((await threads(frame)).map(t => t.texts), [['Say which columns the CSV holds.', 'Done; OK to resolve?']], 'the seeded thread');
  assert.equal(await frame.locator('[data-anchor="report-export"] .hx-pin').count(), 1, 'its pin on the commented block');
  await page.close();

  // #acceptance-served-handoff: a comment saved and handed off from the frame shows pending, and is in the spool.
  ({ page, frame } = await open('served-handoff'));
  await served(frame, `${service}/served-handoff/${REPORT}`);
  await frame.click('#hx-mode');
  await frame.click(TOTALS);
  await frame.fill('.hx-composer textarea', 'Saved in the host frame.');
  await frame.click('.hx-composer [data-act=save]');
  await frame.waitForFunction(() => [...document.querySelectorAll('#hx-threads .hx-thread')].some(t => t.querySelector('.hx-pill')?.textContent === 'draft'));
  await frame.click('#hx-handoff');
  await frame.waitForFunction(() => [...document.querySelectorAll('#hx-threads .hx-thread')].some(t =>
    t.querySelector('.hx-msg .hx-text')?.textContent === 'Saved in the host frame.' && t.querySelector('.hx-pill')?.textContent === 'pending'), null, { timeout: 5000 });
  assert.deepEqual((await threads(frame)).map(t => t.status), ['pending'], 'the one thread is pending');
  const events = spoolEvents('served-handoff');
  const saved = events.find(e => e.event === 'comment');
  assert.equal(saved.text, 'Saved in the host frame.');
  assert.deepEqual(events.find(e => e.event === 'handoff').events, [saved.id], 'the hand-off lists the comment');
  await page.close();

  // #acceptance-served-index: Back to Spec Chat index shows the review index in the host's frame.
  ({ page, frame } = await open('served-index'));
  const hostPage = page.url();
  await frame.click('.hx-service-index-link');
  await frame.waitForURL(url => new URL(url).searchParams.get('page') === `${service}/`);
  await frame.waitForSelector('h1');
  assert.equal(await frame.textContent('h1'), 'Review index');
  assert.equal(await frame.title(), 'Spec Chat index');
  await served(frame, `${service}/`);
  assert.equal(page.url(), hostPage, 'the host page stays; only its frame moved');
  await page.close();

  // #acceptance-served-focus: Change base re-diffs against the chosen commit; the frame's location stays the host's.
  ({ page, frame } = await open('served-focus'));
  const address = `${service}/served-focus/${REPORT}`;
  const git = (...args) => execFileSync('git', ['-C', join(qaRoot, 'site/served-focus'), ...args], { encoding: 'utf8' }).trim();
  const [head, base] = git('rev-parse', 'HEAD', 'HEAD~1').split('\n');
  const choose = async commit => {
    await frame.click('#hx-range-change');
    await frame.click(`.hx-range-commit[data-base="${commit}"]`);
    await frame.waitForFunction(id => document.querySelector('#hx-range-copy .hx-range-id')?.title === id, commit);
  };
  const changed = () => frame.evaluate(() => [...document.querySelectorAll('[data-hx-focus=changed]')].map(el => el.dataset.anchor));
  await choose(head);
  assert.deepEqual(await changed(), [], 'no change against head');
  assert.equal(await frame.evaluate(() => document.body.classList.contains('hx-focus-active')), false);
  await choose(base);
  assert.ok((await changed()).includes('report-export'), 'the export criterion added since base shows changed');
  assert.ok(!(await changed()).includes('report-totals'), 'the totals criterion, unchanged since base, does not');
  assert.equal(await frame.evaluate(() => document.body.classList.contains('hx-focus-active')), true);
  assert.match(await frame.textContent('#hx-range-copy'), new RegExp('^Changes from ' + base.slice(0, 7)));
  await served(frame, address);
  await page.close();

  assert.deepEqual(errors, [], 'no page errors');
} finally {
  await browser.close();
  for (const server of servers) try { process.kill(-server.pid); } catch {}
  rmSync(qaRoot, { recursive: true, force: true });
}

console.log('runtime served host tests passed');
