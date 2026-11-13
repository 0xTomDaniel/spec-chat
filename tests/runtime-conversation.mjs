import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { createServer } from 'node:net';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import { listed } from './fixtures/listed.mjs';

// Threads as one conversation (threads.spec.html#acceptance, spec-page-design #acceptance-color-consistency): the full
// runtime on the QA fixture's threads collections (tests/fixtures/qa reset.sh, spools.py THREADS, panes.py layer-stream
// and layer-video), served by the fixture's serve.py, in headless Chromium, on a desktop and a phone, on a spec page and
// through the comment layer; comments at a moment of a video, with a box, on Report walkthrough's video and a host page's
// (#video-moment); each message's stage, a tag beside its author (#stage-tag), on the QA fixture's stage lanes and host
// pages. Needs PLAYWRIGHT_CORE=<path to playwright-core>.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
assert.equal(readFileSync(resolve(root, 'docs/specs/.viz/runtime.js'), 'utf8'), runtime, 'runtime copies are identical');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_CORE || 'playwright-core');

const fixture = resolve(root, 'tests/fixtures/qa');
const qaRoot = mkdtempSync(join(tmpdir(), 'conversation-'));
const env = { ...process.env, QA_ROOT: qaRoot, QA_FIXTURE: fixture, QA_REPO_SPEC_CHAT: root };
execFileSync('sh', [join(fixture, 'reset.sh')], { env, stdio: 'inherit' });
const port = await new Promise(res => { const s = createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); });
const server = spawn('python3', [join(fixture, 'serve.py'), join(qaRoot, 'serve/registry.toml'), String(port)], {
  stdio: 'ignore', detached: true,
  env: { ...env, HOME: join(qaRoot, 'home'), XDG_STATE_HOME: join(qaRoot, 'state'), XDG_CONFIG_HOME: join(qaRoot, 'config'), HERDR_SOCKET_PATH: '' },
});
// Stop the server on any exit, also when a step throws before the finally below.
process.on('exit', () => { try { process.kill(-server.pid); } catch {} });
const base = `http://127.0.0.1:${port}`;
const LATER = 'docs/specs/later-only.spec.html';
const REPORT = 'docs/specs/report.spec.html';
const VIDEO_SPEC = 'docs/specs/report-video.spec.html';
for (let i = 0; ; i++) {
  try { if ((await fetch(`${base}/threads-order/${LATER}`)).ok) break; } catch {}
  if (i > 300) throw new Error('qa serve did not start');
  await new Promise(r => setTimeout(r, 100));
}

const PAPAYA = JSON.stringify({ browser: 'qa-browser-papaya', author: 'Papaya' });
const DESKTOP = { viewport: { width: 1280, height: 800 } };
const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true };
// Autoplay without a gesture, so a test plays a video as a reviewer's press does.
const browser = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
const errors = [];
const contexts = [];
// The collection's page, as the reviewer opens it; `own`: this browser is Papaya, so Papaya's messages are its own.
const open = async (collection, { page: path = LATER, device = DESKTOP, own = false } = {}) => {
  const context = await browser.newContext(device);
  contexts.push(context);
  if (own) await context.addInitScript(reviewer => localStorage.setItem('spec-chat:reviewer', reviewer), PAPAYA);
  const page = await context.newPage();
  page.on('pageerror', e => errors.push(collection + ': ' + e));
  await page.goto(`${base}/${collection}/${path}`);
  // Ready once its first read lands. A host page shows the comment layer, no toolbar: its first read draws a control. A
  // spec page's status names its threads only once its first read is in (runtime.js refresh); `connected` alone comes
  // before it (startLoops).
  if (path.endsWith('pane.html')) await page.waitForSelector('.hx-layer-control', { timeout: 15000 });
  else await page.waitForFunction(() => /connected · .* · \d+ threads/.test(document.getElementById('hx-status')?.textContent || ''), null, { timeout: 15000 });
  return page;
};
const openPanel = async page => {
  await page.click('#hx-dock-open');
  await page.waitForSelector('.hx-panel.open #hx-threads');
};
// The conversation as it reads: each entry's thread, status, side, bubbles, chip, replies line, receipts, and dot.
const conversation = page => page.evaluate(() => [...document.querySelectorAll('#hx-threads > .hx-entry')].map(entry => ({
  thread: entry.dataset.thread || null,
  question: entry.dataset.question || null,
  side: entry.classList.contains('hx-me') ? 'me' : 'them',
  bubbles: [...entry.querySelectorAll(':scope > .hx-msg .hx-bubble .hx-text')].map(t => t.textContent),
  chip: entry.querySelector(':scope > .hx-chip')?.textContent ?? null,
  replies: entry.querySelector(':scope > .hx-replies')?.textContent ?? null,
  receipts: [...entry.querySelectorAll('.hx-receipt')].map(r => r.textContent),
  dot: Boolean(entry.querySelector(':scope > .hx-msg:first-of-type .hx-dot')),
  resolved: entry.querySelector(':scope > .hx-resolved-line')?.textContent ?? null,
})));
const rect = (page, selector) => page.evaluate(s => { const r = document.querySelector(s).getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height }; }, selector);
// threads #video-moment: the walkthrough's video, as the reviewer plays and pauses it.
const VIDEO = 'figure[data-anchor="walkthrough-video"] video';
const playUntil = (page, selector, seconds) => page.evaluate(async ([s, seconds]) => {
  const video = document.querySelector(s);
  if (video.readyState < 1) await new Promise(done => video.addEventListener('loadedmetadata', done, { once: true }));
  await video.play();
  while (video.currentTime < seconds) await new Promise(done => setTimeout(done, 20));
}, [selector, seconds]);
const pauseVideo = (page, selector) => page.evaluate(s => { const video = document.querySelector(s); video.pause(); return video.currentTime; }, selector);
const videoState = (page, selector) => page.evaluate(s => { const v = document.querySelector(s); return { paused: v.paused, seeking: v.seeking, time: v.currentTime, duration: v.duration }; }, selector);
// The boxes drawn on a video's frame, as fractions of its frame (the video's box: the fixture's frame fills it).
const videoBoxes = (page, selector) => page.evaluate(s => {
  const v = document.querySelector(s).getBoundingClientRect();
  return [...document.querySelectorAll('.hx-video-box')].filter(b => !b.hidden && b.getClientRects().length).map(b => { const r = b.getBoundingClientRect();
    return { left: (r.left - v.left) / v.width, top: (r.top - v.top) / v.height, width: r.width / v.width, height: r.height / v.height }; });
}, selector);
const near = (actual, expected, by, what) => assert.ok(Math.abs(actual - expected) <= by, `${what}: ${actual} near ${expected}`);
const nearBox = (actual, expected, what) => { for (const k of ['left', 'top', 'width', 'height']) near(actual[k], expected[k], 0.01, what + ' ' + k); };
const until = async (check, what, ms = 5000) => {
  for (const end = Date.now() + ms; Date.now() < end; await new Promise(r => setTimeout(r, 100))) if (await check()) return;
  assert.fail('timed out: ' + what);
};
const savedComments = page => page.evaluate(async () => (await (await fetch('/api/events?dir=' + encodeURIComponent(location.pathname.slice(1) + '.review'))).json())
  .filter(e => e.body.event === 'comment').map(e => e.body));
