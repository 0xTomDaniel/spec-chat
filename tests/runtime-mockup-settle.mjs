import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import { rewritingProduct } from './fixtures/rewriting-product.mjs';

// Fitting settles in one pass (mockups #review-settle, #acceptance-fit-settles, #acceptance-fit-settles-prototype): a
// mockup page, drawn or a prototype, puts no line in the error overlay as the window, a frame's content, or the chrome
// changes size. The QA fixture's walkthrough and prototype collections (tests/fixtures/qa reset.sh, spools.py
// WALKTHROUGHS, PROTOTYPES) on the fixture service (serve.py), mockup-steps' drawn screens and prototype-press' Weekly
// report framing the fixture's running prototype (prototype.py) at the lane's prototype origin on the prototype
// collections' service, private on loopback, the route sizing its frame by the shell messages (shell
// #shell-msg-prototype). Headless Chromium showing the scrollbars a desktop
// browser shows, which take room (Playwright hides them by default): a size that showed or hid one would change the
// widths fitting follows. Needs PLAYWRIGHT_CORE=<path to playwright-core>.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
assert.equal(readFileSync(resolve(root, 'docs/specs/.viz/runtime.js'), 'utf8'), runtime, 'runtime copies are identical');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_CORE || 'playwright-core');

const fixture = resolve(root, 'tests/fixtures/qa');
const SET = 'docs/specs/mockups/report-page/';
const freePort = () => new Promise(res => { const s = createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); });
const [port, prototypePort, ownPort, productPort] = [await freePort(), await freePort(), await freePort(), await freePort()];
const service = `http://127.0.0.1:${port}`, prototypes = `http://127.0.0.1:${ownPort}`;
const qaRoot = mkdtempSync(join(tmpdir(), 'mockup-settle-'));
const env = { ...process.env, QA_ROOT: qaRoot, QA_FIXTURE: fixture, QA_REPO_SPEC_CHAT: root, QA_URL_PROTOTYPE: `http://127.0.0.1:${prototypePort}`,
  QA_URL_REVIEW_PROTOTYPE: prototypes };
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
const servers = [start([join(fixture, 'serve.py'), join(qaRoot, 'serve/registry.toml'), String(port)]),
  start([join(fixture, 'serve.py'), join(qaRoot, 'serve-prototype/registry.toml'), String(ownPort)]),
  start([join(fixture, 'prototype.py'), String(productPort)])];
// The product as the lane runs it: the fixture's prototype, its route's page rewritten while a rewrite is set.
const product = await rewritingProduct(prototypePort, productPort);
process.on('exit', () => { for (const server of servers) try { process.kill(-server.pid); } catch {} });
await ready(`${service}/mockup-steps/docs/specs/report-page.spec.html`);
await ready(`${prototypes}/prototype-press/docs/specs/report-page.spec.html`);
await ready(`http://127.0.0.1:${productPort}/`);
await ready(`http://127.0.0.1:${prototypePort}/`);

