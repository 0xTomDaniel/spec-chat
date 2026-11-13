import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { cpSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

// The spec page over its mockup set (mockups.spec #chip, #accept; review-state #live-accept), on a fixture mockup set
// served by the real review-serve.py, in headless Chromium, on a desktop and a phone:
// #acceptance-chip: an illustrated criterion shows one See mockup chip naming the screen, the other none; the chip
// takes the pill radius and link color and covers no text of its block (#chip-look).
// #acceptance-chip-open: pressing the chip opens the mockup page with the illustrating part in view and ringed.
// #acceptance-accept-waits: spec threads resolved, no open TBD, one open mockup thread: no Accept spec, and the panel
// lists the screen with 1 open thread as a link to its page.
// #acceptance-accept-offered: once the mockup's thread is resolved, the panel offers Accept spec.
// #accept-set, #acceptance-accept-covers: Accept spec names each mockup of the set and the version it is served at,
// and the scan reports it. A spec with no set shows no chip, and its Accept spec names no mockup.
// #acceptance-question-accept: spec threads resolved, no open TBD, one open question on its mockup: no Accept spec, and
// the panel lists the screen with 1 open question as a link to its page.
// #acceptance-chip-step: a criterion a mockup's second step illustrates: its chip opens the page at that step.
// #acceptance-screen-index, -open, -drawn, -prototype, -phone, -phone-open, -none (#screen-index): under the header, the
// Screens index lists a drawn screen and a prototype screen by name, kind, viewports, and open counts, in the set's
// order, in the set's status marks and the chrome's look (#screen-index-look); a row opens its mockup page at the base
// the spec page compares, also in comment mode; a thread or a screen added changes it (#screen-index-live); an entry
// telling no route has no kind; the reader's place holds as it arrives; on a phone each row's meta sits under its name
// within the screen's width; a spec with no set shows none, its first section following its header.
// Needs PLAYWRIGHT_CORE=<path to playwright-core>.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
assert.equal(readFileSync(resolve(root, 'docs/specs/.viz/runtime.js'), 'utf8'), runtime, 'runtime copies are identical');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_CORE || 'playwright-core');

