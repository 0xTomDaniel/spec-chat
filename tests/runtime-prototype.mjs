import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, readdirSync } from 'node:fs';
import { createServer } from 'node:net';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import { request } from 'node:http';
import { listed } from './fixtures/listed.mjs';
import { rewritingProduct } from './fixtures/rewriting-product.mjs';

// A running prototype as a screen's mockup (mockups #prototype, #round-video, #before-prototype; shell
// #shell-setting-own, #shell-msg-prototype; review-service #prototype-proxy): the QA fixture's prototype-* collections
// (tests/fixtures/qa reset.sh, spools.py PROTOTYPES) on the fixture service (serve.py) on their own registry, private on
// loopback, each row naming the fixture's running prototype (prototype.py) on loopback, its mockup set holding Weekly
// report, a prototype mockup committed in two rounds, each round's videos in its spool; prototype-own-chrome's naming
// the service itself, the lane's own review service run as its prototype, its set also holding Report spec (spools.py
// OWN_CHROME). The mockup page frames the route at the lane's prototype origin, <slug>-prototype.localhost at the
// service's port, which Chromium reaches on loopback, held across the two origins by the shell messages, at desktop and
// phone width; on a desktop and a phone. The prototype serves its route with helmet's default Content-Security-Policy and
// X-Frame-Options, and the lane runs it rewritten where a case says (rewriting-product.mjs). Headless Chromium. Needs
// PLAYWRIGHT_CORE=<path to playwright-core>.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
assert.equal(readFileSync(resolve(root, 'docs/specs/.viz/runtime.js'), 'utf8'), runtime, 'runtime copies are identical');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_CORE || 'playwright-core');

const fixture = resolve(root, 'tests/fixtures/qa');
const MOCKUP = 'docs/specs/mockups/report-page/06-weekly-report.mockup.html';
const freePort = () => new Promise(res => { const s = createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); });
const [port, prototypePort, productPort] = [await freePort(), await freePort(), await freePort()];
const service = `http://127.0.0.1:${port}`;
const qaRoot = mkdtempSync(join(tmpdir(), 'prototype-'));
const env = { ...process.env, QA_ROOT: qaRoot, QA_FIXTURE: fixture, QA_REPO_SPEC_CHAT: root, QA_URL_PROTOTYPE: `http://127.0.0.1:${prototypePort}`,
  QA_URL_REVIEW_PROTOTYPE: service };
execFileSync('sh', [join(fixture, 'reset.sh')], { env, stdio: 'ignore' });
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
const servers = [start([join(fixture, 'serve.py'), join(qaRoot, 'serve-prototype/registry.toml'), String(port)]),
  start([join(fixture, 'prototype.py'), String(productPort)])];
process.on('exit', () => { for (const server of servers) try { process.kill(-server.pid); } catch {} });
const product = await rewritingProduct(prototypePort, productPort);
await ready(`${service}/prototype-frames/docs/specs/report-page.spec.html`);
await ready(`http://127.0.0.1:${prototypePort}/`);

const site = join(qaRoot, 'site');
const git = (collection, ...args) => execFileSync('git', ['-C', join(site, collection), ...args], { encoding: 'utf8' }).trim();
const [round1, round2] = git('prototype-frames', 'log', '--format=%H', '-2').split('\n').reverse();
const spoolEvents = (collection, mockup = MOCKUP) => {
  const dir = join(site, collection, mockup + '.review', 'human');
  let names = [];
  try { names = readdirSync(dir).filter(n => n.endsWith('.json')).sort(); } catch {}
  return names.map(n => JSON.parse(readFileSync(join(dir, n), 'utf8')));
};
const commented = events => events.some(e => e.event === 'comment');

const sleep = ms => new Promise(r => setTimeout(r, ms));
const until = async (check, what, ms = 8000) => {
  for (const end = Date.now() + ms; Date.now() < end; await sleep(100)) if (await check()) return;
  assert.fail('timed out: ' + what);
};
// Autoplay without a gesture, so a test plays a round's video as a reviewer's press does.
const browser = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
const desktop = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
const errors = [];