const sleep = ms => new Promise(r => setTimeout(r, ms));
const until = async (check, what, ms = 8000) => {
  for (const end = Date.now() + ms; Date.now() < end; await sleep(100)) if (await check()) return;
  assert.fail('timed out: ' + what);
};
const browser = await chromium.launch({ ignoreDefaultArgs: ['--hide-scrollbars'] });
const desktop = await browser.newContext({ viewport: { width: 1440, height: 900 } });
// Every error the page meets, as its overlay would list it.
await desktop.addInitScript(() => {
  window.__errors = [];
  window.addEventListener('error', e => window.__errors.push(e.message));
});
const errors = [];
// The route's frame at a viewport: another origin than its page's, so read and driven through the browser.
const frameOf = async (page, viewport) => (await page.$(`.hx-mockup-slot[data-viewport="${viewport}"] iframe`)).contentFrame();
const inFrame = async (page, viewport, fn, arg) => (await frameOf(page, viewport)).evaluate(fn, arg);
const VIEWPORTS = ['desktop', 'phone'];
const everyFrame = async (page, fn) => {
  for (const viewport of VIEWPORTS) if (!await inFrame(page, viewport, fn).catch(() => false)) return false;
  return true;
};
// A mockup page at `path` on `at`; `held`, once both frames run the route held; `rewriting`, when given, rewrites the
// route's page as the product serves it (product).
const open = async (path, held = false, rewriting = null, at = service) => {
  const page = await desktop.newPage();
  page.on('pageerror', e => errors.push(String(e)));
  product.rewrite = rewriting;
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${at}/${path}`);
  await page.waitForFunction(() => /connected/.test(document.getElementById('hx-status')?.textContent || ''));
  if (held) await until(() => everyFrame(page, () => Boolean(document.querySelector('[data-spec-chat]') && document.querySelector('.team-card'))),
    'both frames run the route held').catch(async error => {
    const shown = [];
    for (const frame of page.frames()) shown.push([frame.url(), await frame.evaluate(() => [document.compatMode, document.body.className,
      Boolean(document.querySelector('[data-spec-chat]')), document.documentElement.outerHTML.slice(0, 200)]).catch(e => String(e))]);
    throw new Error(error.message + ' ' + JSON.stringify(shown));
  });
  return page;
};
const openPrototype = (path, rewriting = null) => open(path, true, rewriting, prototypes);
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
const frames = page => page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
// No error overlay, and no error it would list.
const noOverlay = async (page, when) => {
  await frames(page);
  const shown = await page.evaluate(() => ({ overlay: document.getElementById('hx-errors')?.textContent || null, errors: window.__errors }));
  assert.deepEqual(shown, { overlay: null, errors: [] }, 'no error overlay ' + when);
};
// The window narrowed to phone width and widened again, through the widths a window dragged passes, each one rendered:
// a loop shows only at a width where a size the page sets crosses another, as the top line wrapping while the page's
// height crosses the window's.
const dragWindow = async page => {
  const widths = [];
  for (let w = 1440; w >= 390; w -= 10) widths.push(w);
  for (const width of [...widths, ...widths.slice().reverse()]) {
    await page.setViewportSize({ width, height: 900 });
    await frames(page);
  }
};
// #review-fit: the two frames side by side, desktop first, each scaled whole into its slot, with no sideways scrolling.
const fitted = page => page.evaluate(() => {
  const slots = [...document.querySelectorAll('.hx-mockup-slot')].map(slot => {
    const s = slot.getBoundingClientRect(), f = slot.querySelector('.hx-mockup-fit > *').getBoundingClientRect();
    return { viewport: slot.dataset.viewport, left: s.left, top: s.top, whole: f.left >= s.left - 0.5 && f.right <= s.right + 0.5 };
  });
  return {
    sideBySide: slots.length === 2 && slots[0].viewport === 'desktop' && slots[0].left < slots[1].left && Math.abs(slots[0].top - slots[1].top) < 1,
    whole: slots.every(s => s.whole),
    noSideScroll: document.documentElement.scrollWidth <= window.innerWidth,
  };
});
const settled = async (page, when) => {
  await noOverlay(page, when);
  const fit = await fitted(page);
  assert.deepEqual([fit.sideBySide, fit.whole, fit.noSideScroll], [true, true, true], 'side by side, each scaled whole, ' + when);
};

// #acceptance-fit-settles: a drawn screen's mockup page, narrowed to phone width and widened again: the two frames sit
// side by side, each scaled whole, and no error overlay shows; nor as the review panel opens and closes, or as the step
// bar moves both frames through a walkthrough.
for (const [screen, drag] of [['01-report-page', true], ['04-team-panel', false]]) {
  const page = await open(`mockup-steps/${SET}${screen}.mockup.html`);
  await settled(page, screen + ' as it opens');
  if (drag) {
    await dragWindow(page);
    await settled(page, screen + ' narrowed to phone width and widened again');
  }
  await page.click('#hx-dock-open');
  await page.waitForSelector('.hx-panel.open');
  await settled(page, screen + ' with the panel open');
  await page.click('#hx-panel-toggle');
  await until(() => page.evaluate(() => !document.querySelector('.hx-panel.open')), 'the panel closes');
  await settled(page, screen + ' with the panel closed again');
  for (const step of await page.locator('#hx-steps .hx-step[data-step]').evaluateAll(els => els.map(el => el.dataset.step))) {
    await page.click(`#hx-steps .hx-step[data-step="${step}"]`);
    await sleep(400); // the step's motion
    await settled(page, screen + ' at step ' + step);
  }
  await page.close();
}

