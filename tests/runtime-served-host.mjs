import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

// Pages served at another address (review-service #served-rule, #proof-served-seam; #acceptance-served-threads,
// -handoff, -index, -focus): the QA fixture's served-* collections (tests/fixtures/qa reset.sh + spools.py) on the
// fixture service (serve.py), public through the registry's public base address while on loopback, behind the
// fixture's test host (host.py), which serves each page with a base address into a frame with no origin. On a phone
// (prompt-first-shaping #tdd-mobile-handoff, #acceptance-mobile-*; review-service #acceptance-served-reload-*), the
// panel's one hand-off bar in the bottom toolbar, on a page served directly and in the host's frame, which keeps the
// fruit name and outbox across a reload (review-service #served-storage); the fixture's wake provider is a fake that
// logs each wake. Headless Chromium. Needs PLAYWRIGHT_CORE=<path to playwright-core>.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
assert.equal(readFileSync(resolve(root, 'docs/specs/.viz/runtime.js'), 'utf8'), runtime, 'runtime copies are identical');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_CORE || 'playwright-core');

const fixture = resolve(root, 'tests/fixtures/qa');
const REPORT = 'docs/specs/report.spec.html';
const qaRoot = mkdtempSync(join(tmpdir(), 'served-host-'));
const env = { ...process.env, QA_ROOT: qaRoot, QA_FIXTURE: fixture, QA_REPO_SPEC_CHAT: root };
execFileSync('sh', [join(fixture, 'reset.sh')], { env, stdio: 'inherit' });
// The service's wake provider (review-service #providers): every owner is present, and each send logs the spec it wakes.
const wakes = join(qaRoot, 'wakes.log');
mkdirSync(join(qaRoot, 'serve/providers'), { recursive: true });
writeFileSync(join(qaRoot, 'serve/providers/wake.toml'), `check = ${JSON.stringify(['true'])}\nsend = ${JSON.stringify(['sh', '-c', 'printf "%s\\n" "$1" >> "$0"', wakes, '{artifact}'])}\n`);
const woken = collection => { try { return readFileSync(wakes, 'utf8').split('\n').filter(line => line.endsWith(`/${collection}/${REPORT}`)).length; } catch { return 0; } };
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

const TOTALS = 'p[data-anchor="report-totals"]';
const EXPORT = 'p[data-anchor="report-export"]';
const STORY = 'p[data-anchor="story-totals"]';
const human = collection => join(qaRoot, 'site', collection, REPORT + '.review/human');
const spoolEvents = collection => { try { return readdirSync(human(collection)).sort().map(n => JSON.parse(readFileSync(join(human(collection), n), 'utf8'))); } catch { return []; } };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const until = async (check, what, ms = 5000) => {
  for (const end = Date.now() + ms; Date.now() < end; await sleep(100)) if (check()) return;
  assert.fail('timed out: ' + what);
};
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
const errors = [];