// The route's frame at a viewport: another origin than its page's, so read and driven through the browser, never the
// page's script (#prototype-route).
const frameOf = async (page, viewport) => (await page.$(`.hx-mockup-slot[data-viewport="${viewport}"] iframe`)).contentFrame();
const inFrame = async (page, viewport, fn, arg) => (await frameOf(page, viewport)).evaluate(fn, arg);
const VIEWPORTS = ['desktop', 'phone'];
// A collection's mockup page, once both frames run its route held, each having drawn the marks its page sent once it
// published, and showing what `shows` finds; the product rewriting its route's page by `rewrite`, when given.
const open = async (collection, { on = desktop, query = '', held = true, mockup = MOCKUP, shows = '.team-card', rewrite = null } = {}) => {
  product.rewrite = rewrite;
  const page = await on.newPage();
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto(`${service}/${collection}/${mockup}${query}`);
  await page.waitForFunction(() => /connected/.test(document.getElementById('hx-status')?.textContent || ''));
  if (held) await until(async () => {
    for (const viewport of VIEWPORTS) {
      const frame = await frameOf(page, viewport).catch(() => null);
      if (!frame || !await frame.evaluate(s => Boolean(document.querySelector('[data-spec-chat]') && document.querySelector(s)), shows).catch(() => false)) return false;
    }
    return true;
  }, 'both frames run the route held');
  return page;
};
// A reviewer's click on what `selector` finds in a frame, at the point the page shows it: the frame zoomed whole into its
// slot (mockups #review-fit), so the point is the element's center in the frame, scaled, from the frame's corner; the
// element brought into view first, its frame scrolling the page.
const clickInFrame = async (page, viewport, selector) => {
  const frame = await frameOf(page, viewport);
  await frame.evaluate(s => document.querySelector(s).scrollIntoView({ block: 'center' }), selector);
  const point = async () => {
    const inner = await frame.evaluate(s => { const r = document.querySelector(s).getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; }, selector);
    return page.evaluate(([v, inner]) => {
      const f = document.querySelector(`.hx-mockup-slot[data-viewport="${v}"] iframe`), r = f.getBoundingClientRect(), zoom = r.width / f.offsetWidth;
      return { x: r.left + inner.x * zoom, y: r.top + inner.y * zoom, height: innerHeight };
    }, [viewport, inner]);
  };
  let at = await point();
  if (at.y < 0 || at.y > at.height) {
    await page.evaluate(by => window.scrollBy(0, by), at.y - at.height / 2);
    at = await point();
  }
  await page.mouse.click(at.x, at.y);
};
const panelOpen = (page, viewport) => inFrame(page, viewport, () => {
  const panel = document.getElementById('team-panel');
  return !panel.hidden && getComputedStyle(panel).opacity === '1';
});
const pressed = page => page.evaluate(() => [...document.querySelectorAll('#hx-steps .hx-step[data-step]')]
  .filter(el => el.getAttribute('aria-pressed') === 'true').map(el => el.dataset.step));
const rect = (page, selector) => page.locator(selector).first().evaluate(el => { const r = el.getBoundingClientRect(); return { left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width }; });
const threads = page => page.evaluate(() => [...document.querySelectorAll('#hx-threads .hx-entry[data-thread]')].map(t => ({
  where: t.querySelector('.hx-chip')?.textContent,
  draft: t.querySelector('.hx-bubble')?.hasAttribute('data-draft'),
  text: t.querySelector('.hx-msg .hx-bubble .hx-text')?.textContent,
})));
// The pins a held frame draws in its own content, each with its number and where it sits in the frame.
const framePins = (page, viewport) => inFrame(page, viewport, () => [...(document.querySelector('[data-spec-chat]')?.shadowRoot
  .querySelectorAll('.hx-pin') || [])].map(pin => { const r = pin.getBoundingClientRect(); return { status: pin.dataset.s, left: r.left, top: r.top, right: r.right, bottom: r.bottom }; }));

