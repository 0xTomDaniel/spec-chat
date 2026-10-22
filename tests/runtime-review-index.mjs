import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { createServer } from 'node:net';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

// The review index as quiet rows (lane-hosting #index-design, #index-marks, #story-index-calm): rows grouped by lane,
// never a card, each spec led by its gate's status mark, its title in a column of its own beside its gate word, the
// gate word under the title on a phone, the set's focus ring, and the set's dark values. The QA fixture's collections
// (tests/fixtures/qa reset.sh + spools.py) on the review service and on review-plugged, whose evidence provider the
// fixture's evidence service answers, as qa.toml declares them, in headless Chromium.
// Needs PLAYWRIGHT_CORE=<path to playwright-core>.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_CORE || 'playwright-core');

// The set's status marks as the runtime holds them, read the one way scripts/design-set-sync and the review index read
// its regions (held()): each mark's shape, its colors aside.
const MARKS = JSON.parse(execFileSync('python3', ['-c', `import importlib.machinery, importlib.util, json, sys
from pathlib import Path
loader = importlib.machinery.SourceFileLoader("design_set_sync", sys.argv[1] + "/scripts/design-set-sync")
sync = importlib.util.module_from_spec(importlib.util.spec_from_loader("design_set_sync", loader))
loader.exec_module(sync)
path = "skill/review-spec/assets/viz/runtime.js"
print(json.dumps({Path(k).stem: v for k, v in sync.held(Path(sys.argv[1], path).read_text(), path, "marks").items()}))`, root], { encoding: 'utf8' }));
const shape = svg => [...svg.matchAll(/<(circle|path)\b([^>]*?)\/?>/g)]
  .map(([, tag, attrs]) => tag + ['cx', 'cy', 'r', 'd'].map(name => (attrs.match(new RegExp(` ${name}="([^"]*)"`)) || [])[1] || '').join(' '))
  .join('|');
const SHAPES = Object.fromEntries(Object.entries(MARKS).map(([name, svg]) => [shape(svg), name]));

const fixture = resolve(root, 'tests/fixtures/qa');
const qaRoot = mkdtempSync(join(tmpdir(), 'review-index-'));
const freePort = () => new Promise(res => { const s = createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); });
const [reviewPort, pluggedPort, evidencePort] = [await freePort(), await freePort(), await freePort()];
const evidence = `http://127.0.0.1:${evidencePort}`;
// Reset as capture does once the evidence service runs: its review facts answer and review-plugged's provider file.
const env = { ...process.env, QA_ROOT: qaRoot, QA_FIXTURE: fixture, QA_REPO_SPEC_CHAT: root, QA_URL_EVIDENCE: evidence, QA_STORY: 'story-index-calm' };
execFileSync('sh', [join(fixture, 'reset.sh')], { env, stdio: 'inherit' });
const servers = [];
const start = (args, state) => {
  const server = spawn('python3', args, {
    stdio: 'ignore', detached: true,
    env: { ...env, HOME: join(qaRoot, 'home'), XDG_STATE_HOME: join(qaRoot, state), XDG_CONFIG_HOME: join(qaRoot, 'config'), HERDR_SOCKET_PATH: '' },
  });
  servers.push(server);
};
const stop = () => { for (const server of servers) try { process.kill(-server.pid); } catch {} };
// Stop the servers on any exit, also when a step throws before the finally below.
process.on('exit', stop);
start(['-I', '-m', 'http.server', String(evidencePort), '--bind', '127.0.0.1', '--directory', join(qaRoot, 'evidence')], 'state');
start([join(fixture, 'serve.py'), join(qaRoot, 'serve/registry.toml'), String(reviewPort)], 'state');
start([join(fixture, 'serve.py'), join(qaRoot, 'serve-plugged/registry.toml'), String(pluggedPort)], 'state-plugged');
const review = `http://127.0.0.1:${reviewPort}`;
const plugged = `http://127.0.0.1:${pluggedPort}`;
for (const ready of [`${evidence}/`, `${review}/qa-fixture/docs/specs/report.spec.html`, `${plugged}/qa-8/docs/specs/later-only.spec.html`]) {
  for (let i = 0; ; i++) {
    try { if ((await fetch(ready)).ok) break; } catch {}
    if (i > 150) throw new Error('fixture service did not start: ' + ready);
    await new Promise(r => setTimeout(r, 100));
  }
}

