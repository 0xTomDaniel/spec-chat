import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, readdirSync } from 'node:fs';
import { createServer } from 'node:net';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import { listed } from './fixtures/listed.mjs';

// Pages a shell holds (shell #shell, #acceptance-shell-*, #proof-held; prompt-first-shaping #mobile-held): the QA
// fixture's shell-* collections (tests/fixtures/qa reset.sh, spools.py SHELL, panes.py) on the fixture service (serve.py),
// public through its registry's public base address, which names the fixture's test shell's origin as box setup's --shell
// does (QA_URL_SHELL). The test shell (shell.py) frames a spec page, a mockup page, or a host page held beside the
// service's comment UI page and relays the shell messages; the same shell at another origin is named nowhere. On a
// desktop and a phone; on a phone also in frames with no origin, which take the shell's browser id (#shell-phone-id).
// The fixture's wake provider (reset.sh) logs each wake. Headless Chromium. Needs PLAYWRIGHT_CORE=<path to
// playwright-core>.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
assert.equal(readFileSync(resolve(root, 'docs/specs/.viz/runtime.js'), 'utf8'), runtime, 'runtime copies are identical');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_CORE || 'playwright-core');

const fixture = resolve(root, 'tests/fixtures/qa');
const REPORT = 'docs/specs/report.spec.html';
const FILTERS = 'docs/specs/later-only.spec.html';
const TEAM_PANEL = 'docs/specs/mockups/report-page/04-team-panel.mockup.html';
const VIDEO_SPEC = 'docs/specs/report-video.spec.html'; // Report walkthrough, showing a short video (threads #video-moment)
const freePort = () => new Promise(res => { const s = createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); });
const [port, shellPort, otherPort] = [await freePort(), await freePort(), await freePort()];
const service = `http://127.0.0.1:${port}`;
const shellAt = `http://127.0.0.1:${shellPort}`;
const otherAt = `http://127.0.0.1:${otherPort}`;
const qaRoot = mkdtempSync(join(tmpdir(), 'shell-'));
const env = { ...process.env, QA_ROOT: qaRoot, QA_FIXTURE: fixture, QA_REPO_SPEC_CHAT: root, QA_URL_SHELL: shellAt };
execFileSync('sh', [join(fixture, 'reset.sh')], { env, stdio: 'ignore' });
const wakes = join(qaRoot, 'wakes.log');
const woken = (collection, page = REPORT) => { try { return readFileSync(wakes, 'utf8').split('\n').filter(line => line.endsWith(`/${collection}/${page}`)).length; } catch { return 0; } };
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
process.on('exit', () => { for (const server of servers) try { process.kill(-server.pid); } catch {} });
servers.push(start([join(fixture, 'shell.py'), service, String(shellPort)]));
servers.push(start([join(fixture, 'shell.py'), service, String(otherPort)]));
await ready(`${service}/qa-fixture/${REPORT}`);
await ready(`${shellAt}/`);
await ready(`${otherAt}/`);

const sleep = ms => new Promise(r => setTimeout(r, ms));
const until = async (check, what, ms = 5000) => {
  for (const end = Date.now() + ms; Date.now() < end; await sleep(100)) if (await check()) return;
  assert.fail('timed out: ' + what);
};
// A fixture collection's stored human events, read as the spool reads them: a name starting with a dot, the service's
// file being written, or not ending .json is no event (spool.py read_events). A spool seeded with none has no folder
// until the service stores its first event.
const stored = name => {
  const human = join(qaRoot, 'site', name, REPORT + '.review', 'human');
  let names;
  try { names = readdirSync(human); } catch { return []; }
  return names.filter(n => !n.startsWith('.') && n.endsWith('.json')).sort()
    .map(n => JSON.parse(readFileSync(join(human, n), 'utf8')));
};
// Autoplay without a gesture, so a test plays a video as a reviewer's press does.
const browser = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
const desktop = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
const errors = [];

// The shell's box setup: the comment UI page's answer names the shell's origin, and the shell's alone.
{
  const answer = await fetch(`${service}/comments`, { method: 'HEAD' });
  assert.equal(answer.status, 200);
  assert.equal(answer.headers.get('x-spec-chat-shells'), shellAt, 'the service names the test shell, and no other origin');
}

// The test shell on a collection's page: the page it holds and the comment UI page beside it, once the page is held and
// the comment UI page shows its conversation. `other` frames it from the shell at an origin the box does not name.
const openShell = async (collection, page = REPORT, { on = desktop, variant = '', other = false, query = '', held = !other, size = null } = {}) => {
  const tab = await on.newPage();
  if (size) await tab.setViewportSize(size);
  tab.on('pageerror', e => errors.push(String(e)));
  await tab.goto(`${other ? otherAt : shellAt}/${variant ? variant + '/' : ''}${collection}${page ? '/' + page : ''}${query}`);
  const frame = await (await tab.waitForSelector('#held')).contentFrame();
  const ui = await (await tab.waitForSelector('#comments')).contentFrame();
  await frame.waitForLoadState('load');
  if (held) {
    await frame.waitForFunction(() => document.body.classList.contains('hx-held'));
    await ui.waitForFunction(() => !document.querySelector('.hx-panel').hidden && /thread/.test(document.getElementById('hx-count').textContent));
  }
  return { tab, frame, ui };
};
// The comment UI page's conversation, entry by entry: its kind, its bubbles' texts, its chip, and its receipt.
const conversation = ui => ui.evaluate(() => [...document.querySelectorAll('#hx-threads > .hx-entry')].map(entry => ({
  kind: entry.dataset.question ? 'question' : 'thread',
  texts: [...entry.querySelectorAll(':scope > .hx-msg .hx-text, :scope > .hx-msg .hx-bubble > .hx-text')].map(t => t.textContent),
  chip: entry.querySelector(':scope > .hx-chip')?.textContent ?? null,
  receipt: entry.querySelector(':scope > .hx-receipt, :scope > .hx-msg > .hx-receipt')?.textContent ?? null,
  answers: [...entry.querySelectorAll('.hx-choice:not(.hx-text-choice)')].map(b => b.textContent),
  draft: Boolean(entry.querySelector('.hx-bubble[data-draft]')),
})));
// The page's own comment controls: its review panel, toolbar Comment, hand-off control, and composer.
const ownComments = frame => frame.evaluate(() => ({
  panel: Boolean(document.querySelector('.hx-panel')), comment: Boolean(document.getElementById('hx-mode')),
  handoff: Boolean(document.getElementById('hx-handoff')), composer: Boolean(document.querySelector('.hx-composer')),
}));
const NONE = { panel: false, comment: false, handoff: false, composer: false };
const pins = frame => frame.evaluate(() => [...document.querySelectorAll('.hx-pin')].map(pin => pin.closest('[data-anchor]').dataset.anchor + ' ' + pin.textContent));
// A thread saved in the comment UI page: Comment there, the block on the held page, then its text and Comment.
const commentFromShell = async ({ frame, ui }, block, text, tap = false) => {
  await ui[tap ? 'tap' : 'click']('#hx-point');
  await frame.waitForFunction(() => document.body.classList.contains('hx-comment'));
  await frame[tap ? 'tap' : 'click'](block);
  await ui.waitForSelector('.hx-compose .hx-composer textarea');
  await ui.fill('.hx-compose .hx-composer textarea', text);
  await ui[tap ? 'tap' : 'click']('.hx-compose .hx-composer [data-act=save]');
  await ui.waitForFunction(text => [...document.querySelectorAll('#hx-threads .hx-text')].some(t => t.textContent === text), text);
};
// A shell message as the shell sends it to the held page, or to the comment UI page.
const toHeld = (shell, message) => shell.tab.evaluate(m => document.getElementById('held').contentWindow.postMessage(m, '*'), message);
const toComments = (shell, message) => shell.tab.evaluate(m => document.getElementById('comments').contentWindow.postMessage(m, '*'), message);
const TOTALS = 'p[data-anchor="report-totals"]';
const STORY = 'p[data-anchor="story-totals"]'; // on a phone, above the comment UI page's sheet