// #acceptance-prototype-frames: the route running in a desktop frame and a phone frame side by side, each labeled with
// its viewport, under the top line and the step bar; each frame at its viewport's width, held by the page, as served
// through the review service's prototype proxy at the lane's prototype origin with the comment layer's script added,
// from the service, naming that origin.
const ORIGIN = collection => `http://${collection}-prototype.localhost:${port}`;
{
  const page = await open('prototype-frames');
  const slots = await page.evaluate(() => [...document.querySelectorAll('.hx-mockup-slot')].map(slot => {
    const r = slot.getBoundingClientRect();
    return { label: slot.querySelector('.hx-mockup-label').textContent, left: r.left, top: r.top };
  }));
  for (const [i, viewport] of VIEWPORTS.entries()) Object.assign(slots[i], await inFrame(page, viewport, () => ({
    inner: innerWidth, at: location.origin + location.pathname,
    layer: [...document.scripts].filter(s => s.src).map(s => [s.getAttribute('src'), s.dataset.specChatPrototype || null]) })));
  assert.deepEqual(slots.map(s => [s.label, s.inner, s.at]), [
    ['Desktop', 1440, ORIGIN('prototype-frames') + '/reports/weekly'], ['Phone', 390, ORIGIN('prototype-frames') + '/reports/weekly']]);
  assert.ok(slots[0].left < slots[1].left && Math.abs(slots[0].top - slots[1].top) < 1, 'side by side, desktop first');
  for (const s of slots) {
    assert.deepEqual(s.layer, [[service + '/static/comment-layer.js', ORIGIN('prototype-frames') + '/'], ['/assets/report.js', null]],
      'the proxy adds the comment layer first, naming the lane\'s prototype origin; the product loads its own script from its root');
  }
  const steps = await rect(page, '#hx-steps'), line = await rect(page, '#hx-range-bar'), stage = await rect(page, '.hx-mockup');
  assert.ok(steps.top >= line.bottom - 0.5 && stage.top >= steps.bottom - 0.5, 'under the top line and the step bar');
  assert.deepEqual(await pressed(page), ['totals']);
  assert.equal(await page.locator('.hx-proto-down').count(), 0);
  // review-service #prototype-proxy: the route's page served with the product's helmet policy letting the layer's script
  // load from the service and the mockup page frame it, by either loopback name, its X-Frame-Options gone.
  const served = await new Promise((done, fail) => request({ host: '127.0.0.1', port, path: '/reports/weekly',
    headers: { Host: `prototype-frames-prototype.localhost:${port}` } }, answer => { answer.resume(); done(answer.headers); }).on('error', fail).end());
  assert.equal(served['x-frame-options'], undefined);
  assert.ok(served['content-security-policy'].includes(`script-src 'self' ${service};`), served['content-security-policy']);
  assert.ok(served['content-security-policy'].includes(`frame-ancestors 'self' ${service} http://localhost:${port};`), served['content-security-policy']);

  // #acceptance-round-video, #round-video-show: under the frames the newest round's desktop video, with its step
  // captions and its round's commit.
  const shown = await page.evaluate(async () => {
    const video = document.querySelector('#hx-rounds > .hx-round video');
    if (video.readyState < 1) await new Promise(done => video.addEventListener('loadedmetadata', done, { once: true }));
    const track = video.textTracks[0];
    for (let i = 0; i < 50 && !(track.cues && track.cues.length && track.activeCues && track.activeCues.length); i++) await new Promise(done => setTimeout(done, 100));
    return { commit: video.closest('.hx-round').dataset.commit, src: decodeURIComponent(video.currentSrc), mode: track.mode,
      cues: [...track.cues].map(cue => cue.text), meta: video.closest('.hx-round').querySelector('.hx-round-meta').innerText,
      first: { at: video.currentTime, played: video.played.length, caption: [...(track.activeCues || [])].map(cue => cue.text) },
      under: video.getBoundingClientRect().top >= document.querySelector('.hx-mockup').getBoundingClientRect().bottom - 0.5 };
  });
  assert.equal(shown.commit, round2);
  assert.match(shown.src, new RegExp(round2 + '-desktop\\.webm$'));
  assert.equal(shown.mode, 'showing');
  assert.deepEqual(shown.cues, ["1 Totals · Every team's total for the week", '2 Team open · Payments opens its last three weeks',
    '3 Export · The export dialog names the file']);
  // Loaded, never played nor moved, it shows the caption of the moment it stands at, its first step's.
  assert.deepEqual(shown.first, { at: 0, played: 0, caption: ["1 Totals · Every team's total for the week"] });
  assert.match(shown.meta, new RegExp('This round’s video\\s+Recorded from ' + round2.slice(0, 7) + ', desktop'));
  assert.ok(shown.under, 'under the frames');
  // #acceptance-round-video-old: Earlier rounds offers the older round's video.
  assert.equal(await page.locator('#hx-rounds-earlier').isVisible(), false);
  await page.click('button:has-text("Earlier rounds (1)")');
  assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('#hx-rounds-earlier .hx-round')]
    .map(round => [round.dataset.commit, decodeURIComponent(round.querySelector('video').currentSrc || round.querySelector('video').src).split('file=')[1]])),
  [[round1, round1 + '-desktop.webm']]);

  // #before-new: the row's base holds no Weekly report: New since the base, no Before.
  assert.equal(await page.locator('#hx-range-copy').textContent(), 'New since the base');
  assert.equal(await page.locator('#hx-mockup-before').count(), 0);
  // #step-press is the page's: a step pressed in the bar moves both frames, by the step's actions.
  await page.click('#hx-steps .hx-step[data-step="export"]');
  await until(async () => await inFrame(page, 'desktop', () => !document.getElementById('export-dialog').hidden)
    && await inFrame(page, 'phone', () => !document.getElementById('export-dialog').hidden), 'both frames show the export step');
  await page.close();
}

// #before-prototype: compared against the first round, whose videos the spool holds, Before plays its round video in the
// frames' place, with no pins and no comment control; After returns the running frames.
{
  const page = await open('prototype-frames', { query: '?base=' + round1 });
  await page.waitForSelector('#hx-mockup-before');
  await page.click('#hx-mockup-before');
  const before = await page.evaluate(() => ({
    commit: document.getElementById('hx-round-before')?.dataset.commit,
    frames: document.querySelector('.hx-mockup').hidden,
    pressed: document.getElementById('hx-mockup-before').getAttribute('aria-pressed'),
    comment: getComputedStyle(document.getElementById('hx-mode')).display,
  }));
  assert.deepEqual(before, { commit: round1, frames: true, pressed: 'true', comment: 'none' });
  await page.click('#hx-mockup-after');
  assert.deepEqual(await page.evaluate(() => [document.getElementById('hx-round-before'), document.querySelector('.hx-mockup').hidden]), [null, false]);
  await page.close();
}

// #acceptance-prototype-root: a product that serves its page, script, and style only at its root, loading them by
// absolute paths: both frames show the route styled and running as the product serves it, and no error overlay shows.
{
  const page = await open('prototype-frames');
  for (const viewport of VIEWPORTS) {
    const shown = await inFrame(page, viewport, () => {
      const loaded = performance.getEntriesByType('resource').map(entry => entry.name);
      const bar = getComputedStyle(document.querySelector('.app-bar'));
      const card = document.querySelector('.team-card[data-team="Payments"]');
      card.click(); // its own script opens the team's panel
      return { loaded: ['/assets/report.css', '/assets/report.js'].map(path => loaded.includes(location.origin + path)),
        styled: [bar.display, bar.borderBottomStyle, getComputedStyle(card).borderRadius],
        running: !document.getElementById('team-panel').hidden && document.querySelector('#team-panel h2').textContent };
    });
    assert.deepEqual(shown, { loaded: [true, true], styled: ['flex', 'solid', '8px'], running: 'Payments' }, viewport);
  }
  assert.equal(await page.locator('#hx-errors').count(), 0, 'no error overlay');
  await page.close();
}

