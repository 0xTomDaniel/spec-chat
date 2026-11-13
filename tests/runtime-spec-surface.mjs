import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { createServer } from 'node:net';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

// The spec page as one surface (spec-page-design #one-surface, #top-line, #keys-icons; review-index #index-link):
// the fixed top line holds the back link, the compared range, and Change base on the page color; the document header
// is unboxed; keys show only in tooltips; every chrome icon is the set's line icon; pills read in sentence case.
// The QA fixture's collections (tests/fixtures/qa reset.sh + spools.py) on the fixture service (serve.py), opened
// directly, in headless Chromium. Needs PLAYWRIGHT_CORE=<path to playwright-core>.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
assert.equal(readFileSync(resolve(root, 'docs/specs/.viz/runtime.js'), 'utf8'), runtime, 'runtime copies are identical');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_CORE || 'playwright-core');

const fixture = resolve(root, 'tests/fixtures/qa');
const qaRoot = mkdtempSync(join(tmpdir(), 'spec-surface-'));
const env = { ...process.env, QA_ROOT: qaRoot, QA_FIXTURE: fixture, QA_REPO_SPEC_CHAT: root };
execFileSync('sh', [join(fixture, 'reset.sh')], { env, stdio: 'inherit' });
const port = await new Promise(res => { const s = createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); });
const service = `http://127.0.0.1:${port}`;
const server = spawn('python3', [join(fixture, 'serve.py'), join(qaRoot, 'serve/registry.toml'), String(port)], {
  stdio: 'ignore', detached: true,
  env: { ...env, HOME: join(qaRoot, 'home'), XDG_STATE_HOME: join(qaRoot, 'state'), XDG_CONFIG_HOME: join(qaRoot, 'config'), HERDR_SOCKET_PATH: '' },
});
// Stop the server on any exit, also when a step throws before the finally below.
process.on('exit', () => { try { process.kill(-server.pid); } catch {} });
const REPORT = 'docs/specs/report.spec.html';
for (let i = 0; ; i++) {
  try { if ((await fetch(`${service}/qa-fixture/${REPORT}`)).ok) break; } catch {}
  if (i > 150) throw new Error('fixture service did not start');
  await new Promise(r => setTimeout(r, 100));
}

const DESKTOP = { width: 1280, height: 800 };
const PHONE = { width: 390, height: 844 };
const browser = await chromium.launch();
const errors = [];
// Connected, its compared range shown: the page has rendered and any place it keeps is restored.
const ready = async page => {
  await page.waitForFunction(() => /connected/.test(document.getElementById('hx-status')?.textContent || ''));
  await page.waitForFunction(() => /^Changes from /.test(document.getElementById('hx-range-copy')?.textContent || ''));
};
const open = async (collection, viewport, options = {}, address = '') => {
  const context = await browser.newContext({ viewport, ...options });
  const page = await context.newPage();
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto(`${service}/${collection}/${REPORT}${address}`);
  await ready(page);
  return { page, close: () => context.close() };
};
// What the top of the page shows: the top line's parts, the document header, and the backgrounds they sit on.
const surface = page => page.evaluate(() => {
  const css = el => getComputedStyle(el);
  const rect = el => el.getBoundingClientRect().toJSON();
  const line = document.querySelector('.hx-top-line');
  const link = line.querySelector('.hx-service-index-link');
  const header = document.querySelector('article.spec > header');
  const h1 = header.querySelector('h1');
  const section = document.querySelector('article.spec > section');
  const toolbar = document.querySelector('.hx-toolbar');
  const body = css(document.body).backgroundColor;
  return {
    line: rect(line), lineBorder: css(line).borderBottomWidth + ' ' + css(line).borderBottomStyle,
    lineBox: [...line.children].map(child => child.className),
    link: { text: link.textContent, name: link.getAttribute('aria-label') || link.textContent, href: link.href, icons: link.querySelectorAll('svg').length,
      rect: rect(link), visible: link.checkVisibility() },
    range: { text: document.getElementById('hx-range-copy').textContent, color: css(document.getElementById('hx-range-copy')).color },
    change: { text: document.getElementById('hx-range-change').textContent, background: css(document.getElementById('hx-range-change')).backgroundColor,
      border: css(document.getElementById('hx-range-change')).borderTopStyle, rect: rect(document.getElementById('hx-range-change')) },
    muted: getComputedStyle(document.documentElement).getPropertyValue('--ui-muted').trim(),
    backgrounds: { body, line: css(line).backgroundColor, toolbar: css(toolbar).backgroundColor, html: css(document.documentElement).backgroundColor },
    header: { title: h1.textContent, rect: rect(header), border: ['Top', 'Right', 'Bottom', 'Left'].map(side => css(header)['border' + side + 'Width']),
      background: css(header).backgroundColor, image: css(header).backgroundImage, shadow: css(header).boxShadow },
    title: rect(h1), section: rect(section.querySelector('h2') || section), toolbar: rect(toolbar),
    scroll: { width: document.documentElement.scrollWidth, client: document.documentElement.clientWidth },
    view: { width: innerWidth, height: innerHeight },
  };
});
// The set's line icons as the runtime holds them, read as the review service reads them (tests/fixtures/design-set.py).
const { icons: ICONS } = JSON.parse(execFileSync('python3', [resolve(root, 'tests/fixtures/design-set.py')], { encoding: 'utf8' }));
const rgb = hex => 'rgb(' + hex.match(/[0-9a-f]{2}/gi).map(h => parseInt(h, 16)).join(', ') + ')';