// #acceptance-shell-page-tools, #shell-roles-keep, first, before other pages ask the service for Jev: held, the spec page keeps its Jev marks and its compared range in its
// own top line.
{
  const shell = await openShell('shell-page-tools');
  await shell.frame.waitForSelector('.hx-jev-marker', { timeout: 60000 }); // the service's Jev reads each page once
  assert.match(await shell.frame.textContent('#hx-top-line'), /Changes from [0-9a-f]{7} 2026-01-01 to [0-9a-f]{7} 2026-01-01/);
  assert.ok(await shell.frame.$('#hx-top-line .hx-range-change'), 'Change base');
  await shell.tab.close();
}

// #acceptance-shell-held, #mobile-held: held, the spec page shows its thread's pin and no comment UI of its own; the
// comment UI page shows the spec's conversation with the one Hand off.
{
  const shell = await openShell('shell-held');
  await shell.frame.waitForFunction(() => document.querySelectorAll('.hx-pin').length === 1);
  assert.deepEqual(await pins(shell.frame), ['report-totals 1'], 'held: the thread pin on its block');
  assert.deepEqual(await ownComments(shell.frame), NONE, 'held: no review panel, Comment, composer, or Hand off');
  assert.equal(await shell.frame.evaluate(() => document.querySelector('.hx-thread-dock, .hx-toolbar')), null);
  const shown = await conversation(shell.ui);
  assert.deepEqual(shown[0], { kind: 'thread', texts: ['Round totals to whole units?'], chip: 'Acceptance criteria · The report page shows totals per week',
    receipt: 'Sent', answers: [], draft: false });
  assert.match(await shell.ui.textContent('.hx-panel-head'), /^Conversation Weekly report · 1 thread · 2 questions/);
  assert.equal(await shell.ui.textContent('#hx-handoff'), 'Hand off to agent →');
  assert.equal(await shell.ui.locator('#hx-handoff').count(), 1, 'one hand-off control');
  // #shell-fields-marks, -marks-back: marks name each thread's spot, id, number, state, and status, and the chosen
  // answers; the page answers with the threads whose quote is missing; every relayed message names its frame.
  const log = await shell.tab.evaluate(() => window.shellLog);
  const marks = log.filter(m => m.direction === 'comments' && m.data.type === 'spec-chat-marks').pop().data;
  assert.deepEqual(marks, { type: 'spec-chat-marks', frame: 'page-1', answers: [], threads: [{ id: 'qa-shell', number: 1, state: 'open',
    status: 'pending', anchor: 'report-totals', target: null, quote: null }] });
  assert.deepEqual(log.find(m => m.direction === 'page' && m.data.type === 'spec-chat-marks').data, { type: 'spec-chat-marks', missing: [] });
  // #shell-ui-page-only, #shell-protocol-plain: a publish naming a spec page at an address outside the comment UI page's
  // own review service loads nothing there; the conversation stays the held spec's.
  const elsewhere = [];
  shell.tab.on('request', r => { if (r.frame() === shell.ui && r.url().startsWith(otherAt)) elsewhere.push(r.url()); });
  await shell.tab.evaluate(page => document.getElementById('comments').contentWindow.postMessage(
    { type: 'spec-chat-publish', frame: 'page-2', page, version: 'v1', questions: [] }, window.SERVICE), `${otherAt}/shell-held/${REPORT}`);
  await sleep(1000);
  assert.deepEqual(elsewhere, [], 'no load at an address another service holds');
  assert.match(await shell.ui.textContent('.hx-panel-head'), /^Conversation Weekly report · 1 thread/);
  await shell.tab.close();
}

// #acceptance-shell-alone: in a plain tab the spec page keeps its own review panel, Comment, Hand off, and the pin.
{
  const tab = await desktop.newPage();
  tab.on('pageerror', e => errors.push(String(e)));
  await tab.goto(`${service}/shell-alone/${REPORT}`);
  await tab.waitForFunction(() => /connected/.test(document.getElementById('hx-status')?.textContent || '') && document.querySelectorAll('.hx-pin').length === 1);
  assert.deepEqual(await ownComments(tab.mainFrame()), { panel: true, comment: true, handoff: true, composer: false });
  assert.deepEqual(await pins(tab.mainFrame()), ['report-totals 1']);
  await tab.close();
}

// #acceptance-shell-unnamed, #shell-setting-obey: the same shell at an origin the box does not name says hello with
// comments; the page stays alone, with its own review panel, Comment, and Hand off.
{
  const shell = await openShell('shell-unnamed', REPORT, { other: true });
  await shell.frame.waitForFunction(() => /connected/.test(document.getElementById('hx-status')?.textContent || ''));
  await sleep(1000); // its hello had time to arrive
  assert.equal(await shell.frame.evaluate(() => document.body.classList.contains('hx-held')), false);
  assert.deepEqual(await ownComments(shell.frame), { panel: true, comment: true, handoff: true, composer: false });
  assert.deepEqual(await shell.tab.evaluate(() => window.shellLog.map(m => m.data.type)), [], 'it publishes nothing to that shell');
  assert.equal(await shell.ui.evaluate(() => document.querySelector('.hx-panel').hidden), true, 'its comment UI page shows nothing');
  await shell.tab.close();
}

// #acceptance-shell-pick: Comment in the comment UI page, a block on the held page, then the comment saved there: the
// conversation ends with its bubble, Draft, and its chip naming that block; the block shows a pin.
{
  const shell = await openShell('shell-pick');
  await commentFromShell(shell, TOTALS, 'Picked in the shell.');
  const last = (await conversation(shell.ui)).filter(e => e.kind === 'thread').pop();
  assert.deepEqual(last, { kind: 'thread', texts: ['Picked in the shell.'], chip: 'Acceptance criteria · The report page shows totals per week',
    receipt: 'Draft', answers: [], draft: true });
  await shell.frame.waitForFunction(() => document.querySelectorAll('.hx-pin').length === 1);
  assert.deepEqual(await pins(shell.frame), ['report-totals 1']);
  assert.equal(await shell.frame.evaluate(() => document.body.classList.contains('hx-comment')), false, 'the pick ends pointing');
  // #shell-fields-point: point with off, as when the shell's own Comment turns off, ends pointing on the held page.
  await shell.ui.click('#hx-point');
  await shell.frame.waitForFunction(() => document.body.classList.contains('hx-comment'));
  await toHeld(shell, { type: 'spec-chat-point', off: true });
  await shell.frame.waitForFunction(() => !document.body.classList.contains('hx-comment'));
  await shell.tab.close();
}