// #acceptance-fit-settles-prototype: a running prototype screen's mockup page, after its window changes size, pressed
// through to its next step: both frames show that step, each scaled whole, and no error overlay shows.
const panelOpen = (page, viewport) => inFrame(page, viewport, () => {
  const panel = document.getElementById('team-panel');
  return !panel.hidden && getComputedStyle(panel).opacity === '1';
});
// Each frame's height, its content's width, and what its content runs past its viewport, with nothing past it when it
// fits; and where the bottom of what `selector` finds in it sits against its viewport's. Read from what scrolls its
// content: its root, or in quirks mode its body.
const frameFits = async (page, selector = null) => {
  const fits = [];
  for (const viewport of VIEWPORTS) {
    const height = await page.evaluate(v => parseFloat(document.querySelector(`.hx-proto-frame[data-mockup-frame="${v}"]`).style.height), viewport);
    fits.push({ viewport, height, ...await inFrame(page, viewport, selector => {
      const doc = document.scrollingElement, last = selector && document.querySelector(selector);
      return { width: doc.clientWidth, past: doc.scrollHeight - doc.clientHeight, fits: doc.scrollHeight <= doc.clientHeight,
        shown: last ? last.getBoundingClientRect().bottom <= doc.clientHeight : null };
    }, selector) });
  }
  return fits;
};
const SCREEN = { desktop: 900, phone: 844 };
// The frames' heights once they hold, within a few frames of a change: then for 30 more.
const heldHeights = async (page, when) => {
  for (let i = 0; i < 5; i++) await frames(page);
  const held = (await frameFits(page)).map(f => f.height);
  for (let i = 0; i < 15; i++) await frames(page);
  assert.deepEqual((await frameFits(page)).map(f => f.height), held, 'each frame\'s height holds ' + when);
  return held;
};
{
  const page = await openPrototype(`prototype-press/${SET}06-weekly-report.mockup.html`);
  await settled(page, 'on the prototype as it opens');
  await dragWindow(page);
  await settled(page, 'on the prototype narrowed to phone width and widened again');
  assert.deepEqual((await frameFits(page)).map(f => [f.viewport, f.height, f.width, f.fits]),
    [['desktop', 900, 1440, true], ['phone', 844, 390, true]], 'each frame a screen tall, its content shorter, as it was framed');

  await clickInFrame(page, 'desktop', '.team-card[data-team="Payments"]');
  await until(() => page.evaluate(() => [...document.querySelectorAll('#hx-steps .hx-step[aria-pressed="true"]')].map(el => el.dataset.step).join() === 'team-open'),
    'the step bar shows the next step');
  await until(async () => await panelOpen(page, 'desktop') && await panelOpen(page, 'phone'), 'both frames show the next step');
  await settled(page, 'on the prototype pressed through to its next step');

  // #review-settle, a frame's content: the product lays its page out anew, its root and body as tall as their viewport,
  // as an app's often are, its content now running past the screen: each frame grows to its content, which keeps its
  // viewport's width, though growing the frame resizes the root and body it follows.
  for (const viewport of ['desktop', 'phone']) await inFrame(page, viewport, () => {
    document.head.appendChild(document.createElement('style')).textContent = 'html,body{height:100%}';
    document.body.appendChild(document.createElement('section')).style.height = '1500px';
  });
  await until(async () => (await frameFits(page)).every(f => f.fits && f.height > 1500), 'each frame grows to its content');
  assert.deepEqual((await frameFits(page)).map(f => [f.viewport, f.width]), [['desktop', 1440], ['phone', 390]],
    'the content keeps its viewport\'s width');
  await settled(page, 'on the prototype as its content grows past the screen');
  // mockups #frame-viewports: its content shorter again, inside a root and body still as tall as their viewport, each
  // frame shrinks back to its screen, as tall as its content.
  for (const viewport of ['desktop', 'phone']) await inFrame(page, viewport, () => { document.querySelector('body > section').remove(); });
  await until(async () => (await frameFits(page)).every(f => f.height === SCREEN[f.viewport]), 'each frame shrinks to its screen');
  await heldHeights(page, 'once its content is shorter again');
  await settled(page, 'on the prototype as its content shrinks inside a root as tall as its viewport');
  await page.close();
}