try {
  // spec-page-design #acceptance-one-surface: the top line reads the back link, the compared range, and Change base on
  // the page color with a hairline under it; the header shows the title unboxed; document, top line, toolbar share one background.
  {
    const { page, close } = await open('qa-fixture', DESKTOP);
    const s = await surface(page);
    assert.deepEqual([s.line.top, s.line.left, s.line.width], [0, 0, DESKTOP.width], 'one line across the top of the page');
    assert.deepEqual(s.lineBox, ['hx-service-index-link', 'hx-range-bar'], 'the top line holds the back link, then the range bar');
    assert.equal(s.link.text, 'Review index', 'the back link reads Review index');
    assert.equal(s.link.icons, 1, 'after a chevron');
    assert.equal(s.link.href, `${service}/`, 'it opens the review index');
    assert.match(s.range.text, /^Changes from [0-9a-f]{7} \d{4}-\d{2}-\d{2} to (working copy of )?[0-9a-f]{7}/, 'then the compared range');
    assert.equal(s.range.color, rgb(s.muted), 'as muted meta');
    assert.equal(s.change.text, 'Change base', 'Change base at its end');
    assert.ok(s.change.rect.right > s.line.width - 40, 'at the end of the line');
    assert.deepEqual([s.change.background, s.change.border], ['rgba(0, 0, 0, 0)', 'none'], 'Change base is a text button');
    assert.ok(s.link.rect.top >= s.line.top && s.link.rect.bottom <= s.line.bottom && s.change.rect.top >= s.line.top
      && s.change.rect.bottom <= s.line.bottom && s.line.height < 50, 'one line');
    assert.equal(s.lineBorder, '1px solid', 'a hairline under it');
    assert.equal(s.backgrounds.line, s.backgrounds.body, 'the top line is on the page color');
    assert.equal(s.backgrounds.toolbar, s.backgrounds.body, 'the toolbar is on the page color');
    assert.equal(s.header.title, 'Weekly report', "the header shows the spec's title");
    assert.deepEqual(s.header.border, ['0px', '0px', '0px', '0px'], 'no rule around the header');
    assert.deepEqual([s.header.background, s.header.image, s.header.shadow], ['rgba(0, 0, 0, 0)', 'none', 'none'], 'no box around the header');
    assert.ok(s.header.rect.top >= s.line.bottom, 'the top line covers no part of the document');
    // the range picker opens under the line and pushes the page, so it covers no review control (compared-range #bar-flow)
    await page.click('#hx-range-change');
    const opened = await page.evaluate(() => ({
      line: document.querySelector('.hx-top-line').getBoundingClientRect().bottom,
      picker: document.getElementById('hx-range-picker').getBoundingClientRect().bottom,
      dock: document.querySelector('.hx-thread-dock').getBoundingClientRect().top,
      header: document.querySelector('article.spec > header').getBoundingClientRect().top,
    }));
    assert.ok(opened.picker <= opened.line && opened.dock >= opened.line && opened.header >= opened.line, 'the picker opens inside the line, above the dock and the document');
    await page.keyboard.press('Escape');

    // #acceptance-key-tooltip: the Comment button's tooltip names the C key; the button reads only Comment.
    const comment = page.getByRole('button', { name: 'Comment', exact: true });
    assert.equal(await comment.textContent(), 'Comment', 'the button reads only Comment');
    assert.equal(await comment.getAttribute('title'), 'Comment (C)', 'its tooltip names the C key');
    await close();
  }

  // A control's icons as drawn: how many, the control's own text beside them, and the first one's size and stroke.
  const iconOfPage = (page, selector) => page.evaluate(sel => [...document.querySelectorAll(sel)].map(control => {
    const svgs = control.querySelectorAll('svg');
    // the dock's unread count is a badge beside the icon, not part of it
    const text = [...control.childNodes].filter(node => !node.matches?.('.hx-unread-badge')).map(node => node.textContent).join('').trim();
    return { svgs: svgs.length, text, size: svgs[0] && [svgs[0].getAttribute('width'), svgs[0].getAttribute('height')],
      stroke: svgs[0] && svgs[0].getAttribute('stroke-width'), color: svgs[0] && svgs[0].getAttribute('stroke') };
  }), selector);
  const LINE = { svgs: 1, text: '', size: ['16', '16'], stroke: '1.75', color: 'currentColor' };

  // #acceptance-icons-dock: on a spec page with threads, opened with its review panel closed, the thread dock shows a thin
  // line icon, drawn as an outline. qa-1 holds a draft, a handed-off, a replied, and a resolved thread (spools.py
  // every_status).
  {
    const { page, close } = await open('qa-1', DESKTOP);
    await page.waitForSelector('.hx-dock-thread');
    assert.equal(await page.locator('.hx-panel.open').count(), 0, 'the page opens with its review panel closed');
    assert.deepEqual(await iconOfPage(page, '.hx-dock-open'), [LINE], 'the dock holds the set line icon');
    const fills = await page.evaluate(() => [...document.querySelectorAll('.hx-dock-open svg, .hx-dock-open svg *')]
      .map(node => getComputedStyle(node).fill));
    assert.ok(fills.length > 1 && fills.every(fill => fill === 'none'), 'drawn as an outline, nothing filled: ' + fills);
    await close();
  }

  // #acceptance-icons: on a spec page with threads, the panel toggle, the gear, and the focused thread's Back are line
  // icons of one size and stroke weight. #acceptance-receipt-case: every receipt in the conversation (threads #stream)
  // reads in sentence case. qa-1, as above.
  {
    const { page, close } = await open('qa-1', DESKTOP);
    await page.waitForSelector('.hx-dock-thread');
    const iconOf = selector => iconOfPage(page, selector);
    await page.click('#hx-dock-open');
    await page.waitForSelector('.hx-panel.open #hx-threads .hx-entry');
    assert.deepEqual(await iconOf('#hx-panel-toggle'), [LINE], 'the panel toggle holds the set line icon');
    assert.deepEqual(await iconOf('#hx-panel-gear'), [LINE], 'the gear holds the set line icon');
    const receipts = await page.evaluate(() => [...document.querySelectorAll('#hx-threads .hx-receipt')]
      .map(receipt => ({ shown: receipt.innerText, transform: getComputedStyle(receipt).textTransform })));
    assert.deepEqual(receipts.map(r => r.shown).sort(), ['Draft', 'Resolved', 'Seen', 'Sent'], 'every receipt reads in sentence case');
    for (const receipt of receipts) assert.equal(receipt.transform, 'none', 'never capitals');
    // #icons-rule: every chrome icon is the set's line icon, never a glyph. The focused thread's Back holds the set's
    // chevron; Resolve reads its name alone, the set holding no check icon.
    await page.click('#hx-threads [data-thread="qa-replied"] .hx-bubble');
    await page.waitForSelector('.hx-focused');
    assert.deepEqual(await iconOf('.hx-focused [data-act=back]'), [{ ...LINE, text: 'Conversation' }], 'Back holds the set line icon');
    assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('.hx-focused [data-act=back] svg path')].map(path => path.getAttribute('d'))),
      [ICONS['chevron-left'].match(/ d="([^"]*)"/)[1]], "it is the set's chevron");
    assert.deepEqual(await iconOf('.hx-focused [data-act=save]'), [{ svgs: 0, text: 'Save', size: undefined, stroke: undefined, color: undefined }],
      'Save reads its name alone (threads #focus-reply)');
    assert.deepEqual(await iconOf('[data-act=resolve]'), [{ svgs: 0, text: 'Resolve', size: undefined, stroke: undefined, color: undefined }],
      'Resolve reads its name alone');
    await page.click('.hx-focused [data-act=back]');
    // the toggle keeps one icon open or closed; its name says what it does
    await page.click('#hx-panel-toggle');
    assert.deepEqual(await iconOf('#hx-panel-toggle'), [LINE], 'the closed toggle keeps the icon');
    await page.click('#hx-dock-open');

    // review-index #acceptance-index-link-wide: on a desktop screen the index link stays visible beside the open panel.
    const wide = await page.evaluate(() => ({
      visible: document.querySelector('.hx-service-index-link').checkVisibility(),
      link: document.querySelector('.hx-service-index-link').getBoundingClientRect().right,
      line: document.querySelector('.hx-top-line').getBoundingClientRect().right,
      panel: document.querySelector('.hx-panel.open').getBoundingClientRect().left,
    }));
    assert.ok(wide.visible, 'the index link stays visible');
    assert.ok(wide.link <= wide.panel && wide.line <= wide.panel, 'beside the open panel, never under it');
    await close();
  }

  // The review panel's header text runs between the panel toggle and the gear at every width, never under either: it
  // wraps instead. qa-1's agent has replied, so the header reads its last event time. Desktop widths keep the 330px
  // panel; at and under the 640px breakpoint the panel takes the whole width.
  const MOBILE = { isMobile: true, hasTouch: true };
  for (const [viewport, options] of [[DESKTOP, {}], [{ width: 641, height: 800 }, {}], [{ width: 640, height: 800 }, MOBILE],
    [PHONE, MOBILE], [{ width: 320, height: 640 }, MOBILE]]) {
    const { page, close } = await open('qa-1', viewport, options);
    await page.click('#hx-dock-open');
    await page.waitForSelector('.hx-panel.open');
    await page.waitForFunction(() => /agent last event/.test(document.getElementById('hx-agent').textContent));
    const head = await page.evaluate(() => {
      const box = el => el.getBoundingClientRect().toJSON();
      const header = document.querySelector('.hx-panel-head');
      const lines = [];
      const walker = document.createTreeWalker(header, NodeFilter.SHOW_TEXT);
      for (let node; (node = walker.nextNode());) {
        if (!node.textContent.trim() || node.parentElement.closest('button')) continue;
        const range = document.createRange();
        range.selectNodeContents(node);
        lines.push(...[...range.getClientRects()].map(rect => rect.toJSON()));
      }
      const style = getComputedStyle(header);
      return { lines, header: box(header), toggle: box(document.getElementById('hx-panel-toggle')),
        gear: box(document.getElementById('hx-panel-gear')), padding: [parseFloat(style.paddingLeft), parseFloat(style.paddingRight)],
        gap: parseFloat(style.getPropertyValue('--hx-head-gap')) };
    });
    const overlaps = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
    // The header's text box is derived from the gear and the toggle: it ends one gap before the gear, starts one after
    // the toggle, wherever either sits and whatever its size.
    assert.ok(head.gap > 0, 'the header has its gap');
    const near = (a, b) => Math.abs(a - b) < 0.01;
    assert.ok(near(head.gear.left - (head.header.right - head.padding[1]), head.gap), `the text box ends one gap before the gear at ${viewport.width}px`);
    assert.ok(near(head.header.left + head.padding[0] - head.toggle.right, head.gap), `the text box starts one gap after the toggle at ${viewport.width}px`);
    assert.ok(head.lines.length >= 2, `the header text shows at ${viewport.width}px`);
    for (const line of head.lines) {
      assert.ok(!overlaps(line, head.gear), `header text ends before the gear at ${viewport.width}px: ${JSON.stringify(line)} vs ${JSON.stringify(head.gear)}`);
      assert.ok(!overlaps(line, head.toggle), `header text starts after the panel toggle at ${viewport.width}px`);
      assert.ok(line.right <= head.header.right && line.bottom <= head.header.bottom, `header text stays inside its header at ${viewport.width}px`);
    }
    await close();
  }

  // #acceptance-one-surface-phone: the first screen shows the top line, the title, and the start of the first section,
  // on one background, without horizontal scrolling. #acceptance-key-phone: no key in any toolbar button.
  {
    const { page, close } = await open('qa-fixture', PHONE, { isMobile: true, hasTouch: true });
    const s = await surface(page);
    assert.equal(s.line.top, 0, 'the top line first');
    assert.ok(s.link.visible && s.link.rect.bottom <= s.line.bottom, 'it keeps the back link');
    assert.ok(s.change.rect.top < s.link.rect.bottom && s.change.rect.right <= s.view.width, 'and Change base beside it');
    assert.ok(s.title.top >= s.line.bottom && s.title.bottom <= s.toolbar.top, "then the spec's title");
    assert.ok(s.section.top < s.toolbar.top, 'and the start of its first section, all on the first screen');
    assert.equal(s.backgrounds.line, s.backgrounds.body, 'on one background');
    assert.equal(s.backgrounds.toolbar, s.backgrounds.body, 'the toolbar too');
    assert.ok(s.scroll.width <= s.scroll.client, 'without horizontal scrolling');
    // The hand-off button reads its own state (Hand off, Accept spec, or the open TBDs that come first).
    const labels = await page.evaluate(() => [...document.querySelectorAll('.hx-toolbar button')].filter(b => b.checkVisibility())
      .map(b => ({ id: b.id, text: b.textContent, title: b.getAttribute('title') })));
    assert.deepEqual(labels.map(l => l.id), ['hx-mode', 'hx-handoff', 'hx-reading'], 'the toolbar holds Comment, the panel\'s one hand-off, and Reading view');
    assert.deepEqual([labels[0].text, labels[2].text], ['Comment', 'Reading view'], 'they read Comment and Reading view');
    for (const label of labels) {
      assert.doesNotMatch(label.text, /\([A-Z]\)|⌘|Ctrl|Shift/, 'no key in a label: ' + label.text);
      assert.equal(label.title, null, 'no tooltip key on a phone: ' + label.text);
    }

    // review-index #acceptance-index-link-panel, -panel-closed: on a narrow screen the open panel hides the index link;
    // closing it shows the link again.
    const linkShown = () => page.evaluate(() => document.querySelector('.hx-service-index-link').checkVisibility());
    await page.click('#hx-dock-open');
    await page.waitForSelector('.hx-panel.open');
    assert.equal(await linkShown(), false, 'the open panel hides the index link');
    await page.click('#hx-panel-toggle');
    assert.equal(await linkShown(), true, 'closing the panel shows it again');
    await close();
  }

  // reading-place #acceptance-place-restore: scrolled to a later section, a reader returns there at the same offset
  // reload after reload, under the fixed top line (spec-page-design #top-line); #place-restore: an anchor in the address
  // lands right under the top line. On a desktop and a phone screen short enough that the place is 200px down.
  for (const [viewport, options] of [[{ width: 1280, height: 300 }, {}], [{ width: 390, height: 500 }, { isMobile: true, hasTouch: true }]]) {
    const where = page => page.evaluate(() => ({ y: Math.round(scrollY),
      line: document.querySelector('.hx-top-line').getBoundingClientRect().bottom,
      body: parseFloat(getComputedStyle(document.body).paddingTop) }));
    const { page, close } = await open('qa-fixture', viewport, options);
    await page.evaluate(() => scrollTo(0, 200));
    assert.equal((await where(page)).y, 200, 'scrolled to a later section');
    for (const reload of [1, 2, 3]) {
      await page.reload();
      await ready(page);
      await page.waitForTimeout(300); // nothing moves the page once it is shown
      const now = await where(page);
      assert.equal(now.y, 200, `reload ${reload} at ${viewport.width}px returns to the same place`);
      assert.equal(Math.round(now.body), Math.round(now.line), 'the page starts right under the top line');
    }
    await close();
    const addressed = await open('qa-fixture', viewport, options, '#story-export');
    await addressed.page.waitForTimeout(300);
    const landed = await addressed.page.evaluate(() => ({
      anchor: document.querySelector('[data-anchor="story-export"]').getBoundingClientRect().top,
      line: document.querySelector('.hx-top-line').getBoundingClientRect().bottom }));
    assert.equal(Math.round(landed.anchor), Math.round(landed.line), `the address anchor lands right under the top line at ${viewport.width}px`);
    await addressed.close();
  }

  // #error-overlay: one line per distinct error, counting its times, at the top of the page, under the top line when the
  // page shows one, on every width: the page, the dock, the toolbar, and the hand-off control in view
  // (prompt-first-shaping #acceptance-one-handoff-rule). The page starts right under it (space: the body's top padding).
  const overlap = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
  const placed = page => page.evaluate(() => {
    const rect = el => el && el.getBoundingClientRect().toJSON();
    const hit = el => { const box = el.getBoundingClientRect(); return el.contains(document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2)); };
    const errors = document.getElementById('hx-errors');
    const handoff = document.querySelector('.hx-toolbar #hx-handoff');
    const opener = document.getElementById('hx-dock-open');
    return { lines: [...errors.children].map(line => line.textContent), errors: rect(errors), shown: errors.checkVisibility(),
      line: rect(document.querySelector('.hx-top-line')), space: parseFloat(getComputedStyle(document.body).paddingTop),
      header: rect(document.querySelector('article.spec > header')), title: rect(document.querySelector('article.spec > header h1')),
      host: rect(document.querySelector('body > main')), dock: rect(document.querySelector('.hx-thread-dock')), opener: opener && hit(opener),
      toolbar: rect(document.querySelector('.hx-toolbar')), panel: rect(document.querySelector('.hx-panel.open')),
      handoff: handoff && { text: handoff.textContent, rect: rect(handoff), visible: handoff.checkVisibility(), top: hit(handoff) },
      scrolls: errors.scrollHeight > errors.clientHeight, view: { width: innerWidth, height: innerHeight } };
  });
  const rendered = page => page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
  const distinct = (page, count) => page.evaluate(count => { for (let i = 0; i < count; i++) setTimeout(() => { throw new Error('distinct ' + i); }); }, count);

  // An embed host keeps its own look (the runtime loaded with data-review-dir): no top line. Its spool is named as
  // this service serves it, under the collection's prefix.
  {
    const context = await browser.newContext({ viewport: DESKTOP });
    const page = await context.newPage();
    await page.route(`${service}/qa-fixture/embed-host.html`, route => route.fulfill({ contentType: 'text/html', body:
      '<!doctype html><html><body><main>Host app</main><script defer src="/qa-fixture/docs/specs/.viz/runtime.js" data-review-dir="qa-fixture/docs/specs/report.spec.html.review"></script></body></html>' }));
    await page.goto(`${service}/qa-fixture/embed-host.html`);
    // The first refresh has read the spool: only a read writes its thread count (the boot status says connected only).
    await page.waitForFunction(() => /threads/.test(document.getElementById('hx-status')?.textContent || ''));
    // A later refresh too meets no error: the next events answer, and the status the runtime writes once it has read it.
    await Promise.all([
      page.waitForResponse(r => new URL(r.url()).pathname === '/api/events' && r.request().method() === 'GET'),
      page.evaluate(() => new Promise((done, fail) => {
        const late = setTimeout(() => fail(new Error('no later refresh wrote the status (#hx-status) within 5 s')), 5000);
        new MutationObserver((_, seen) => { seen.disconnect(); clearTimeout(late); done(); })
          .observe(document.getElementById('hx-status'), { childList: true });
      })),
    ]);
    const host = await page.evaluate(() => ({ line: document.querySelectorAll('.hx-top-line').length, padding: parseFloat(getComputedStyle(document.body).paddingTop),
      overlay: document.getElementById('hx-errors')?.textContent ?? null, status: document.getElementById('hx-status').textContent }));
    assert.match(host.status, /^connected .* threads$/, 'the embed host reads its spool');
    assert.equal(host.overlay, null, 'the embed host meets no error');
    // With neither a top line nor an overlay, the host's page does not move.
    assert.deepEqual([host.line, host.padding], [0, 0], 'an embed host gets no top line');
    // With no top line the overlay sits at the top of the page, and the host's page and the dock start under it.
    await distinct(page, 3);
    await page.waitForFunction(() => [...document.getElementById('hx-errors')?.children || []].filter(line => /distinct/.test(line.textContent)).length === 3);
    const at = await placed(page);
    assert.equal(at.errors.top, 0, 'the overlay at the top of the page');
    assert.equal(Math.round(at.space), Math.round(at.errors.bottom), 'the page starts right under it');
    assert.ok(at.host.top >= at.errors.bottom && at.dock.top >= at.errors.bottom, "the host's page and the dock under it");
    assert.ok(at.opener, "the dock's panel opener in reach");
    await context.close();
  }

  // file://, the primary transport, shows no top line either: on the spec page the overlay sits at the top, and the
  // title and the dock's panel opener start under it.
  {
    const context = await browser.newContext({ viewport: DESKTOP });
    const page = await context.newPage();
    await page.goto('file://' + join(qaRoot, 'site/qa-fixture', REPORT));
    await page.waitForSelector('#hx-dock-open');
    await distinct(page, 3);
    await page.waitForFunction(() => [...document.getElementById('hx-errors')?.children || []].filter(line => /distinct/.test(line.textContent)).length === 3);
    const at = await placed(page);
    assert.equal(at.line, null, 'no top line on file://');
    assert.equal(at.errors.top, 0, 'the overlay at the top of the page');
    assert.equal(Math.round(at.space), Math.round(at.errors.bottom), 'the page starts right under it');
    assert.ok(at.title.top >= at.errors.bottom && at.dock.top >= at.errors.bottom, 'the title and the dock under it');
    assert.ok(at.opener, "the dock's panel opener in reach");
    await context.close();
  }

  // The error overlay reads in the set's error colors from the first moment the runtime runs, also when the page fails
  // before its chrome mounts: here keepPlace throws as it arms its listeners.
  {
    const context = await browser.newContext({ viewport: DESKTOP });
    await context.addInitScript(() => {
      const add = EventTarget.prototype.addEventListener;
      EventTarget.prototype.addEventListener = function (type, ...rest) {
        if (this === window && type === 'wheel') { EventTarget.prototype.addEventListener = add; throw new Error('induced failure'); }
        return add.call(this, type, ...rest);
      };
    });
    const page = await context.newPage();
    await page.goto(`${service}/qa-fixture/${REPORT}`);
    await page.waitForSelector('#hx-errors');
    const shown = await page.evaluate(() => {
      const el = document.getElementById('hx-errors');
      return { text: el.textContent, chrome: !!document.querySelector('.hx-toolbar'), top: el.getBoundingClientRect().top,
        background: getComputedStyle(el).backgroundColor,
        color: getComputedStyle(el).color, error: getComputedStyle(document.documentElement).getPropertyValue('--ui-error').trim() };
    });
    assert.match(shown.text, /induced failure/, 'the failure shows');
    assert.equal(shown.chrome, false, 'before the chrome mounts');
    assert.equal(shown.top, 0, 'at the top of the page while no top line shows (#error-overlay)');
    assert.match(shown.error, /^#[0-9a-f]{6}$/i, "the set's colors are on the page");
    assert.equal(shown.background, rgb(shown.error), "on the set's error color, never transparent");
    assert.notEqual(shown.color, shown.background, 'in a text color of its own');
    await context.close();
  }

  // #acceptance-error-repeat: on a desktop, the same error on every refresh (each read of the review's events), and 6
  // seconds later the overlay under the top line shows it once, counting its times.
  {
    const context = await browser.newContext({ viewport: DESKTOP });
    await context.addInitScript(() => {
      const fetch = window.fetch;
      window.fetch = function (url, ...rest) {
        if (String(url).includes('/api/events')) Promise.reject(new TypeError('induced on every refresh'));
        return fetch.call(this, url, ...rest);
      };
    });
    const page = await context.newPage();
    await page.goto(`${service}/qa-1/${REPORT}`);
    await ready(page);
    await page.waitForTimeout(6000);
    let at = await placed(page);
    assert.equal(at.lines.length, 1, 'the error shows once: ' + JSON.stringify(at.lines));
    const [, times] = at.lines[0].match(/^rejection: TypeError: induced on every refresh \(×(\d+)\)$/) || [];
    assert.ok(Number(times) >= 3, 'with a count of the times it came: ' + at.lines[0]);
    assert.equal(Math.round(at.errors.top), Math.round(at.line.bottom), 'right under the top line');
    assert.deepEqual([at.errors.left, at.errors.right], [0, DESKTOP.width], 'across the page');
    assert.ok(at.header.top >= at.errors.bottom && at.dock.top >= at.errors.bottom, 'the document and the dock start under it');
    assert.equal(Math.round(at.space), Math.round(at.errors.bottom), 'the page starts right under it');
    assert.ok(!overlap(at.errors, at.toolbar), 'the toolbar stays in view');
    // Many distinct errors: at most a fifth of the screen tall, its lines scrolling within it.
    await distinct(page, 40);
    await page.waitForFunction(() => document.getElementById('hx-errors').children.length >= 41);
    at = await placed(page);
    assert.ok(at.errors.height <= at.view.height / 5 + 0.5 && at.scrolls, 'at most a fifth of the screen tall, scrolling within');
    assert.ok(at.header.top >= at.errors.bottom && !overlap(at.errors, at.toolbar), 'the page and the toolbar still in view');
    // A shorter screen: the capped overlay's height alone changes, and the page follows it, no gap and no overlap.
    await page.setViewportSize({ width: DESKTOP.width, height: 600 });
    await rendered(page);
    at = await placed(page);
    assert.ok(at.errors.height <= 600 / 5 + 0.5, 'at most a fifth of the shorter screen');
    assert.equal(Math.round(at.space), Math.round(at.errors.bottom), 'the page still starts right under it');
    // The open panel holds the hand-off bar on a desktop: the overlay ends beside it, as the top line does.
    await page.click('#hx-dock-open');
    await page.waitForSelector('.hx-panel.open #hx-handoff');
    at = await placed(page);
    assert.ok(at.errors.right <= at.panel.left && at.line.right <= at.panel.left, 'beside the open panel, never over it');
    await context.close();
  }

  // #acceptance-error-phone: on a phone, an error as the page opens: the overlay shows under the top line, and the bottom
  // toolbar shows its Hand off control in full.
  {
    const context = await browser.newContext({ viewport: PHONE, isMobile: true, hasTouch: true });
    await context.addInitScript(() => document.addEventListener('DOMContentLoaded', () => { throw new Error('induced as the page opens'); }));
    const page = await context.newPage();
    await page.goto(`${service}/qa-1/${REPORT}`);
    await ready(page);
    let at = await placed(page);
    assert.equal(at.lines.length, 1, 'the error shows: ' + JSON.stringify(at.lines));
    assert.match(at.lines[0], /^error: Uncaught Error: induced as the page opens @ /, 'the error as the page opened');
    assert.equal(Math.round(at.errors.top), Math.round(at.line.bottom), 'under the top line');
    assert.ok(at.header.top >= at.errors.bottom, 'the document starts under it');
    assert.ok(at.handoff && at.handoff.visible && at.handoff.top, 'the bottom toolbar shows its Hand off control');
    assert.ok(at.handoff.rect.top >= 0 && at.handoff.rect.bottom <= at.view.height && at.handoff.rect.left >= 0
      && at.handoff.rect.right <= at.view.width, 'in full');
    assert.match(at.handoff.text, /Hand off/, 'it reads Hand off: ' + at.handoff.text);
    assert.ok(!overlap(at.errors, at.toolbar) && !overlap(at.errors, at.handoff.rect), 'the overlay covers no part of the toolbar');
    // The open panel covers the page: the overlay hides under it as the top line does, so it covers no panel control.
    await page.click('#hx-dock-open');
    await page.waitForSelector('.hx-panel.open');
    const under = await page.evaluate(() => {
      const toolbar = document.querySelector('.hx-toolbar').getBoundingClientRect();
      return { shown: document.getElementById('hx-errors').checkVisibility(), covered: [...document.querySelectorAll('.hx-panel.open :is(button,a[href])')]
        .filter(control => control.checkVisibility()).map(control => [control, control.getBoundingClientRect()])
        .filter(([, box]) => box.top + box.height / 2 > 0 && box.top + box.height / 2 < toolbar.top)
        // the conversation opens at its bottom (threads #stream-context): a control scrolled out of its view is not covered
        .filter(([control, box]) => { const stream = control.closest('#hx-threads')?.getBoundingClientRect();
          return !stream || (box.top + box.height / 2 > stream.top && box.top + box.height / 2 < stream.bottom); })
        .filter(([control, box]) => !control.contains(document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2)))
        .map(([control]) => control.id || control.className || control.textContent) };
    });
    assert.equal(under.shown, false, 'the open panel hides the overlay');
    assert.deepEqual(under.covered, [], "every panel control above the toolbar in reach");
    // An error while it is hidden shows once the panel closes, the page right under it.
    await page.evaluate(() => setTimeout(() => { throw new Error('induced under the panel'); }));
    await page.waitForFunction(() => document.getElementById('hx-errors').children.length === 2);
    await page.click('#hx-panel-toggle');
    await page.waitForFunction(() => !document.querySelector('.hx-panel.open'));
    await rendered(page);
    at = await placed(page);
    assert.ok(at.shown && at.lines.length === 2, 'the overlay shows again, the new error on its own line: ' + JSON.stringify(at.lines));
    assert.equal(Math.round(at.errors.top), Math.round(at.line.bottom), 'under the top line');
    assert.equal(Math.round(at.space), Math.round(at.errors.bottom), 'the page starts right under it');
    await context.close();
  }

  assert.deepEqual(errors, [], 'no page errors');
} finally {
  await browser.close();
  try { process.kill(-server.pid); } catch {}
  rmSync(qaRoot, { recursive: true, force: true });
}
console.log('ok runtime spec surface');