// A new comment's Save held down while `other` saves as another reviewer; `redrawn` waits for the live read that brings
// that save, which draws the conversation again under the press; then released. The press is a click only if the Save
// pressed is still in the document: Chrome sends no click when the press and the release share no node.
const holdSave = async (page, save, other, redrawn) => {
  const at = await page.locator(save).boundingBox();
  const [x, y] = [at.x + at.width / 2, at.y + at.height / 2];
  await page.mouse.move(x, y);
  await page.mouse.down();
  await other();
  await redrawn();
  // Through the comment layer's shadow root, to the button the release lands on.
  const under = await page.evaluate(([x, y]) => {
    let el = document.elementFromPoint(x, y);
    while (el && el.shadowRoot) { const inner = el.shadowRoot.elementFromPoint(x, y); if (!inner || inner === el) break; el = inner; }
    return el && el.closest('[data-act]') ? el.closest('[data-act]').dataset.act : null;
  }, [x, y]);
  assert.equal(under, 'save', 'Save still under the held press after the draw');
  await page.mouse.up();
};

try {
  // #acceptance-stream-order: three threads begun at different times on different blocks read as their first messages
  // in the order written, the newest at the bottom, each with a chip naming its block; the panel opens at its bottom.
  let page = await open('threads-order');
  await openPanel(page);
  let entries = await conversation(page);
  assert.deepEqual(entries.map(e => e.bubbles), [['Is saving filters in this slice?'], ['Say which week the report shows.'], ['Name the teams the filter lists.']],
    'first messages in the order written, not page order');
  assert.deepEqual(entries.map(e => e.chip), ['Behavior · TBD: saved filters…', 'QA fixture: a spec whose only TBD…',
    'Acceptance criteria · The report page filters rows by the…'], 'each chip names its block: its section, then its first words');
  const tops = await page.evaluate(() => [...document.querySelectorAll('#hx-threads > .hx-entry .hx-bubble')].map(b => b.getBoundingClientRect().top));
  assert.deepEqual([...tops].sort((a, b) => a - b), tops, 'the newest at the bottom');
  assert.ok(await page.evaluate(() => { const s = document.getElementById('hx-threads'); return s.scrollHeight - s.scrollTop - s.clientHeight < 8; }), 'opens scrolled to its bottom');
  assert.equal(await page.textContent('.hx-panel-head'), 'Conversation 3 threads ' + await page.textContent('#hx-agent'), 'the head reads Conversation and counts its threads');
  // #acceptance-stream-rule: no separate thread cards or thread list.
  assert.equal(await page.locator('.hx-thread').count(), 0, 'no thread cards');
  await page.close();

  // #acceptance-sides: this browser's comment at the panel's right; pressing the replies line shows the agent's reply
  // under it at the left.
  page = await open('threads-sides', { own: true });
  await openPanel(page);
  entries = await conversation(page);
  assert.deepEqual([entries[0].side, entries[0].bubbles, entries[0].replies], ['me', ['List the teams alphabetically?'], '1 reply · Agent: Yes: the filter lists teams A to…']);
  await page.click('.hx-replies');
  await page.waitForFunction(() => document.querySelectorAll('#hx-threads .hx-bubble').length === 2);
  const panel = await rect(page, '.hx-panel');
  const [mine, reply] = await page.evaluate(() => [...document.querySelectorAll('#hx-threads .hx-bubble')].map(b => { const r = b.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top }; }));
  assert.ok(panel.right - mine.right < 20 && mine.left - panel.left > 40, 'the reviewer\'s bubble at the right');
  assert.ok(reply.left - panel.left < 20 && panel.right - reply.right > 40, 'the agent\'s reply at the left');
  assert.ok(reply.top > mine.top, 'the reply under the comment');
  assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('#hx-threads .hx-msg')].map(m => m.className)), ['hx-msg hx-me', 'hx-msg hx-them']);
  await page.close();

  // #acceptance-replies-folded: three replies fold into one line, the newest reply's author and first words, no reply
  // bubble shown; #acceptance-replies-open: pressing it shows them in order, the other thread unchanged.
  page = await open('threads-replies');
  await openPanel(page);
  entries = await conversation(page);
  assert.deepEqual(entries.map(e => [e.bubbles, e.replies]), [[['Does the filter remember the last team?'], '3 replies · Agent: Done: the filter keeps the last team'],
    [['Say which week the report shows.'], null]], 'replies folded into one line');
  await page.click('.hx-replies');
  await page.waitForFunction(() => document.querySelectorAll('#hx-threads .hx-bubble').length === 5);
  entries = await conversation(page);
  assert.deepEqual(entries.map(e => e.bubbles), [['Does the filter remember the last team?', 'Not yet; it resets on each visit.', 'Please keep it between visits.',
    'Done: the filter keeps the last team.'], ['Say which week the report shows.']], 'the replies under their first message in order; the other thread unchanged');
  assert.equal(await page.getAttribute('.hx-replies', 'aria-expanded'), 'true');
  await page.click('.hx-replies');
  await page.waitForFunction(() => document.querySelectorAll('#hx-threads .hx-bubble').length === 2);

  // #acceptance-focus: pressing a thread's first message shows its messages alone above a reply field, over the
  // conversation dimmed; on a desktop it fills the panel.
  await page.click('#hx-threads [data-thread="qa-long"] .hx-bubble');
  await page.waitForSelector('.hx-panel .hx-focused');
  assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('.hx-focused .hx-bubble .hx-text')].map(t => t.textContent)),
    ['Does the filter remember the last team?', 'Not yet; it resets on each visit.', 'Please keep it between visits.', 'Done: the filter keeps the last team.']);
  const focused = await rect(page, '.hx-focused');
  const field = await rect(page, '.hx-focused textarea');
  const lastBubble = await page.evaluate(() => [...document.querySelectorAll('.hx-focused .hx-bubble')].pop().getBoundingClientRect().bottom);
  assert.ok(field.top > lastBubble, 'the reply field under its messages');
  const panelNow = await rect(page, '.hx-panel');
  assert.ok(focused.left >= panelNow.left && focused.right <= panelNow.right && focused.height > panelNow.height * 0.7, 'it fills the review panel');
  assert.equal(await page.evaluate(() => getComputedStyle(document.querySelector('.hx-panel > .hx-veil')).backgroundColor), 'rgba(0, 0, 0, 0.42)', 'the conversation dimmed');
  assert.equal(await page.locator('.hx-focused [data-act=back]').textContent(), 'Conversation');
  await page.click('.hx-focused [data-act=back]');
  await page.waitForSelector('.hx-focused', { state: 'detached' });
  assert.equal(await page.locator('.hx-veil').count(), 0, 'Back returns to the conversation');
  await page.close();

  // #acceptance-focus-reply: a reply saved in the focused thread joins it; closed, the replies line reads 2 replies with
  // the reviewer's reply as the newest.
  page = await open('threads-focus-reply', { own: true });
  await openPanel(page);
  await page.click('#hx-threads .hx-bubble');
  // #focus-reply, as the focused thread's mockup draws it: a one-line field that grows with its text, beside Save.
  const fieldHeight = () => page.evaluate(() => document.querySelector('.hx-focused textarea').getBoundingClientRect().height);
  const oneLine = await fieldHeight();
  assert.ok(oneLine >= 36 && oneLine < 44, 'a one-line field: ' + oneLine);
  assert.equal(await page.textContent('.hx-focused [data-act=save]'), 'Save');
  const saveAt = await rect(page, '.hx-focused [data-act=save]');
  const fieldAt = await rect(page, '.hx-focused textarea');
  assert.ok(saveAt.left >= fieldAt.right && Math.abs(saveAt.bottom - fieldAt.bottom) < 1, 'Save beside the field');
  await page.fill('.hx-focused textarea', 'Keep the last team\nfor a week,\nthen reset.');
  assert.ok(await fieldHeight() > oneLine + 30, 'the field grows with its lines');
  await page.fill('.hx-focused textarea', 'Keep it.');
  assert.equal(await fieldHeight(), oneLine, 'and returns to one line');
  await page.fill('.hx-focused textarea', 'Keep the last team for a week.');
  await page.click('.hx-focused [data-act=save]');
  await page.waitForFunction(() => document.querySelectorAll('.hx-focused .hx-bubble').length === 3);
  assert.equal(await page.inputValue('.hx-focused textarea'), '', 'the field empties once saved');
  await page.click('.hx-focused [data-act=back]');
  await page.waitForSelector('.hx-focused', { state: 'detached' });
  entries = await conversation(page);
  assert.equal(entries[0].replies, '2 replies · You: Keep the last team for a week');
  assert.deepEqual(entries[0].receipts, ['Draft'], 'the reply is a draft');
  await page.close();

  // #acceptance-focus-pin: pressing a block's pin opens that thread focused in the panel, over the conversation dimmed.
  page = await open('threads-pin');
  assert.equal(await page.evaluate(() => document.querySelector('.hx-panel').classList.contains('open')), false);
  await page.click('.hx-pin');
  await page.waitForSelector('.hx-panel.open .hx-focused');
  assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('.hx-focused .hx-bubble .hx-text')].map(t => t.textContent)),
    ['Name the teams the filter lists.', 'Done; OK to resolve?']);
  assert.equal(await page.locator('.hx-panel > .hx-veil').count(), 1);
  await page.close();

  // #acceptance-focus-phone: a tapped pin opens a sheet over the screen's lower part, within its width, with the
  // thread's messages and a reply field; the block stays in view above it; the bottom toolbar keeps the one Hand off.
  page = await open('threads-pin', { device: PHONE });
  await page.tap('.hx-pin');
  await page.waitForSelector('body > .hx-focused.hx-sheet');
  await page.waitForTimeout(800); // the smooth scroll that brings the block above the sheet
  const sheet = await rect(page, '.hx-focused.hx-sheet');
  const block = await rect(page, '[data-anchor="filter-team"]');
  const line = await rect(page, '.hx-top-line');
  assert.ok(sheet.left >= 0 && sheet.right <= 390 && sheet.top >= 844 * 0.4, 'a sheet over the lower part, within the width');
  assert.ok(block.top >= line.bottom - 1 && block.bottom <= sheet.top, 'the block in view above the sheet');
  assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('.hx-sheet .hx-bubble .hx-text')].map(t => t.textContent)),
    ['Name the teams the filter lists.', 'Done; OK to resolve?']);
  const sheetField = await rect(page, '.hx-sheet textarea');
  assert.ok(sheetField.left >= 0 && sheetField.right <= 390, 'its reply field within the width');
  assert.ok(sheetField.height >= 44 && sheetField.height < 52, 'a one-line field: ' + sheetField.height);
  assert.equal(await page.textContent('.hx-sheet [data-act=save]'), 'Save');
  assert.equal(await page.evaluate(() => document.querySelector('.hx-panel').classList.contains('open')), false, 'the panel stays closed');
  const handoffs = await page.evaluate(() => [...document.querySelectorAll('button')].filter(b => /Hand off/.test(b.textContent) && b.getClientRects().length)
    .map(b => b.closest('.hx-toolbar') ? 'toolbar' : 'elsewhere'));
  assert.deepEqual(handoffs, ['toolbar'], 'the bottom toolbar shows the one Hand off');
  const toolbar = await rect(page, '.hx-toolbar');
  assert.ok(toolbar.top >= sheetField.bottom, 'the toolbar under the sheet\'s field');
  assert.equal(await page.evaluate(() => document.elementFromPoint(195, document.getElementById('hx-handoff').getBoundingClientRect().top + 4)?.closest('.hx-toolbar') !== null), true,
    'Hand off is not covered by the sheet');
  await page.close();

  // #acceptance-chip-jump: a thread's chip brings its block, far below the top, into view and rings it.
  page = await open('threads-chip');
  assert.ok((await rect(page, '[data-anchor="filter-team"]')).top > 800, 'the block starts far below the screen');
  await openPanel(page);
  await page.click('#hx-threads .hx-chip');
  // In view, and ringed once the smooth scroll and the ring's own move settle.
  await page.waitForFunction(() => {
    const block = document.querySelector('[data-anchor="filter-team"]').getBoundingClientRect();
    const ring = document.querySelector('.hx-thread-ring');
    const r = ring && !ring.hidden && ring.getBoundingClientRect();
    return block.top >= 0 && block.bottom <= innerHeight && r && r.left <= block.left && r.right >= block.right && r.top <= block.top && r.bottom >= block.bottom;
  }, null, { timeout: 5000 });
  await page.close();

  // #acceptance-receipt-sent: a draft's receipt reads Draft, then Sent once handed off, in the draft/pending color
  // (#receipt-place, spec-page-design #map-draft), as the thread's pin.
  page = await open('threads-sent', { own: true });
  await openPanel(page);
  assert.deepEqual((await conversation(page))[0].receipts, ['Draft']);
  const draftColor = await page.evaluate(() => getComputedStyle(document.querySelector('#hx-threads .hx-receipt')).color);
  await page.click('#hx-handoff');
  await page.waitForFunction(() => document.querySelector('#hx-threads .hx-receipt')?.textContent === 'Sent', null, { timeout: 5000 });
  assert.deepEqual(await page.evaluate(() => [getComputedStyle(document.querySelector('#hx-threads .hx-receipt')).color,
    getComputedStyle(document.querySelector('.hx-pin')).backgroundColor]), [draftColor, draftColor], 'Sent in the draft/pending color, as its pin');
  assert.equal(await page.evaluate(() => getComputedStyle(document.querySelector('#hx-threads .hx-bubble')).boxShadow), 'none', 'handed off: no longer outlined as a draft');
  await page.close();

  // #acceptance-receipt-draft: a comment saved from comment mode ends the conversation, its receipt Draft.
  page = await open('threads-new');
  await page.click('#hx-mode');
  await page.click('[data-anchor="filter-team"]');
  await page.fill('.hx-compose textarea', 'List the teams alphabetically?');
  await page.click('.hx-compose [data-act=save]');
  await page.waitForSelector('#hx-threads > .hx-entry');
  assert.equal(await page.evaluate(() => document.querySelector('.hx-panel').classList.contains('open')), true);
  entries = await conversation(page);
  assert.deepEqual(entries.map(e => [e.side, e.bubbles, e.receipts]), [['me', ['List the teams alphabetically?'], ['Draft']]]);
  assert.equal(await page.evaluate(() => document.getElementById('hx-threads').lastElementChild.dataset.thread !== undefined), true, 'the conversation ends with it');
  assert.equal(await page.evaluate(() => getComputedStyle(document.querySelector('#hx-threads .hx-bubble')).backgroundColor !== getComputedStyle(document.querySelector('#hx-threads .hx-bubble')).color), true);
  await page.close();

  // #stream-new through a live read (review-state #live-pull): Save held down on a new comment while another reviewer's
  // save on another block lands and the live read draws the conversation again; released, the comment is saved and ends
  // the conversation.
  page = await open('threads-new');
  let other = await open('threads-new');
  await page.click('#hx-mode');
  await page.click('[data-anchor="context"]');
  await page.fill('.hx-compose textarea', 'Name the week in the title.');
  await holdSave(page, '.hx-compose [data-act=save]', async () => {
    await other.click('#hx-mode');
    await other.click('[data-anchor="story-filter"]');
    await other.fill('.hx-compose textarea', 'Who can save a filter?');
    await other.click('.hx-compose [data-act=save]');
  }, () => page.waitForFunction(() => [...document.querySelectorAll('#hx-threads .hx-text')].some(t => t.textContent === 'Who can save a filter?'), null, { timeout: 10000 }));
  await until(async () => (await conversation(page)).at(-1)?.bubbles[0] === 'Name the week in the title.', 'the held save shown');
  assert.deepEqual((await conversation(page)).map(e => [e.side, e.bubbles]), [['them', ['List the teams alphabetically?']],
    ['them', ['Who can save a filter?']], ['me', ['Name the week in the title.']]], 'the held save ends the conversation');
  assert.deepEqual((await listed(() => savedComments(page), 3, 'the held save')).map(c => [c.anchorId, c.text]).sort(),
    [['context', 'Name the week in the title.'], ['filter-team', 'List the teams alphabetically?'], ['story-filter', 'Who can save a filter?']]);
  await page.close();
  await other.close();

  // #acceptance-receipt-seen: acknowledged without a reply reads Seen, under the comment's bubble.
  page = await open('threads-seen');
  await openPanel(page);
  entries = await conversation(page);
  assert.deepEqual(entries.map(e => [e.bubbles, e.replies, e.receipts]), [[['Name the teams the filter lists.'], null, ['Seen']]]);
  await page.close();

  // #acceptance-receipt-resolved: a resolved thread of three messages is one line, Resolved and its first words.
  page = await open('threads-resolved');
  await openPanel(page);
  entries = await conversation(page);
  assert.deepEqual(entries.map(e => [e.resolved, e.bubbles]), [['Resolved · Name the teams the filter lists', []]]);
  // prompt-first-shaping #thread-expand: pressing it opens it focused, with its whole history.
  await page.click('.hx-resolved-line');
  await page.waitForSelector('.hx-focused');
  assert.equal(await page.locator('.hx-focused .hx-bubble').count(), 3);
  await page.close();

  // #acceptance-waiting-root: the thread whose last message is the agent's shows a dot on its first message; the one
  // ending with the reviewer's shows none. #acceptance-waiting-pin: with the panel closed, its block's pin shows the dot.
  page = await open('threads-waiting', { own: true });
  await page.waitForFunction(() => document.querySelectorAll('.hx-pin').length === 2); // drawn once the first read lands
  const pins = await page.evaluate(() => [...document.querySelectorAll('.hx-pin')].map(p => [p.parentElement.closest('[data-anchor]').dataset.anchor, Boolean(p.querySelector('.hx-dot'))]));
  assert.deepEqual(pins.sort(), [['context', false], ['filter-team', true]], 'the waiting thread\'s pin shows a dot');
  await openPanel(page);
  entries = await conversation(page);
  assert.deepEqual(entries.map(e => [e.bubbles[0], e.dot]), [['Name the teams the filter lists.', true], ['Say which week the report shows.', false]]);
  await page.close();

  // #acceptance-question-bubble: the open question ends the conversation as the agent's bubble with both answer buttons
  // and a chip naming its block, under the thread.
  page = await open('threads-question', { page: REPORT });
  await openPanel(page);
  entries = await conversation(page);
  assert.deepEqual(entries.map(e => e.thread || 'question ' + e.question), ['qa-totals', 'question export-format']);
  const question = await page.evaluate(() => { const q = document.querySelector('#hx-threads > .hx-question');
    return { side: q.classList.contains('hx-them'), who: q.querySelector('.hx-who').textContent, text: q.querySelector('.hx-bubble .hx-text').textContent,
      buttons: [...q.querySelectorAll('.hx-bubble .hx-choice')].map(b => b.textContent), chip: q.querySelector(':scope > .hx-chip').textContent }; });
  assert.deepEqual(question, { side: true, who: 'Agent', text: 'TBD: whether the export also offers XLSX.', buttons: ['CSV only (recommended)', 'CSV and XLSX', 'Reply'],
    chip: 'Behavior · TBD: whether the export also offers XLSX' });
  assert.equal(await page.textContent('#hx-count'), '1 thread · 1 question');
  // #stream, as the conversation's mockup draws it: with Next TBD showing, the hand-off bar keeps its note, Next TBD, and
  // Hand off on one line, none wrapping.
  const bar = await page.evaluate(() => [...document.querySelectorAll('.hx-panel .hx-handoff > *')].filter(el => !el.hidden)
    .map(el => { const r = el.getBoundingClientRect(); return { id: el.id, middle: r.top + r.height / 2, height: r.height }; }));
  assert.deepEqual(bar.map(el => el.id), ['hx-drafts', 'hx-next-tbd', 'hx-handoff']);
  assert.ok(bar.every(el => Math.abs(el.middle - bar[0].middle) < 1), 'one line: ' + JSON.stringify(bar));
  assert.ok(bar.slice(1).every(el => el.height < 40), 'neither button wraps: ' + JSON.stringify(bar));
  await page.close();

  // #acceptance-question-answer: an answer pressed in the question's bubble shows as chosen, and the reviewer's answer
  // shows under the bubble as a draft; nothing opens.
  page = await open('threads-answer', { page: REPORT });
  await openPanel(page);
  await page.click('.hx-question .hx-choice >> text="CSV only (recommended)"');
  await page.waitForFunction(() => document.querySelector('.hx-question .hx-choice')?.getAttribute('aria-pressed') === 'true', null, { timeout: 5000 });
  const answered = await page.evaluate(() => { const q = document.querySelector('.hx-question');
    const answer = q.querySelector('.hx-entry');
    return { pressed: [...q.querySelectorAll('.hx-bubble .hx-choice')].map(b => b.getAttribute('aria-pressed')), side: answer.classList.contains('hx-me'),
      text: answer.querySelector('.hx-bubble .hx-text').textContent, draft: answer.querySelector('.hx-bubble').hasAttribute('data-draft'),
      receipt: answer.querySelector('.hx-receipt').textContent, below: answer.getBoundingClientRect().top > q.querySelector('.hx-bubble').getBoundingClientRect().bottom,
      composers: document.querySelectorAll('.hx-composer').length, focused: document.querySelectorAll('.hx-focused').length }; });
  assert.deepEqual(answered, { pressed: ['true', 'false', null], side: true, text: 'CSV only', draft: true, receipt: 'Draft', below: true, composers: 0, focused: 0 });
  await page.close();

  // #acceptance-question-same: the same answer button in the question's block on the page shows as chosen.
  page = await open('threads-answer-same', { page: REPORT });
  await openPanel(page);
  await page.click('.hx-question .hx-choice >> text="CSV only (recommended)"');
  await page.waitForFunction(() => document.querySelector('[data-anchor="export-format"] button[data-spec-choice="CSV only"]').getAttribute('aria-pressed') === 'true', null, { timeout: 5000 });
  assert.equal(await page.getAttribute('[data-anchor="export-format"] button[data-spec-choice="CSV and XLSX"]', 'aria-pressed'), 'false');
  const saved = (await listed(() => savedComments(page), 1, 'the answer comment')).map(c => [c.anchorId, c.target, c.text]);
  assert.deepEqual(saved, [['export-format', { type: 'element', key: 'button[1]' }, 'CSV only']], 'the same answer comment as the page\'s button');
  await page.close();

  // spec-page-design #acceptance-color-consistency: every pin and every receipt of one status share its color, and no
  // two statuses share one.
  page = await open('threads-colors');
  await openPanel(page);
  const colors = await page.evaluate(() => {
    const pin = s => { const p = getComputedStyle(document.querySelector('.hx-pin[data-s="' + s + '"]')); return s === 'resolved' ? p.color : p.backgroundColor; };
    const receipts = s => [...new Set([...document.querySelectorAll('#hx-threads .hx-receipt[data-s="' + s + '"]')].map(r => getComputedStyle(r).color))];
    return Object.fromEntries(['draft', 'acknowledged', 'resolved'].map(s => [s, { pin: pin(s), receipts: receipts(s) }]));
  });
  for (const [status, { pin, receipts }] of Object.entries(colors)) assert.deepEqual(receipts, [pin], status + ': pins and receipts share one color');
  assert.equal(new Set(Object.values(colors).map(c => c.pin)).size, 3, 'no two statuses share a color');
  await page.close();

  // #acceptance-layer-stream: an anchored item's conversation shows both its threads' first messages in the order
  // written, the newest at the bottom.
  page = await open('layer-stream', { page: 'docs/pane.html' });
  await page.waitForSelector('.hx-layer-control[aria-label="Comment on Agent turn 42"]');
  await page.click('.hx-layer-control[aria-label="Comment on Agent turn 42"]');
  await page.waitForSelector('.hx-layer-box .hx-entry');
  const layerStream = await page.evaluate(() => [...document.querySelector('[data-spec-chat=comment-layer]').shadowRoot.querySelectorAll('.hx-layer-box .hx-threads > .hx-entry')]
    .map(e => ({ text: e.querySelector('.hx-bubble .hx-text').textContent, top: e.getBoundingClientRect().top, receipt: e.querySelector('.hx-receipt').textContent })));
  assert.deepEqual(layerStream.map(e => e.text), ['Question: why a CSV and not a sheet?', 'Change: name the columns after the report.']);
  assert.ok(layerStream[0].top < layerStream[1].top, 'the newest at the bottom');
  assert.equal(await page.locator('.hx-layer-box .hx-thread').count(), 0, 'no thread cards');
  // threads #focus in the item's box: a message opens its thread alone over the box's conversation dimmed.
  await page.click('.hx-layer-box .hx-entry .hx-bubble');
  await page.waitForSelector('.hx-layer-box .hx-focused');
  assert.deepEqual(await page.evaluate(() => [...document.querySelector('[data-spec-chat=comment-layer]').shadowRoot.querySelectorAll('.hx-focused .hx-bubble .hx-text')].map(t => t.textContent)),
    ['Question: why a CSV and not a sheet?']);
  assert.equal(await page.locator('.hx-layer-box > .hx-veil').count(), 1);
  await page.close();

  // The same in an item's box, through the layer's live read (comment-layer #ui-same): Save held down on a new comment
  // on Agent turn 42 while another reviewer's save on Totals per week lands and the live read draws the box again (that
  // item's pin drawn in the same pass); released, the comment is saved and ends the item's conversation.
  page = await open('layer-stream', { page: 'docs/pane.html' });
  other = await open('layer-stream', { page: 'docs/pane.html' });
  await page.click('.hx-layer-control[aria-label="Comment on Agent turn 42"]');
  await page.fill('.hx-layer-box .hx-compose textarea', 'Change: date the export file.');
  await holdSave(page, '.hx-layer-box .hx-compose [data-act=save]', async () => {
    await other.click('.hx-layer-control[aria-label="Comment on Totals per week"]');
    await other.fill('.hx-layer-box .hx-compose textarea', 'Question: rounded to whole units?');
    await other.click('.hx-layer-box .hx-compose [data-act=save]');
  }, () => page.waitForSelector('.hx-pin[aria-label^="Open thread"][aria-label*=": Totals per week, "]', { timeout: 10000 }));
  const layerTexts = () => page.evaluate(() => [...document.querySelector('[data-spec-chat=comment-layer]').shadowRoot.querySelectorAll('.hx-layer-box .hx-threads > .hx-entry')]
    .map(e => e.querySelector('.hx-bubble .hx-text').textContent));
  await until(async () => (await layerTexts()).at(-1) === 'Change: date the export file.', 'the held save shown in the box');
  assert.deepEqual(await layerTexts(), ['Question: why a CSV and not a sheet?', 'Change: name the columns after the report.', 'Change: date the export file.']);
  assert.deepEqual(await listed(() => page.evaluate(async () => (await (await fetch('/api/events?scope=layer-stream&subject=agent-turn:qa:42')).json()).events
    .filter(e => e.body.event === 'comment').map(e => e.body.text)), 3, 'the held save in the box'),
  ['Question: why a CSV and not a sheet?', 'Change: name the columns after the report.', 'Change: date the export file.']);
  await page.close();
  await other.close();

  // #acceptance-video-moment, #video-target, #video-fields: played, then paused, comment mode on, a click on the video
  // marks the moment it shows: the comment's bubble shows a chip reading that moment in minutes and seconds, after the
  // video's section (#root-chip-moment); the click plays nothing.
  page = await open('threads-video', { page: VIDEO_SPEC, own: true });
  await playUntil(page, VIDEO, 1.3);
  const pausedAt = await pauseVideo(page, VIDEO);
  await page.click('#hx-mode');
  await page.click(VIDEO);
  await page.fill('.hx-compose textarea', 'The totals should land first.');
  await page.click('.hx-compose [data-act=save]');
  await page.waitForSelector('#hx-threads > .hx-entry');
  assert.equal(await page.evaluate(() => document.querySelector('.hx-panel').classList.contains('open')), true);
  entries = await conversation(page);
  assert.deepEqual(entries.map(e => [e.side, e.bubbles, e.chip, e.receipts]), [['me', ['The totals should land first.'], 'Walkthrough · 0:01', ['Draft']]]);
  let shown = await videoState(page, VIDEO);
  assert.ok(shown.paused, 'the click marked the moment and played nothing');
  near(shown.time, pausedAt, 0.001, 'still at the moment it was paused at');
  let [videoComment] = await listed(() => savedComments(page), 1, 'the video comment');
  assert.deepEqual([videoComment.anchorId, videoComment.target, 'box' in videoComment], ['walkthrough-video', { type: 'element', key: 'video[1]' }, false]);
  near(videoComment.moment, pausedAt, 0.001, 'the comment carries the moment, in seconds');
  // #video-rail: under the video, as wide as it, a rail marks the thread at its moment; a moment with no box draws none.
  await page.waitForSelector('.hx-video-rail .hx-video-mark');
  const player = await rect(page, VIDEO);
  const rail = await rect(page, '.hx-video-rail');
  const mark = await rect(page, '.hx-video-mark');
  assert.ok(rail.top >= player.bottom && rail.top - player.bottom < 16, 'the rail under the video');
  near(rail.left, player.left, 1, 'the rail starts with the video');
  near(rail.width, player.width, 1, 'as wide as the video');
  near(mark.left + mark.width / 2, rail.left + rail.width * pausedAt / shown.duration, 1.5, 'the mark at the moment');
  assert.equal(await page.textContent('.hx-video-mark'), '1');
  assert.equal(await page.locator('.hx-pin').count(), 0, 'its rail mark is its pin');
  assert.equal(await page.textContent('.hx-video-time'), '0:04');
  assert.deepEqual(await videoBoxes(page, VIDEO), []);
  await page.close();

  // #acceptance-video-box: paused, comment mode on, a box dragged across part of the frame, then the comment saved: the
  // video shows the box on its paused frame, and the rail a mark at that moment; the comment carries the box as fractions
  // of the frame, and its chip reads the moment, then Box.
  page = await open('threads-video-box', { page: VIDEO_SPEC, own: true });
  await playUntil(page, VIDEO, 0.6);
  const boxedAt = await pauseVideo(page, VIDEO);
  await page.click('#hx-mode');
  const frame = await rect(page, VIDEO);
  await page.mouse.move(frame.left + frame.width * 0.1, frame.top + frame.height * 0.2);
  await page.mouse.down();
  await page.mouse.move(frame.left + frame.width * 0.3, frame.top + frame.height * 0.4, { steps: 4 });
  // While dragged, the box shows where it is drawn.
  nearBox((await videoBoxes(page, VIDEO))[0], { left: 0.1, top: 0.2, width: 0.2, height: 0.2 }, 'the box as it is dragged');
  await page.mouse.move(frame.left + frame.width * 0.4, frame.top + frame.height * 0.6, { steps: 4 });
  await page.mouse.up();
  await page.waitForSelector('.hx-compose textarea');
  assert.equal(await page.evaluate(() => getSelection().toString()), '', 'the drag selects no text');
  await page.fill('.hx-compose textarea', 'This card should open the team page.');
  await page.click('.hx-compose [data-act=save]');
  await page.waitForSelector('#hx-threads > .hx-entry');
  await page.waitForSelector('.hx-video-rail .hx-video-mark');
  shown = await videoState(page, VIDEO);
  assert.ok(shown.paused, 'the drag played nothing');
  near(shown.time, boxedAt, 0.001, 'still at its moment');
  const drawn = await videoBoxes(page, VIDEO);
  assert.equal(drawn.length, 1, 'the box drawn on its paused frame');
  nearBox(drawn[0], { left: 0.1, top: 0.2, width: 0.3, height: 0.4 }, 'the box where it was dragged');
  const boxRail = await rect(page, '.hx-video-rail');
  const boxMark = await rect(page, '.hx-video-mark');
  near(boxMark.left + boxMark.width / 2, boxRail.left + boxRail.width * boxedAt / shown.duration, 1.5, 'the rail\'s mark at that moment');
  [videoComment] = await listed(() => savedComments(page), 1, 'the boxed video comment');
  nearBox(videoComment.box, { left: 0.1, top: 0.2, width: 0.3, height: 0.4 }, 'the saved box');
  near(videoComment.moment, boxedAt, 0.001, 'the saved moment');
  assert.equal((await conversation(page))[0].chip, 'Walkthrough · 0:00 · Box');
  // Played on, the box leaves the frame; paused at the moment again, it shows again.
  await playUntil(page, VIDEO, boxedAt + 0.4);
  await page.waitForFunction(() => !document.querySelector('.hx-video-box'));
  await page.evaluate(([s, t]) => { const v = document.querySelector(s); v.pause(); v.currentTime = t; }, [VIDEO, videoComment.moment]);
  await page.waitForSelector('.hx-video-box');
  await page.close();

  // #acceptance-video-chip, #root-chip-moment: a thread on a box at one moment; the video played past it, its chip
  // pressed: the video shows paused at that moment with the box drawn on its frame.
  page = await open('threads-video-chip', { page: VIDEO_SPEC });
  await playUntil(page, VIDEO, 2.4);
  await openPanel(page);
  entries = await conversation(page);
  assert.deepEqual(entries.map(e => [e.bubbles, e.chip]), [[['The team panel should fade in faster.'], 'Walkthrough · 0:01 · Box']]);
  await page.click('#hx-threads .hx-chip');
  await page.waitForFunction(s => { const v = document.querySelector(s); return v.paused && !v.seeking && Math.abs(v.currentTime - 1.8) < 0.001; }, VIDEO);
  await page.waitForSelector('.hx-video-box');
  nearBox((await videoBoxes(page, VIDEO))[0], { left: 0.05, top: 0.3, width: 0.3, height: 0.45 }, 'the thread\'s box');
  assert.equal(await page.getAttribute('.hx-video-mark', 'class'), 'hx-video-mark active', 'its rail mark active');
  // #video-rail: played on, a press on the thread's rail mark seeks there, pauses, draws its box, and opens it focused.
  await playUntil(page, VIDEO, 2.4);
  await page.click('.hx-video-mark');
  await page.waitForSelector('.hx-panel .hx-focused[data-thread="qa-moment"]');
  await page.waitForFunction(s => { const v = document.querySelector(s); return v.paused && Math.abs(v.currentTime - 1.8) < 0.001; }, VIDEO);
  await page.waitForSelector('.hx-video-box');
  await page.close();

  // On a phone: a tap on the thread's rail mark opens it as the sheet, the video paused at its moment with its box drawn
  // above it; the marks take a phone's touch size.
  page = await open('threads-video-chip', { page: VIDEO_SPEC, device: PHONE });
  await page.waitForSelector('.hx-video-mark');
  assert.deepEqual(await page.evaluate(() => { const r = document.querySelector('.hx-video-mark').getBoundingClientRect(); return [r.width, r.height]; }), [36, 36]);
  const phoneRail = await rect(page, '.hx-video-rail');
  near(phoneRail.width, (await rect(page, VIDEO)).width, 1, 'on a phone, the rail as wide as the video');
  await page.tap('.hx-video-mark');
  await page.waitForSelector('body > .hx-focused.hx-sheet[data-thread="qa-moment"]');
  await page.waitForFunction(s => { const v = document.querySelector(s); return v.paused && Math.abs(v.currentTime - 1.8) < 0.001; }, VIDEO);
  await page.waitForSelector('.hx-video-box');
  assert.equal((await videoBoxes(page, VIDEO)).length, 1);
  assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('.hx-sheet .hx-chip')].map(c => c.textContent)), ['Walkthrough · 0:01 · Box']);
  await page.close();

  // #acceptance-video-layer, comment-layer #ui-same: on a host page, an anchored item's paused video; the item's comment
  // control opened and a comment saved: the item's conversation shows its bubble with a chip reading that moment. The
  // host's look stays its own: no rail and no box under its video (comment-layer #ui-place).
  page = await open('layer-video', { page: 'docs/pane.html' });
  const HOSTED = '.item[data-comment-label="Report walkthrough"] video';
  await page.waitForSelector('.hx-layer-control[aria-label="Comment on Report walkthrough"]');
  await playUntil(page, HOSTED, 1.2);
  const hostedAt = await pauseVideo(page, HOSTED);
  await page.click('.hx-layer-control[aria-label="Comment on Report walkthrough"]');
  await page.fill('.hx-layer-box .hx-composer textarea', 'Name the export file after the week.');
  await page.click('.hx-layer-box .hx-composer [data-act=save]');
  await page.waitForSelector('.hx-layer-box .hx-threads > .hx-entry');
  const layerVideo = () => page.evaluate(() => [...document.querySelector('[data-spec-chat=comment-layer]').shadowRoot.querySelectorAll('.hx-layer-box .hx-threads > .hx-entry')]
    .map(e => [e.querySelector('.hx-bubble .hx-text').textContent, e.querySelector(':scope > .hx-chip').textContent]));
  assert.deepEqual(await layerVideo(), [['Name the export file after the week.', 'Report walkthrough · 0:01']]);
  assert.equal(await page.locator('.hx-video-rail, .hx-video-box').count(), 0, 'the host page keeps its look');
  // #root-chip-moment through the layer: played on, the chip pressed, its video paused at the moment.
  await playUntil(page, HOSTED, hostedAt + 0.8);
  await page.click('.hx-layer-box .hx-chip');
  await page.waitForFunction(([s, t]) => { const v = document.querySelector(s); return v.paused && Math.abs(v.currentTime - t) < 0.001; }, [HOSTED, Math.round(hostedAt * 1000) / 1000]);
  await page.close();

  // threads #stage-tag: each bubble's author line, its author and the stage tag beside it.
  // `within`: the conversation, a focused thread, or, through the comment layer, the item's box.
  const heads = (page, within = '#hx-threads') => page.evaluate(within => {
    const root = within === 'layer' ? document.querySelector('[data-spec-chat=comment-layer]').shadowRoot.querySelector('.hx-layer-box') : document.querySelector(within);
    return [...root.querySelectorAll('.hx-msg')].map(m => [m.querySelector('.hx-bubble .hx-text').textContent,
      m.querySelector(':scope > .hx-who')?.firstChild?.textContent ?? null, m.querySelector(':scope > .hx-who .hx-stage')?.textContent ?? null]);
  }, within);
  const savedEvents = page => page.evaluate(async () => (await (await fetch('/api/events?dir=' + encodeURIComponent(location.pathname.slice(1) + '.review'))).json())
    .filter(e => e.actor === 'human' && e.body.event !== 'handoff').map(e => [e.body.event, e.body.text, e.body.stage ?? null]));

  // #acceptance-stage-tag: a comment saved on the page of a lane at Spec review shows the tag Spec review on its bubble,
  // beside its author; the comment carries it (review-state #model-fields-stage). #stage-tag-fixed: an edit carries none of
  // its own, and its message keeps its stage.
  page = await open('qa-17');
  await page.click('#hx-mode');
  await page.click('[data-anchor="filter-team"]');
  await page.fill('.hx-compose textarea', 'List the teams alphabetically?');
  await page.click('.hx-compose [data-act=save]');
  await page.waitForSelector('#hx-threads > .hx-entry');
  const me = await page.evaluate(() => JSON.parse(localStorage.getItem('spec-chat:reviewer')).author);
  assert.deepEqual(await heads(page), [['List the teams alphabetically?', me, 'Spec review']]);
  assert.deepEqual(await listed(() => savedEvents(page), 1, 'the comment'), [['comment', 'List the teams alphabetically?', 'Spec review']]);
  // review-state #model-fields-stage: the comment records the lane it took its stage from.
  assert.equal(await page.evaluate(async () => (await (await fetch('/api/events?dir=' + encodeURIComponent(location.pathname.slice(1) + '.review'))).json())
    .find(e => e.body.event === 'comment').body.lane), 'qa-17');
  await page.click('#hx-threads .hx-bubble');
  await page.click('.hx-focused [data-act=edit]');
  await page.fill('.hx-focused textarea', 'List the teams by name?');
  await page.click('.hx-focused [data-act=save]');
  await page.waitForFunction(() => document.querySelector('.hx-focused .hx-edited'));
  assert.deepEqual(await heads(page, '.hx-focused'), [['List the teams by name?', me, 'Spec review']], 'the edited message keeps its stage');
  assert.deepEqual(await listed(() => savedEvents(page), 2, 'the edit'), [['comment', 'List the teams alphabetically?', 'Spec review'], ['edit', 'List the teams by name?', null]]);
  await page.close();

  // #acceptance-stage-kept: a comment written at Spec review still shows Spec review with its lane now at Code review; a
  // comment written now shows Code review (#stage-tag-fixed), on a desktop, and in the phone's sheet.
  page = await open('qa-18');
  await openPanel(page);
  assert.deepEqual(await heads(page), [['Name the teams the filter lists.', 'Mango', 'Spec review']]);
  await page.click('#hx-mode');
  await page.click('[data-anchor="context"]');
  await page.fill('.hx-compose textarea', 'Say which week the report shows.');
  await page.click('.hx-compose [data-act=save]');
  await page.waitForFunction(() => document.querySelectorAll('#hx-threads > .hx-entry').length === 2);
  assert.deepEqual((await heads(page)).map(([, , stage]) => stage), ['Spec review', 'Code review'], 'a later stage shows only on later messages');
  await page.close();
  page = await open('qa-18', { device: PHONE });
  await page.tap('.hx-dock-thread[title^="#filter-team"]'); // the thread dock, over the block's pin at this width
  await page.waitForSelector('body > .hx-focused.hx-sheet');
  assert.deepEqual(await heads(page, '.hx-sheet'), [['Name the teams the filter lists.', 'Mango', 'Spec review']], 'on a phone, the tag beside its author');
  await page.close();

  // #acceptance-stage-reply: the agent's reply written at Code review shows the tag Code review, its lane now at Merge ask.
  page = await open('qa-19');
  await openPanel(page);
  await page.click('.hx-replies');
  await page.waitForFunction(() => document.querySelectorAll('#hx-threads .hx-bubble').length === 2);
  assert.deepEqual(await heads(page), [['Name the teams the filter lists.', 'Mango', 'Spec review'],
    ['Done: the filter lists every team by name.', 'Agent', 'Code review']]);
  await page.close();

  // #acceptance-stage-lane, -none, comment-layer #anchor-lane: an item in a scope that holds no spec; its anchor names a
  // lane the box names at QA, or none. The comment saved through its control shows the tag QA, or no tag, and carries it.
  for (const [collection, scope, stage] of [['layer-stage-lane', 'stage-lane-evidence', 'QA'], ['layer-stage-none', 'stage-none-evidence', null]]) {
    page = await open(collection, { page: 'docs/pane.html' });
    await page.click('.hx-layer-control[aria-label="Comment on Weekly report QA run"]');
    await page.fill('.hx-layer-box .hx-composer textarea', 'Which criteria did this run skip?');
    await page.click('.hx-layer-box .hx-composer [data-act=save]');
    await page.waitForSelector('.hx-layer-box .hx-threads > .hx-entry');
    const author = await page.evaluate(() => JSON.parse(localStorage.getItem('spec-chat:reviewer')).author);
    assert.deepEqual(await heads(page, 'layer'), [['Which criteria did this run skip?', author, stage]], collection);
    const stored = await listed(() => page.evaluate(async scope => (await (await fetch('/api/events?scope=' + scope + '&subject=qa-run:qa:9')).json()).events
      .map(e => e.body), scope), 1, collection + ': the comment');
    assert.equal(stored.length, 1, collection + ': one comment stored');
    const [hosted] = stored;
    assert.equal(hosted.stage ?? null, stage, collection + ': the comment carries its lane\'s stage');
    assert.equal(hosted.lane ?? null, stage ? 'qa-20' : null, collection + ': and records the lane it took it from');
    await page.close();
  }

  // #acceptance-stage-reply-lane, #stage-tag-source: the item's thread, in a scope that holds no spec, which the agent
  // answered while its anchor's lane was at QA: the agent's reply shows the tag QA. A reply takes its thread's lane, the
  // one its comment recorded: with the anchor naming no lane any more, a reply saved there still carries QA, its page
  // asking that lane's stage for its thread.
  page = await open('layer-stage-reply-lane', { page: 'docs/pane.html' });
  await page.click('.hx-layer-control[aria-label="Comment on Weekly report QA run"]');
  await page.waitForSelector('.hx-layer-box .hx-replies');
  await page.click('.hx-layer-box .hx-replies');
  await page.waitForFunction(() => document.querySelector('[data-spec-chat=comment-layer]').shadowRoot.querySelectorAll('.hx-layer-box .hx-bubble').length === 2);
  assert.deepEqual(await heads(page, 'layer'), [['Which criteria did this run skip?', 'Mango', 'QA'], ['None: it ran all three.', 'Agent', 'QA']]);
  const lanesAsked = [];
  page.on('request', r => { const at = new URL(r.url()); if (at.pathname === '/api/events' && r.method() === 'GET') lanesAsked.push(at.searchParams.getAll('lane')); });
  await page.evaluate(() => document.querySelector('[data-comment-subject="qa-run:qa:9"]').removeAttribute('data-comment-lane'));
  await until(() => lanesAsked.length > 1, 'live reads after the anchor dropped its lane', 8000);
  assert.deepEqual(lanesAsked.at(-1), ['qa-20'], 'its read asks its thread\'s lane');
  await page.click('.hx-layer-box .hx-entry .hx-bubble');
  await page.waitForSelector('.hx-layer-box .hx-focused textarea');
  await page.fill('.hx-layer-box .hx-focused textarea', 'Thanks: keep it that way.');
  await page.click('.hx-layer-box .hx-focused [data-act=save]');
  const replies = () => page.evaluate(async () => (await (await fetch('/api/events?scope=stage-reply-lane-evidence&subject=qa-run:qa:9')).json()).events
    .filter(e => e.actor === 'human' && e.body.event === 'reply').map(e => [e.body.text, e.body.stage ?? null, e.body.lane ?? null]));
  assert.deepEqual(await listed(replies, 1, 'the reply'), [['Thanks: keep it that way.', 'QA', null]], 'the reply carries its thread\'s lane\'s stage');
  await page.close();

  assert.deepEqual(errors, [], 'no page errors');
} finally {
  for (const context of contexts) await context.close().catch(() => {});
  await browser.close();
  try { process.kill(-server.pid); } catch {}
  rmSync(qaRoot, { recursive: true, force: true });
}
console.log('runtime conversation tests passed');