const connected = frame => frame.waitForFunction(() => /connected/.test(document.getElementById('hx-status')?.textContent || ''));
// The reviewer opens a collection's report spec in the host (or served directly); returns the page and the frame the
// spec runs in, once connected.
const open = async (collection, { on = context, direct = false } = {}) => {
  const page = await on.newPage();
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto(`${direct ? service : host}/${collection}/${REPORT}`);
  const frame = direct ? page.mainFrame() : await (await page.waitForSelector('iframe')).contentFrame();
  await connected(frame);
  return { page, frame };
};
// The spec reloads in its frame, the host page staying.
const reload = async frame => {
  await frame.evaluate(() => { window.reloaded = false; setTimeout(() => location.reload(), 0); });
  await frame.waitForFunction(() => !('reloaded' in window) && /connected/.test(document.getElementById('hx-status')?.textContent || ''));
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
const shows = (frame, text, status) => frame.waitForFunction(([text, status]) => [...document.querySelectorAll('#hx-threads .hx-thread')].some(t =>
  t.querySelector('.hx-msg .hx-text')?.textContent === text && t.querySelector('.hx-pill')?.textContent === status), [text, status], { timeout: 5000 });
const panelOpen = frame => frame.evaluate(() => document.querySelector('.hx-panel').classList.contains('open'));
// On a phone: Comment, then a tap on the block, then the composer's Comment; saving leaves the panel open.
const phoneComment = async (frame, block, text) => {
  if (await panelOpen(frame)) await frame.tap('#hx-panel-toggle');
  await frame.tap('#hx-mode');
  await frame.tap(block);
  await frame.fill('.hx-composer textarea', text);
  await frame.tap('.hx-composer [data-act=save]');
  await shows(frame, text, 'Draft');
};
// The bottom toolbar's drafts note and shown buttons, and every shown hand-off control on the screen.
const toolbar = frame => frame.evaluate(() => {
  const bar = document.querySelector('.hx-toolbar');
  return {
    drafts: bar.querySelector('#hx-drafts')?.textContent ?? null,
    buttons: [...bar.querySelectorAll('button')].filter(b => b.checkVisibility()).map(b => ({ text: b.textContent, tall: b.getBoundingClientRect().height >= 44 })),
  };
});
const handoffControls = frame => frame.evaluate(() => [...document.querySelectorAll('button')]
  .filter(b => b.checkVisibility() && /Hand off|Next TBD|TBD open|Accept spec/.test(b.textContent)).map(b => b.textContent));
const draftsNote = (frame, text) => frame.waitForFunction(text => document.querySelector('.hx-toolbar #hx-drafts')?.textContent === text, text, { timeout: 5000 });
const notice = (frame, text) => frame.waitForFunction(text => document.getElementById('hx-toast')?.textContent === text, text, { timeout: 5000 });
// The spool's comment with this text, and its hand-off, once stored.
const handedOff = async (collection, text) => {
  await until(() => spoolEvents(collection).some(e => e.event === 'handoff'), collection + ' hand-off stored');
  const events = spoolEvents(collection);
  const saved = events.find(e => e.event === 'comment' && e.text === text);
  assert.ok(saved, 'the comment is stored');
  assert.deepEqual(events.filter(e => e.event === 'handoff').map(e => e.events), [[saved.id]], 'one hand-off, listing the comment');
};
const HANDED_OFF = 'Handed off 1 comment, agent notified';

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
  await frame.waitForFunction(() => [...document.querySelectorAll('#hx-threads .hx-thread')].some(t => t.querySelector('.hx-pill')?.textContent === 'Draft'));
  await frame.click('#hx-handoff');
  await frame.waitForFunction(() => [...document.querySelectorAll('#hx-threads .hx-thread')].some(t =>
    t.querySelector('.hx-msg .hx-text')?.textContent === 'Saved in the host frame.' && t.querySelector('.hx-pill')?.textContent === 'Pending'), null, { timeout: 5000 });
  assert.deepEqual((await threads(frame)).map(t => t.status), ['Pending'], 'the one thread is pending');
  await handedOff('served-handoff', 'Saved in the host frame.'); // shown pending at once, stored once the service answers
  await page.close();

  // #acceptance-served-index: the Review index link shows the review index in the host's frame.
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

  // Phone, served directly. #acceptance-mobile-review: the toolbar holds the drafts note, Next TBD (2), and Hand off.
  ({ page, frame } = await open('mobile-handoff', { on: phone, direct: true }));
  await phoneComment(frame, TOTALS, 'Saved on a phone.');
  assert.deepEqual(await toolbar(frame), { drafts: '1 draft', buttons: [
    { text: 'Comment', tall: true }, { text: 'Next TBD (2)', tall: true }, { text: 'Hand off to agent →', tall: true }, { text: 'Reading view', tall: true },
  ] }, 'the panel\'s one hand-off bar in the bottom toolbar, every control 44 pixels tall');
  // #acceptance-mobile-one-control: with the panel open, one Hand off and one Next TBD (2) on the screen.
  assert.equal(await panelOpen(frame), true);
  assert.deepEqual(await handoffControls(frame), ['Next TBD (2)', 'Hand off to agent →'], 'no second Hand off or Next TBD');
  // #acceptance-mobile-next-tbd: the first open TBD in document order, in view and focused, the panel out of its way.
  await frame.tap('.hx-toolbar #hx-next-tbd');
  await frame.waitForFunction(() => {
    const el = document.activeElement, box = el.getBoundingClientRect();
    return el.dataset.anchor === 'export-format' && box.top >= 0 && box.bottom <= innerHeight;
  }, null, { timeout: 5000 });
  assert.equal(await panelOpen(frame), false);
  // #acceptance-mobile-handoff, -handoff-wake: tapped with the panel closed, it lands in the spool and wakes the owner once.
  await frame.tap('.hx-toolbar #hx-handoff');
  await notice(frame, HANDED_OFF);
  await draftsNote(frame, '0 drafts');
  await handedOff('mobile-handoff', 'Saved on a phone.');
  await until(() => woken('mobile-handoff') > 0, 'the owner is woken', 12000);
  await sleep(4000); // one more wake poll and its settle time
  assert.equal(woken('mobile-handoff'), 1, 'woken once for the hand-off');
  // #acceptance-mobile-count: the note counts each saved comment.
  await phoneComment(frame, EXPORT, 'First on a phone.');
  await draftsNote(frame, '1 draft');
  await phoneComment(frame, STORY, 'Second on a phone.');
  await draftsNote(frame, '2 drafts');
  await page.close();

  // #acceptance-mobile-handoff-frame, review-service #acceptance-served-reload-handoff: in the host's frame, a comment
  // saved, the page reloaded, then Hand off in the toolbar hands it off.
  ({ page, frame } = await open('served-reload-handoff', { on: phone }));
  await served(frame, `${service}/served-reload-handoff/${REPORT}`);
  await phoneComment(frame, TOTALS, 'Saved on a phone in the host frame.');
  await reload(frame);
  await served(frame, `${service}/served-reload-handoff/${REPORT}`);
  await draftsNote(frame, '1 draft');
  await frame.tap('.hx-toolbar #hx-handoff');
  await notice(frame, HANDED_OFF);
  await draftsNote(frame, '0 drafts');
  await handedOff('served-reload-handoff', 'Saved on a phone in the host frame.');
  await page.close();

  // review-service #acceptance-served-reload-outbox: saved while the service is unreachable, sent after a reload.
  ({ page, frame } = await open('served-reload-outbox', { on: phone }));
  let reachable = false;
  await page.route(url => url.pathname.endsWith('/api/events') && url.searchParams.has('name'), route => reachable ? route.continue() : route.abort('internetdisconnected'));
  await phoneComment(frame, TOTALS, 'Saved offline in the host frame.');
  await frame.waitForFunction(() => document.getElementById('hx-offline').textContent === 'Offline: 1 change waiting', null, { timeout: 5000 });
  assert.deepEqual(spoolEvents('served-reload-outbox'), [], 'nothing stored while unreachable');
  reachable = true;
  await reload(frame);
  await shows(frame, 'Saved offline in the host frame.', 'Draft');
  await draftsNote(frame, '1 draft');
  await until(() => spoolEvents('served-reload-outbox').some(e => e.text === 'Saved offline in the host frame.'), 'the waiting comment is stored');
  await page.close();

  assert.deepEqual(errors, [], 'no page errors');
} finally {
  await browser.close();
  for (const server of servers) try { process.kill(-server.pid); } catch {}
  rmSync(qaRoot, { recursive: true, force: true });
}

console.log('runtime served host tests passed');
