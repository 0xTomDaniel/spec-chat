import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

// The comment layer on a host page (comment-layer #load, #ui, #subjects, #proof-seam; #acceptance-host-*,
// #acceptance-one-thread-*, #acceptance-no-scope, #acceptance-no-service, #acceptance-private-other-origin): the QA
// fixture's comment-layer collections (tests/fixtures/qa reset.sh + panes.py) on the fixture service (serve.py), public
// through the registry's public base address while on loopback. Each host page (docs/pane.html) is served directly by
// the service, at another origin by the fixture's test host (host.py /page), and in a frame with no origin (host.py).
// Headless Chromium. Needs PLAYWRIGHT_CORE=<path to playwright-core>.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
assert.equal(readFileSync(resolve(root, 'docs/specs/.viz/runtime.js'), 'utf8'), runtime, 'runtime copies are identical');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_CORE || 'playwright-core');

const fixture = resolve(root, 'tests/fixtures/qa');
const qaRoot = mkdtempSync(join(tmpdir(), 'comment-layer-'));
const env = { ...process.env, QA_ROOT: qaRoot, QA_FIXTURE: fixture, QA_REPO_SPEC_CHAT: root };
execFileSync('sh', [join(fixture, 'reset.sh')], { env, stdio: 'inherit' });
const freePort = () => new Promise(res => { const s = createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); });
const [port, hostPort, privatePort, privateHostPort] = [await freePort(), await freePort(), await freePort(), await freePort()];
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
await ready(`${service}/host-draft/docs/pane.html`);
servers.push(start([join(fixture, 'host.py'), service, String(hostPort)]));
await ready(`${host}/`);

const REPORT = 'docs/specs/report.spec.html';
const CRITERION = 'Totals per week';
const TURN = 'Agent turn 42';
const MARKUP = '<img src=x onerror="window.__xss=1">'; // panes.py layer-markup
const pane = (collection, file = 'pane.html') => `${service}/${collection}/docs/${file}`;
const files = dir => { try { return readdirSync(dir).sort().map(n => JSON.parse(readFileSync(join(dir, n), 'utf8'))); } catch { return []; } };
const specEvents = collection => files(join(qaRoot, 'site', collection, REPORT + '.review/human'));
const hostedDir = collection => join(qaRoot, 'serve/comments', collection);
const hostedEvents = collection => files(join(hostedDir(collection), 'human'));
const browser = await chromium.launch();
const desktop = await browser.newContext({ viewport: { width: 1280, height: 800 } });
const errors = [];

const watch = page => { page.on('pageerror', e => errors.push(page.url() + ': ' + e)); return page; };
// The host page at its address; returns the page and the frame its document runs in.
const open = async (url, context = desktop) => {
  const page = watch(await context.newPage());
  await page.goto(url);
  const framed = new URL(url).origin === host && !new URL(url).pathname.startsWith('/page');
  const frame = framed ? await (await page.waitForSelector('iframe')).contentFrame() : page.mainFrame();
  await frame.waitForSelector('h1');
  return { page, frame };
};
const control = (frame, name) => frame.getByRole('button', { name: 'Comment on ' + name, exact: true });
const box = frame => frame.locator('.hx-layer-box');
const cards = frame => frame.evaluate(() => {
  const layer = document.querySelector('[data-spec-chat="comment-layer"]');
  return [...(layer ? layer.shadowRoot.querySelectorAll('.hx-layer-box .hx-thread') : [])].map(t => ({
    status: t.querySelector('.hx-pill')?.textContent || null,
    who: [...t.querySelectorAll('.hx-msg .hx-who')].map(m => m.textContent),
    texts: [...t.querySelectorAll('.hx-msg .hx-text')].map(m => m.textContent),
    read: [...t.querySelectorAll('.hx-msg')].map(m => m.innerText), // as drawn: author, then the text
    quotes: [...t.querySelectorAll('.hx-msg .hx-quote')].map(m => m.textContent),
    notice: t.querySelector('.hx-place-notice')?.textContent || null,
    kind: t.querySelector('.hx-host-kind')?.textContent || null,
    from: t.querySelector('.hx-host-from a')?.href || null,
  }));
});
const waitCards = (frame, test, timeout = 5000) => frame.waitForFunction(test, null, { timeout }).catch(async error => {
  throw new Error(error.message + '\ncards: ' + JSON.stringify(await cards(frame)));
});
const cardsMatch = expect => `(() => { const layer = document.querySelector('[data-spec-chat="comment-layer"]');
  const cards = [...(layer ? layer.shadowRoot.querySelectorAll('.hx-layer-box .hx-thread') : [])];
  return (${expect})(cards.map(t => ({ status: t.querySelector('.hx-pill')?.textContent, texts: [...t.querySelectorAll('.hx-msg .hx-text')].map(m => m.textContent) }))); })()`;