// #acceptance-prototype-own-chrome (mockups #prototype-drawn): the lane's own review service run as its prototype, a
// mockup page framing its Report spec page: the page loads Spec Chat's runtime once and its own copy holds it, drawing no
// review panel or toolbar of its own; comment mode on, a click on a criterion in the desktop frame, a comment typed and
// saved: the criterion shows a pin, and the conversation ends with the comment's bubble as a draft, naming the region.
const OWN = 'docs/specs/mockups/report-page/07-report-spec.mockup.html';
// spec-page-design #error-overlay, shell #shell-roles, #shell-msg-prototype: the held spec page keeps its top line, and
// the page under it follows the line's height with no error, while the route says its size: in each frame its top line
// grown by a style outside it, as its font loading does, the page lies under it, and neither frame shows an error overlay.
const heldUnderItsLine = async page => {
  for (const viewport of VIEWPORTS) {
    await inFrame(page, viewport, () => { document.head.appendChild(document.createElement('style')).textContent = '.hx-top-line{font-size:20px}'; });
    await until(() => inFrame(page, viewport, () => {
      const line = document.getElementById('hx-top-line');
      return line.offsetHeight > 40 && document.documentElement.style.getPropertyValue('--hx-line-space') === line.offsetHeight + 'px';
    }), viewport + ': the grown top line measured');
  }
  await sleep(500);
  for (const viewport of VIEWPORTS) {
    assert.equal(await inFrame(page, viewport, () => document.getElementById('hx-errors')?.textContent ?? null), null, viewport + ': no error overlay');
    assert.ok(await inFrame(page, viewport, () => getComputedStyle(document.body).paddingTop === document.getElementById('hx-top-line').offsetHeight + 'px'),
      viewport + ': the page under its top line');
  }
};
{
  const page = await open('prototype-own-chrome', { mockup: OWN, shows: '[data-anchor="report-totals"]' });
  for (const viewport of VIEWPORTS) {
    assert.deepEqual(await inFrame(page, viewport, () => ({
      at: location.origin + location.pathname, held: document.body.classList.contains('hx-held'),
      runtimes: [...document.scripts].filter(s => /(\.viz\/runtime|static\/comment-layer)\.js$/.test(s.src)).length,
      chrome: document.querySelectorAll('.hx-panel, .hx-toolbar, .hx-thread-dock, #hx-mode').length,
    })), { at: ORIGIN('prototype-own-chrome') + '/prototype-own-chrome/docs/specs/report.spec.html', held: true, runtimes: 1, chrome: 0 }, viewport);
  }
  await page.click('#hx-mode');
  await clickInFrame(page, 'desktop', '[data-anchor="report-totals"]');
  await page.waitForSelector('.hx-composer textarea');
  await page.fill('.hx-composer textarea', 'The totals criterion should name the week it sums.');
  await page.click('.hx-composer [data-act=save]');
  await until(async () => (await threads(page)).some(t => t.text === 'The totals criterion should name the week it sums.'), 'the comment shows');
  assert.deepEqual((await threads(page)).at(-1), { where: 'Totals criterion', draft: true, text: 'The totals criterion should name the week it sums.' });
  const event = (await listed(() => spoolEvents('prototype-own-chrome', OWN), commented, 'the comment')).find(e => e.event === 'comment');
  assert.equal(event.anchorId, 'totals-criterion');
  await until(async () => (await framePins(page, 'desktop')).length === 1, 'the criterion shows a pin');
  const [pin] = await framePins(page, 'desktop');
  const criterion = await inFrame(page, 'desktop', () => { const r = document.querySelector('[data-anchor="report-totals"]').getBoundingClientRect(); return { left: r.left, top: r.top, right: r.right, bottom: r.bottom }; });
  assert.equal(pin.status, 'draft');
  assert.ok(pin.left < criterion.right && pin.right > criterion.left && pin.top < criterion.bottom + 4 && pin.bottom > criterion.top - 4, 'the pin sits on the criterion');
  assert.equal(await inFrame(page, 'desktop', () => document.querySelectorAll('.hx-panel, .hx-toolbar, #hx-mode').length), 0,
    'the framed spec page shows no review panel or toolbar of its own');
  await heldUnderItsLine(page);
  await page.close();
}
{
  const page = await open('prototype-own-chrome', { on: phone, mockup: OWN, shows: '[data-anchor="report-totals"]' });
  await heldUnderItsLine(page);
  await page.close();
}

