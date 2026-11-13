import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { createServer } from 'node:net';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

// Pages served at another address (served-pages #served-rule, #proof-served-seam; #acceptance-served-threads,
// -handoff, -index, -focus): the QA fixture's served-* collections (tests/fixtures/qa reset.sh + spools.py) on the
// fixture service (serve.py), public through the registry's public base address while on loopback, behind the
// fixture's test host (host.py), which serves each page with a base address into a frame with no origin. On a phone
// (prompt-first-shaping #tdd-mobile-handoff, #acceptance-mobile-*; served-pages #acceptance-served-reload-*), the
// panel's one hand-off bar in the bottom toolbar, on a page served directly and in the host's frame, which keeps the
// fruit name and outbox across a reload (served-pages #served-storage); the fixture's wake provider (reset.sh) is a
// fake that logs each wake. The host also frames a second review service, on reset.sh's serve-second registry (qa.toml
// review-second), and names its frame on /named/ (served-pages #acceptance-served-storage-*). Headless Chromium. Needs
// PLAYWRIGHT_CORE=<path to playwright-core>.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
assert.equal(readFileSync(resolve(root, 'docs/specs/.viz/runtime.js'), 'utf8'), runtime, 'runtime copies are identical');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_CORE || 'playwright-core');

const fixture = resolve(root, 'tests/fixtures/qa');
const REPORT = 'docs/specs/report.spec.html';
const qaRoot = mkdtempSync(join(tmpdir(), 'served-host-'));
const env = { ...process.env, QA_ROOT: qaRoot, QA_FIXTURE: fixture, QA_REPO_SPEC_CHAT: root };
execFileSync('sh', [join(fixture, 'reset.sh')], { env, stdio: 'inherit' });
// The service's wake provider (reset.sh): every owner is present, and each send logs the spec it wakes.
const wakes = join(qaRoot, 'wakes.log');
const woken = collection => { try { return readFileSync(wakes, 'utf8').split('\n').filter(line => line.endsWith(`/${collection}/${REPORT}`)).length; } catch { return 0; } };
const freePort = () => new Promise(res => { const s = createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); });
const [port, secondPort, hostPort] = [await freePort(), await freePort(), await freePort()];
const service = `http://127.0.0.1:${port}`;
const second = `http://127.0.0.1:${secondPort}`;
const host = `http://127.0.0.1:${hostPort}`;
const start = (argv, state = 'state') => spawn('python3', argv, {
  stdio: 'ignore', detached: true,
  env: { ...env, HOME: join(qaRoot, 'home'), XDG_STATE_HOME: join(qaRoot, state), XDG_CONFIG_HOME: join(qaRoot, 'config'), HERDR_SOCKET_PATH: '' },
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
servers.push(start([join(fixture, 'serve.py'), join(qaRoot, 'serve-second/registry.toml'), String(secondPort)], 'state-second'));
await ready(`${service}/qa-fixture/docs/specs/report.spec.html`);
await ready(`${second}/served-storage-return/${REPORT}`);
servers.push(start([join(fixture, 'host.py'), service, second, String(hostPort)]));
await ready(`${host}/`);

const TOTALS = 'p[data-anchor="report-totals"]';
const EXPORT = 'p[data-anchor="report-export"]';
const STORY = 'p[data-anchor="story-totals"]';
const human = (collection, site = 'site') => join(qaRoot, site, collection, REPORT + '.review/human');
// The spool's events, read by the spool's own rule (spool.py read_events): a dot name is a write not yet linked in. The
// second service holds served-storage-service at its own root, under site-second (reset.sh).
const spoolEvents = (collection, site = 'site') => {
  let names;
  try { names = readdirSync(human(collection, site)); } catch { return []; }
  return names.filter(n => !n.startsWith('.') && n.endsWith('.json')).sort().map(n => JSON.parse(readFileSync(join(human(collection, site), n), 'utf8')));
};
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
const open = async (collection, { on = context, direct = false, named = false } = {}) => {
  const page = await on.newPage();
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto(`${direct ? service : host}/${named ? 'named/' : ''}${collection}/${REPORT}`);
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
// The host's Service N opens the spec at that address in the frame. It sets the frame's address, so the frame and its
// old, still connected page stay until the new page commits: wait for the frame to load the new page, then to connect.
const press = async (page, n, address) => {
  await page.click(`header button:text-is("Service ${n}")`);
  const frame = await (await page.waitForSelector('iframe')).contentFrame();
  await frame.waitForURL(url => new URL(url).searchParams.get('page') === address);
  await connected(frame);
  await served(frame, address);
  return frame;
};
// The conversation's threads (threads #stream): each one's receipt, its shown bubbles, and its folded replies line.
const threads = frame => frame.evaluate(() => [...document.querySelectorAll('#hx-threads .hx-entry[data-thread]')].map(t => ({
  status: t.querySelector('.hx-receipt')?.textContent,
  texts: [...t.querySelectorAll('.hx-msg .hx-text')].map(m => m.textContent),
  replies: t.querySelector('.hx-replies')?.textContent ?? null,
})));
const shows = (frame, text, status) => frame.waitForFunction(([text, status]) => [...document.querySelectorAll('#hx-threads .hx-entry[data-thread]')].some(t =>
  t.querySelector('.hx-msg .hx-text')?.textContent === text && t.querySelector('.hx-receipt')?.textContent === status), [text, status], { timeout: 5000 });
const panelOpen = frame => frame.evaluate(() => document.querySelector('.hx-panel').classList.contains('open'));
// At desktop width: Comment, then a click on the block, then the composer's Comment.
const deskComment = async (frame, block, text) => {
  await frame.click('#hx-mode');
  await frame.click(block);
  await frame.fill('.hx-composer textarea', text);
  await frame.click('.hx-composer [data-act=save]');
  await shows(frame, text, 'Draft');
};
const offlineNote = (frame, text) => frame.waitForFunction(text => document.getElementById('hx-offline').textContent === text, text, { timeout: 5000 });
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
  await frame.waitForSelector('#hx-threads .hx-entry', { state: 'attached' });
  assert.equal(await frame.textContent('h1'), 'Weekly report');
  assert.deepEqual((await threads(frame)).map(t => [t.texts, t.replies]), [[['Say which columns the CSV holds.'], '1 reply · Agent: Done; OK to resolve?']],
    'the seeded thread, its reply folded under it');
  assert.equal(await frame.locator('[data-anchor="report-export"] .hx-pin').count(), 1, 'its pin on the commented block');
  await page.close();

  // #acceptance-served-handoff: a comment saved and handed off from the frame shows pending, and is in the spool.
  ({ page, frame } = await open('served-handoff'));
  await served(frame, `${service}/served-handoff/${REPORT}`);
  await frame.click('#hx-mode');
  await frame.click(TOTALS);
  await frame.fill('.hx-composer textarea', 'Saved in the host frame.');
  await frame.click('.hx-composer [data-act=save]');
  await shows(frame, 'Saved in the host frame.', 'Draft');
  await frame.click('#hx-handoff');
  await shows(frame, 'Saved in the host frame.', 'Sent');
  assert.deepEqual((await threads(frame)).map(t => t.status), ['Sent'], 'the one thread is handed off');
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
  // #acceptance-mobile-handoff, #acceptance-mobile-handoff-wake: tapped with the panel closed, it lands in the spool
  // and wakes the owner; woken once is checked after a later wake poll, below.
  await frame.tap('.hx-toolbar #hx-handoff');
  await notice(frame, HANDED_OFF);
  await draftsNote(frame, '0 drafts');
  await handedOff('mobile-handoff', 'Saved on a phone.');
  await until(() => woken('mobile-handoff') > 0, 'the owner is woken', 12000);
  await page.close();

  // #acceptance-mobile-count: on a spec with no comment thread, the note counts each saved comment.
  ({ page, frame } = await open('mobile-count', { on: phone, direct: true }));
  await phoneComment(frame, EXPORT, 'First on a phone.');
  await draftsNote(frame, '1 draft');
  await phoneComment(frame, STORY, 'Second on a phone.');
  await draftsNote(frame, '2 drafts');
  // #acceptance-one-handoff-rule-desktop: at desktop width the same one control, from the same review state, ends the panel.
  await page.setViewportSize({ width: 1280, height: 800 });
  await frame.waitForFunction(() => document.querySelector('#hx-panel-content > .hx-handoff:last-child #hx-drafts')?.textContent === '2 drafts', null, { timeout: 5000 });
  assert.deepEqual(await handoffControls(frame), ['Next TBD (2)', 'Hand off to agent →'], 'one Hand off and one Next TBD at desktop width too');
  await page.close();

  // #acceptance-mobile-handoff-frame, served-pages #acceptance-served-reload-handoff: in the host's frame, a comment
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
  // #acceptance-mobile-handoff-wake, once: this hand-off was stored after the phone's woke its owner, so the poll that
  // wakes this owner is a later one, which found the phone's batch again and did not wake its owner a second time.
  await until(() => woken('served-reload-handoff') > 0, 'the frame hand-off\'s owner is woken', 12000);
  assert.equal(woken('mobile-handoff'), 1, 'woken once for the phone\'s hand-off');
  await page.close();

  // served-pages #acceptance-served-reload-outbox: saved while the service is unreachable, sent after a reload. Its
  // writes stay refused until the reloaded page shows the comment, so the draft shown is the one the frame kept.
  ({ page, frame } = await open('served-reload-outbox', { on: phone }));
  let reachable = false;
  await page.route(url => url.pathname.endsWith('/api/events') && url.searchParams.has('name'), route => reachable ? route.continue() : route.abort('internetdisconnected'));
  const waiting = () => frame.waitForFunction(() => document.getElementById('hx-offline').textContent === 'Offline: 1 change waiting', null, { timeout: 5000 });
  await phoneComment(frame, TOTALS, 'Saved offline in the host frame.');
  await waiting();
  await reload(frame);
  await shows(frame, 'Saved offline in the host frame.', 'Draft');
  await draftsNote(frame, '1 draft');
  await waiting();
  assert.deepEqual(spoolEvents('served-reload-outbox'), [], 'nothing stored while unreachable');
  reachable = true;
  await until(() => spoolEvents('served-reload-outbox').some(e => e.text === 'Saved offline in the host frame.'), 'the waiting comment is stored');
  await page.close();

  // served-pages #acceptance-served-storage-service, -return: offline, a comment saved on the first service's spec;
  // the host's Service 2 pressed while still offline takes the frame off that page, so it sends nothing once the browser
  // is back online, and pressed again opens the second service's spec in the frame. Service 1 then opens the first
  // service's spec again, whose hand-off lists the comment.
  ({ page, frame } = await open('served-storage-return'));
  await context.setOffline(true);
  await deskComment(frame, TOTALS, 'Saved offline in the host frame.');
  await offlineNote(frame, 'Offline: 1 change waiting');
  await page.click('header button:text-is("Service 2")');
  await frame.waitForURL(url => !url.href.startsWith(host), { timeout: 5000 }); // the browser's offline page
  await context.setOffline(false);
  frame = await press(page, 2, `${second}/served-storage-return/${REPORT}`);
  assert.deepEqual(await threads(frame), [], 'the second spec shows no draft of the comment');
  assert.equal(await frame.textContent('#hx-offline'), '', 'nor waits to send it');
  assert.deepEqual(spoolEvents('served-storage-return'), [], 'and no service stored it');
  frame = await press(page, 1, `${service}/served-storage-return/${REPORT}`);
  await shows(frame, 'Saved offline in the host frame.', 'Draft');
  if (!(await panelOpen(frame))) await frame.click('#hx-dock-open');
  await frame.click('#hx-handoff');
  await notice(frame, HANDED_OFF);
  await handedOff('served-storage-return', 'Saved offline in the host frame.');
  await page.close();

  // served-pages #acceptance-served-storage-service, the frame alone offline, as a capture takes it: a comment saved
  // on the first service's spec while the frame cannot reach it waits; Service 2 pressed while offline takes the frame to
  // the browser's offline page, and pressed again opens the second service's spec. The frame's own offline state ends as
  // the frame leaves the first page, which, still running, sends its comment to its own service: here the move waits
  // until it has, so the first service always holds it. The second service's page shows no draft of it, and the second
  // service, whose spool is its own (reset.sh), stores nothing.
  ({ page, frame } = await open('served-storage-service'));
  await served(frame, `${service}/served-storage-service/${REPORT}`);
  let offline = true;
  await page.route(url => url.pathname.endsWith('/api/events') && url.searchParams.has('name'), route => offline ? route.abort('internetdisconnected') : route.continue());
  let leaving = null; // the move off the first page, offline once that page has sent its comment
  await page.route(url => url.pathname === '/frame' && url.searchParams.get('page') === `${second}/served-storage-service/${REPORT}`,
    async route => leaving ? (await leaving, leaving = null, route.abort('internetdisconnected')) : route.continue());
  await deskComment(frame, EXPORT, 'Saved offline in the host frame.');
  await offlineNote(frame, 'Offline: 1 change waiting');
  let left;
  leaving = new Promise(done => left = done);
  await page.click('header button:text-is("Service 2")', { noWaitAfter: true });
  offline = false;
  try {
    await until(() => spoolEvents('served-storage-service').some(e => e.text === 'Saved offline in the host frame.'), 'the leaving page sends its comment to its own service');
  } finally { left(); }
  await until(() => !frame.url().startsWith(host), 'the browser\'s offline page');
  frame = await press(page, 2, `${second}/served-storage-service/${REPORT}`);
  await sleep(2500); // the second page's next read, after any send it would make
  assert.deepEqual(await threads(frame), [], 'the second spec shows no draft of the comment');
  assert.equal(await frame.textContent('#hx-offline'), '', 'nor waits to send it');
  assert.deepEqual(spoolEvents('served-storage-service', 'site-second'), [], 'and the second service stored nothing');
  await page.close();

  // served-pages #acceptance-served-storage-stored: online, a comment saved and stored on the first service's spec;
  // the host's Service 2 opens the second service's spec in the frame, where a comment saved writes the frame; Service 1
  // then opens the first service's spec again as the same reviewer, so its hand-off lists the comment stored before, and
  // only that one, though the second service's comment is in the same spool.
  ({ page, frame } = await open('served-storage-stored'));
  await served(frame, `${service}/served-storage-stored/${REPORT}`);
  await deskComment(frame, TOTALS, 'Saved on the first service.');
  await until(() => spoolEvents('served-storage-stored').some(e => e.text === 'Saved on the first service.'), 'the first comment is stored');
  frame = await press(page, 2, `${second}/served-storage-stored/${REPORT}`);
  await deskComment(frame, EXPORT, 'Saved on the second service.');
  await until(() => spoolEvents('served-storage-stored').some(e => e.text === 'Saved on the second service.'), 'the second comment is stored');
  frame = await press(page, 1, `${service}/served-storage-stored/${REPORT}`);
  await shows(frame, 'Saved on the first service.', 'Draft');
  if (!(await panelOpen(frame))) await frame.click('#hx-dock-open');
  await frame.click('#hx-handoff');
  await notice(frame, HANDED_OFF);
  await handedOff('served-storage-stored', 'Saved on the first service.');
  await page.close();

  // served-pages #acceptance-served-storage-host-named: the host names the frame and clears the name once the page has
  // loaded; offline, a comment saved there waits in the page's memory, never the name, and back online it is the draft.
  ({ page, frame } = await open('served-storage-host-named', { named: true }));
  await served(frame, `${service}/served-storage-host-named/${REPORT}`);
  assert.equal(await frame.evaluate(() => window.name), '', 'the host cleared the name it gave the frame');
  await context.setOffline(true);
  await deskComment(frame, TOTALS, 'Saved offline in the host frame.');
  await offlineNote(frame, 'Offline: 1 change waiting');
  assert.equal(await frame.evaluate(() => window.name), '', 'the page keeps nothing in the name the host cleared');
  await context.setOffline(false);
  await until(() => spoolEvents('served-storage-host-named').some(e => e.text === 'Saved offline in the host frame.'), 'the waiting comment is stored');
  await offlineNote(frame, '');
  await shows(frame, 'Saved offline in the host frame.', 'Draft');
  await page.close();

  assert.deepEqual(errors, [], 'no page errors');
} finally {
  await browser.close();
  for (const server of servers) try { process.kill(-server.pid); } catch {}
  rmSync(qaRoot, { recursive: true, force: true });
}

console.log('runtime served host tests passed');