// Save a comment on an item: its control, an optional starter, the text, then Comment.
const comment = async (frame, name, text, starter = null) => {
  await control(frame, name).click();
  if (starter) await box(frame).locator(`[data-starter="${starter}"]`).click();
  await box(frame).locator('.hx-composer textarea').pressSequentially(text);
  await box(frame).locator('.hx-composer [data-act=save]').click();
  await waitCards(frame, cardsMatch(`cards => cards.some(c => c.status === 'draft')`));
};
// A message as drawn reads its author name, a space or a line, then its text, never the two run together.
const apart = (who, text) => new RegExp('^' + who + '\\s+' + text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i');
const pinOf = (frame, name) => frame.locator(`.hx-pin[aria-label*=": ${name}, "]`);
// The host adds an item for another agent turn: after the turn 42 item, or before it.
const addTurn = (frame, n, where = 'after') => frame.evaluate(([n, where]) => {
  const turn = document.querySelector('[data-comment-subject="agent-turn:qa:42"]');
  const added = turn.cloneNode(true);
  added.dataset.commentSubject = 'agent-turn:qa:' + n;
  added.dataset.commentLabel = 'Agent turn ' + n;
  turn[where](added);
}, [n, where]);
// The aria-label of what has focus inside the layer.
const focused = frame => frame.evaluate(() =>
  document.querySelector('[data-spec-chat="comment-layer"]').shadowRoot.activeElement?.getAttribute('aria-label') || null);
const noLayer = async (frame, page) => {
  await page.waitForTimeout(2500); // past a live read
  assert.equal(await frame.locator('[data-spec-chat="comment-layer"]').count(), 0, 'the layer draws nothing');
  assert.equal(await frame.locator('#hx-errors').count(), 0, 'no error shows');
  assert.equal(await frame.locator('.item').count(), 3, 'the page shows its items');
};

try {
  // #acceptance-host-starters, #acceptance-host-only-comments, #acceptance-host-draft, #acceptance-no-scope; served
  // directly by the service. #ui-style: the host's own look reaches neither the layer, nor the layer the host's items.
  let { page, frame } = await open(pane('host-draft'));
  await control(frame, CRITERION).waitFor();
  const before = await frame.evaluate(() => [...document.querySelectorAll('.item')].map(item => item.outerHTML));
  assert.equal(await control(frame, 'Agent turn 43').count(), 0, 'an item whose scope no row has gets no control');
  await control(frame, CRITERION).click();
  await box(frame).waitFor();
  assert.deepEqual(await box(frame).locator('[data-starter]').allTextContents(), ['Question', 'Change', 'Approve'], 'the three starters');
  assert.equal(await box(frame).locator('.hx-composer textarea').inputValue(), '', 'above an empty comment box');
  assert.ok(await box(frame).evaluate(b => {
    const row = b.querySelector('.hx-starters'), area = b.querySelector('.hx-composer textarea');
    return row.compareDocumentPosition(area) & Node.DOCUMENT_POSITION_FOLLOWING;
  }), 'starters sit above the comment box');
  assert.equal(await box(frame).locator('#hx-handoff').textContent(), 'Hand off to agent →', 'Hand off shows');
  const chrome = await frame.evaluate(() => {
    const layer = document.querySelector('[data-spec-chat="comment-layer"]').shadowRoot;
    const text = document.body.innerText + '\n' + [...layer.querySelectorAll('*')].map(el => el.textContent).join('\n');
    return { accept: /Accept spec/.test(text), jev: /\bJev\b/.test(text), focus: /Change base|focus on changes|Reading view/i.test(text),
      controls: document.querySelectorAll('.hx-jev-marker,.hx-range-bar,.hx-toolbar,.hx-thread-dock,.hx-panel,.hx-service-index-link').length
        + layer.querySelectorAll('.hx-jev-marker,.hx-range-bar,.hx-toolbar,.hx-thread-dock,.hx-panel,.hx-service-index-link').length };
  });
  assert.deepEqual(chrome, { accept: false, jev: false, focus: false, controls: 0 }, 'no Accept spec, Jev, evidence, or focus control');
  assert.notEqual(await box(frame).locator('#hx-handoff').evaluate(b => getComputedStyle(b).backgroundColor), 'rgb(122, 31, 31)', "the host's button look stays out");
  await box(frame).locator('[data-starter="Question"]').click();
  assert.equal(await box(frame).locator('.hx-composer textarea').inputValue(), 'Question ', 'the starter begins the draft');
  await box(frame).locator('.hx-composer textarea').pressSequentially('why per week and not per day?');
  await box(frame).locator('.hx-composer [data-act=save]').click();
  await waitCards(frame, cardsMatch(`cards => cards.length === 1 && cards[0].status === 'draft'`));
  assert.deepEqual((await cards(frame)).map(c => c.texts), [['Question why per week and not per day?']], 'the comment, beginning with Question');
  const [drawn] = await cards(frame);
  assert.match(drawn.read[0], apart(drawn.who[0], 'Question why per week and not per day?'), 'its author name stands apart from its text');
  await pinOf(frame, CRITERION).waitFor();
  assert.equal(await pinOf(frame, CRITERION).getAttribute('data-s'), 'draft', 'the item shows a pin');
  assert.deepEqual(await frame.evaluate(() => [...document.querySelectorAll('.item')].map(item => item.outerHTML)), before, "the host's items are unchanged");
  // #load-scan: a host that draws an item again keeps its thread; one it adds gets a control.
  await frame.evaluate(() => {
    const item = document.querySelector('.item');
    item.replaceWith(item.cloneNode(true));
    const added = document.body.appendChild(item.cloneNode(true));
    added.dataset.commentSubject = 'agent-turn:qa:44';
    added.dataset.commentLabel = 'Agent turn 44';
  });
  await control(frame, 'Agent turn 44').waitFor();
  await frame.waitForFunction(() => {
    const pin = document.querySelector('[data-spec-chat="comment-layer"]').shadowRoot.querySelector('.hx-pin');
    const item = document.querySelector('.item').getBoundingClientRect(), at = pin && pin.getBoundingClientRect();
    return at && at.top >= item.top && at.bottom <= item.bottom && at.right <= item.right;
  });
  for (let i = 0; i < 50 && !specEvents('host-draft').length; i++) await page.waitForTimeout(100);
  const saved = specEvents('host-draft').find(e => e.event === 'comment');
  assert.ok(saved, 'kept in the criterion spec\'s spool');
  assert.equal(saved.anchorId, 'report-totals', 'at the criterion');
  assert.deepEqual([saved.kind, saved.label, saved.from, saved.version, saved.subject],
    ['Criterion evidence', CRITERION, pane('host-draft'), undefined, undefined], 'with its kind, label, and from address, and no version');
  await page.close();

  // #acceptance-host-handoff, #acceptance-host-handoff-two: at another site's address, one draft on a criterion of the
  // scope's spec and one on a subject that names no spec, handed off together: one hand-off into each store.
  ({ page, frame } = await open(`${host}/page?page=${encodeURIComponent(pane('host-handoff-two'))}`));
  assert.equal(await frame.evaluate(() => self.origin), host, 'another site\'s page');
  await control(frame, CRITERION).waitFor();
  await comment(frame, CRITERION, 'Round to whole units.');
  await comment(frame, TURN, 'Why the table headers?', 'Question');
  assert.equal(await box(frame).locator('#hx-drafts').textContent(), '2 drafts');
  await box(frame).locator('#hx-handoff').click();
  await waitCards(frame, cardsMatch(`cards => cards.length === 1 && cards[0].status === 'pending'`));
  await pinOf(frame, CRITERION).click();
  await waitCards(frame, cardsMatch(`cards => cards.length === 1 && cards[0].status === 'pending' && cards[0].texts[0] === 'Round to whole units.'`));
  const specSaved = specEvents('host-handoff-two'), hostedSaved = hostedEvents('host-handoff-two');
  assert.deepEqual(specSaved.find(e => e.event === 'handoff').events, [specSaved.find(e => e.event === 'comment').id], 'the spec\'s hand-off lists its draft');
  assert.deepEqual(hostedSaved.find(e => e.event === 'handoff').events, [hostedSaved.find(e => e.event === 'comment').id], 'the hosted threads\' hand-off lists its draft');
  assert.equal(hostedSaved.find(e => e.event === 'comment').subject, 'agent-turn:qa:42', 'kept by its subject');
  assert.equal(specSaved.filter(e => e.event === 'handoff').length + hostedSaved.filter(e => e.event === 'handoff').length, 2, 'one hand-off per store');
  await page.close();

  // #acceptance-host-frame: in a frame with no origin, on a public service.
  ({ page, frame } = await open(`${host}/host-frame/docs/pane.html`));
  assert.equal(await frame.evaluate(() => self.origin), 'null', 'a frame with no origin');
  await control(frame, TURN).waitFor();
  await comment(frame, TURN, 'Saved in a frame with no origin.');
  await box(frame).locator('#hx-handoff').click();
  await waitCards(frame, cardsMatch(`cards => cards.length === 1 && cards[0].status === 'pending'`));
  assert.equal(hostedEvents('host-frame').filter(e => e.event === 'handoff').length, 1);
  await page.close();

  // #acceptance-host-reply: the owner's answer under the handed-off comment, with the comment's kind and from link.
  ({ page, frame } = await open(pane('host-reply')));
  await pinOf(frame, TURN).click();
  await waitCards(frame, cardsMatch(`cards => cards.length === 1 && cards[0].texts.length === 2`));
  let [card] = await cards(frame);
  assert.deepEqual([card.who, card.texts, card.status], [['Papaya', 'Agent'],
    ['Question: why name the columns after the table headers?', 'So the CSV reads like the table the manager sees.'], 'acknowledged']);
  assert.deepEqual([card.kind, card.from], ['Agent turn', 'https://review.qa.invalid/host-pages/docs/pane.html'], '#ui-from');
  await page.close();

  // No field of a stored event becomes markup on a host page: author, kind, label, quote, texts, and a status no
  // lifecycle has are text, and a from that is no web address is no link (review 1, B1).
  ({ page, frame } = await open(pane('layer-markup')));
  await pinOf(frame, TURN).click();
  await waitCards(frame, cardsMatch(`cards => cards.length === 1 && cards[0].texts.length === 2`));
  await page.waitForTimeout(300); // past an injected image's error
  [card] = await cards(frame);
  assert.deepEqual([card.status, card.who, card.texts, card.kind, card.from, card.notice],
    ['acknowledged', [MARKUP, 'Agent'], [MARKUP, MARKUP], MARKUP, null, `Text removed“${MARKUP}”`]);
  assert.deepEqual(await frame.evaluate(() => [window.__xss,
    document.querySelector('[data-spec-chat="comment-layer"]').shadowRoot.querySelectorAll('img').length]), [undefined, 0]);
  assert.equal(await pinOf(frame, TURN).getAttribute('data-s'), 'acknowledged');
  await page.close();

  // #ui-handoff, offline (review 1, D1): drafts on two subjects kept in one store, never read back before Hand off,
  // write one hand-off there listing both; another page's waiting draft on a subject not on this page stays a draft.
  const offline = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  ({ page, frame } = await open(pane('layer-stores'), offline));
  await addTurn(frame, 44);
  await control(frame, 'Agent turn 44').waitFor();
  const other = await open(pane('layer-stores', 'pane-other.html'), offline);
  await addTurn(other.frame, 45);
  await control(other.frame, 'Agent turn 45').waitFor();
  await offline.setOffline(true);
  await comment(other.frame, 'Agent turn 45', 'Waiting on another page.');
  await comment(frame, TURN, 'First of two in one store.');
  await comment(frame, 'Agent turn 44', 'Second of two in one store.');
  assert.equal(await box(frame).locator('#hx-drafts').textContent(), '2 drafts', "this page's drafts");
  await box(frame).locator('#hx-handoff').click();
  await other.page.close();
  await offline.setOffline(false);
  for (let i = 0; i < 400 && !hostedEvents('layer-stores').some(e => e.event === 'handoff'); i++) await page.waitForTimeout(100);
  await page.waitForTimeout(1000); // any further hand-off this page holds is sent by now
  const kept = hostedEvents('layer-stores');
  const on = n => kept.filter(e => e.event === 'comment' && e.subject === 'agent-turn:qa:' + n).map(e => e.id);
  const handoffs = kept.filter(e => e.event === 'handoff');
  assert.equal(handoffs.length, 1, 'one hand-off into the store: ' + JSON.stringify(handoffs));
  assert.deepEqual([...handoffs[0].events].sort(), [...on(42), ...on(44)].sort(), 'listing both drafts');
  assert.equal(on(45).length, 1, "the other page's draft is kept, and stays a draft");
  await offline.close();

  // #load-scan (review 1, D2): a host adding an earlier item moves no focus: a focused pin keeps it, and a focused
  // control's Enter opens its own item.
  ({ page, frame } = await open(pane('layer-focus')));
  await comment(frame, TURN, 'Keep my focus.');
  await box(frame).locator('.hx-layer-close').click();
  await pinOf(frame, TURN).focus();
  assert.match(await focused(frame), /: Agent turn 42, /);
  await addTurn(frame, 41, 'before');
  await control(frame, 'Agent turn 41').waitFor();
  assert.match(String(await focused(frame)), /: Agent turn 42, /, 'the pin keeps focus');
  await control(frame, TURN).focus();
  await addTurn(frame, 40, 'before');
  await control(frame, 'Agent turn 40').waitFor();
  assert.equal(await focused(frame), 'Comment on ' + TURN, "the focused control stays its item's");
  await page.keyboard.press('Enter');
  await box(frame).waitFor();
  assert.equal(await box(frame).getAttribute('aria-label'), 'Comments on ' + TURN, 'Enter opens its item');
  await page.close();

  // #acceptance-host-live: the owner replies in the pending thread; the open page shows it within 2 seconds.
  ({ page, frame } = await open(pane('host-live')));
  await pinOf(frame, TURN).click();
  await waitCards(frame, cardsMatch(`cards => cards.length === 1 && cards[0].status === 'pending'`));
  mkdirSync(join(hostedDir('host-live'), 'agent'), { recursive: true });
  writeFileSync(join(hostedDir('host-live'), 'agent', `${BigInt(Date.now()) * 1000000n}-reply-qa-live-answer.json`), JSON.stringify({
    id: 'qa-live-answer', event: 'reply', actor: 'agent', respondsTo: 'qa-turn', threadId: 'qa-turn', target: null,
    text: 'Answered live.', status: 'acknowledged', change: 'no spec change', createdAt: new Date().toISOString(), schemaVersion: 1 }));
  await waitCards(frame, cardsMatch(`cards => cards[0].texts[1] === 'Answered live.'`), 2500);
  await page.close();

  // #acceptance-host-same-subject: another host page anchoring the same subject in the same scope shows the thread.
  ({ page, frame } = await open(pane('host-same-subject', 'pane-other.html')));
  await pinOf(frame, TURN).click();
  await waitCards(frame, cardsMatch(`cards => cards.length === 1`));
  assert.deepEqual((await cards(frame))[0].texts, ['Question: why name the columns after the table headers?']);
  await page.close();

  // #acceptance-host-quote: the selected text inside the item is the comment's quote, and is marked on the item.
  ({ page, frame } = await open(pane('host-quote')));
  await control(frame, TURN).waitFor();
  const QUOTE = 'named the columns after the table headers';
  const selected = await frame.evaluate(quote => {
    const p = [...document.querySelectorAll('.item p')].find(el => el.textContent.includes(quote));
    const text = p.firstChild, at = text.data.indexOf(quote);
    const range = document.createRange();
    range.setStart(text, at);
    range.setEnd(text, at + quote.length);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    const r = range.getBoundingClientRect();
    return { left: r.left, top: r.top, right: r.right, bottom: r.bottom };
  }, QUOTE);
  await control(frame, TURN).click();
  assert.equal(await box(frame).locator('.hx-thread.active > .hx-quote').textContent(), `“${QUOTE}”`, 'the composer holds the quote');
  await box(frame).locator('.hx-composer textarea').pressSequentially('Which headers?');
  await box(frame).locator('.hx-composer [data-act=save]').click();
  await waitCards(frame, cardsMatch(`cards => cards.length === 1 && cards[0].status === 'draft'`));
  assert.deepEqual((await cards(frame))[0].quotes, [`“${QUOTE}”`], 'its thread shows that quote');
  const marks = await frame.locator('.hx-quote-mark').evaluateAll(els => els.map(el => el.getBoundingClientRect().toJSON()));
  assert.ok(marks.length >= 1, 'the selected text is marked');
  for (const m of marks) assert.ok(m.left >= selected.left - 1 && m.right <= selected.right + 1 && m.top >= selected.top - 1 && m.bottom <= selected.bottom + 3, 'on the selected text');
  await page.close();

  // #acceptance-host-quote-gone: a quote the item no longer holds is Text removed, with its pin on the item.
  ({ page, frame } = await open(pane('host-quote-gone')));
  await pinOf(frame, TURN).click();
  await waitCards(frame, cardsMatch(`cards => cards.length === 1`));
  assert.equal((await cards(frame))[0].notice, 'Text removed“named the columns after the report headers”');
  const turn = await frame.locator('.item', { hasText: 'I added the CSV export' }).boundingBox();
  const pin = await pinOf(frame, TURN).boundingBox();
  assert.ok(pin.x >= turn.x && pin.x + pin.width <= turn.x + turn.width && pin.y >= turn.y && pin.y + pin.height <= turn.y + turn.height, 'the pin sits on the item');
  assert.equal(await frame.locator('.hx-quote-mark').count(), 0, 'nothing marked');
  await page.close();

  // #acceptance-host-phone: tap the control, a starter, type, save: a pin, and the draft within the screen's width.
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  ({ page, frame } = await open(pane('host-phone'), phone));
  await control(frame, TURN).tap();
  await box(frame).locator('[data-starter="Change"]').tap();
  await box(frame).locator('.hx-composer textarea').pressSequentially('name the columns by the export spec.');
  await box(frame).locator('.hx-composer [data-act=save]').tap();
  await waitCards(frame, cardsMatch(`cards => cards.length === 1 && cards[0].status === 'draft'`));
  assert.deepEqual((await cards(frame))[0].texts, ['Change name the columns by the export spec.']);
  await pinOf(frame, TURN).waitFor();
  const sheet = await box(frame).boundingBox();
  assert.ok(sheet.x >= 0 && sheet.x + sheet.width <= 390, 'within the screen\'s width');
  assert.equal(await frame.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'no sideways scroll');
  assert.equal(await control(frame, TURN).evaluate(b => b.getBoundingClientRect().width), 44, 'a 44px tap target');
  await phone.close();

  // #acceptance-one-thread-spec, #ui-from: a comment saved on the host page's criterion item is the spec page's
  // thread at that criterion, with its kind and a link to the address it was written from.
  ({ page, frame } = await open(pane('one-thread-spec')));
  await control(frame, CRITERION).waitFor();
  await comment(frame, CRITERION, 'Totals per week, or per day too?');
  await page.goto(`${service}/one-thread-spec/${REPORT}#report-totals`);
  await page.waitForFunction(() => /connected/.test(document.getElementById('hx-status')?.textContent || ''));
  await page.waitForSelector('#hx-threads .hx-thread .hx-host-from', { state: 'attached' });
  const specCard = await page.evaluate(() => [...document.querySelectorAll('#hx-threads .hx-thread')].map(t => ({
    text: t.querySelector('.hx-msg .hx-text')?.textContent, kind: t.querySelector('.hx-host-kind')?.textContent,
    from: t.querySelector('.hx-host-from a')?.href })));
  assert.deepEqual(specCard, [{ text: 'Totals per week, or per day too?', kind: 'Criterion evidence', from: pane('one-thread-spec') }]);
  const [who, read] = await page.locator('#hx-threads .hx-msg').evaluate(m => [m.querySelector('.hx-who').textContent, m.innerText]);
  assert.match(read, apart(who, 'Totals per week, or per day too?'), "the spec page's card sets its author name apart from its text");
  assert.equal(await page.locator('[data-anchor="report-totals"] .hx-pin').count(), 1, "the criterion's pin");
  await page.close();

  // #acceptance-one-thread-host: the spec page's comment at the criterion is the host item's thread.
  ({ page, frame } = await open(pane('one-thread-host')));
  await pinOf(frame, CRITERION).click();
  await waitCards(frame, cardsMatch(`cards => cards.length === 1`));
  card = (await cards(frame))[0];
  assert.deepEqual([card.who, card.texts], [['Papaya'], ['Round totals to whole units?']]);
  await page.close();

  // #acceptance-no-scope: an item whose scope no row has shows no control, and no error (also above).
  ({ page, frame } = await open(pane('no-scope')));
  await control(frame, TURN).waitFor();
  assert.equal(await control(frame, 'Agent turn 43').count(), 0);
  await page.close();

  // #acceptance-no-service: the layer's service stopped. Its script does not load; or, loaded, its reads find nothing.
  ({ page, frame } = await open(pane('no-service')));
  await noLayer(frame, page);
  await page.close();
  const unreachable = watch(await desktop.newPage());
  await unreachable.route('**/api/events**', route => route.abort('connectionrefused'));
  await unreachable.goto(pane('host-draft'));
  await noLayer(unreachable.mainFrame(), unreachable);
  await unreachable.close();

  // #acceptance-private-other-origin: a private service (no public base, on loopback) answers another site's page with
  // no cross-origin read, so the layer it loaded draws nothing.
  mkdirSync(join(qaRoot, 'private'), { recursive: true });
  writeFileSync(join(qaRoot, 'private/registry.toml'), readFileSync(join(qaRoot, 'serve/registry.toml'), 'utf8').replace(/^\[process\][^\n]*\n[^\n]*\n/, ''));
  assert.doesNotMatch(readFileSync(join(qaRoot, 'private/registry.toml'), 'utf8'), /public_base/);
  const privateService = `http://127.0.0.1:${privatePort}`;
  servers.push(start([join(fixture, 'serve.py'), join(qaRoot, 'private/registry.toml'), String(privatePort)]));
  await ready(`${privateService}/host-draft/docs/pane.html`);
  servers.push(start([join(fixture, 'host.py'), privateService, String(privateHostPort)]));
  await ready(`http://127.0.0.1:${privateHostPort}/`);
  ({ page, frame } = await open(`http://127.0.0.1:${privateHostPort}/page?page=${encodeURIComponent(`${privateService}/host-draft/docs/pane.html`)}`));
  await noLayer(frame, page);
  await page.close();

  assert.deepEqual(errors, [], 'no page errors');
} finally {
  await browser.close();
  for (const server of servers) try { process.kill(-server.pid); } catch {}
  rmSync(qaRoot, { recursive: true, force: true });
}

console.log('runtime comment layer tests passed');