// #acceptance-prototype-comment: at the second step, comment mode on, a click on a button in the prototype's desktop
// frame comments on it: the button shows a pin, and the conversation ends with the comment's bubble as a draft, its chip
// naming the button and the second step. The comment names its region and step and carries no version (#prototype-marks).
// The product's policy here allows no inline style: the layer's own sheet still styles its marks (review-service
// #prototype-proxy).
const NO_INLINE_STYLE = (text, headers) => {
  headers['content-security-policy'] = headers['content-security-policy'].replace("style-src 'self' https: 'unsafe-inline'", "style-src 'self'");
  return text;
};
{
  const page = await open('prototype-comment', { rewrite: NO_INLINE_STYLE });
  await page.click('#hx-steps .hx-step[data-step="team-open"]');
  await until(async () => await panelOpen(page, 'desktop') && await panelOpen(page, 'phone'), 'both frames show the team panel');
  await page.click('#hx-mode');
  await clickInFrame(page, 'desktop', '#export');
  await page.waitForSelector('.hx-composer textarea');
  assert.equal(await inFrame(page, 'desktop', () => document.getElementById('export-dialog').hidden), true, 'a pointed click presses nothing');
  await page.fill('.hx-composer textarea', 'Make Export CSV the primary action.');
  await page.click('.hx-composer [data-act=save]');
  await until(async () => (await threads(page)).some(t => t.text === 'Make Export CSV the primary action.'), 'the comment shows');
  const last = (await threads(page)).at(-1);
  assert.deepEqual(last, { where: 'Export button · Step 2 Team open', draft: true, text: 'Make Export CSV the primary action.' });
  const event = (await listed(() => spoolEvents('prototype-comment'), commented, 'the comment')).find(e => e.event === 'comment');
  assert.deepEqual([event.anchorId, event.step, event.target, 'version' in event], ['export-button', 'team-open', null, false]);
  await until(async () => (await framePins(page, 'desktop')).length === 1, 'the button shows a pin');
  const [pin] = await framePins(page, 'desktop');
  const button = await inFrame(page, 'desktop', () => { const r = document.getElementById('export').getBoundingClientRect(); return { left: r.left, top: r.top, right: r.right, bottom: r.bottom }; });
  assert.equal(pin.status, 'draft');
  assert.ok(pin.left < button.right && pin.right > button.left && pin.top < button.bottom + 4 && pin.bottom > button.top - 4, 'the pin sits on the button');
  assert.deepEqual(await inFrame(page, 'desktop', () => {
    const style = getComputedStyle(document.querySelector('[data-spec-chat]').shadowRoot.querySelector('.hx-pin'));
    return [style.position, style.display];
  }), ['fixed', 'flex'], 'the pin styled by the layer\'s own sheet, though the route allows no inline style');
  // prompt-first-shaping #acceptance-one-handoff-rule: the page's one Hand off; a held frame draws no comment UI of its own.
  assert.equal(await page.evaluate(() => [...document.querySelectorAll('button')]
    .filter(b => b.getClientRects().length && /^Hand off/.test(b.textContent.trim())).length), 1);
  for (const viewport of ['desktop', 'phone']) {
    assert.equal(await inFrame(page, viewport, () => [...(document.querySelector('[data-spec-chat]')?.shadowRoot.querySelectorAll('button') || [])]
      .filter(b => !b.matches('.hx-pin,.hx-qmark')).length), 0, viewport + ' frame draws only its marks');
  }
  // Its pin shows at its step alone (mockups #step-threads-pin).
  await page.click('#hx-steps .hx-step[data-step="totals"]');
  await until(async () => (await framePins(page, 'desktop')).length === 0, 'no pin at the first step');
  // #prototype-steps: a step before the one a frame shows loads the route again, its own frame element moving it.
  for (const viewport of ['desktop', 'phone']) {
    await until(() => inFrame(page, viewport, () => document.readyState === 'complete' && document.getElementById('team-panel').hidden
      && !document.querySelector('.team-card[aria-pressed=true]')).catch(() => false), viewport + ' frame shows the route again');
  }
  await page.close();
}

// review-service #prototype-proxy: a product whose policy trusts scripts by nonce and 'strict-dynamic', as Next.js ships
// one, which ignores any address: the layer runs by the nonce of that answer's own the proxy gives its tag, beside the
// product's own script by its nonce, and a step's action is done in both frames.
const STRICT_DYNAMIC = (text, headers) => {
  headers['content-security-policy'] = "script-src 'nonce-product' 'strict-dynamic'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'";
  return text.replace(/<script\b/g, '<script nonce="product"');
};
{
  const page = await open('prototype-frames', { rewrite: STRICT_DYNAMIC });
  await page.click('#hx-steps .hx-step[data-step="team-open"]');
  await until(async () => await panelOpen(page, 'desktop') && await panelOpen(page, 'phone'), 'both frames show the team panel');
  await page.close();
}