// #acceptance-shell-publish: the shell receives the page's publish: its address, the version it shows, and its one open
// question with its answers.
{
  const shell = await openShell('shell-publish');
  const publish = await shell.tab.evaluate(() => window.shellLog.find(m => m.data.type === 'spec-chat-publish').data);
  const served = await fetch(`${service}/shell-publish/${REPORT}`);
  // #shell-fields-publish: page, version, and questions, each {anchor, text, answers, about, step}.
  assert.deepEqual(Object.keys(publish).sort(), ['page', 'questions', 'type', 'version']);
  assert.equal(publish.page, `${service}/shell-publish/${REPORT}`);
  assert.equal(publish.version, served.headers.get('etag').replace(/"/g, ''), 'the version it shows');
  assert.deepEqual(publish.questions, [{ anchor: 'export-format', text: 'TBD: whether the export also offers XLSX.',
    answers: ['CSV only', 'CSV and XLSX'], about: [], step: null }]);
  for (const message of await shell.tab.evaluate(() => window.shellLog.map(m => m.data))) {
    assert.equal(JSON.stringify(message), JSON.stringify(JSON.parse(JSON.stringify(message))), 'plain data');
  }
  await shell.tab.close();
}

// #acceptance-shell-pick-key, #shell-protocol-keys: a click in the held page, then its own C, then a block: the comment
// UI page shows its composer on that block.
{
  const shell = await openShell('shell-pick-key');
  await shell.frame.click('h1');
  await shell.frame.press('body', 'c');
  await shell.frame.waitForFunction(() => document.body.classList.contains('hx-comment'));
  await shell.frame.click(TOTALS);
  await shell.ui.waitForSelector('.hx-compose .hx-composer textarea');
  assert.equal(await shell.ui.textContent('.hx-compose .hx-anchor'), '#report-totals', 'the composer names that block');
  await shell.tab.close();
}

// #acceptance-shell-show: a thread's chip in the comment UI page brings its block, far below, into view and rings it.
{
  const shell = await openShell('shell-show', FILTERS);
  const block = 'p[data-anchor="filter-team"]';
  const before = await shell.frame.$eval(block, el => el.getBoundingClientRect().top);
  assert.ok(before > 900, 'the block starts far below the screen');
  await shell.ui.click('#hx-threads .hx-entry[data-thread] .hx-chip');
  await shell.frame.waitForFunction(sel => { const r = document.querySelector(sel).getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; }, block);
  await shell.frame.waitForFunction(sel => {
    const ring = document.querySelector('.hx-thread-ring'), r = document.querySelector(sel).getBoundingClientRect();
    if (!ring || ring.hidden) return false;
    const g = ring.getBoundingClientRect();
    return g.left <= r.left && g.top <= r.top && g.right >= r.right && g.bottom >= r.bottom;
  }, block);
  await shell.tab.close();
}

// #shell-fields-spot, threads #video-moment on a held page: the held spec page's video paused, a pointed click on it
// picks its moment; a box dragged across it picks the box too, as fractions of its frame; the comment UI page's chips read
// the moment, then Box. The held page draws the rail its marks name and the box while its video is paused at the moment;
// a chip pressed shows the video there (#shell-msg-show).
{
  const shell = await openShell('threads-video', VIDEO_SPEC);
  const VIDEO = 'figure[data-anchor="walkthrough-video"] video';
  const playUntil = seconds => shell.frame.evaluate(async ([s, seconds]) => {
    const video = document.querySelector(s);
    if (video.readyState < 1) await new Promise(done => video.addEventListener('loadedmetadata', done, { once: true }));
    await video.play();
    while (video.currentTime < seconds) await new Promise(done => setTimeout(done, 20));
    video.pause();
    return video.currentTime;
  }, [VIDEO, seconds]);
  const picks = () => shell.tab.evaluate(() => window.shellLog.filter(m => m.data.type === 'spec-chat-pick').map(m => m.data));
  const pausedAt = await playUntil(1.3);
  await shell.ui.click('#hx-point');
  await shell.frame.waitForFunction(() => document.body.classList.contains('hx-comment'));
  await shell.frame.click(VIDEO);
  await shell.ui.waitForSelector('.hx-compose .hx-composer textarea');
  let [pick] = await picks();
  assert.deepEqual([pick.anchor, pick.target, 'box' in pick], ['walkthrough-video', { type: 'element', key: 'video[1]' }, false]);
  assert.ok(Math.abs(pick.moment - pausedAt) < 0.001, 'the pick carries the moment');
  await shell.ui.fill('.hx-compose .hx-composer textarea', 'The totals should land first.');
  await shell.ui.click('.hx-compose .hx-composer [data-act=save]');
  await shell.ui.waitForFunction(() => [...document.querySelectorAll('#hx-threads .hx-chip')].some(c => c.textContent === 'Walkthrough · 0:01'));
  // A box: dragged across the held page's video, in the page the shell frames.
  await shell.ui.click('#hx-point');
  await shell.frame.waitForFunction(() => document.body.classList.contains('hx-comment'));
  const at = await shell.frame.locator(VIDEO).boundingBox();
  await shell.tab.mouse.move(at.x + at.width * 0.5, at.y + at.height * 0.1);
  await shell.tab.mouse.down();
  await shell.tab.mouse.move(at.x + at.width * 0.75, at.y + at.height * 0.5, { steps: 5 });
  await shell.tab.mouse.up();
  await shell.ui.waitForSelector('.hx-compose .hx-composer textarea');
  pick = (await picks()).at(-1);
  for (const [k, v] of Object.entries({ left: 0.5, top: 0.1, width: 0.25, height: 0.4 })) assert.ok(Math.abs(pick.box[k] - v) < 0.01, 'the pick\'s box ' + k);
  assert.equal(await shell.frame.evaluate(s => document.querySelector(s).paused, VIDEO), true, 'the drag played nothing');
  await shell.ui.fill('.hx-compose .hx-composer textarea', 'This card should open the team page.');
  await shell.ui.click('.hx-compose .hx-composer [data-act=save]');
  await shell.ui.waitForFunction(() => [...document.querySelectorAll('#hx-threads .hx-chip')].some(c => c.textContent === 'Walkthrough · 0:01 · Box'));
  // The held page's rail marks both threads, as its marks name them, and its paused frame shows the box.
  await shell.frame.waitForFunction(() => document.querySelectorAll('.hx-video-rail .hx-video-mark').length === 2);
  await shell.frame.waitForSelector('.hx-video-box');
  // #shell-msg-show: played on, the box thread's chip pressed: the held page's video paused at its moment, its box drawn.
  await playUntil(2.4);
  await shell.ui.locator('#hx-threads .hx-chip', { hasText: 'Walkthrough · 0:01 · Box' }).click();
  await shell.frame.waitForFunction(([s, t]) => { const v = document.querySelector(s); return v.paused && !v.seeking && Math.abs(v.currentTime - t) < 0.001; },
    [VIDEO, Math.round(pick.moment * 1000) / 1000]);
  await shell.frame.waitForSelector('.hx-video-box');
  await shell.tab.close();
}

// #acceptance-shell-pin-focus: a pin pressed in the held page focuses its thread in the comment UI page.
{
  const shell = await openShell('shell-pin-focus', FILTERS);
  await shell.frame.waitForSelector('.hx-pin');
  await shell.frame.click('.hx-pin');
  await shell.ui.waitForSelector('.hx-focused[data-thread="qa-pin"]');
  // #shell-fields-pick: a pin's pick is its thread's spot and its thread.
  assert.deepEqual(await shell.tab.evaluate(() => window.shellLog.find(m => m.data.type === 'spec-chat-pick').data),
    { type: 'spec-chat-pick', thread: 'qa-pin', anchor: 'filter-team', target: null, quote: null });
  assert.deepEqual(await shell.ui.$$eval('.hx-focused .hx-text', ts => ts.map(t => t.textContent)), ['Name the teams the filter lists.', 'Done; OK to resolve?']);
  // #shell-protocol-relay: another page the shell holds published last, so its conversation shows; the pin pressed on
  // this page then focuses its thread once this page's threads are read again.
  await toComments(shell, { type: 'spec-chat-publish', frame: 'page-2', page: `${service}/shell-pin-focus/${REPORT}`, version: null, questions: [] });
  await shell.ui.waitForFunction(() => !document.querySelector('.hx-focused'));
  await shell.frame.click('.hx-pin');
  await shell.ui.waitForSelector('.hx-focused[data-thread="qa-pin"]');
  assert.equal(await shell.ui.$('.hx-compose .hx-composer'), null, 'the pin opens no composer');
  await shell.tab.close();
}

// #acceptance-shell-handoff: Hand off in the comment UI page: the notice, and the comment's receipt Sent. Its one draft is
// this browser's (spools.py shell_draft): the shell's frames have no origin, so the comment UI page takes the shell's
// browser id, Papaya's in the shell's storage, as qa.toml's start point keeps it.
{
  const papaya = await browser.newContext({ viewport: { width: 1440, height: 900 }, storageState: { cookies: [], origins: [{
    origin: shellAt, localStorage: [{ name: 'test-shell:browser', value: 'qa-browser-papaya' }] }] } });
  const shell = await openShell('shell-handoff', REPORT, { on: papaya, variant: 'bare' });
  assert.deepEqual((await conversation(shell.ui)).filter(e => e.kind === 'thread').map(e => [e.texts, e.receipt]),
    [[['Round totals to whole units?'], 'Draft']]);
  assert.equal(await shell.ui.textContent('#hx-drafts'), '1 draft');
  await shell.ui.click('#hx-handoff');
  await shell.ui.waitForFunction(() => document.getElementById('hx-toast')?.textContent === 'Handed off 1 comment, agent notified');
  await shell.ui.waitForFunction(() => document.querySelector('#hx-threads .hx-entry[data-thread] .hx-receipt')?.textContent === 'Sent');
  await papaya.close();
}

// #acceptance-shell-wake: a comment saved and handed off in the comment UI page wakes the spec's owner.
{
  const shell = await openShell('shell-wake');
  await commentFromShell(shell, TOTALS, 'Wake the owner.');
  assert.equal(woken('shell-wake'), 0);
  await shell.ui.click('#hx-handoff');
  await listed(() => stored('shell-wake'), events => events.some(e => e.event === 'handoff'), 'the hand-off'); // the wake polls the spool
  await until(() => woken('shell-wake') === 1, 'the owner is woken for the hand-off');
  await shell.tab.close();
}

// #acceptance-shell-accept: every thread resolved and no open TBD: the comment UI page offers Accept spec, the held page
// none of its own.
{
  const shell = await openShell('shell-accept', FILTERS);
  await shell.ui.waitForFunction(() => document.getElementById('hx-handoff').textContent === 'Accept spec' && !document.getElementById('hx-handoff').disabled);
  assert.equal(await shell.frame.evaluate(() => [...document.querySelectorAll('button')].some(b => b.textContent === 'Accept spec')), false);
  await shell.tab.close();
}

// #acceptance-shell-accept-waits: one open TBD: no Accept spec, and the conversation ends with the question as the agent's
// bubble with its answer buttons.
{
  const shell = await openShell('shell-accept-waits');
  await sleep(500);
  assert.notEqual(await shell.ui.textContent('#hx-handoff'), 'Accept spec');
  const last = (await conversation(shell.ui)).pop();
  assert.deepEqual([last.kind, last.texts, last.answers], ['question', ['TBD: whether the export also offers XLSX.'], ['CSV only (recommended)', 'CSV and XLSX']]);
  assert.equal(await shell.ui.textContent('#hx-threads > .hx-question .hx-who'), 'Agent');
  await shell.tab.close();
}

// #acceptance-shell-answer: an answer button pressed in the held page: the comment UI page shows the answer under its
// question as a draft, and the button shows as chosen.
{
  const shell = await openShell('shell-answer');
  const csv = 'p[data-anchor="export-format"] button[data-spec-choice="CSV only"]';
  await shell.frame.click(csv);
  await shell.ui.waitForFunction(() => [...document.querySelectorAll('#hx-threads > .hx-question .hx-entry[data-thread] .hx-text')].some(t => t.textContent === 'CSV only'));
  const question = (await conversation(shell.ui)).find(e => e.kind === 'question');
  assert.equal(question.texts[0], 'TBD: whether the export also offers XLSX.');
  assert.equal(await shell.ui.textContent('#hx-threads > .hx-question .hx-entry[data-thread] .hx-receipt'), 'Draft');
  await shell.frame.waitForFunction(sel => document.querySelector(sel).getAttribute('aria-pressed') === 'true', csv);
  // #shell-fields-pick, -marks: the answer's pick names its words; the marks name it chosen.
  const log = await shell.tab.evaluate(() => window.shellLog);
  assert.equal(log.find(m => m.data.type === 'spec-chat-pick').data.answer, 'CSV only');
  assert.deepEqual(log.filter(m => m.data.type === 'spec-chat-marks' && m.direction === 'comments').pop().data.answers, [{ anchor: 'export-format', answer: 'CSV only' }]);
  // The same answer comment as the page's own press: its button's element target.
  const answers = () => stored('shell-answer').filter(b => b.event === 'comment');
  const saved = await listed(answers, 1, 'the answer');
  assert.deepEqual(saved.map(b => [b.anchorId, b.target, b.text]), [['export-format', { type: 'element', key: 'button[1]' }, 'CSV only']]);
  // Pressed in the question's bubble, the other answer moves the chosen mark on the page, saving the same comment.
  await shell.ui.click('#hx-threads > .hx-question .hx-bubble .hx-choice:text-is("CSV and XLSX")');
  await shell.frame.waitForFunction(() => document.querySelector('p[data-anchor="export-format"] button[data-spec-choice="CSV and XLSX"]').getAttribute('aria-pressed') === 'true');
  await shell.tab.close();
}

// #acceptance-shell-mockup-step: a held mockup page opened at its first step; the chip of a thread at the second step
// shows both frames at that step, its card ringed.
{
  const shell = await openShell('shell-mockup-step', TEAM_PANEL, { query: '?step=totals' });
  await shell.frame.waitForFunction(() => document.querySelector('.hx-step[aria-pressed="true"]')?.dataset.step === 'totals');
  await shell.ui.click('#hx-threads .hx-entry[data-thread] .hx-chip');
  await shell.frame.waitForFunction(() => document.querySelector('.hx-step[aria-pressed="true"]')?.dataset.step === 'team-open'
    && [...document.querySelectorAll('[data-mockup-frame]')].every(frame => frame.getAttribute('data-at') === 'totals team-open'));
  await shell.frame.waitForFunction(() => {
    const ring = document.querySelector('.hx-thread-ring'), card = document.querySelector('[data-anchor="desktop-card-payments"]').getBoundingClientRect();
    if (!ring || ring.hidden) return false;
    const g = ring.getBoundingClientRect();
    return g.left <= card.left + 1 && g.top <= card.top + 1 && g.right >= card.right - 1 && g.bottom >= card.bottom - 1;
  });
  assert.match((await conversation(shell.ui))[0].chip, /^Desktop card payments · Step 2 Team open$/);
  await shell.tab.close();
}

// #acceptance-shell-host-item: a held host page's item control: the comment UI page shows the item's conversation with
// both threads' first messages; the host page draws no comment box.
{
  const shell = await openShell('shell-host-item', 'docs/pane.html', { held: false });
  await shell.frame.waitForFunction(() => document.body.classList.contains('hx-held') || true);
  await shell.ui.waitForFunction(() => !document.querySelector('.hx-panel').hidden);
  const control = () => shell.frame.evaluate(() => {
    const overlay = document.querySelector('[data-spec-chat="comment-layer"]');
    return Boolean(overlay && overlay.shadowRoot.querySelector('.hx-layer-control[aria-label="Comment on Agent turn 42"]'));
  });
  await until(control, 'the held host page shows the item control');
  // #scope-rule, #shell-ui-page-only: held, the page still reads its scopes' rows, so Agent turn 43, in a scope no row
  // has, gets no control; it asks for no subject at those reads, so it reads no thread.
  const controls = () => shell.frame.evaluate(() => [...document.querySelector('[data-spec-chat="comment-layer"]').shadowRoot
    .querySelectorAll('.hx-layer-control')].map(c => c.getAttribute('aria-label')).sort());
  await until(async () => !(await controls()).includes('Comment on Agent turn 43'), 'no control in a scope no row has');
  const reads = [];
  shell.tab.on('request', r => { if (r.frame() === shell.frame && /\/api\/events\?/.test(r.url())) reads.push(new URL(r.url())); });
  await until(() => reads.length > 0, 'a live read while held', 6000);
  assert.deepEqual(await controls(), ['Comment on Agent turn 42', 'Comment on Totals per week']);
  assert.deepEqual(reads.map(url => [url.searchParams.get('scope') !== null, url.searchParams.getAll('subject')]),
    reads.map(() => [true, []]), 'the held page reads rows by scope alone, no subject, so no thread');
  await shell.frame.evaluate(() => document.querySelector('[data-spec-chat="comment-layer"]').shadowRoot.querySelector('.hx-layer-control[aria-label="Comment on Agent turn 42"]').click());
  await shell.ui.waitForFunction(() => document.querySelectorAll('#hx-threads > .hx-entry[data-thread]').length === 2);
  assert.deepEqual((await conversation(shell.ui)).map(e => e.texts[0]), ['Question: why a CSV and not a sheet?', 'Change: name the columns after the report.']);
  assert.match(await shell.ui.textContent('#hx-count'), /^Agent turn 42 · 2 threads/);
  assert.equal(await shell.frame.evaluate(() => Boolean(document.querySelector('[data-spec-chat="comment-layer"]').shadowRoot.querySelector('.hx-layer-box'))), false);
  // A comment there is kept in the item's hosted threads, and its pin shows on the item.
  await shell.ui.fill('.hx-compose .hx-composer textarea', 'Question: from the shell?');
  await shell.ui.click('.hx-compose .hx-composer [data-act=save]');
  await until(() => shell.frame.evaluate(() => document.querySelector('[data-spec-chat="comment-layer"]').shadowRoot.querySelectorAll('.hx-pin').length === 3), 'three pins on the item');
  // #shell-msg-point, #shell-fields-point on a held host page: point rings the item under the pointer; point with off
  // ends it, so no item rings.
  const ITEM = '[data-comment-subject="agent-turn:qa:42"]';
  const ring = () => shell.frame.evaluate(() => Boolean(document.querySelector('[data-spec-chat="comment-layer"]').shadowRoot.querySelector('.hx-point-ring')));
  await toHeld(shell, { type: 'spec-chat-point' });
  await shell.frame.hover(ITEM, { position: { x: 4, y: 4 } });
  await shell.frame.hover(ITEM, { position: { x: 8, y: 8 } });
  await until(ring, 'the item under the pointer ringed');
  await toHeld(shell, { type: 'spec-chat-point', off: true });
  await until(async () => !(await ring()), 'point off ends pointing');
  await shell.frame.hover(ITEM, { position: { x: 12, y: 12 } });
  await sleep(200);
  assert.equal(await ring(), false, 'no item rings after point off');
  await shell.tab.close();
}

// threads #root-chip-box on a held host page: a thread on a box drawn on a thing with no video; its chip reads the thing's
// label, then Box, and pressing it sends show with the box: the host page rings the thing and draws the box on its view,
// the item's element.
{
  const shell = await openShell('shell-host-box', 'docs/pane.html', { held: false });
  await shell.ui.waitForFunction(() => !document.querySelector('.hx-panel').hidden);
  const overlay = selector => shell.frame.evaluate(selector => {
    const node = document.querySelector('[data-spec-chat="comment-layer"]')?.shadowRoot.querySelector(selector);
    if (!node) return null;
    const r = node.getBoundingClientRect();
    return { left: r.left, top: r.top, width: r.width, height: r.height };
  }, selector);
  await until(() => overlay('.hx-layer-control[aria-label="Comment on Agent turn 42"]'), 'the held host page shows the item control');
  await shell.frame.evaluate(() => document.querySelector('[data-spec-chat="comment-layer"]').shadowRoot.querySelector('.hx-layer-control[aria-label="Comment on Agent turn 42"]').click());
  await shell.ui.waitForFunction(() => document.querySelectorAll('#hx-threads > .hx-entry[data-thread]').length === 1);
  assert.deepEqual((await conversation(shell.ui)).map(e => [e.texts[0], e.chip]), [['This line reads as an error.', 'Agent turn 42 · Box']]);
  assert.equal(await overlay('.hx-thing-box'), null, 'no box before its chip is pressed');
  await shell.ui.click('#hx-threads .hx-chip');
  await until(() => overlay('.hx-thing-box'), 'the box drawn on the thing');
  const item = await shell.frame.$eval('[data-comment-subject="agent-turn:qa:42"]', el => { const r = el.getBoundingClientRect(); return { left: r.left, top: r.top, width: r.width, height: r.height }; });
  const box = await overlay('.hx-thing-box');
  const near = (a, b) => Math.abs(a - b) < 1.5;
  assert.ok(near(box.left, item.left + item.width * 0.5) && near(box.top, item.top + item.height * 0.25)
    && near(box.width, item.width * 0.25) && near(box.height, item.height * 0.5), 'at its fractions of the thing: ' + JSON.stringify({ box, item }));
  assert.ok(await overlay('.hx-thread-ring'), 'the thing ringed');
  const shows = await shell.tab.evaluate(() => window.shellLog.filter(m => m.data.type === 'spec-chat-show').map(m => m.data));
  assert.deepEqual(shows.map(m => [m.subject, m.box]), [['agent-turn:qa:42', { left: 0.5, top: 0.25, width: 0.25, height: 0.5 }]]);
  await shell.tab.close();
}

// The test shell's native page (shell.py /canvas/<collection>): a factory floor drawn on a canvas, which speaks the page
// side of the shell messages with no Spec Chat script (#shell-native). `floor` reads what it drew, its pins, and its
// cards' and controls' places; `at` turns a place on it into the shell's own, for the mouse.
const CARD = 'lane-card:qa:1514';
const openNative = async (collection, options = {}) => {
  const shell = await openShell(collection, '', { variant: 'native', held: false, ...options });
  // The held frame as the canvas page loaded in it: the frame first read may be the one before its address was set.
  await until(() => shell.tab.frames().some(f => f.url().endsWith('/canvas/' + collection)), 'the native page loads');
  shell.frame = shell.tab.frames().find(f => f.url().endsWith('/canvas/' + collection));
  await shell.frame.waitForFunction(() => window.floor);
  await shell.ui.waitForFunction(() => !document.querySelector('.hx-panel').hidden);
  assert.equal(await shell.frame.evaluate(() => Boolean(window.specChat || document.querySelector('script[src*="runtime"], script[src*="comment-layer"]'))), false, 'no Spec Chat script');
  const offset = await shell.tab.$eval('#held', el => { const r = el.getBoundingClientRect(); return { x: r.left, y: r.top }; });
  return Object.assign(shell, {
    floor: () => shell.frame.evaluate(() => JSON.parse(JSON.stringify({ pointing: window.floor.pointing, shown: window.floor.shown, pins: window.floor.pins }))),
    place: (what, subject) => shell.frame.evaluate(([what, subject]) => window.floor[what](subject), [what, subject]),
    at: (x, y) => ({ x: offset.x + x, y: offset.y + y }),
  });
};
// A canvas pixel's color at a place on the floor, as it drew it.
const pixel = (frame, x, y) => frame.evaluate(([x, y]) => {
  const canvas = document.getElementById('floor'), ratio = canvas.width / innerWidth;
  return [...canvas.getContext('2d').getImageData(Math.round(x * ratio), Math.round(y * ratio), 1, 1).data].slice(0, 3);
}, [x, y]);

// #acceptance-shell-native: a page that speaks the shell messages natively publishes a thing with no page element holding
// one thread; its comment control pressed, the comment UI page shows the thing's conversation with the thread's first
// message, and the page draws the thread's pin on the thing from the marks. It sends no marks answer, holding no quoted
// text (#shell-fields-marks-back), and its pin pressed focuses the thread.
{
  const shell = await openNative('shell-native');
  const publish = await shell.tab.evaluate(() => window.shellLog.filter(m => m.data.type === 'spec-chat-publish').at(-1).data);
  assert.deepEqual(publish.items.find(item => item.subject === CARD), { subject: CARD, scope: 'shell-native', kind: 'Lane card',
    label: 'QA-1514 card on the floor', from: publish.page, starters: [], lane: 'qa-20' });
  await shell.frame.waitForFunction(() => window.floor.pins.length === 1);
  const [pin] = (await shell.floor()).pins;
  assert.deepEqual([pin.subject, pin.number, pin.status], [CARD, 1, 'pending']);
  const card = await shell.place('rect', CARD);
  assert.ok(pin.rect.x >= card.x && pin.rect.x + pin.rect.w <= card.x + card.w && pin.rect.y >= card.y, 'the pin on its card');
  assert.deepEqual(await pixel(shell.frame, pin.rect.x + 12, pin.rect.y + 20), [176, 84, 14], 'drawn in the sent color');
  const control = await shell.place('control', CARD);
  const press = shell.at(control.x + control.w / 2, control.y + control.h / 2);
  await shell.tab.mouse.click(press.x, press.y);
  await shell.ui.waitForFunction(() => document.querySelectorAll('#hx-threads > .hx-entry[data-thread]').length === 1);
  assert.deepEqual((await conversation(shell.ui)).map(e => e.texts[0]), ['Why does this card still say QA failed?']);
  assert.match(await shell.ui.textContent('#hx-count'), /^QA-1514 card on the floor · 1 thread/);
  assert.deepEqual((await conversation(shell.ui)).map(e => e.chip), ['QA-1514 card on the floor']);
  // The pin pressed in the page: its thread focused in the comment UI page.
  const at = shell.at(pin.rect.x + 12, pin.rect.y + 12);
  await shell.tab.mouse.click(at.x, at.y);
  await shell.ui.waitForFunction(() => document.querySelector('.hx-focused .hx-text')?.textContent === 'Why does this card still say QA failed?');
  assert.equal(await shell.tab.evaluate(() => window.shellLog.some(m => m.direction === 'page' && m.data.type === 'spec-chat-marks')), false,
    'no marks answer');
  await shell.tab.close();
}

// threads #acceptance-box-picture, #box-picture-what, -show; shell #shell-fields-pick, #shell-fields-spot: Comment
// in the comment UI page, then a box dragged across the native page: its pick carries the box as fractions of the page's
// view and a picture of the boxed spot, the box drawn on it. Saved, the comment keeps the picture beside it in its spool,
// naming its file, and the focused thread shows it under the comment, as the accepted mockup draws it: the stage tag by
// its author, the chip reading the thing's label then Box, the picture captioned, the receipt under it. No read of the
// threads fetches it: only the focused thread loads it, by its file, also after a reload. The chip pressed shows the
// thing with the box drawn on its view (#root-chip-box).
{
  let shell = await openNative('shell-box-picture');
  const pictures = [];
  const watch = tab => tab.on('request', r => { if (/\/api\/pictures\?/.test(r.url())) pictures.push(r.url()); });
  watch(shell.tab);
  await shell.ui.click('#hx-point');
  await shell.frame.waitForFunction(() => window.floor.pointing);
  const card = await shell.place('rect', CARD);
  const view = await shell.frame.evaluate(() => ({ width: innerWidth, height: innerHeight }));
  // Across the card's lower part, its badge reading QA failed: a box apart from the ring the card shows as pointed at.
  const dragged = { x: card.x + 8, y: card.y + 30, w: card.w - 48, h: card.h - 32 };
  const from = shell.at(dragged.x, dragged.y), to = shell.at(dragged.x + dragged.w, dragged.y + dragged.h);
  await shell.tab.mouse.move(from.x, from.y);
  await shell.tab.mouse.down();
  await shell.tab.mouse.move(to.x, to.y, { steps: 6 });
  await shell.tab.mouse.up();
  await shell.ui.waitForSelector('.hx-compose .hx-composer textarea');
  const pick = await shell.tab.evaluate(() => window.shellLog.filter(m => m.data.type === 'spec-chat-pick').at(-1).data);
  assert.equal(pick.subject, CARD);
  for (const [k, v] of Object.entries({ left: dragged.x / view.width, top: dragged.y / view.height,
    width: dragged.w / view.width, height: dragged.h / view.height })) assert.ok(Math.abs(pick.box[k] - v) < 0.005, 'the box ' + k);
  assert.equal(pick.picture.type, 'image/png');
  const TEXT = 'This card says QA failed, but its bundle passed on the rerun.';
  await shell.ui.fill('.hx-compose .hx-composer textarea', TEXT);
  await shell.ui.click('.hx-compose .hx-composer [data-act=save]');
  await shell.ui.waitForFunction(text => [...document.querySelectorAll('#hx-threads .hx-text')].some(t => t.textContent === text), TEXT);
  // Kept: the comment in the scope's hosted threads names the picture's file, kept beside it with the pick's bytes.
  const hosted = join(qaRoot, 'serve', 'comments', 'shell-box-picture');
  const kept = () => { try { return readdirSync(join(hosted, 'human')).filter(n => n.endsWith('.json')).map(n => JSON.parse(readFileSync(join(hosted, 'human', n), 'utf8'))); } catch { return []; } };
  const [comment] = await listed(kept, events => events.length === 1, 'the comment');
  assert.match(comment.picture, /^[0-9a-f]{64}\.png$/);
  assert.deepEqual([comment.subject, comment.box, comment.lane, comment.stage], [CARD, pick.box, 'qa-20', 'QA']);
  assert.equal(readFileSync(join(hosted, 'pictures', comment.picture)).toString('base64'), pick.picture.data);
  // The focused thread, as the mockup draws it.
  const focused = async () => {
    await shell.ui.click('#hx-threads .hx-entry[data-thread] .hx-bubble');
    await shell.ui.waitForFunction(() => { const img = document.querySelector('.hx-focused .hx-picture img'); return img && img.complete && img.naturalWidth > 0; });
    return shell.ui.evaluate(() => {
      const row = document.querySelector('.hx-focused .hx-msg');
      const img = row.querySelector('.hx-picture img');
      // The box drawn on the picture: its top edge, in the attention color, 12 px in from the picture's top, the
      // margin the page keeps around the box.
      const canvas = Object.assign(document.createElement('canvas'), { width: img.naturalWidth, height: img.naturalHeight });
      const c = canvas.getContext('2d');
      c.drawImage(img, 0, 0);
      const stroke = [...c.getImageData(Math.floor(canvas.width / 2), 12, 1, 1).data].slice(0, 3);
      const parts = [...row.children].map(el => el.className.split(' ')[0]);
      // Its side, where it sits in the stream's content box: at the right is its right edge on the box's and its left
      // edge in from the box's, never the keyword its style computes to, which browsers name differently.
      const stream = row.parentElement, box = stream.getBoundingClientRect(), pad = getComputedStyle(stream), at = row.getBoundingClientRect();
      const left = box.left + stream.clientLeft + parseFloat(pad.paddingLeft);
      const right = box.left + stream.clientLeft + stream.clientWidth - parseFloat(pad.paddingRight);
      const side = Math.abs(at.right - right) < 1 && at.left > left + 1 ? 'right' : `left ${at.left - left}, right ${right - at.right}`;
      return { parts, who: row.querySelector('.hx-who').textContent.trim(), chip: row.querySelector('.hx-chip').textContent,
        caption: row.querySelector('.hx-picture figcaption').textContent, src: img.getAttribute('src').slice(0, 30),
        receipt: row.querySelector('.hx-receipt').textContent, stroke, side, width: img.getBoundingClientRect().width };
    });
  };
  let shown = await focused();
  // In the mockup's order; the host's kind and from line (comment-layer #ui-from) and Edit sit between, as on any thread.
  const drawn = ['hx-who', 'hx-bubble', 'hx-chip', 'hx-picture', 'hx-receipt'];
  assert.deepEqual(shown.parts.filter(part => drawn.includes(part)), drawn);
  assert.deepEqual(shown.parts.filter(part => !drawn.includes(part)), ['hx-host-from', 'hx-msg-actions']);
  assert.match(shown.who, / · QA$/, 'the stage tag beside its author');
  assert.deepEqual([shown.chip, shown.caption, shown.receipt, shown.side], ['QA-1514 card on the floor · Box', 'What you saw when you wrote', 'Draft', 'right']);
  assert.deepEqual(shown.stroke, [176, 84, 14], 'the box drawn on the picture');
  assert.ok(shown.width <= 220, 'the picture at the mockup\'s width');
  // #root-chip-box: the chip pressed sends show with the box; the page draws it on its view.
  await shell.ui.click('.hx-focused .hx-chip');
  await shell.frame.waitForFunction(() => window.floor.shown && window.floor.shown.box);
  assert.deepEqual((await shell.floor()).shown, { subject: CARD, box: pick.box });
  const onView = { x: pick.box.left * view.width, y: pick.box.top * view.height };
  assert.deepEqual(await pixel(shell.frame, Math.floor(onView.x + pick.box.width * view.width / 2), Math.floor(onView.y)), [176, 84, 14], 'the box on the page\'s view');
  await shell.tab.close();
  // Reloaded: the focused thread loads the picture by its file; the conversation's reads fetched none before.
  pictures.length = 0;
  shell = await openNative('shell-box-picture');
  watch(shell.tab);
  const control = await shell.place('control', CARD);
  const press = shell.at(control.x + control.w / 2, control.y + control.h / 2);
  await shell.tab.mouse.click(press.x, press.y);
  await shell.ui.waitForFunction(() => document.querySelectorAll('#hx-threads > .hx-entry[data-thread]').length === 1);
  await sleep(2500); // a live read or more
  assert.deepEqual(pictures, [], 'no read of the threads fetches a picture');
  shown = await focused();
  assert.equal(pictures.length, 1);
  const asked = new URL(pictures[0]);
  assert.deepEqual([asked.pathname, asked.searchParams.get('scope'), asked.searchParams.get('subject'), asked.searchParams.get('name')],
    ['/api/pictures', 'shell-box-picture', CARD, comment.picture]);
  assert.deepEqual(shown.stroke, [176, 84, 14]);
  await shell.tab.close();
  // On a phone, as the mockup draws it: the comment UI page under the page, its focused thread showing the picture in
  // view, also after a live read draws the thread again. The same browser, so its draft shows.
  shell = await openNative('shell-box-picture', { size: { width: 390, height: 844 } });
  const phoneControl = await shell.place('control', CARD);
  const phonePress = shell.at(phoneControl.x + phoneControl.w / 2, phoneControl.y + phoneControl.h / 2);
  await shell.tab.mouse.click(phonePress.x, phonePress.y);
  await shell.ui.waitForFunction(() => document.querySelectorAll('#hx-threads > .hx-entry[data-thread]').length === 1);
  await focused();
  await sleep(2500); // a live read or more
  const inView = await shell.ui.evaluate(() => {
    const stream = document.querySelector('.hx-focused .hx-focused-stream').getBoundingClientRect();
    const picture = document.querySelector('.hx-focused .hx-picture').getBoundingClientRect();
    return { stream: [stream.top, stream.bottom], picture: [picture.top, picture.bottom], height: innerHeight };
  });
  assert.ok(inView.height < 500, 'the comment UI page as a phone shows it, under the page');
  assert.ok(inView.picture[0] >= inView.stream[0] - 0.5 && inView.picture[1] <= inView.stream[1] + 0.5, 'the picture in view: ' + JSON.stringify(inView));
  await shell.tab.close();
}

// #acceptance-shell-close: the comment UI page's close control: the shell shows the spec page, its pins, and no comment UI.
{
  const shell = await openShell('shell-close');
  await shell.ui.click('#hx-ui-close');
  await shell.tab.waitForFunction(() => document.body.classList.contains('closed') && getComputedStyle(document.getElementById('comments')).display === 'none');
  assert.deepEqual(await pins(shell.frame), ['report-totals 1']);
  assert.equal(await shell.tab.evaluate(() => window.shellLog.some(m => m.direction === 'comments' && m.data.type === 'spec-chat-close')), true);
  await shell.tab.close();
}

// #shell-roles-late: a hello after the page drew its own comment UI: text typed in its composer is saved as a draft,
// read by the comment UI page, and the page's comment UI is gone.
{
  const shell = await openShell('shell-pick', REPORT, { variant: 'late', held: false });
  await shell.frame.waitForFunction(() => /connected/.test(document.getElementById('hx-status')?.textContent || ''));
  await shell.frame.click('#hx-mode');
  await shell.frame.click('p[data-anchor="report-export"]');
  await shell.frame.fill('.hx-composer textarea', 'Typed before the hello.');
  await shell.tab.click('#hold');
  await shell.frame.waitForFunction(() => document.body.classList.contains('hx-held'));
  assert.deepEqual(await ownComments(shell.frame), NONE);
  await shell.ui.waitForFunction(() => [...document.querySelectorAll('#hx-threads .hx-entry[data-thread]')].some(e =>
    e.querySelector('.hx-text')?.textContent === 'Typed before the hello.' && e.querySelector('.hx-receipt')?.textContent === 'Draft'));
  await shell.tab.close();
}

// #acceptance-shell-phone, #mobile-held: on a phone the comment UI page is a sheet over the lower part of the screen,
// within its width, the comment's bubble a draft in it, and the one Hand off under it.
{
  const shell = await openShell('shell-pick-key', REPORT, { on: phone }); // its desktop criterion saved nothing
  assert.ok(await shell.frame.$eval(STORY, el => el.getBoundingClientRect().bottom) + 40 < await shell.tab.$eval('#comments', el => el.getBoundingClientRect().top));
  await commentFromShell(shell, STORY, 'Saved on a phone.', true);
  const sheet = await shell.tab.$eval('#comments', el => { const r = el.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom }; });
  assert.deepEqual([sheet.left, sheet.right, sheet.bottom], [0, 390, 844], 'across the screen, to its bottom');
  assert.ok(sheet.top > 300 && sheet.top < 600, 'over the lower part of the screen');
  const ui = await shell.ui.evaluate(() => {
    const bar = document.querySelector('.hx-handoff').getBoundingClientRect(), stream = document.getElementById('hx-threads').getBoundingClientRect();
    const draft = [...document.querySelectorAll('#hx-threads .hx-entry[data-thread]')].find(e => e.querySelector('.hx-text')?.textContent === 'Saved on a phone.');
    return { width: document.documentElement.scrollWidth, inner: innerWidth, handoffs: document.querySelectorAll('#hx-handoff').length,
      under: bar.top >= stream.bottom - 1, draft: Boolean(draft && draft.querySelector('.hx-bubble[data-draft]')),
      touch: [...document.querySelectorAll('.hx-handoff button')].every(b => b.getBoundingClientRect().height >= 44) };
  });
  assert.deepEqual(ui, { width: ui.inner, inner: 390, handoffs: 1, under: true, draft: true, touch: true });
  assert.deepEqual(await ownComments(shell.frame), NONE, 'no hand-off control on the held page');
  await shell.tab.close();
}

// #acceptance-shell-phone-id: on a phone in a shell whose frames have no origin, a comment, the shell reloaded, then a
// reply: both by the same fruit name.
{
  const shell = await openShell('shell-close', REPORT, { on: phone, variant: 'bare' }); // its seeded thread is Mango's
  assert.equal(await shell.ui.evaluate(() => self.origin), 'null', 'the comment UI page has no origin');
  assert.match(await shell.ui.evaluate(() => location.hash), /^#browser=[a-z0-9]+$/);
  await commentFromShell(shell, STORY, 'Named before the reload.', true);
  await listed(() => stored('shell-close'), events => events.some(e => e.text === 'Named before the reload.'), 'the comment'); // the reloaded page reads it there
  await shell.tab.reload();
  const ui = await (await shell.tab.waitForSelector('#comments')).contentFrame();
  const frame = await (await shell.tab.waitForSelector('#held')).contentFrame();
  await frame.waitForFunction(() => document.body.classList.contains('hx-held'));
  await ui.waitForFunction(() => [...document.querySelectorAll('#hx-threads .hx-text')].some(t => t.textContent === 'Named before the reload.'));
  await ui.tap('#hx-threads .hx-entry[data-thread] .hx-bubble >> text=Named before the reload.');
  await ui.waitForSelector('.hx-focused .hx-composer textarea');
  await ui.fill('.hx-focused .hx-composer textarea', 'Named after it.');
  await ui.tap('.hx-focused .hx-composer [data-act=save]');
  await ui.waitForFunction(() => [...document.querySelectorAll('.hx-focused .hx-text')].some(t => t.textContent === 'Named after it.'));
  const mine = () => stored('shell-close').filter(b => ['Named before the reload.', 'Named after it.'].includes(b.text));
  const saved = await listed(mine, 2, 'both comments');
  assert.equal(saved.length, 2);
  assert.equal(saved[0].author, saved[1].author, 'one fruit name');
  assert.equal(saved[0].browser, saved[1].browser, 'one browser');
  // review-state #model-author-shown: the fruit is drawn at random, so one run in 24 it is Mango, the seeded thread's
  // other browser's: browsers sharing it are numbered by their first event's file name, so this one shows as Mango 2.
  const sharing = [...new Set(stored('shell-close').filter(b => b.author === saved[0].author).map(b => b.browser))];
  const shown = sharing.indexOf(saved[0].browser) ? `${saved[0].author} ${sharing.indexOf(saved[0].browser) + 1}` : saved[0].author;
  assert.deepEqual(await ui.$$eval('.hx-focused .hx-who', whos => whos.map(w => w.textContent)), [shown], 'shown as one author');
  await shell.tab.close();
}

assert.deepEqual(errors, [], 'no page errors');
await browser.close();
for (const server of servers) try { process.kill(-server.pid); } catch {}
console.log('ok runtime-shell');