const DESKTOP = { width: 1280, height: 800 };
const PHONE = { width: 390, height: 844 };
const NARROW = { width: 720, height: 800 };  // qa.toml story-index-calm: under 48rem, over the set's phone width
const LONG = 'Weekly report export to spreadsheets, with per-team totals, saved filters, and the chosen sort kept between visits';
const browser = await chromium.launch();
const errors = [];
const open = async (url, viewport, options = {}) => {
  const context = await browser.newContext({ viewport, ...options });
  const page = await context.newPage();
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto(url);
  return { page, close: () => context.close() };
};

// What the index shows: its rows, each with its lane, mark, title, gate word, and their boxes and colors; every text
// and every mark color with the ground it sits on; every border and background in the page.
const read = page => page.evaluate(() => {
  const css = el => getComputedStyle(el);
  const rect = el => el.getBoundingClientRect().toJSON();
  const shown = el => el.checkVisibility();
  const page = css(document.body).backgroundColor;
  const rows = [...document.querySelectorAll('li.spec')].map(li => {
    const title = li.querySelector('.title');
    const gate = li.querySelector('.gate');
    const mark = li.querySelector('.mark svg');
    return {
      lane: li.closest('section.lane')?.getAttribute('aria-label') || null, settled: !!li.closest('details'), shown: shown(li),
      title: title.textContent, gate: gate ? gate.textContent : null, gates: li.querySelectorAll('.gate').length,
      marks: li.querySelectorAll('svg').length, first: li.firstElementChild.className,
      mark: [...mark.querySelectorAll('circle, path')].map(el => ({ tag: el.tagName, cx: el.getAttribute('cx') || '',
        cy: el.getAttribute('cy') || '', r: el.getAttribute('r') || '', d: el.getAttribute('d') || '', stroke: css(el).stroke, fill: css(el).fill })),
      row: rect(li), titleBox: rect(title), gateBox: gate && rect(gate), markBox: rect(mark),
      lineHeight: parseFloat(css(title).lineHeight),
    };
  });
  // Every element that holds its own text, as shown.
  const texts = [];
  for (const el of document.querySelectorAll('main *')) {
    if (!shown(el) || ![...el.childNodes].some(node => node.nodeType === 3 && node.textContent.trim())) continue;
    texts.push({ text: el.textContent.trim(), color: css(el).color });
  }
  const borders = [];
  const grounds = [];
  for (const el of document.querySelectorAll('main, main *')) {
    if (el.closest('svg')) continue;
    for (const side of ['Top', 'Right', 'Bottom', 'Left'])
      if (parseFloat(css(el)['border' + side + 'Width'])) borders.push(el.tagName.toLowerCase() + '.' + el.className + ' ' + side);
    if (css(el).backgroundColor !== 'rgba(0, 0, 0, 0)') grounds.push(el.tagName.toLowerCase() + '.' + el.className);
  }
  return {
    page, ink: getComputedStyle(document.documentElement).getPropertyValue('--ui-ink').trim(), rows, texts, borders, grounds,
    lanes: [...document.querySelectorAll('section.lane')].map(lane => ({ key: lane.getAttribute('aria-label'), count: lane.querySelector('.count').textContent })),
    words: document.body.innerText,
    scroll: { width: document.documentElement.scrollWidth, client: document.documentElement.clientWidth },
  };
});
const channels = color => color.match(/[\d.]+/g).slice(0, 3).map(Number);
const luminance = color => {
  const [r, g, b] = channels(color).map(c => c / 255).map(c => c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const contrast = (one, two) => { const [hi, lo] = [luminance(one), luminance(two)].sort((a, b) => b - a); return (hi + 0.05) / (lo + 0.05); };
const markName = row => SHAPES[row.mark.map(el => el.tag + [el.cx, el.cy, el.r, el.d].join(' ')).join('|')];
// Text at 4.5:1 and every mark at 3:1 on the page; the pass mark's check on its own fill.
const readable = (s, label) => {
  for (const t of s.texts) assert.ok(contrast(t.color, s.page) >= 4.5, `${label}: "${t.text}" at 4.5:1 (${t.color} on ${s.page})`);
  for (const row of s.rows.filter(r => r.shown)) {
    const colors = [...new Set(row.mark.flatMap(el => [el.stroke, el.fill]).filter(c => c !== 'none'))];
    for (const color of colors) {
      const ground = color === s.page ? colors.find(other => other !== color) : s.page;
      assert.ok(contrast(color, ground) >= 3, `${label}: ${row.title}'s ${markName(row)} mark at 3:1 (${color} on ${ground})`);
    }
  }
};
// #acceptance-index-design, -design-phone: each lane row its gate word once beside its mark, each lane header its need
// count, no Up to date, no card border or stripe, and nothing scrolls sideways.
const designed = (s, label, { settled = true } = {}) => {
  const lane = s.rows.filter(r => r.lane);
  assert.ok(lane.length > 3 && s.rows.some(r => r.settled) === settled, `${label}: lane rows${settled ? ' and settled rows' : ''}`);
  for (const row of lane) {
    assert.equal(row.gates, 1, `${label}: ${row.lane} ${row.title} shows its gate word once`);
    assert.equal(row.first, 'mark', `${label}: ${row.title} leads with its mark`);
    assert.ok(markName(row), `${label}: ${row.title}'s mark is one of the set's`);
  }
  for (const { key, count } of s.lanes) assert.match(count, /^(1 needs you|\d+ need you|Nothing needs you)$/, `${label}: ${key} header counts what needs you`);
  assert.doesNotMatch(s.words, /Up to date/i, `${label}: Up to date shows nowhere`);
  assert.deepEqual(s.grounds, [], `${label}: no box or pill sits on its own background`);
  for (const border of s.borders)
    assert.ok(['h2. Bottom', 'li.spec Bottom', 'li.spec stale Bottom'].includes(border), `${label}: only hairlines under headings and rows, no card border or stripe: ${border}`);
  assert.ok(s.scroll.width <= s.scroll.client, `${label}: nothing scrolls sideways`);
  readable(s, label);
};

try {
  // Desktop: the gate word beside the title and its mark, on the title's first line.
  let desktopOrder;
  {
    const { page, close } = await open(`${review}/`, DESKTOP);
    const s = await read(page);
    designed(s, 'desktop');
    for (const row of s.rows.filter(r => r.lane)) {
      assert.ok(row.gateBox.left >= row.titleBox.right, `desktop: ${row.title}'s gate word sits beside its title`);
      assert.ok(row.gateBox.top < row.titleBox.top + row.lineHeight && row.markBox.top < row.titleBox.top + row.lineHeight,
        `desktop: ${row.title}'s mark and gate word sit on its first line`);
    }
    desktopOrder = s.rows.map(r => r.lane + ' ' + r.title);
    await close();
  }

  // #acceptance-index-design-phone: the gate word moves under its title, same order; every row a full-size touch target.
  {
    const { page, close } = await open(`${review}/`, PHONE, { isMobile: true, hasTouch: true });
    const s = await read(page);
    designed(s, 'phone');
    assert.deepEqual(s.rows.map(r => r.lane + ' ' + r.title), desktopOrder, 'phone: the same rows in the same order');
    for (const row of s.rows.filter(r => r.lane)) {
      assert.ok(row.gateBox.top >= row.titleBox.bottom - 1 && Math.abs(row.gateBox.left - row.titleBox.left) < 1,
        `phone: ${row.title}'s gate word sits under its title`);
      assert.ok(row.row.height >= 44, `phone: ${row.title}'s row is a full-size touch target (${row.row.height}px)`);
    }
    // The whole row opens its spec: a tap at its far corner, away from its title.
    const row = page.locator('section.lane[aria-label="QA-4"] li.spec');
    await row.scrollIntoViewIfNeeded();
    const box = await row.boundingBox();
    await row.tap({ position: { x: box.width - 4, y: box.height - 4 } });
    await page.waitForURL(/\/qa-4\/docs\/specs\/later-only\.spec\.html$/);
    await close();
  }

  // #acceptance-index-dark: light text on a dark page; every mark, gate word, count, and Changed readable.
  {
    const { page, close } = await open(`${review}/`, DESKTOP, { colorScheme: 'dark' });
    const s = await read(page);
    assert.ok(luminance(s.page) < 0.05 && luminance(s.texts.find(t => t.text === 'Weekly report').color) > 0.5, 'dark: light text on a dark page');
    const qa1 = s.texts.map(t => t.text);
    for (const word of ['3 open · 1 resolved · Changed', 'Decision open', 'Ready to accept', '4 need you']) assert.ok(qa1.includes(word), 'dark shows ' + word);
    designed(s, 'dark');
    await close();
  }

  // story-index-calm, in a narrow pane on review-plugged (qa.toml).
  {
    const { page, close } = await open(`${plugged}/`, NARROW);
    const s = await read(page);
    designed(s, 'narrow', { settled: false });  // review-plugged registers lanes only
    // #acceptance-index-title-column: the long title wraps in its own column, Ready to accept beside it, never under it.
    const long = s.rows.find(r => r.title === LONG);
    assert.equal(long.gate, 'Ready to accept');
    assert.ok(long.titleBox.height > 1.5 * long.lineHeight, 'narrow: the long title wraps');
    assert.ok(long.gateBox.left >= long.titleBox.right && long.gateBox.top < long.titleBox.top + long.lineHeight,
      'narrow: its gate word sits beside its first line, never under its text');
    // #acceptance-index-marks: the decision, the spec with the agent, and the accepted spec lead with the attention,
    // running, and pass marks, each differing in shape, each beside its gate word.
    const decision = s.rows.find(r => r.lane === 'QA-10' && r.title === 'Weekly report');
    const agent = s.rows.find(r => r.lane === 'QA-10' && r.title === 'Report filters');
    const accepted = s.rows.find(r => r.lane === 'QA-8');
    assert.deepEqual([decision.gate, agent.gate], ['Decision open', 'With the agent']);
    assert.match(accepted.gate, /^Accepted [0-9a-f]{7}$/);
    assert.deepEqual([decision, agent, accepted].map(markName), ['attention', 'running', 'pass']);
    assert.equal(new Set([decision, agent, accepted].map(r => JSON.stringify(r.mark.map(({ stroke, fill, ...geometry }) => geometry)))).size, 3, 'each differs in shape');
    for (const row of [decision, agent, accepted])
      assert.ok(row.gateBox.top < row.markBox.bottom && row.gateBox.bottom > row.markBox.top, `${row.title}'s gate word sits beside its mark`);

    // #acceptance-index-focus: Tab to the QA-10 Weekly report title; it shows the set's focus ring, the one the spec
    // page's buttons show.
    const ring = () => page.evaluate(() => {
      const el = document.activeElement;
      const css = getComputedStyle(el);
      return { text: el.textContent, lane: el.closest('section.lane')?.getAttribute('aria-label') || null,
        ring: [css.outlineStyle, css.outlineWidth, css.outlineColor, css.outlineOffset], visible: el.matches(':focus-visible') };
    });
    let focused;
    for (let i = 0; i < 40; i++) {
      await page.keyboard.press('Tab');
      focused = await ring();
      if (focused.lane === 'QA-10' && focused.text === 'Weekly report') break;
    }
    assert.equal(focused.text, 'Weekly report', 'Tab reaches the title');
    assert.ok(focused.visible, 'the title shows its keyboard focus');
    await close();

    const spec = await open(`${plugged}/qa-10/docs/specs/report.spec.html`, NARROW);
    await spec.page.waitForFunction(() => /connected/.test(document.getElementById('hx-status')?.textContent || ''));
    let button;
    for (let i = 0; i < 20; i++) {
      await spec.page.keyboard.press('Tab');
      button = await spec.page.evaluate(() => {
        const el = document.activeElement;
        const css = getComputedStyle(el);
        return { tag: el.tagName, ring: [css.outlineStyle, css.outlineWidth, css.outlineColor, css.outlineOffset] };
      });
      if (button.tag === 'BUTTON') break;
    }
    assert.equal(button.tag, 'BUTTON', 'Tab reaches a spec page button');
    assert.deepEqual(focused.ring, button.ring, "the title's focus ring is the one the spec page's buttons show");
    assert.equal(focused.ring[0], 'solid');
    await spec.close();
  }

  assert.deepEqual(errors, [], 'no page errors');
} finally {
  await browser.close();
  stop();
  rmSync(qaRoot, { recursive: true, force: true });
}
console.log('ok runtime review index');