// #prototype-steps, shell #shell-msg-prototype: a frame scrolled wholly out of view, which the browser draws no frame
// of and moves no motion on, still does each action after the motion before it ends, by the clock: on a phone, the page
// scrolled to the round videos past the desktop frame, the step bar's Export reaches the third step in both frames within
// 3 s, the press of a team card and its motion on the way, under the 5 s a motion waits at most.
{
  const page = await open('prototype-frames', { on: phone });
  const away = () => page.evaluate(() => {
    const r = document.querySelector('.hx-mockup-slot[data-viewport="desktop"] iframe').getBoundingClientRect();
    return r.bottom <= 0 || r.top >= innerHeight;
  });
  await page.evaluate(() => document.getElementById('hx-rounds').scrollIntoView({ block: 'end' }));
  assert.ok(await away(), 'the desktop frame is wholly out of view');
  const dialog = viewport => inFrame(page, viewport, () => !document.getElementById('export-dialog').hidden);
  await page.tap('#hx-steps .hx-step[data-step="export"]');
  await until(async () => await dialog('desktop') && await dialog('phone'), 'both frames reach Export within 3 s', 3000);
  assert.ok(await away(), 'the desktop frame stayed out of view');
  assert.equal(await inFrame(page, 'desktop', () => document.querySelector('.team-card[aria-pressed=true]')?.dataset.team), 'Payments');
  await page.close();
}

// #acceptance-prototype-press: with comment mode off, pressing the button the second step names in the desktop frame
// moves the step bar to the second step, and the phone frame follows; any other action leaves it on no step.
{
  const page = await open('prototype-press');
  assert.deepEqual(await pressed(page), ['totals']);
  await clickInFrame(page, 'desktop', '.team-card[data-team="Payments"]');
  await until(async () => (await pressed(page)).join() === 'team-open', 'the step bar shows the second step pressed');
  await until(() => panelOpen(page, 'phone'), 'the phone frame follows');
  assert.equal(new URL(page.url()).searchParams.get('step'), 'team-open');
  await clickInFrame(page, 'desktop', '.team-card[data-team="Search"]');
  await until(async () => (await pressed(page)).length === 0, 'another action leaves the step bar on no step');
  await page.close();
}

// #prototype-steps, #acceptance-prototype-press, shell #shell-msg-prototype: a step reached by a page load, the team
// cards being links to the route naming the team (?team=), which opens it as the page loads. The step bar's Export
// reaches the third step in both frames through the page the card loads; pressing through, the Payments card loads its
// page and moves the step bar to the second step as the phone frame follows, and Export pressed in the page it loaded
// moves it to the third.
const LINKS = text => text.replace(/<button type="button" class="team-card" data-team="([^"]+)">(.*?)<\/button>/g,
  (_, team, inner) => `<a class="team-card" data-team="${team}" href="?team=${encodeURIComponent(team)}">${inner}</a>`);
{
  const page = await open('prototype-press', { rewrite: LINKS });
  assert.equal(await inFrame(page, 'desktop', () => document.querySelector('.team-card').tagName), 'A');
  const team = viewport => inFrame(page, viewport, () => new URLSearchParams(location.search).get('team')).catch(() => undefined);
  const dialog = viewport => inFrame(page, viewport, () => !document.getElementById('export-dialog').hidden).catch(() => false);
  const held = viewport => inFrame(page, viewport, () => Boolean(document.querySelector('[data-spec-chat]'))).catch(() => false);
  await page.click('#hx-steps .hx-step[data-step="export"]');
  await until(async () => await dialog('desktop') && await dialog('phone'), 'both frames reach Export through the page the card loads', 15000);
  assert.deepEqual([await team('desktop'), await team('phone'), await pressed(page)], ['Payments', 'Payments', ['export']]);

  await page.click('#hx-steps .hx-step[data-step="totals"]');
  await until(async () => await team('desktop') === null && await team('phone') === null && await held('desktop') && await held('phone'),
    'both frames load the route again, held', 15000);
  await clickInFrame(page, 'desktop', '.team-card[data-team="Payments"]');
  await until(async () => (await pressed(page)).join() === 'team-open', 'the step bar shows the second step');
  await until(async () => await team('desktop') === 'Payments' && await held('desktop') && await panelOpen(page, 'phone').catch(() => false),
    'the desktop frame holds the page the card loaded, and the phone frame follows', 15000);
  await clickInFrame(page, 'desktop', '#export');
  await until(async () => (await pressed(page)).join() === 'export', 'Export pressed in the loaded page moves the step bar to the third step');
  await until(() => dialog('phone'), 'the phone frame follows');
  await page.close();
}

// shell #shell-msg-prototype: each frame's hello carries its own id, which the route sends back on every message, and the
// page takes a message from the prototype origin as from the frame its id names, since one the route sends as its
// document leaves reaches the page with no source window. Pressed, said by the desktop frame's route as it leaves for the
// page the second step's card loads, moves the step bar to that step, and the phone frame follows. The route says it
// from its pagehide, which every browser hands the page with no source window, so the case never rests on a race.
{
  const own = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await own.addInitScript(() => {
    const said = window.routeSaid = [];
    addEventListener('message', e => {
      const data = e.data;
      if (!data || typeof data.type !== 'string') return;
      if (data.type === 'spec-chat-host') window.hello = data; // in a frame: its page's hello
      else if (window.parent === window) {
        const slot = [...document.querySelectorAll('.hx-mockup-slot')].find(s => s.querySelector('iframe')?.contentWindow === e.source);
        said.push({ type: data.type, id: data.id, from: e.source === null ? null : slot ? slot.dataset.viewport : 'elsewhere' });
      }
    }, true);
  });
  const page = await open('prototype-press', { on: own, rewrite: LINKS });
  const ids = {};
  for (const viewport of VIEWPORTS) ids[viewport] = await inFrame(page, viewport, () => window.hello.id);
  assert.ok(ids.desktop && ids.phone && ids.desktop !== ids.phone, 'each frame its own id: ' + JSON.stringify(ids));
  const said = () => page.evaluate(() => window.routeSaid);
  for (const viewport of VIEWPORTS) {
    const from = (await said()).filter(m => m.from === viewport);
    assert.ok(from.some(m => m.type === 'spec-chat-publish') && from.every(m => m.id === ids[viewport]), viewport + ' sends its id back');
  }
  await inFrame(page, 'desktop', id => {
    addEventListener('pagehide', () => parent.postMessage({ type: 'spec-chat-pressed', reached: true, id }, '*'));
    setTimeout(() => { location.href = '?team=Payments'; });
  }, ids.desktop);
  await until(async () => (await pressed(page)).join() === 'team-open', 'Pressed from a leaving document moves the step bar to the second step');
  assert.ok((await said()).some(m => m.type === 'spec-chat-pressed' && m.from === null && m.id === ids.desktop),
    'the press reached the page with no source window');
  await until(() => panelOpen(page, 'phone').catch(() => false), 'the phone frame follows', 15000);
  await own.close();
}