// The fixture: specs with one criterion a mockup part illustrates and one none does, each with its own set and spools.
const repo = mkdtempSync(join(tmpdir(), 'spec-mockups-'));
const specs = join(repo, 'docs/specs');
mkdirSync(specs, { recursive: true });
cpSync(resolve(root, 'docs/specs/.viz'), join(specs, '.viz'), { recursive: true });
cpSync(resolve(root, 'docs/specs/.style'), join(specs, '.style'), { recursive: true });
const spec = name => `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="./.style/spec.css">
<title>Comment layer</title>
<script defer src="./.viz/runtime.js"></script>
</head>
<body>
<article class="spec" data-spec-version="1" data-spec-contract="shaped-sections-v1">
<header data-anchor="header">
<h1 data-anchor="title">Comment layer</h1>
<nav data-anchor="breadcrumbs">docs / specs / ${name}.spec.html</nav>
</header>
<section data-spec-section="acceptance" data-anchor="acceptance">
<h2>Acceptance criteria</h2>
<p data-acceptance-criterion data-anchor="acceptance-thread">Given a reviewer on a host page with a handed-off comment, When the owner answers it, Then the comment's thread shows the owner's reply under the comment.</p>
<p data-acceptance-criterion data-anchor="acceptance-plain">Given a reviewer with no comment on a host page, When the reviewer opens the host page, Then no pin shows.</p>
<p>Read the <a href="#host-layout">host page rules</a> first.</p>
</section>
<section data-anchor="host-layout">
<h2>Host page</h2>
<p data-anchor="host-layout-rule">The host page keeps its own look; the layer draws only its controls.</p>
</section>
<section data-anchor="host-totals">
<h2>Totals</h2>
<table><tr data-anchor="host-totals-open"><td>Open orders</td><td>Above the fold</td></tr></table>
</section>
</article>
</body>
</html>
`;
const mockup = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Host page - mockup</title>
<style>
  [data-mockup-frame] { --ui-ink: #333333; --ui-surface: #ffffff; --ui-border: #dfdfdf; }
  [data-mockup-frame="desktop"] { width: 1440px; }
  [data-mockup-frame="phone"] { width: 390px; }
  .rows { height: 1400px; }
  .card { border: 1px solid var(--ui-border); background: var(--ui-surface); color: var(--ui-ink); padding: 12px; }
</style>
<script defer src="../../.viz/runtime.js"></script>
</head>
<body>
<section data-mockup-frame="desktop" data-anchor="desktop" data-illustrates="host-layout">
  <div class="rows" data-anchor="desktop-rows" data-illustrates="host-totals-open">Orders: 1042 open, 87 shipped today.</div>
  <div class="card" data-anchor="desktop-thread" data-illustrates="acceptance-thread">Ada Lovelace: Move the total above the fold.</div>
</section>
<section data-mockup-frame="phone" data-anchor="phone" data-illustrates="host-layout">
  <div class="card" data-anchor="phone-thread" data-illustrates="acceptance-thread">Ada Lovelace: Move the total above the fold.</div>
</section>
</body>
</html>
`;
// asks: the mockup's walkthrough, its second step illustrating acceptance-plain, and one open question at that step
// (mockups #markup-steps, #markup-questions).
const asking = mockup.replace('</body>', `<ol data-mockup-steps>
<li data-step="orders">Every open order</li>
<li data-step="orders-shipped" data-step-by="desktop-rows" data-illustrates="acceptance-plain">Today's shipped orders</li>
</ol>
<aside data-mockup-questions>
<p data-anchor="question-fold" data-spec-tbd data-about="desktop-thread phone-thread" data-step="orders-shipped">Keep the total above the fold?
<button type="button" data-spec-choice="Keep it">Keep it (recommended)</button></p>
</aside>
</body>`);
// screens: a drawn screen and a prototype one (mockups #prototype-file), its route and one open question; the spec long
// enough to read far below its header.
const prototype = `<!doctype html><html><head><meta charset="utf-8"><title>Weekly report - mockup</title>
<meta name="mockup-prototype" content="/reports/weekly"><script defer src="../../.viz/runtime.js"></script></head><body>
<ul data-mockup-regions><li data-anchor="totals" data-select="main .totals">Weekly totals</li></ul>
<ol data-mockup-steps><li data-step="totals">Every team's total</li></ol>
<aside data-mockup-questions><p data-anchor="question-trend" data-spec-tbd data-about="totals" data-step="totals">Trend line?
<button type="button" data-spec-choice="No">No</button></p></aside>
</body></html>
`;
// screens' screens illustrate nothing, so no chip moves the page above the reader (below).
const unillustrated = mockup.replace(/ data-illustrates="[^"]*"/g, '');
const SETS = ['shown', 'waits', 'phone', 'keys', 'asks', 'screens'];
for (const name of [...SETS, 'plain']) writeFileSync(join(specs, name + '.spec.html'), name === 'screens'
  ? spec(name).replace('<section data-anchor="host-totals">', '<section data-anchor="host-filler"><h2>Filler</h2>'
    + Array.from({ length: 40 }, (_, i) => `<p data-anchor="filler-${i}">Filler paragraph ${i}.</p>`).join('\n') + '</section>\n<section data-anchor="host-totals">')
  : spec(name));
for (const name of SETS) {
  mkdirSync(join(specs, 'mockups', name), { recursive: true });
  writeFileSync(join(specs, 'mockups', name, '01-host-page.mockup.html'), name === 'asks' ? asking : name === 'screens' ? unillustrated : mockup);
}
writeFileSync(join(specs, 'mockups', 'screens', '02-weekly-report.mockup.html'), prototype);
const git = (...args) => execFileSync('git', ['-C', repo, ...args], { env: { ...process.env, GIT_CONFIG_GLOBAL: '/dev/null', GIT_AUTHOR_NAME: 't', GIT_AUTHOR_EMAIL: 't@x', GIT_COMMITTER_NAME: 't', GIT_COMMITTER_EMAIL: 't@x' } });
git('init', '-q', '-b', 'main');
git('add', '-A');
git('commit', '-qm', 'fixture');

// Threads written into a spool as the page and emit-reply.sh write them.
let stamp = BigInt(Date.now() - 60000) * 1000000n;
const write = (review, actor, body) => {
  stamp += 1000n;
  mkdirSync(join(review, actor), { recursive: true });
  writeFileSync(join(review, actor, `${stamp}-${body.event}-${body.id}.json`),
    JSON.stringify({ ...body, actor, createdAt: new Date().toISOString(), schemaVersion: 1 }));
};
const openThread = (review, id, anchorId) => {
  write(review, 'human', { id, event: 'comment', anchorId, target: null, quote: null, text: 'Show the total in the card.', browser: 'b-fixture', author: 'Mango' });
  write(review, 'human', { id: 'h-' + id, event: 'handoff', events: [id], anchorId: '', target: null, quote: null, text: 'batch from http', browser: 'b-fixture', author: 'Mango' });
};
const resolveThread = (review, id, anchorId) => {
  write(review, 'agent', { id: 'r-' + id, event: 'reply', respondsTo: id, anchorId, target: null, text: 'Done.', status: 'acknowledged' });
  write(review, 'human', { id: 's-' + id, event: 'status', respondsTo: id, status: 'resolved', browser: 'b-fixture', author: 'Mango' });
};
const specReview = name => join(specs, name + '.spec.html.review');
const mockupReview = name => join(specs, 'mockups', name, '01-host-page.mockup.html.review');
const handoffs = name => {
  const dir = join(specReview(name), 'human');
  let names = [];
  try { names = readdirSync(dir); } catch { /* no spool yet */ }
  return names.filter(n => n.endsWith('.json')).sort().map(n => ({ name: n, body: JSON.parse(readFileSync(join(dir, n), 'utf8')) }))
    .filter(e => e.body.event === 'handoff');
};
// waits: a resolved thread on the spec and an open one on its mockup. phone: an open one on its mockup.
openThread(specReview('waits'), 'u-spec', 'acceptance-plain');
resolveThread(specReview('waits'), 'u-spec', 'acceptance-plain');
openThread(mockupReview('waits'), 'u-mock', 'desktop-thread');
openThread(mockupReview('phone'), 'u-mock', 'phone-thread');
// screens: one open thread on the prototype screen, which holds an open question too; none on the drawn one.
const weeklyReview = join(specs, 'mockups', 'screens', '02-weekly-report.mockup.html.review');
openThread(weeklyReview, 'u-weekly', 'totals');

const port = await new Promise(res => { const s = createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); });
const server = spawn('python3', [resolve(root, 'skill/review-spec/assets/review-serve.py'), join(repo, 'docs'), String(port)], {
  stdio: 'ignore', env: { ...process.env, HOME: join(repo, '.home'), XDG_STATE_HOME: join(repo, '.state'), XDG_CONFIG_HOME: join(repo, '.config'), HERDR_SOCKET_PATH: '' },
});
// Stop the server on any exit, also when a step throws before the finally below.
process.on('exit', () => server.kill());
const service = `http://127.0.0.1:${port}`;
const url = name => `${service}/specs/${name}.spec.html`;
const mockupUrl = name => `${service}/specs/mockups/${name}/01-host-page.mockup.html`;
const sleep = ms => new Promise(r => setTimeout(r, ms));
for (let i = 0; ; i++) {
  try { if ((await fetch(url('shown'))).ok) break; } catch {}
  if (i > 100) throw new Error('review-serve did not start');
  await sleep(100);
}

const browser = await chromium.launch();
const errors = [];
const desktop = await browser.newContext({ viewport: { width: 1280, height: 800 } });
const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
const open = async (context, name) => {
  const page = await context.newPage();
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto(url(name));
  await page.waitForFunction(() => /connected/.test(document.getElementById('hx-status')?.textContent || ''));
  return page;
};
const handoffText = (page, text) => page.waitForFunction(text => document.getElementById('hx-handoff')?.textContent === text, text, { timeout: 6000 });
const openList = page => page.evaluate(() => [...document.querySelectorAll('.hx-panel .hx-mockup-open a')].map(a => {
  const box = a.getBoundingClientRect();
  return { text: a.textContent, href: a.href, shown: a.checkVisibility() && box.left >= 0 && box.right <= innerWidth };
}));
const comments = name => {
  const dir = join(specReview(name), 'human');
  let names = [];
  try { names = readdirSync(dir); } catch { /* no spool yet */ }
  return names.filter(n => n.endsWith('.json')).sort().map(n => JSON.parse(readFileSync(join(dir, n), 'utf8'))).filter(e => e.event === 'comment');
};
const draft = async (page, text, count) => {
  await page.fill('.hx-composer textarea', text);
  await page.click('.hx-composer [data-act=save]');
  for (const end = Date.now() + 5000; comments('keys').length < count && Date.now() < end;) await sleep(100);
  return comments('keys').at(-1);
};
const chips = (page, anchor) => page.evaluate(anchor => [...document.querySelectorAll(`[data-anchor="${anchor}"] .hx-mockup-chip`)].map(c => c.textContent), anchor);

try {
  // #acceptance-chip on a desktop: the illustrated criterion shows one chip naming the screen, the other none.
  const page = await open(desktop, 'shown');
  await page.waitForSelector('[data-anchor="acceptance-thread"] .hx-mockup-chip');
  assert.deepEqual(await chips(page, 'acceptance-thread'), ['See mockup · Host page'], 'the illustrated criterion shows one chip naming the screen');
  assert.deepEqual(await chips(page, 'acceptance-plain'), [], 'a criterion no mockup illustrates shows no chip');
  assert.deepEqual(await chips(page, 'host-layout'), ['See mockup · Host page'], 'a block a whole frame illustrates shows the chip once, however many frames name it');
  // #chip-look: the chrome's pill radius and link color, and it covers none of its block's text.
  const look = await page.evaluate(() => {
    const chip = document.querySelector('[data-anchor="acceptance-thread"] .hx-mockup-chip');
    const probe = document.body.appendChild(document.createElement('span'));
    probe.style.color = 'var(--ui-link)';
    const link = getComputedStyle(probe).color;
    probe.remove();
    const box = chip.getBoundingClientRect();
    const block = chip.closest('[data-anchor]');
    const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT, { acceptNode: n => n.parentElement.closest('.hx-mockup-chip') || !n.nodeValue.trim() ? NodeFilter.FILTER_SKIP : NodeFilter.FILTER_ACCEPT });
    let covered = 0;
    for (let node; (node = walker.nextNode());) {
      const range = document.createRange();
      range.selectNodeContents(node);
      for (const r of range.getClientRects()) if (r.left < box.right - 0.5 && box.left < r.right - 0.5 && r.top < box.bottom - 0.5 && box.top < r.bottom - 0.5) covered++;
    }
    const style = getComputedStyle(chip);
    return { radius: style.borderTopLeftRadius, color: style.color, link, covered, after: block.lastChild === chip };
  });
  assert.equal(look.radius, '999px', 'the chip takes the pill radius');
  assert.equal(look.color, look.link, 'the chip takes the link color');
  assert.equal(look.covered, 0, 'the chip covers none of its block\'s text');
  assert.ok(look.after, 'the chip sits after its block\'s text');
  // A set with no open thread and a spec with none: Accept spec is offered.
  await handoffText(page, 'Accept spec');

  // #acceptance-chip-open: the chip opens the mockup page at the illustrating part, in view and ringed.
  await page.click('[data-anchor="acceptance-thread"] .hx-mockup-chip');
  await page.waitForURL(mockupUrl('shown') + '#desktop-thread');
  await page.waitForFunction(() => {
    const part = [...document.querySelectorAll('[data-anchor]')].find(el => el.dataset.anchor === 'desktop-thread');
    const box = part && part.getBoundingClientRect();
    const style = part && getComputedStyle(part);
    const ringed = style && (style.outlineStyle !== 'none' || style.boxShadow !== 'none');
    return box && box.bottom > 0 && box.top < innerHeight && ringed;
  }, null, { timeout: 6000 });
  await page.close();

  // #acceptance-accept-waits on a desktop: the spec's thread resolved, its mockup's open: no Accept spec, and the panel
  // lists the screen with its open count as a link to its page.
  const waits = await open(desktop, 'waits');
  await waits.click('#hx-dock-open');
  await waits.waitForSelector('.hx-panel .hx-mockup-open a');
  assert.deepEqual(await openList(waits), [{ text: 'Host page · 1 open thread', href: mockupUrl('waits'), shown: true }], 'the panel lists the mockup with its open thread as a link to its page');
  assert.equal(await waits.textContent('#hx-handoff'), 'Hand off to agent →', 'no Accept spec while a mockup thread is open');
  assert.equal(await waits.isDisabled('#hx-handoff'), true, 'nothing to hand off');
  // #acceptance-accept-offered: the mockup's thread resolved, Accept spec is offered and the list is gone.
  resolveThread(mockupReview('waits'), 'u-mock', 'desktop-thread');
  await handoffText(waits, 'Accept spec');
  assert.deepEqual(await openList(waits), [], 'no mockup holds an open thread');
  // #accept-set: Accept spec names each mockup of the set and the version it is served at.
  const version = (await fetch(mockupUrl('waits'))).headers.get('etag').replace(/^W\//, '').replace(/"/g, '');
  const before = handoffs('waits').length;
  await waits.click('#hx-handoff');
  for (const end = Date.now() + 5000; handoffs('waits').length === before && Date.now() < end;) await sleep(100);
  const accept = handoffs('waits').at(-1);
  assert.equal(handoffs('waits').length, before + 1, 'Accept spec writes one hand-off');
  assert.equal(accept.body.events, undefined, 'Accept spec lists no drafts');
  assert.deepEqual(accept.body.mockups, [{ path: 'mockups/waits/01-host-page.mockup.html', version }], 'Accept spec names the mockup and the version it is served at');
  // #acceptance-accept-covers: the scan reports the hand-off naming the mockup with that version.
  const scanned = execFileSync('python3', [resolve(root, 'skill/review-spec/assets/spool.py'), 'accept', specReview('waits')], { input: accept.name + '\n', encoding: 'utf8' });
  assert.equal(scanned, ['accept', accept.name, join(specs, 'mockups/waits/01-host-page.mockup.html'), version].join('\t') + '\n', 'the scan reports the accepted mockup and its version');
  await waits.close();

  // On a phone: the chip fits the screen, the open mockup shows in the panel, and Accept spec waits for it.
  const small = await open(phone, 'phone');
  await small.waitForSelector('[data-anchor="acceptance-thread"] .hx-mockup-chip');
  assert.deepEqual(await chips(small, 'acceptance-thread'), ['See mockup · Host page'], 'the chip shows on a phone');
  const fit = await small.evaluate(() => {
    const box = document.querySelector('[data-anchor="acceptance-thread"] .hx-mockup-chip').getBoundingClientRect();
    return { inside: box.left >= 0 && box.right <= innerWidth, scroll: document.documentElement.scrollWidth <= document.documentElement.clientWidth };
  });
  assert.deepEqual(fit, { inside: true, scroll: true }, 'the chip sits within the screen\'s width with no horizontal scrolling');
  await small.waitForFunction(() => document.querySelector('.hx-toolbar #hx-handoff')?.textContent === 'Hand off to agent →');
  await small.tap('#hx-dock-open');
  await small.waitForSelector('.hx-panel .hx-mockup-open a');
  assert.deepEqual(await openList(small), [{ text: 'Host page · 1 open thread', href: mockupUrl('phone'), shown: true }], 'the phone panel lists the mockup as a link within the screen');
  resolveThread(mockupReview('phone'), 'u-mock', 'phone-thread');
  await small.waitForFunction(() => document.querySelector('.hx-toolbar #hx-handoff')?.textContent === 'Accept spec', null, { timeout: 6000 });
  await small.close();

  // A chip is no part of its block's text: a link after it keeps the key the source counts (place.py), and a selection
  // over its block quotes the block's own text, so the service finds the quote.
  const keys = await open(desktop, 'keys');
  await keys.waitForSelector('[data-anchor="acceptance-thread"] .hx-mockup-chip');
  await keys.click('#hx-mode');
  await keys.click('[data-anchor="acceptance"] > p:not([data-anchor]) a');
  const link = await draft(keys, 'Name the rules', 1);
  assert.deepEqual([link.anchorId, link.target], ['acceptance', { type: 'element', key: 'a[1]' }], 'the link keeps its key with a chip before it');
  await keys.click('#hx-mode');
  await keys.evaluate(() => {
    const block = document.querySelector('[data-anchor="acceptance-thread"]');
    const range = document.createRange();
    range.selectNodeContents(block);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    block.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
  });
  const quoted = await draft(keys, 'Say who answers', 2);
  assert.equal(quoted.quote, "Given a reviewer on a host page with a handed-off comment, When the owner answers it, Then the comment's thread shows the owner's reply under the comment.", 'the quote holds no chip text');
  // An element comment on a cell that ends in its row's chip quotes the cell's own text, as the one shared resolver reads
  // the source (review-state #model-place).
  await keys.waitForSelector('[data-anchor="host-totals-open"] td:last-child > .hx-mockup-chip');
  await keys.click('#hx-mode');
  await keys.evaluate(() => document.querySelector('[data-anchor="host-totals-open"] td:last-child')
    .dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true })));
  const cell = await draft(keys, 'Say which fold', 3);
  assert.deepEqual([cell.anchorId, cell.target, cell.quote], ['host-totals-open', { type: 'element', key: 'td[2]' }, 'Above the fold'],
    'the cell keeps its key, and its quote holds no chip text');
  const answer = await (await fetch(`${service}/api/events?dir=/specs/keys.spec.html.review`)).json();
  const states = Object.fromEntries((Array.isArray(answer) ? answer : answer.events).filter(e => e.body.event === 'comment').map(e => [e.body.id, e.place && e.place.state]));
  assert.deepEqual([states[link.id], states[quoted.id], states[cell.id]], ['kept', 'kept', 'kept'], 'the service places every mark');
  await keys.close();

  // #acceptance-question-accept: no thread anywhere, no open TBD in the spec, one open question on its mockup: no Accept
  // spec, and the panel lists the screen with 1 open question as a link to its page.
  const asks = await open(desktop, 'asks');
  await asks.click('#hx-dock-open');
  await asks.waitForSelector('.hx-panel .hx-mockup-open a');
  assert.deepEqual(await openList(asks), [{ text: 'Host page · 1 open question', href: mockupUrl('asks'), shown: true }], 'the panel lists the mockup with its open question');
  assert.equal(await asks.textContent('#hx-handoff'), 'Hand off to agent →', 'no Accept spec while a mockup question is open');
  assert.equal(await asks.isDisabled('#hx-handoff'), true);
  // #acceptance-chip-step: the chip of a criterion the second step illustrates opens the mockup page at that step.
  assert.deepEqual(await chips(asks, 'acceptance-plain'), ['See mockup · Host page'], 'a step illustrating a criterion gives it a chip');
  await asks.click('#hx-panel-toggle');
  await asks.click('[data-anchor="acceptance-plain"] .hx-mockup-chip');
  await asks.waitForURL(mockupUrl('asks') + '?step=orders-shipped');
  await asks.waitForFunction(() => /connected/.test(document.getElementById('hx-status')?.textContent || ''));
  assert.deepEqual(await asks.evaluate(() => ({
    at: [...document.querySelectorAll('[data-mockup-frame]')].map(f => f.dataset.at),
    pressed: [...document.querySelectorAll('#hx-steps .hx-step[aria-pressed="true"]')].map(b => b.dataset.step),
  })), { at: ['orders orders-shipped', 'orders orders-shipped'], pressed: ['orders-shipped'] }, 'both frames at the second step, pressed in the step bar');
  await asks.close();

  // #acceptance-screen-index, -open on a desktop, at the base the address names: under the header, before the first
  // section, Screens 2, then each screen of the set in its order.
  const screensUrl = url('screens') + '?base=main';
  const screens = await desktop.newPage();
  screens.on('pageerror', e => errors.push(String(e)));
  await screens.goto(screensUrl);
  await screens.waitForSelector('#hx-screens .hx-screen');
  const index = page => page.evaluate(() => {
    const nav = document.getElementById('hx-screens');
    const box = el => el.getBoundingClientRect();
    return {
      after: nav.previousElementSibling?.tagName, before: nav.nextElementSibling?.dataset.specSection, label: nav.getAttribute('aria-label'),
      head: nav.querySelector('.hx-screens-head').textContent,
      headGap: box(nav.querySelector('ul')).top - box(nav.querySelector('.hx-screens-head')).bottom,
      rows: [...nav.querySelectorAll('a.hx-screen')].map(a => ({
        name: a.querySelector('.hx-screen-name').textContent, kind: a.querySelector('.hx-screen-kind')?.textContent ?? null,
        views: a.querySelector('.hx-screen-views').textContent, open: a.querySelector('.hx-screen-open').textContent,
        mark: a.firstElementChild.className, markColor: getComputedStyle(a.querySelector('.hx-screen-mark circle')).stroke,
        last: a.lastElementChild.className, href: a.href, label: a.getAttribute('aria-label'),
        height: box(a).height, inside: box(a).left >= 0 && box(a).right <= innerWidth,
        nameBottom: box(a.querySelector('.hx-screen-name')).bottom, metaTop: box(a.querySelector('.hx-screen-meta')).top,
        openTop: box(a.querySelector('.hx-screen-open')).top, nameTop: box(a.querySelector('.hx-screen-name')).top,
      })),
      scroll: document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    };
  });
  const shownIndex = await index(screens);
  assert.deepEqual([shownIndex.after, shownIndex.before, shownIndex.label, shownIndex.head], ['HEADER', 'acceptance', 'Screens', 'Screens 2'], 'the index sits under the header, before the first section');
  assert.equal(shownIndex.headGap, 0, 'the list starts right under its heading, as the accepted frame draws it');
  assert.deepEqual(shownIndex.rows.map(r => [r.name, r.kind, r.views, r.open]), [
    ['Host page', 'Drawn mockup', 'Desktop and phone', 'No open threads'],
    ['Weekly report', 'Prototype/reports/weekly', 'Desktop and phone', '1 open thread · 1 open question'],
  ], 'each screen by name, kind, viewports, and open counts, in the set\'s order');
  assert.deepEqual(shownIndex.rows.map(r => r.label), ['Host page, Drawn mockup, Desktop and phone, No open threads',
    'Weekly report, Prototype /reports/weekly, Desktop and phone, 1 open thread · 1 open question'], 'each row names what it shows');
  assert.deepEqual(shownIndex.rows.map(r => r.href), [`${service}/specs/mockups/screens/01-host-page.mockup.html?base=main`,
    `${service}/specs/mockups/screens/02-weekly-report.mockup.html?base=main`], 'each row links its mockup page at the base the page compares');
  assert.ok(shownIndex.rows.every(r => r.mark === 'hx-screen-mark' && r.last === 'hx-screen-go' && r.height >= 44 && Math.abs(r.metaTop - r.nameTop) < 8 && Math.abs(r.openTop - r.nameTop) < 8),
    'on a desktop each row is one line: mark first, name, meta, open counts, chevron last');
  // #screen-index-look, spec-page-design #surface-screen-index: the set's tokens and status marks.
  const tokens = await screens.evaluate(() => {
    const probe = document.body.appendChild(document.createElement('span'));
    const read = v => { probe.style.color = `var(${v})`; return getComputedStyle(probe).color; };
    const out = Object.fromEntries(['--ui-page', '--ui-ink', '--ui-muted', '--ui-border', '--ui-attention'].map(v => [v, read(v)]));
    probe.remove();
    const nav = document.getElementById('hx-screens');
    const [drawn, weekly] = nav.querySelectorAll('a.hx-screen');
    const style = el => getComputedStyle(el);
    return { out, list: style(nav.querySelector('ul')).borderTopColor, radius: style(nav.querySelector('ul')).borderTopLeftRadius,
      hairline: style(nav.querySelectorAll('li')[1]).borderTopColor, page: style(nav).backgroundColor,
      name: [style(drawn.querySelector('.hx-screen-name')).color, style(drawn.querySelector('.hx-screen-name')).fontSize, style(drawn.querySelector('.hx-screen-name')).fontWeight],
      meta: [style(drawn.querySelector('.hx-screen-meta')).color, style(drawn.querySelector('.hx-screen-meta')).fontSize],
      quiet: style(drawn.querySelector('.hx-screen-open')).color, waiting: style(weekly.querySelector('.hx-screen-open')).color, underline: style(drawn).textDecorationLine };
  });
  assert.deepEqual(tokens, { out: tokens.out, list: tokens.out['--ui-border'], radius: '8px', hairline: tokens.out['--ui-border'], page: tokens.out['--ui-page'],
    name: [tokens.out['--ui-ink'], '14px', '500'], meta: [tokens.out['--ui-muted'], '12px'], quiet: tokens.out['--ui-muted'],
    waiting: tokens.out['--ui-attention'], underline: 'none' }, 'the chrome\'s page color, hairlines, text steps, and muted and attention meta');
  assert.deepEqual(shownIndex.rows.map(r => r.markColor), [tokens.out['--ui-muted'], tokens.out['--ui-attention']], 'the neutral mark, then the attention mark');
  // #screen-index-live: a thread opened on the drawn screen and a screen added to the set change the index.
  openThread(mockupReview('screens'), 'u-host', 'desktop-thread');
  writeFileSync(join(specs, 'mockups', 'screens', '03-settings.mockup.html'), unillustrated.replace('Host page - mockup', 'Settings - mockup'));
  await screens.waitForFunction(() => document.querySelectorAll('#hx-screens a.hx-screen').length === 3
    && document.querySelector('#hx-screens a.hx-screen .hx-screen-open').textContent === '1 open thread', null, { timeout: 8000 });
  assert.equal(await screens.textContent('#hx-screens .hx-screens-head'), 'Screens 3', 'the count follows the set');
  // The reader's place holds as the index grows above it, in a browser with no scroll anchoring of its own, as Safari's.
  await screens.evaluate(() => { document.documentElement.style.overflowAnchor = 'none'; document.querySelector('[data-anchor="filler-30"]').scrollIntoView(); });
  await sleep(300);
  const reading = () => screens.evaluate(() => document.querySelector('[data-anchor="filler-30"]').getBoundingClientRect().top);
  const placeBefore = await reading();
  writeFileSync(join(specs, 'mockups', 'screens', '04-empty-state.mockup.html'), unillustrated.replace('Host page - mockup', 'Empty state - mockup'));
  await screens.waitForFunction(() => document.querySelectorAll('#hx-screens a.hx-screen').length === 4, null, { timeout: 8000 });
  const placeAfter = await reading();
  assert.ok(Math.abs(placeAfter - placeBefore) <= 1, `the reader's place holds as the index grows (${placeBefore} then ${placeAfter})`);
  // #acceptance-screen-index-drawn: pressing anywhere on the drawn screen's row, also in comment mode, opens its page.
  await screens.evaluate(() => scrollTo(0, 0));
  await screens.click('#hx-mode');
  await screens.click('#hx-screens a.hx-screen .hx-screen-views');
  await screens.waitForURL(`${service}/specs/mockups/screens/01-host-page.mockup.html?base=main`);
  await screens.waitForFunction(() => document.querySelectorAll('[data-mockup-frame]').length === 2 && document.getElementById('hx-mode'));
  // #acceptance-screen-index-prototype: the prototype screen's row opens its mockup page, which frames its route; with
  // no prototype running here it reads so with the route (tests/runtime-prototype.mjs frames a running one).
  await screens.goto(screensUrl);
  await screens.click('#hx-screens li:nth-child(2) a.hx-screen');
  await screens.waitForURL(`${service}/specs/mockups/screens/02-weekly-report.mockup.html?base=main`);
  await screens.waitForFunction(() => document.getElementById('hx-mode') && document.querySelector('.hx-proto-down code')?.textContent === '/reports/weekly', null, { timeout: 6000 });
  // #screen-index-rule: an entry that tells no route, from a service of another version, has no kind.
  await screens.route('**/api/mockups?*', async route => {
    const answer = await (await route.fetch()).json();
    await route.fulfill({ json: { mockups: answer.mockups.map(({ route: _, ...m }) => m) } });
  });
  await screens.goto(screensUrl);
  await screens.waitForSelector('#hx-screens .hx-screen');
  assert.deepEqual((await index(screens)).rows.map(r => [r.name, r.kind, r.views]), [['Host page', null, 'Desktop and phone'],
    ['Weekly report', null, 'Desktop and phone'], ['Settings', null, 'Desktop and phone'], ['Empty state', null, 'Desktop and phone']], 'a row without a route names no kind');
  await screens.close();

  // #acceptance-screen-index-phone, -phone-open: each row's kind, viewports, and open counts under its name, within the
  // screen's width, every row at least 44px; a tap opens the prototype screen's page.
  const phoneScreens = await phone.newPage();
  phoneScreens.on('pageerror', e => errors.push(String(e)));
  await phoneScreens.goto(screensUrl);
  await phoneScreens.waitForSelector('#hx-screens .hx-screen');
  const narrow = await index(phoneScreens);
  assert.ok(narrow.scroll, 'no horizontal scrolling');
  assert.equal(narrow.headGap, 0, 'on a phone too the list starts right under its heading');
  assert.deepEqual(narrow.rows.map(r => r.name), ['Host page', 'Weekly report', 'Settings', 'Empty state']);
  for (const r of narrow.rows) {
    assert.ok(r.inside && r.height >= 44, `${r.name}: within the screen's width, at least a 44px target`);
    assert.ok(r.metaTop >= r.nameBottom - 1 && r.openTop > r.metaTop, `${r.name}: kind and viewports, then open counts, under its name`);
  }
  assert.equal(await phoneScreens.evaluate(() => getComputedStyle(document.querySelector('#hx-screens .hx-screen-kind'), '::after').content), '" ·"', 'kind and viewports read as one line');
  await phoneScreens.tap('#hx-screens li:nth-child(2) a.hx-screen');
  await phoneScreens.waitForURL(`${service}/specs/mockups/screens/02-weekly-report.mockup.html?base=main`);
  await phoneScreens.close();

  // A spec with no set: no chip, and Accept spec names no mockup.
  const plain = await open(desktop, 'plain');
  // #acceptance-screen-index-none: no Screens index, and the first section follows the header.
  assert.equal(await plain.locator('#hx-screens').count(), 0, 'a spec with no set shows no Screens index');
  assert.equal(await plain.evaluate(() => document.querySelector('article.spec > header').nextElementSibling.dataset.specSection), 'acceptance', 'its first section follows its header');
  await handoffText(plain, 'Accept spec');
  assert.equal(await plain.locator('.hx-mockup-chip').count(), 0, 'a spec with no set shows no chip');
  await plain.click('#hx-dock-open');
  await plain.click('#hx-handoff');
  for (const end = Date.now() + 5000; !handoffs('plain').length && Date.now() < end;) await sleep(100);
  const [plainAccept] = handoffs('plain');
  assert.equal(plainAccept.body.mockups, undefined, 'a spec with no set accepts with no mockups field');
  await plain.close();

  assert.deepEqual(errors, [], 'no page errors');
  console.log('runtime spec mockups tests passed');
} finally {
  await browser.close();
  server.kill();
}