// #review-settle, content laid out against its viewport: a root at least as tall as the viewport inside the body's
// margin, so always taller than its viewport by that margin and what sits above it. Each frame settles within a few
// frames at the screen's height plus that excess, cut by it, never chasing it; content that grows on its own still grows
// the frame, and shows whole.
{
  const page = await openPrototype(`prototype-frames/${SET}06-weekly-report.mockup.html`);
  for (const viewport of ['desktop', 'phone']) await inFrame(page, viewport, () => {
    document.head.appendChild(document.createElement('style')).textContent = 'body{margin:8px}main{min-height:100vh}';
  });
  await heldHeights(page, 'with a root as tall as its viewport');
  for (const f of await frameFits(page)) {
    assert.ok(f.past > 16, f.viewport + ': its content runs past its viewport, the body\'s margin and the bar above');
    assert.ok(f.height >= SCREEN[f.viewport] + f.past && f.height <= SCREEN[f.viewport] + f.past + 3,
      f.viewport + ': the screen\'s height plus the excess, ' + JSON.stringify(f));
  }
  await settled(page, 'on a prototype whose root is as tall as its viewport');

  for (const viewport of ['desktop', 'phone']) await inFrame(page, viewport, () => {
    document.querySelector('main').appendChild(document.createElement('section')).style.cssText = 'flex:none;width:200px;height:1500px';
  });
  await until(async () => (await frameFits(page, 'main > section')).every(f => f.shown && f.height > 1500), 'each frame grows to show its grown content');
  await heldHeights(page, 'once its content grows');
  assert.ok((await frameFits(page, 'main > section')).every(f => f.shown), 'its grown content shows whole');
  await settled(page, 'on a prototype whose root is as tall as its viewport, once its content grows');
  await page.close();
}

// #review-settle, a route with no doctype, laid out in quirks mode, where its body is what scrolls: content running past
// the screen grows each frame to show it whole.
{
  const page = await openPrototype(`prototype-frames/${SET}06-weekly-report.mockup.html`, text => text.replace(/^<!doctype html>\s*/i, ''));
  assert.deepEqual([await inFrame(page, 'desktop', () => document.compatMode), await inFrame(page, 'phone', () => document.compatMode)],
    ['BackCompat', 'BackCompat'], 'the route in quirks mode');
  for (const viewport of ['desktop', 'phone']) await inFrame(page, viewport, () => {
    document.querySelector('main').appendChild(document.createElement('section')).style.cssText = 'flex:none;width:200px;height:1500px';
  });
  await until(async () => (await frameFits(page, 'main > section')).every(f => f.shown && f.height > 1500), 'each frame grows to show its content');
  await heldHeights(page, 'on a route with no doctype');
  await settled(page, 'on a route with no doctype');
  // mockups #frame-viewports: its content shorter again, where its body fills its viewport, each frame shrinks back.
  for (const viewport of ['desktop', 'phone']) await inFrame(page, viewport, () => { document.querySelector('main').lastElementChild.remove(); });
  await until(async () => (await frameFits(page)).every(f => f.height === SCREEN[f.viewport]), 'each frame shrinks to its screen');
  await heldHeights(page, 'on a route with no doctype, its content shorter again');
  await settled(page, 'on a route with no doctype, its content shorter again');
  await page.close();
}