// shell #shell-fields-tolerant, #shell-msg-prototype: a route on an older runtime, which never reads its hello's id,
// sends no id back, and the page takes each message it sends as from its source window: it is held, and pressing the
// button the second step names in its desktop frame moves the step bar to that step as the phone frame follows.
{
  const older = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await older.addInitScript(() => {
    const said = window.routeSaid = [];
    addEventListener('message', e => {
      const data = e.data;
      if (!data || typeof data.type !== 'string') return;
      if (window.parent !== window) { if (data.type === 'spec-chat-host') delete data.id; } // in a frame: the hello as an older route reads it
      else said.push({ type: data.type, id: 'id' in data });
    }, true);
  });
  const page = await open('prototype-press', { on: older });
  const said = await page.evaluate(() => window.routeSaid);
  assert.ok(said.some(m => m.type === 'spec-chat-publish') && said.every(m => !m.id), 'the route sends no id: ' + JSON.stringify(said));
  assert.deepEqual(await pressed(page), ['totals']);
  await clickInFrame(page, 'desktop', '.team-card[data-team="Payments"]');
  await until(async () => (await pressed(page)).join() === 'team-open', 'the step bar shows the second step pressed');
  await until(() => panelOpen(page, 'phone'), 'the phone frame follows');
  assert.equal(await page.locator('.hx-proto-down').count(), 0, 'held: no Prototype not running');
  await older.close();
}

// review-service #prototype-front, #acceptance-prototype-down: a front that does not answer the lane's prototype origin
// leaves the mockup page served as before, its frames loading no route that says hello back: the page reads Prototype not
// running with the route, and its conversation still shows the mockup's threads.
{
  const front = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await front.route(url => url.hostname.endsWith('-prototype.localhost'), route => route.abort('connectionrefused'));
  const page = await open('screens-index', { on: front, held: false });
  await page.waitForSelector('.hx-proto-down', { timeout: 15000 });
  assert.deepEqual(await page.evaluate(() => [document.querySelector('.hx-proto-down strong').textContent,
    document.querySelector('.hx-proto-down code').textContent, document.querySelectorAll('.hx-proto-frame').length]),
  ['Prototype not running', '/reports/weekly', 0]);
  if (!await page.locator('.hx-panel.open').count()) await page.click('#hx-dock-open');
  await until(async () => (await threads(page)).some(t => t.text === 'Show the change from last week?'), 'the thread shows');
  await front.close();
}

// #acceptance-prototype-down: a prototype that does not answer: the page reads Prototype not running with the route, and
// its conversation still shows the mockup's threads and questions.
{
  const page = await open('prototype-down', { held: false });
  await page.waitForSelector('.hx-proto-down');
  assert.deepEqual(await page.evaluate(() => [document.querySelector('.hx-proto-down strong').textContent,
    document.querySelector('.hx-proto-down code').textContent, document.querySelectorAll('.hx-proto-frame').length]),
  ['Prototype not running', '/reports/weekly', 0]);
  if (!await page.locator('.hx-panel.open').count()) await page.click('#hx-dock-open');
  await until(async () => (await threads(page)).some(t => t.text === 'Show the change from last week?'), 'the thread shows');
  assert.equal((await threads(page)).find(t => t.text === 'Show the change from last week?').where, 'Team panel · Step 2 Team open');
  assert.match(await page.locator('#hx-threads').innerText(), /Should the team panel show a trend line\?/);
  await page.close();
}

// #acceptance-prototype-updated: the agent commits to the lane branch while the page is open: within 5 seconds (plus a
// poll's slack) Prototype updated by agent with Reload, and the frames keep what they loaded until Reload.
{
  const before = git('prototype-updated', 'rev-parse', 'HEAD');
  const page = await open('prototype-updated');
  await inFrame(page, 'desktop', () => { window.kept = true; });
  await page.waitForFunction(() => /Prototype updated by agent/.test(document.getElementById('hx-banner')?.textContent || ''), null, { timeout: 6500 });
  assert.notEqual(git('prototype-updated', 'rev-parse', 'HEAD'), before, 'the fixture agent committed');
  assert.equal(await page.locator('#hx-banner button', { hasText: 'Reload' }).count(), 1);
  assert.equal(await inFrame(page, 'desktop', () => window.kept === true), true, 'the frames keep what they loaded');
  await page.close();
}

