import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { createServer } from 'node:net';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

// The spec page as one surface (spec-page-design #one-surface, #top-line, #keys-icons; lane-hosting #index-link):
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
const open = async (collection, viewport, options = {}) => {
  const context = await browser.newContext({ viewport, ...options });
  const page = await context.newPage();
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto(`${service}/${collection}/${REPORT}`);
  await page.waitForFunction(() => /connected/.test(document.getElementById('hx-status')?.textContent || ''));
  await page.waitForFunction(() => /^Changes from /.test(document.getElementById('hx-range-copy')?.textContent || ''));
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

  // #acceptance-icons: on a spec page with threads, the panel toggle, gear, dock, and disclosure icons are line icons of
  // one size and stroke weight, and every status pill reads in sentence case. qa-1 holds a draft, a handed-off, a
  // replied, and a resolved thread (spools.py every_status).
  {
    const { page, close } = await open('qa-1', DESKTOP);
    await page.waitForSelector('.hx-dock-thread');
    const iconOf = selector => page.evaluate(sel => [...document.querySelectorAll(sel)].map(control => {
      const svgs = control.querySelectorAll('svg');
      // the dock's unread count is a badge beside the icon, not part of it
      const text = [...control.childNodes].filter(node => !node.matches?.('.hx-unread-badge')).map(node => node.textContent).join('').trim();
      return { svgs: svgs.length, text, size: svgs[0] && [svgs[0].getAttribute('width'), svgs[0].getAttribute('height')],
        stroke: svgs[0] && svgs[0].getAttribute('stroke-width'), color: svgs[0] && svgs[0].getAttribute('stroke') };
    }), selector);
    const LINE = { svgs: 1, text: '', size: ['16', '16'], stroke: '1.75', color: 'currentColor' };
    assert.deepEqual(await iconOf('.hx-dock-open'), [LINE], 'the dock holds the set line icon');
    await page.click('#hx-dock-open');
    await page.waitForSelector('.hx-panel.open #hx-threads .hx-thread');
    assert.deepEqual(await iconOf('#hx-panel-toggle'), [LINE], 'the panel toggle holds the set line icon');
    assert.deepEqual(await iconOf('#hx-panel-gear'), [LINE], 'the gear holds the set line icon');
    const disclosures = await iconOf('.hx-disclosure');
    assert.ok(disclosures.length >= 1, 'a resolved thread offers its disclosure');
    for (const disclosure of disclosures) assert.deepEqual(disclosure, LINE, 'the disclosure holds the set line icon');
    const pills = await page.evaluate(() => [...document.querySelectorAll('#hx-threads .hx-pill')]
      .map(pill => ({ shown: pill.innerText, transform: getComputedStyle(pill).textTransform })));
    assert.deepEqual(pills.map(p => p.shown).sort(), ['Acknowledged', 'Draft', 'Pending', 'Resolved'], 'every status pill reads in sentence case');
    for (const pill of pills) assert.equal(pill.transform, 'none', 'never capitals');
    // the toggle keeps one icon open or closed; its name says what it does
    await page.click('#hx-panel-toggle');
    assert.deepEqual(await iconOf('#hx-panel-toggle'), [LINE], 'the closed toggle keeps the icon');
    await page.click('#hx-dock-open');

    // lane-hosting #acceptance-index-link-wide: on a desktop screen the index link stays visible beside the open panel.
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
    assert.deepEqual(labels.map(l => l.id), ['hx-mode', 'hx-mobile-handoff', 'hx-reading'], 'the toolbar holds Comment, the hand-off, and Reading view');
    assert.deepEqual([labels[0].text, labels[2].text], ['Comment', 'Reading view'], 'they read Comment and Reading view');
    for (const label of labels) {
      assert.doesNotMatch(label.text, /\([A-Z]\)|⌘|Ctrl|Shift/, 'no key in a label: ' + label.text);
      assert.equal(label.title, null, 'no tooltip key on a phone: ' + label.text);
    }

    // lane-hosting #acceptance-index-link-panel, -panel-closed: on a narrow screen the open panel hides the index link;
    // closing it shows the link again.
    const linkShown = () => page.evaluate(() => document.querySelector('.hx-service-index-link').checkVisibility());
    await page.click('#hx-dock-open');
    await page.waitForSelector('.hx-panel.open');
    assert.equal(await linkShown(), false, 'the open panel hides the index link');
    await page.click('#hx-panel-toggle');
    assert.equal(await linkShown(), true, 'closing the panel shows it again');
    await close();
  }

  assert.deepEqual(errors, [], 'no page errors');
} finally {
  await browser.close();
  try { process.kill(-server.pid); } catch {}
  rmSync(qaRoot, { recursive: true, force: true });
}
console.log('ok runtime spec surface');