// #review-settle, a route that sizes itself by script from its viewport, as products do where 100vh falls short: on
// its window's or visual viewport's resize event its main as tall as the viewport, inside the body's margin. Each frame
// settles within a few frames at the screen's height plus that excess, cut by it as a root 100vh tall is, never chasing
// it; a load, as reaching an earlier step, lays the route out at its screen's height again, so the frame is as tall as
// it first was; content growing on its own still grows the frame, and shows whole.
const SIZERS = {
  'innerHeight': "addEventListener('resize', fit); function fit() { document.querySelector('main').style.minHeight = innerHeight + 'px'; }",
  'a --vh property': "addEventListener('resize', fit); function fit() { document.documentElement.style.setProperty('--vh', innerHeight / 100 + 'px'); }",
  'the visual viewport': "visualViewport.addEventListener('resize', fit); function fit() { document.querySelector('main').style.minHeight = visualViewport.height + 'px'; }",
};
for (const [how, sizer] of Object.entries(SIZERS)) {
  // A product sizing itself by an inline script serves no policy that holds it back.
  const page = await openPrototype(`prototype-press/${SET}06-weekly-report.mockup.html`, (text, headers) => {
    delete headers['content-security-policy'];
    return text.replace('</body>', `<style>body{margin:8px}main{min-height:calc(var(--vh, 0px) * 100)}</style><script>${sizer} fit();</script></body>`);
  });
  const when = 'on a route sized from ' + how;
  const settledAt = async what => {
    const held = await heldHeights(page, what);
    for (const f of await frameFits(page)) {
      assert.ok(f.past > 16 && f.height >= SCREEN[f.viewport] + f.past && f.height <= SCREEN[f.viewport] + f.past + 3,
        f.viewport + ': the screen\'s height plus the excess, ' + what + ' ' + JSON.stringify(f));
    }
    await settled(page, what);
    return held;
  };
  const first = await settledAt(when);
  const steps = await page.locator('#hx-steps .hx-step[data-step]').evaluateAll(els => els.map(el => el.dataset.step));
  await page.click(`#hx-steps .hx-step[data-step="${steps[1]}"]`);
  await until(async () => await panelOpen(page, 'desktop') && await panelOpen(page, 'phone'), 'both frames show the next step');
  await page.click(`#hx-steps .hx-step[data-step="${steps[0]}"]`);
  await until(() => everyFrame(page, () => document.readyState === 'complete' && document.getElementById('team-panel').hidden
    && !document.querySelector('.team-card[aria-pressed=true]') && Boolean(document.querySelector('[data-spec-chat]'))),
  'both frames load the route again at its first step');
  assert.deepEqual(await settledAt(when + ', loaded again'), first, 'each frame as tall as it first was ' + when);

  for (const viewport of ['desktop', 'phone']) await inFrame(page, viewport, () => {
    document.querySelector('main').appendChild(document.createElement('section')).style.cssText = 'flex:none;width:200px;height:1500px';
  });
  await until(async () => (await frameFits(page, 'main > section')).every(f => f.shown && f.height > 1500), 'each frame grows to show its grown content');
  await heldHeights(page, when + ', once its content grows');
  assert.ok((await frameFits(page, 'main > section')).every(f => f.shown), 'its grown content shows whole ' + when);
  await settled(page, when + ', once its content grows');
  await page.close();
}

// #review-settle, a load of another origin: as the route leaves, its slot keeps its height until the next load is laid
// out; a route that leaves for a page of another origin, which the frame cannot size, ends that hold too, so the slot
// leaves no gap under the frame.
{
  const page = await openPrototype(`prototype-frames/${SET}06-weekly-report.mockup.html`);
  await inFrame(page, 'desktop', () => { document.body.appendChild(document.createElement('section')).style.height = '1500px'; });
  await until(async () => (await frameFits(page)).find(f => f.viewport === 'desktop').height > 1500, 'the desktop frame grows to its content');
  const other = `http://127.0.0.1:${prototypePort}/`;
  const slot = async () => ({ ...await page.evaluate(() => {
    const slot = document.querySelector('.hx-mockup-slot[data-viewport="desktop"]'), frame = slot.querySelector('iframe');
    return { hold: slot.style.minHeight, height: slot.getBoundingClientRect().height, below: slot.getBoundingClientRect().bottom - frame.getBoundingClientRect().bottom,
      loaded: frame.dataset.loads };
  }), other: (await frameOf(page, 'desktop')).url() === other });
  const tall = (await slot()).height;
  await page.evaluate(() => {
    const frame = document.querySelector('.hx-mockup-slot[data-viewport="desktop"] iframe');
    frame.addEventListener('load', () => { frame.dataset.loads = String(Number(frame.dataset.loads || 0) + 1); });
  });
  await inFrame(page, 'desktop', to => { location.href = to; }, other);
  await until(async () => { const s = await slot(); return s.other && s.loaded === '1'; }, 'the frame loads a page of another origin');
  await frames(page);
  const left = await slot();
  assert.equal(left.hold, '', 'no hold on the slot once the other origin loads');
  assert.ok(left.height < tall && left.below < 40, 'the slot as tall as its frame and label, no gap: ' + JSON.stringify({ tall, left }));
  await page.close();
}

assert.deepEqual(errors, []);
await browser.close();
console.log('runtime-mockup-settle: ok');
process.exit(0);