// #acceptance-round-video-comment, #round-video-comment, threads #video-target: the round's video played, then paused,
// comment mode on, a click on the video: the conversation ends with the comment's bubble, its chip reading the round and
// the moment the video was paused at. The comment is kept in the mockup's spool on the round's video, by its round, at
// that moment, with no step and no version; the rail under the video marks it, and its chip returns the video there.
{
  const page = await open('prototype-video-comment');
  const VIDEO = '#hx-rounds > .hx-round video';
  const pausedAt = await page.evaluate(async s => {
    const video = document.querySelector(s);
    if (video.readyState < 1) await new Promise(done => video.addEventListener('loadedmetadata', done, { once: true }));
    video.scrollIntoView({ block: 'center' });
    await video.play();
    while (video.currentTime < 1.3) await new Promise(done => setTimeout(done, 20));
    video.pause();
    return video.currentTime;
  }, VIDEO);
  await page.click('#hx-mode');
  await page.click(VIDEO);
  await page.waitForSelector('.hx-composer textarea');
  await page.fill('.hx-composer textarea', 'The panel should fade in faster here.');
  await page.click('.hx-composer [data-act=save]');
  await until(async () => (await threads(page)).some(t => t.text === 'The panel should fade in faster here.'), 'the comment shows');
  assert.deepEqual((await threads(page)).at(-1), { where: 'Round ' + round2.slice(0, 7) + ' · 0:01', draft: true, text: 'The panel should fade in faster here.' });
  assert.equal(await page.evaluate(s => document.querySelector(s).paused, VIDEO), true, 'the click played nothing');
  const [kept] = (await listed(() => spoolEvents('prototype-video-comment'), commented, 'the video comment')).filter(e => e.event === 'comment');
  assert.deepEqual([kept.anchorId, kept.target, 'step' in kept, 'version' in kept], ['round-' + round2 + '-desktop', { type: 'element', key: 'video[1]' }, false, false]);
  assert.ok(Math.abs(kept.moment - pausedAt) < 0.001, 'at the moment it was paused at');
  await page.waitForSelector('#hx-rounds .hx-video-rail .hx-video-mark');
  const [video, rail] = await Promise.all([rect(page, VIDEO), rect(page, '#hx-rounds .hx-video-rail')]);
  assert.ok(rail.top >= video.bottom && Math.abs(rail.width - video.width) <= 1, 'a rail under the round\'s video, as wide as it');
  // #root-chip-moment: played on, the chip pressed, the round's video paused at the moment.
  await page.evaluate(s => document.querySelector(s).play(), VIDEO);
  await until(() => page.evaluate(s => document.querySelector(s).currentTime > 2, VIDEO), 'played on');
  await page.locator('#hx-threads .hx-entry[data-thread]', { hasText: 'The panel should fade in faster here.' }).locator(':scope > .hx-chip').click();
  await until(() => page.evaluate(([s, t]) => { const v = document.querySelector(s); return v.paused && Math.abs(v.currentTime - t) < 0.001; }, [VIDEO, kept.moment]), 'paused at its moment');
  await page.close();
}

// On a phone: the frames one above the other within the screen's width, and #acceptance-round-video-phone: the round's
// phone video plays within the screen's width with its step captions.
{
  const page = await open('prototype-frames', { on: phone });
  const [first, second] = await page.evaluate(() => [...document.querySelectorAll('.hx-mockup-slot')].map(slot => {
    const r = slot.getBoundingClientRect();
    return { top: r.top, bottom: r.bottom, left: r.left, right: r.right };
  }));
  assert.ok(second.top >= first.bottom - 0.5 && first.right <= 390 && second.right <= 390, 'stacked within the width');
  const played = await page.evaluate(async () => {
    const video = document.querySelector('#hx-rounds > .hx-round video');
    video.scrollIntoView();
    await video.play();
    const track = video.textTracks[0];
    for (let i = 0; i < 60 && !(video.currentTime > 0.3 && track.activeCues && track.activeCues.length); i++) await new Promise(done => setTimeout(done, 100));
    const r = video.getBoundingClientRect();
    return { viewport: video.closest('.hx-round').dataset.viewport, src: decodeURIComponent(video.currentSrc), playing: video.currentTime > 0.3,
      mode: track.mode, cue: track.activeCues && track.activeCues[0] && track.activeCues[0].text, left: r.left, right: r.right,
      wide: document.documentElement.scrollWidth <= window.innerWidth };
  });
  assert.equal(played.viewport, 'phone');
  assert.match(played.src, new RegExp(round2 + '-phone\\.webm$'));
  assert.ok(played.playing, 'it plays');
  assert.equal(played.mode, 'showing');
  assert.match(played.cue || '', /^[123] /);
  assert.ok(played.left >= 0 && played.right <= 390 && played.wide, 'within the screen\'s width');
  await page.close();
}

assert.deepEqual(errors, []);
await browser.close();
console.log('runtime-prototype: ok');
process.exit(0);
