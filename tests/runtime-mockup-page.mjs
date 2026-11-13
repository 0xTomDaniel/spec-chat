import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { cpSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import { listed } from './fixtures/listed.mjs';

// The mockup page's review in the runtime (mockups #review, #before-after, #walkthrough, #step-threads,
// #mockup-questions, #next-screen): a fixture spec whose mockup set holds a changed screen, a screen new since the base,
// an unchanged one, and screens with walkthroughs and questions, and specs whose sets hold three screens and one, served
// by the real review-serve.py, in headless Chromium on a desktop and a phone. Needs PLAYWRIGHT_CORE=<path to
// playwright-core>.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
assert.equal(readFileSync(resolve(root, 'docs/specs/.viz/runtime.js'), 'utf8'), runtime, 'runtime copies are identical');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_CORE || 'playwright-core');

const repo = mkdtempSync(join(tmpdir(), 'mockup-page-'));
const specs = join(repo, 'docs/specs');
const set = join(specs, 'mockups/plans');
mkdirSync(set, { recursive: true });
cpSync(resolve(root, 'docs/specs/.viz'), join(specs, '.viz'), { recursive: true });
cpSync(resolve(root, 'docs/specs/.style'), join(specs, '.style'), { recursive: true });
writeFileSync(join(specs, 'plans.spec.html'), `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Plans - spec</title>
<link rel="stylesheet" href="./.style/spec.css"><script defer src="./.viz/runtime.js"></script></head>
<body><article class="spec" data-spec-version="1">
<header data-anchor="header"><h1 data-anchor="title">Plans</h1></header>
<section data-anchor="acceptance"><h2>Acceptance criteria</h2>
<p data-anchor="acceptance-plan">The plan page shows the reviewer's plan.</p>
<p data-anchor="acceptance-upgrade">Upgrade moves the account to the next plan.</p>
<p data-anchor="acceptance-usage">The usage screen lists seats in use.</p>
</section></article></body></html>
`);
writeFileSync(join(set, 'avatar.svg'), '<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96"><circle cx="48" cy="48" r="48" fill="#7896ef"/></svg>\n');
// A mockup page as shaping writes one (mockups #markup-table): its own styles by the repo's tokens, two frames.
const STYLE = `[data-mockup-frame]{--ui-ink:#333333;--ui-page:#ffffff;--ui-action:#1d4ed8;--ui-border:#dfdfdf}
[data-mockup-frame]{box-sizing:border-box;background:var(--ui-page);color:var(--ui-ink);font:16px/1.5 system-ui,sans-serif;padding:32px}
[data-mockup-frame="desktop"]{width:1440px}
[data-mockup-frame="phone"]{width:390px;padding:16px}
[data-mockup-frame] button{min-height:32px;padding:8px 12px;border:1px solid var(--ui-border);border-radius:6px;background:var(--ui-page);color:var(--ui-ink)}
[data-mockup-frame] button:hover{background:#f5f5f5}
.bar{display:flex;justify-content:space-between;padding:16px 24px;background:var(--ui-ink);color:var(--ui-page)}
.card{margin-top:24px;padding:24px;min-height:260px;border:1px solid var(--ui-border);border-radius:12px}
.card button{font:inherit;padding:10px 20px;border:0;border-radius:8px;background:var(--ui-action);color:var(--ui-page)}
.card img{display:block;width:96px;height:96px;border-radius:50%}`;
const mockupPage = (title, desktop, phone) => `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>${title} - mockup</title>
<style>${STYLE}</style>
<script defer src="../../.viz/runtime.js"></script></head>
<body>
<section data-mockup-frame="desktop" data-anchor="desktop" data-illustrates="acceptance-plan">
<div class="bar" data-anchor="desktop-bar"><strong>Acme billing</strong><span>Ada Lovelace</span></div>
${desktop}
</section>
<section data-mockup-frame="phone" data-anchor="phone" data-illustrates="acceptance-plan">
${phone}
</section>
</body></html>
`;
const planPage = (price, extra = '') => mockupPage('Plan page',
  `<div class="card" data-anchor="desktop-plan" data-illustrates="acceptance-upgrade"><h2>Pro plan</h2><p>${price} per seat each month, billed yearly.</p>${extra}<button>Upgrade</button></div>`,
  `<div class="card" data-anchor="phone-plan"><img src="avatar.svg" alt="Ada Lovelace"><h2>Pro plan</h2><p>${price} per seat each month.</p>${extra}<button>Upgrade</button></div>`);
const usagePage = mockupPage('Usage on phone',
  '<div class="card" data-anchor="desktop-usage" data-illustrates="acceptance-usage"><h2>Seats in use</h2><p>12 of 20 seats.</p><button>Add seats</button></div>',
  '<div class="card" data-anchor="phone-usage"><h2>Seats in use</h2><p>12 of 20 seats.</p><button>Add seats</button></div>');
// A screen with a walkthrough (mockups #markup-steps, #markup-at): its style draws each step from data-at, the team
// panel fading in by its own motion; Open Payments reaches the second step and Export CSV the third (data-step-by).
// `asks` holds its questions (#markup-questions); `price` is what the lane changes.
const WALK_STYLE = `.panel{margin-top:16px;padding:24px;border:1px solid var(--ui-border);border-radius:12px;opacity:0;transition:opacity 200ms ease-out}
[data-at~="team-open"] .panel{opacity:1}
.dialog{display:none;margin-top:16px;padding:24px;border:1px solid var(--ui-border)}
[data-at~="export"] .dialog{display:block}
@media (prefers-reduced-motion: reduce){.panel{transition:none}}`;
const teamFrame = (frame, total) => `<div class="card" data-anchor="${frame}-card"><h2>Payments</h2><p>${total} this week</p><button data-anchor="${frame}-open">Open Payments</button></div>
<div class="panel" data-anchor="${frame}-panel"><h3>Last three weeks</h3><p>1,190 · 1,236 · ${total}</p><button data-anchor="${frame}-export">Export CSV</button></div>
<div class="dialog" data-anchor="${frame}-dialog"><p>payments-2026-W01.csv downloads.</p></div>`;
const STEPS = `<ol data-mockup-steps>
<li data-step="totals">Every team's total for the week</li>
<li data-step="team-open" data-step-by="desktop-open phone-open" data-illustrates="acceptance-upgrade">Payments opens its last three weeks</li>
<li data-step="export" data-step-by="desktop-export phone-export">The export dialog names the file</li>
</ol>`;
const QUESTIONS = `<aside data-mockup-questions>
<p data-anchor="question-trend" data-spec-tbd data-about="desktop-panel phone-panel" data-step="team-open">Should the panel show a trend line?
<button type="button" data-spec-choice="Totals only">Totals only (recommended)</button>
<button type="button" data-spec-choice="Add a trend line">Add a trend line</button></p>
<p data-anchor="question-file" data-spec-tbd data-about="desktop-dialog phone-dialog" data-step="export">Name the file after the team?
<button type="button" data-spec-choice="After the team">After the team</button>
<button type="button" data-spec-choice="After the week">After the week</button></p>
</aside>`;
const teamPage = (title, total, asks = '') => mockupPage(title, teamFrame('desktop', total), teamFrame('phone', total))
  .replace('</style>', WALK_STYLE + '</style>').replace('</body>', STEPS + asks + '\n</body>');
const SIX = ['totals', 'team-open', 'export', 'naming', 'downloading', 'downloaded'];
const sixPage = mockupPage('Export flow',
  '<div class="card" data-anchor="desktop-flow"><h2>Export</h2><button data-anchor="desktop-next">Next</button></div>',
  '<div class="card" data-anchor="phone-flow"><h2>Export</h2><button data-anchor="phone-next">Next</button></div>')
  .replace('</body>', '<ol data-mockup-steps>' + SIX.map(name => `<li data-step="${name}">The export is at ${name}</li>`).join('') + '</ol>\n</body>');
const invoicePage = mockupPage('Invoice list',
  '<div class="card" data-anchor="desktop-invoices"><h2>Invoices</h2><p>March 2026, $480, paid.</p><button>Download</button></div>',
  '<div class="card" data-anchor="phone-invoices"><h2>Invoices</h2><p>March 2026, $480, paid.</p><button>Download</button></div>');

// Next and previous screen (mockups #next-screen): Trips, a set of three screens, and Single, a set of one.
const screenPage = title => mockupPage(title,
  `<div class="card" data-anchor="desktop-card"><h2>${title}</h2><p>Lisbon to Porto, 2 travellers.</p><button>Continue</button></div>`,
  `<div class="card" data-anchor="phone-card"><h2>${title}</h2><p>Lisbon to Porto.</p><button>Continue</button></div>`);
const SETS = { trips: ['Arrivals', 'Search', 'Checkout'], single: ['Only screen'] };
for (const [name, screens] of Object.entries(SETS)) {
  writeFileSync(join(specs, name + '.spec.html'), readFileSync(join(specs, 'plans.spec.html'), 'utf8').replace('<title>Plans', '<title>' + name));
  mkdirSync(join(specs, 'mockups', name));
  screens.forEach((screen, i) => writeFileSync(join(specs, 'mockups', name, `0${i + 1}-${screen.toLowerCase().replace(/ /g, '-')}.mockup.html`), screenPage(screen)));
}

const git = (...args) => execFileSync('git', ['-C', repo, ...args], { env: { ...process.env, GIT_CONFIG_GLOBAL: '/dev/null', GIT_AUTHOR_NAME: 't', GIT_AUTHOR_EMAIL: 't@x', GIT_COMMITTER_NAME: 't', GIT_COMMITTER_EMAIL: 't@x' } });
git('init', '-q', '-b', 'main');
writeFileSync(join(set, '01-plan-page.mockup.html'), planPage('$20'));
writeFileSync(join(set, '03-invoice-list.mockup.html'), invoicePage);
writeFileSync(join(set, '04-team-panel.mockup.html'), teamPage('Team panel', '1,280'));
git('add', '-A');
git('commit', '-qm', 'base');
// The lane's change: the plan page changes, the usage screen is new, the invoice list stays as it was.
git('checkout', '-qb', 'lane');
writeFileSync(join(set, '01-plan-page.mockup.html'), planPage('$24'));
writeFileSync(join(set, '02-usage-on-phone.mockup.html'), usagePage);
writeFileSync(join(set, '04-team-panel.mockup.html'), teamPage('Team panel', '1,284'));
writeFileSync(join(set, '05-team-questions.mockup.html'), teamPage('Team questions', '1,284', QUESTIONS));
writeFileSync(join(set, '06-export-flow.mockup.html'), sixPage);
writeFileSync(join(set, '07-team-questions-phone.mockup.html'), teamPage('Team questions on phone', '1,284', QUESTIONS));
git('add', '-A');
git('commit', '-qm', 'lane');

const port = await new Promise(res => { const s = createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); });
const server = spawn('python3', [resolve(root, 'skill/review-spec/assets/review-serve.py'), join(repo, 'docs'), String(port)], {
  stdio: 'ignore', env: { ...process.env, HOME: join(repo, '.home'), XDG_STATE_HOME: join(repo, '.state'), XDG_CONFIG_HOME: join(repo, '.config'), HERDR_SOCKET_PATH: '' },
});
process.on('exit', () => server.kill());
const SET = `http://127.0.0.1:${port}/specs/mockups/plans/`;
const PLAN = SET + '01-plan-page.mockup.html', USAGE = SET + '02-usage-on-phone.mockup.html', INVOICES = SET + '03-invoice-list.mockup.html';
const TEAM = SET + '04-team-panel.mockup.html', ASKS = SET + '05-team-questions.mockup.html', SIXES = SET + '06-export-flow.mockup.html';
const ASKS_PHONE = SET + '07-team-questions-phone.mockup.html';
for (let i = 0; ; i++) {
  try { if ((await fetch(PLAN)).ok) break; } catch {}
  if (i > 100) throw new Error('review-serve did not start');
  await new Promise(r => setTimeout(r, 100));
}
const spoolEvents = (name, side = 'human', folder = set) => {
  const dir = join(folder, name + '.review', side);
  let names = [];
  try { names = readdirSync(dir).filter(n => n.endsWith('.json')).sort(); } catch {}
  return names.map(n => JSON.parse(readFileSync(join(dir, n), 'utf8')));
};
// A mockup's spool once it lists the comment saved with `text`.
const keptWith = (name, text, folder = set) => listed(() => spoolEvents(name, 'human', folder), events => events.some(e => e.text === text), text);

const browser = await chromium.launch();
const errors = [];
// A page once its first read of the threads answers: it reads `connected` as its loops start, and counts its threads
// only once that read answers.
const firstRead = page => page.waitForFunction(() => / threads/.test(document.getElementById('hx-status')?.textContent || ''));
const open = async (context, url) => {
  const page = await context.newPage();
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto(url);
  await firstRead(page);
  return page;
};
const rect = (page, target) => (typeof target === 'string' ? page.locator(target) : target).first().evaluate(el => { const r = el.getBoundingClientRect(); return { left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height }; });
const meets = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
const within = (inner, outer) => inner.left >= outer.left - 0.5 && inner.right <= outer.right + 0.5;
// The conversation's threads (threads #stream): each one's receipt, chip, and notice, and its messages as shown, in the
// conversation, or all of them while it shows focused (a pin opens its thread focused, threads #focus).
const threads = page => page.evaluate(() => [...document.querySelectorAll('#hx-threads .hx-entry[data-thread]')].map(t => ({
  status: t.querySelector('.hx-receipt')?.textContent,
  where: t.querySelector('.hx-chip')?.textContent,
  notice: t.querySelector('.hx-place-notice')?.textContent || null,
  messages: [...(document.querySelector('.hx-focused[data-thread="' + t.dataset.thread + '"] .hx-focused-stream') || t).querySelectorAll('.hx-msg')]
    .map(m => m.querySelector('.hx-bubble .hx-text')?.textContent),
})));
const save = async (page, text) => {
  await page.fill('.hx-composer textarea', text);
  await page.click('.hx-composer [data-act=save]');
  await page.waitForFunction(text => [...document.querySelectorAll('#hx-threads .hx-entry')].some(t => t.textContent.includes(text)), text);
};
// The pin of the thread whose first message is `text`.
const pinOf = async (page, text) => {
  const id = await page.evaluate(text => [...document.querySelectorAll('#hx-threads .hx-entry[data-thread]')]
    .find(t => t.querySelector(':scope > .hx-msg .hx-text')?.textContent === text)?.dataset.thread, text);
  assert.ok(id, 'thread ' + text + ' listed');
  return page.locator('.hx-pin[data-thread="' + id + '"]');
};
// mockups #review-chrome: exactly one Hand off, the drafts note, and no Accept spec, Next TBD, Jev, or evidence.
const oneControl = page => page.evaluate(() => {
  const shown = el => el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const buttons = [...document.querySelectorAll('button')].filter(shown).map(b => b.textContent.trim());
  return {
    handoff: buttons.filter(t => /^Hand off/.test(t)).length,
    drafts: document.getElementById('hx-drafts')?.textContent,
    others: buttons.filter(t => /Accept spec|Next TBD|TBD open|Reading view|Jev/i.test(t)),
    jev: document.querySelectorAll('.hx-jev-marker,.hx-jev-note,.hx-jev-pop,[class*="evidence"]').length,
  };
});
const noSideScroll = page => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
// mockups #walkthrough-bar: the step bar as shown: where it sits, Play, each step by number and name, the pressed one,
// each step's count, and the caption.
const stepBar = page => page.evaluate(() => {
  const bar = document.getElementById('hx-steps');
  const row = document.querySelector('#hx-range-bar');
  const button = el => [el.querySelector('.hx-step-n').textContent, el.querySelector('.hx-step-name').textContent].join(' ');
  const steps = [...bar.querySelectorAll('.hx-step[data-step]')];
  return {
    under: bar.parentElement.id === 'hx-top-line' && bar.getBoundingClientRect().top >= row.getBoundingClientRect().bottom - 0.5,
    play: document.getElementById('hx-play').textContent,
    playing: document.getElementById('hx-play').getAttribute('aria-pressed'),
    steps: steps.map(button),
    pressed: steps.filter(el => el.getAttribute('aria-pressed') === 'true').map(el => el.dataset.step),
    counts: Object.fromEntries(steps.map(el => [el.dataset.step, el.querySelector('.hx-step-count').hidden ? 0 : Number(el.querySelector('.hx-step-count').textContent)])),
    caption: document.getElementById('hx-step-caption').textContent,
  };
});
// #markup-at: each frame's data-at, desktop first.
const shownAt = page => page.evaluate(() => [...document.querySelectorAll('.hx-mockup-slot [data-mockup-frame]')].map(f => f.dataset.at));
const both = at => [at, at];
const pressed = async page => (await stepBar(page)).pressed;

try {
  const desktop = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  let page = await open(desktop, PLAN);

  // #acceptance-frames: the desktop frame and the phone frame side by side, desktop first, each labeled and whole, under
  // a top line holding the Review index back link and a link to the spec.
  const layout = await page.evaluate(() => {
    const slots = [...document.querySelectorAll('.hx-mockup-slot')];
    const box = el => { const r = el.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom }; };
    const line = document.getElementById('hx-top-line');
    return {
      labels: slots.map(s => s.querySelector('.hx-mockup-label').textContent),
      frames: slots.map(s => s.querySelector('[data-mockup-frame]')?.dataset.mockupFrame),
      slots: slots.map(box),
      shown: slots.map(s => box(s.querySelector('[data-mockup-frame]'))),
      links: [...line.querySelectorAll('a')].map(a => ({ text: a.textContent, href: a.href })),
    };
  });
  assert.deepEqual(layout.labels, ['Desktop', 'Phone']);
  assert.deepEqual(layout.frames, ['desktop', 'phone'], 'desktop first');
  assert.ok(layout.slots[0].right <= layout.slots[1].left, 'side by side');
  layout.shown.forEach((frame, i) => assert.ok(within(frame, layout.slots[i]) && frame.right <= 1280, 'each frame whole in its space'));
  assert.ok(layout.shown[0].right - layout.shown[0].left < 1440, 'the desktop frame is scaled down to fit');
  assert.deepEqual(layout.links.map(l => l.text), ['Review index', 'Plans spec'], 'back link, then the spec link');
  assert.equal(layout.links[1].href, `http://127.0.0.1:${port}/specs/plans.spec.html`);
  assert.ok(await noSideScroll(page), 'no horizontal scrolling on a desktop');
  // #review-chrome, #acceptance-before: the compared range, then Before and After, After pressed; the frames keep the
  // mockup's own look; no display settings, reading view, or badge.
  await page.waitForFunction(() => document.getElementById('hx-mockup-view')?.hidden === false
    && /^Changes from /.test(document.getElementById('hx-range-copy')?.textContent || ''));
  assert.equal(await page.getAttribute('#hx-mockup-after', 'aria-pressed'), 'true');
  assert.equal(await page.getAttribute('#hx-mockup-before', 'aria-pressed'), 'false');
  assert.equal(await page.locator('#hx-panel-gear, #hx-reading').count(), 0);
  assert.equal(await page.evaluate(() => getComputedStyle(document.querySelector('.card button')).backgroundColor), 'rgb(29, 78, 216)', "a frame keeps the mockup's own look");

  // #acceptance-one-control: a draft on a button, then the review panel: one Hand off, 1 draft, nothing else.
  await page.click('#hx-mode');
  await page.click('[data-anchor="desktop-plan"] button');
  await save(page, 'Upgrade reads as a link');
  if (!await page.locator('.hx-panel.open').count()) await page.click('#hx-dock-open');
  assert.deepEqual(await oneControl(page), { handoff: 1, drafts: '1 draft', others: [], jev: 0 });
  const [button] = await keptWith('01-plan-page.mockup.html', 'Upgrade reads as a link');
  assert.deepEqual([button.anchorId, button.target], ['desktop-plan', { type: 'element', key: 'button[1]' }]);
  assert.ok(meets(await rect(page, await pinOf(page, 'Upgrade reads as a link')), await rect(page, '[data-anchor="desktop-plan"] button')), 'the button shows a pin');
  // A pin among the frame's own rules for its buttons keeps the chrome's look, also hovered: none of them reaches it.
  const pinLook = async () => (await pinOf(page, 'Upgrade reads as a link')).evaluate(pin => {
    const s = getComputedStyle(pin);
    return { padding: s.padding, minHeight: s.minHeight, border: s.borderTopStyle, radius: s.borderTopLeftRadius, width: s.width, height: s.height, background: s.backgroundColor };
  });
  const PIN_LOOK = { padding: '0px', minHeight: '0px', border: 'none', radius: '50%', width: '24px', height: '24px', background: 'rgb(176, 84, 14)' };
  assert.deepEqual(await pinLook(), PIN_LOOK);
  await (await pinOf(page, 'Upgrade reads as a link')).hover();
  assert.deepEqual(await pinLook(), PIN_LOOK, 'hovered');

  // #acceptance-handoff
  await page.click('#hx-handoff');
  await page.waitForFunction(() => document.getElementById('hx-toast')?.textContent === 'Handed off 1 comment, agent notified');
  await page.waitForFunction(() => document.querySelector('#hx-threads .hx-entry .hx-receipt')?.textContent === 'Sent');
  const handoff = (await listed(() => spoolEvents('01-plan-page.mockup.html'), events => events.some(e => e.event === 'handoff'), 'the hand-off'))
    .find(e => e.event === 'handoff');
  assert.deepEqual(handoff.events, [button.id], 'a mockup hand-off lists its drafts');

  // #acceptance-comment-element: an image in the phone frame is a target of its own.
  await page.click('#hx-mode');
  await page.click('[data-anchor="phone-plan"] img');
  await save(page, 'Show initials when no photo');
  assert.ok(meets(await rect(page, await pinOf(page, 'Show initials when no photo')), await rect(page, '[data-anchor="phone-plan"] img')), 'the image shows a pin');
  assert.equal((await threads(page)).find(t => t.messages[0] === 'Show initials when no photo').status, 'Draft');
  assert.deepEqual((await keptWith('01-plan-page.mockup.html', 'Show initials when no photo')).at(-1).target, { type: 'element', key: 'img[1]' });

  // #acceptance-comment-region: an empty spot inside the card marks the card.
  await page.click('#hx-mode');
  const card = await rect(page, '[data-anchor="desktop-plan"]');
  await page.mouse.click(card.right - 6, card.bottom - 6);
  await save(page, 'Too much empty space');
  const cardPin = await rect(page, await pinOf(page, 'Too much empty space'));
  assert.ok(meets(cardPin, await rect(page, '[data-anchor="desktop-plan"]')), 'the card shows a pin');
  assert.equal((await threads(page)).find(t => t.messages[0] === 'Too much empty space').status, 'Draft');
  const region = (await keptWith('01-plan-page.mockup.html', 'Too much empty space')).at(-1);
  assert.deepEqual([region.anchorId, region.target], ['desktop-plan', null]);

  // #acceptance-reply: the owner's answer shows under the comment in its thread.
  execFileSync('sh', [resolve(root, 'skill/review-spec/scripts/emit-reply.sh'), join(set, '01-plan-page.mockup.html.review'), button.id, 'desktop-plan', JSON.stringify(button.target), 'acknowledged', 'mockup edited', 'Made it a filled button']);
  await page.waitForFunction(() => document.body.textContent.includes('Made it a filled button'), null, { timeout: 4000 });
  await (await pinOf(page, 'Upgrade reads as a link')).click();
  assert.deepEqual((await threads(page)).find(t => t.messages[0] === 'Upgrade reads as a link').messages, ['Upgrade reads as a link', 'Made it a filled button']);

  // #acceptance-agent-edit: within 5 seconds (plus a poll's slack) the banner, and the page keeps its screen until Reload.
  await page.waitForTimeout(1100); // the edit lands in a later second than the page's Last-Modified
  writeFileSync(join(set, '01-plan-page.mockup.html'), planPage('$24', '<button>Compare plans</button>'));
  await page.waitForFunction(() => /Mockup updated by agent/.test(document.getElementById('hx-banner')?.textContent || ''), null, { timeout: 6000 });
  assert.equal(await page.locator('#hx-banner button', { hasText: 'Reload' }).count(), 1);
  assert.equal(await page.locator('text=Compare plans').count(), 0, 'the page keeps the screen it loaded');
  // #acceptance-mark-follows: after Reload the pin sits on the same button, and its thread shows no change notice.
  await Promise.all([page.waitForNavigation(), page.click('#hx-banner button')]);
  await firstRead(page);
  await page.waitForFunction(() => document.querySelectorAll('.hx-pin').length === 3);
  const upgrade = await rect(page, '[data-anchor="desktop-plan"] button:has-text("Upgrade")');
  const compare = await rect(page, '[data-anchor="desktop-plan"] button:has-text("Compare plans")');
  const moved = await rect(page, await pinOf(page, 'Upgrade reads as a link'));
  assert.ok(meets(moved, upgrade) && !meets(moved, compare), 'the pin follows the button');
  assert.equal((await threads(page)).find(t => t.messages[0] === 'Upgrade reads as a link').notice, null, 'no change notice');

  // #acceptance-before: Before shows both frames at the base, with no pins and no comment control, Before pressed.
  await page.click('#hx-mockup-before');
  assert.equal(await page.getAttribute('#hx-mockup-before', 'aria-pressed'), 'true');
  const before = await page.evaluate(() => ({
    text: [...document.querySelectorAll('[data-mockup-frame]')].map(f => f.textContent.includes('$20') && !f.textContent.includes('$24')),
    pins: [...document.querySelectorAll('.hx-pin')].filter(p => p.getClientRects().length).length,
    comment: document.getElementById('hx-mode').getClientRects().length,
  }));
  assert.deepEqual(before, { text: [true, true], pins: 0, comment: 0 });
  await page.keyboard.press('c');
  assert.equal(await page.evaluate(() => document.body.classList.contains('hx-comment')), false, 'Before takes no comment');
  // #acceptance-after: After returns the current frames with the thread's pin, After pressed.
  await page.click('#hx-mockup-after');
  assert.equal(await page.getAttribute('#hx-mockup-after', 'aria-pressed'), 'true');
  const after = await page.evaluate(() => ({
    text: [...document.querySelectorAll('[data-mockup-frame]')].map(f => f.textContent.includes('$24')),
    pins: [...document.querySelectorAll('.hx-pin')].filter(p => p.getClientRects().length).length,
    comment: document.getElementById('hx-mode').getClientRects().length > 0,
  }));
  assert.deepEqual(after, { text: [true, true], pins: 3, comment: true });
  assert.ok(meets(await rect(page, await pinOf(page, 'Upgrade reads as a link')), upgrade), 'the pin is back on its button');
  // #before-base: after Change base the address names the new base, and the spec link opens the spec at that base.
  const main = git('rev-parse', 'main').toString().trim();
  await page.click('#hx-range-change');
  await page.fill('#hx-range-input', main);
  await page.click('#hx-range-apply');
  await page.waitForFunction(main => new URL(location.href).searchParams.get('base') === main, main);
  assert.equal(new URL(await page.getAttribute('#hx-spec-link', 'href')).searchParams.get('base'), main, 'the spec link names the new base');
  await page.close();

  // #acceptance-chip-open: arriving at a part of the screen shows it in view and ringed.
  page = await open(desktop, PLAN + '#phone-plan');
  const arrived = await page.evaluate(() => {
    const el = document.querySelector('[data-anchor="phone-plan"]');
    const r = el.getBoundingClientRect();
    return { ringed: el.classList.contains('hx-tbd-open') && getComputedStyle(el).outlineStyle === 'solid', inView: r.top >= 0 && r.top < window.innerHeight };
  });
  assert.deepEqual(arrived, { ringed: true, inView: true });
  await page.close();

  // #acceptance-new: absent at the base, the top line reads New since the base and offers no Before.
  page = await open(desktop, USAGE);
  await page.waitForFunction(() => document.getElementById('hx-range-copy')?.textContent === 'New since the base');
  assert.equal(await page.locator('#hx-mockup-before:visible').count(), 0);
  await page.close();
  // #acceptance-unchanged: the same at the base, the compared range and no Before.
  page = await open(desktop, INVOICES);
  await page.waitForFunction(() => /^Changes from /.test(document.getElementById('hx-range-copy')?.textContent || '') && document.getElementById('hx-mockup-view'));
  assert.equal(await page.locator('#hx-mockup-before:visible').count(), 0);
  await page.close();

  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  // #acceptance-frames-phone: one above the other, each whole within the screen's width, no horizontal scrolling.
  page = await open(phone, PLAN);
  const stacked = await page.evaluate(() => [...document.querySelectorAll('.hx-mockup-slot')].map(s => {
    const r = s.querySelector('[data-mockup-frame]').getBoundingClientRect();
    return { frame: s.dataset.viewport, left: r.left, right: r.right, top: r.top, bottom: r.bottom };
  }));
  assert.deepEqual(stacked.map(s => s.frame), ['desktop', 'phone']);
  assert.ok(stacked[0].bottom <= stacked[1].top, 'one above the other');
  stacked.forEach(s => assert.ok(s.left >= 0 && s.right <= 390, s.frame + ' frame whole within the screen'));
  assert.ok(await noSideScroll(page), 'no horizontal scrolling on a phone');
  await page.close();

  // #acceptance-one-control-phone
  page = await open(phone, INVOICES);
  await page.tap('#hx-mode');
  await page.tap('[data-anchor="desktop-invoices"] button');
  await save(page, 'Download needs a format');
  assert.deepEqual(await oneControl(page), { handoff: 1, drafts: '1 draft', others: [], jev: 0 });
  assert.ok(await noSideScroll(page));
  await page.close();

  // #acceptance-comment-phone: the desktop frame's button, on a phone; the thread within the screen's width.
  page = await open(phone, USAGE);
  await page.tap('#hx-mode');
  await page.tap('[data-anchor="desktop-usage"] button');
  await save(page, 'Add seats goes first');
  assert.ok(meets(await rect(page, await pinOf(page, 'Add seats goes first')), await rect(page, '[data-anchor="desktop-usage"] button')), 'the button shows a pin');
  const card2 = await rect(page, '#hx-threads .hx-entry');
  assert.equal((await threads(page))[0].status, 'Draft');
  assert.ok(card2.left >= 0 && card2.right <= 390, 'the thread within the screen width');
  assert.deepEqual((await keptWith('02-usage-on-phone.mockup.html', 'Add seats goes first')).at(-1).target, { type: 'element', key: 'button[1]' });
  await page.close();

  // Walkthroughs (mockups #walkthrough). #walkthrough-none: a mockup with no walkthrough shows no step bar.
  page = await open(desktop, PLAN);
  assert.equal(await page.locator('#hx-steps').count(), 0, 'no walkthrough, no step bar');
  await page.close();
  // #acceptance-steps-bar: under the top line, Play and the three steps by number and name, the first pressed with its
  // caption, and both frames at the first step as the mockup's style draws it; the steps list shows in the chrome alone.
  page = await open(desktop, TEAM);
  assert.deepEqual(await stepBar(page), { under: true, play: 'Play', playing: 'false', steps: ['1 Totals', '2 Team open', '3 Export'],
    pressed: ['totals'], counts: { totals: 0, 'team-open': 0, export: 0 }, caption: "Every team's total for the week" });
  assert.deepEqual(await shownAt(page), both('totals'));
  assert.equal(await page.evaluate(() => getComputedStyle(document.querySelector('[data-anchor="desktop-panel"]')).opacity), '0', 'the first step as the mockup draws it');
  assert.equal(await page.locator('[data-mockup-steps]').isVisible(), false, 'the walkthrough shows in the step bar, not as the page');
  const line = await rect(page, '#hx-top-line'), frames = await rect(page, '.hx-mockup');
  assert.ok(frames.top >= line.bottom - 0.5, 'the frames start under the top line and its step bar');
  assert.ok(await noSideScroll(page));
  // #acceptance-step-jump: the last step pressed shows it in both frames, pressed with its caption; the address names it.
  await page.click('.hx-step[data-step="export"]');
  assert.deepEqual(await shownAt(page), both('totals team-open export'));
  assert.deepEqual((({ pressed, caption }) => ({ pressed, caption }))(await stepBar(page)), { pressed: ['export'], caption: 'The export dialog names the file' });
  assert.equal(await page.evaluate(() => getComputedStyle(document.querySelector('[data-anchor="desktop-dialog"]')).display), 'block', 'the step as the mockup draws it');
  assert.equal(new URL(page.url()).searchParams.get('step'), 'export', 'the address names the step showing');
  // #acceptance-play: Play shows every step in order from the first, holding each, then stops on the last and offers
  // Play again.
  await page.evaluate(() => {
    window.seenAt = [];
    new MutationObserver(() => window.seenAt.push(document.querySelector('[data-mockup-frame="desktop"]').dataset.at))
      .observe(document.querySelector('[data-mockup-frame="desktop"]'), { attributes: true, attributeFilter: ['data-at'] });
  });
  await page.click('#hx-play');
  assert.equal((await stepBar(page)).playing, 'true');
  await page.waitForTimeout(600);
  assert.deepEqual(await shownAt(page), both('totals'), 'the first step is held');
  await page.waitForFunction(() => document.getElementById('hx-play').getAttribute('aria-pressed') === 'false', null, { timeout: 15000 });
  assert.deepEqual(await page.evaluate(() => window.seenAt), ['totals', 'totals team-open', 'totals team-open export'], 'every step in order from the first');
  assert.deepEqual(await shownAt(page), both('totals team-open export'));
  assert.deepEqual(await pressed(page), ['export']);
  assert.ok(await page.isVisible('#hx-play') && await page.isEnabled('#hx-play'), 'Play is offered again');
  // Pressing a step, or turning on comment mode, stops it.
  await page.click('#hx-play');
  await page.click('.hx-step[data-step="totals"]');
  assert.equal((await stepBar(page)).playing, 'false', 'a step pressed stops Play');
  await page.click('#hx-play');
  await page.click('#hx-mode');
  assert.equal((await stepBar(page)).playing, 'false', 'comment mode stops Play');
  await page.click('#hx-mode');
  await page.waitForTimeout(2200);
  assert.deepEqual(await shownAt(page), both('totals'), 'a stopped walkthrough stays where it was');
  // #acceptance-step-press: outside comment mode, Open Payments in the desktop frame shows the second step in both frames.
  await page.click('[data-anchor="desktop-open"]');
  assert.deepEqual(await shownAt(page), both('totals team-open'));
  assert.deepEqual(await pressed(page), ['team-open']);
  // #acceptance-step-thread: at the second step, a comment on the card carries that step; its chip names the card and step.
  await page.click('#hx-mode');
  await page.click('[data-anchor="desktop-open"]');
  assert.deepEqual(await pressed(page), ['team-open'], 'in comment mode a part a step names is a comment target');
  await page.click('.hx-composer [data-act=cancel]');
  await page.click('#hx-mode');
  const teamCard = await rect(page, '[data-anchor="desktop-card"]');
  await page.mouse.click(teamCard.right - 6, teamCard.bottom - 6);
  await save(page, 'Show the week number');
  assert.equal((await threads(page)).find(t => t.messages[0] === 'Show the week number').where, 'Desktop card · Step 2 Team open');
  const atStep = (await keptWith('04-team-panel.mockup.html', 'Show the week number')).at(-1);
  assert.deepEqual([atStep.anchorId, atStep.step], ['desktop-card', 'team-open'], 'the comment carries the step its frames showed');
  assert.ok(meets(await rect(page, await pinOf(page, 'Show the week number')), teamCard), 'its pin on the card at its step');
  // #acceptance-step-pin: at the first step the card shows no pin of that thread, and the second step counts 1.
  await page.click('.hx-step[data-step="totals"]');
  assert.equal(await page.locator('.hx-pin[data-thread="' + atStep.id + '"]').count(), 0, 'no pin at another step');
  assert.deepEqual((({ pressed, counts }) => ({ pressed, counts }))(await stepBar(page)), { pressed: ['totals'], counts: { totals: 0, 'team-open': 1, export: 0 } });
  await page.close();
  // #acceptance-step-chip: opened again, the thread's chip moves both frames to the second step, the card ringed with its pin.
  page = await open(desktop, TEAM);
  assert.deepEqual(await shownAt(page), both('totals'));
  if (!await page.locator('.hx-panel.open').count()) await page.click('#hx-dock-open');
  await page.click('#hx-threads .hx-entry[data-thread] .hx-chip');
  assert.deepEqual(await shownAt(page), both('totals team-open'));
  assert.deepEqual(await pressed(page), ['team-open']);
  await page.waitForFunction(() => {
    const ring = document.querySelector('.hx-thread-ring'), card = document.querySelector('[data-anchor="desktop-card"]');
    const r = ring && !ring.hidden && ring.getBoundingClientRect(), c = card.getBoundingClientRect();
    return r && r.left <= c.left + 1 && r.right >= c.right - 1 && r.top <= c.top + 1 && r.bottom >= c.bottom - 1;
  }, null, { timeout: 4000 });
  assert.ok(meets(await rect(page, await pinOf(page, 'Show the week number')), await rect(page, '[data-anchor="desktop-card"]')), 'the card shows the thread\'s pin');
  // #step-threads-before: Before and After keep the step showing when the other version holds it.
  await page.waitForFunction(() => document.getElementById('hx-mockup-view')?.hidden === false);
  await page.click('#hx-mockup-before');
  assert.deepEqual(await shownAt(page), both('totals team-open'), 'Before keeps the step');
  assert.ok((await page.textContent('[data-mockup-frame="desktop"]')).includes('1,280'), 'the frames as they were at the base');
  assert.deepEqual(await pressed(page), ['team-open']);
  await page.click('#hx-mockup-after');
  assert.deepEqual(await shownAt(page), both('totals team-open'), 'After keeps the step');
  await page.close();
  // #walkthrough-address: a page whose address names a step opens at it.
  page = await open(desktop, TEAM + '?step=export');
  assert.deepEqual(await shownAt(page), both('totals team-open export'));
  assert.deepEqual(await pressed(page), ['export']);
  await page.close();

  // Questions on the mockup (mockups #mockup-questions). #acceptance-question-mark: at the second step a question mark on
  // the panel the question is about, and the second step counts 1.
  page = await open(desktop, ASKS);
  assert.equal(await page.locator('.hx-qmark').count(), 0, 'no question asked at the first step');
  await page.click('.hx-step[data-step="team-open"]');
  assert.equal(await page.locator('[data-anchor="desktop-panel"] > .hx-qmark').count(), 1, 'the panel shows a question mark');
  assert.ok(await page.isVisible('[data-anchor="desktop-panel"] > .hx-qmark'));
  // The question mark keeps the chrome's look among the frame's button rules, also hovered: its size to the pixel, as
  // zoomed back out of the frame's zoom it falls a layout unit (1/64 px) short at some zooms.
  const markLook = () => page.$eval('[data-anchor="desktop-panel"] > .hx-qmark', mark => {
    const s = getComputedStyle(mark), px = size => Math.round(parseFloat(size)) + 'px';
    return { padding: s.padding, minHeight: s.minHeight, border: s.borderTopStyle, width: px(s.width), height: px(s.height), background: s.backgroundColor };
  });
  const MARK_LOOK = { padding: '0px', minHeight: '0px', border: 'none', width: '24px', height: '24px', background: 'rgb(176, 84, 14)' };
  assert.deepEqual(await markLook(), MARK_LOOK);
  await page.hover('[data-anchor="desktop-panel"] > .hx-qmark');
  assert.deepEqual(await markLook(), MARK_LOOK, 'hovered');
  assert.deepEqual((await stepBar(page)).counts, { totals: 0, 'team-open': 1, export: 1 });
  assert.equal(await page.locator('[data-mockup-questions]').isVisible(), false, 'the questions show in the conversation, not as the page');
  // #acceptance-question-next: from the first step, Next TBD reaches the question at the second step: both frames there,
  // the panel ringed, and the review panel shows that question alone with its answer buttons.
  await page.click('.hx-step[data-step="totals"]');
  if (!await page.locator('.hx-panel.open').count()) await page.click('#hx-dock-open');
  assert.equal(await page.textContent('#hx-next-tbd'), 'Next TBD (2)', 'Next TBD shows while a question is open');
  await page.click('#hx-next-tbd');
  await page.waitForSelector('.hx-focused[data-question="question-trend"]');
  assert.deepEqual(await shownAt(page), both('totals team-open'));
  assert.equal(await page.evaluate(() => {
    const part = document.querySelector('[data-anchor="desktop-panel"]');
    return part.classList.contains('hx-asked') && getComputedStyle(part).outlineStyle;
  }), 'solid', 'the panel ringed');
  const focused = () => page.evaluate(() => {
    const card = document.querySelector('.hx-focused');
    return { questions: card.querySelectorAll('.hx-question').length, text: card.querySelector('.hx-question .hx-bubble .hx-text').textContent,
      choices: [...card.querySelectorAll('.hx-question > .hx-msg .hx-choice')].map(c => [c.textContent, c.getAttribute('aria-pressed')]),
      chip: card.querySelector('.hx-question > .hx-chip').textContent,
      answers: [...card.querySelectorAll('.hx-entry[data-thread] .hx-bubble')].map(b => [b.querySelector('.hx-text').textContent, b.hasAttribute('data-draft')]) };
  });
  assert.deepEqual(await focused(), { questions: 1, text: 'Should the panel show a trend line?', chip: 'Desktop panel · Step 2 Team open',
    choices: [['Totals only (recommended)', 'false'], ['Add a trend line', 'false'], ['Reply', null]], answers: [] });
  // #acceptance-mockup-question-answer: an answer button chosen, the answer under the question as a draft, 1 draft.
  await page.click('.hx-focused .hx-choice:has-text("Totals only")');
  await page.waitForFunction(() => document.querySelector('.hx-focused .hx-choice[aria-pressed="true"]')?.textContent === 'Totals only (recommended)');
  assert.deepEqual((await focused()).answers, [['Totals only', true]]);
  assert.equal(await page.textContent('#hx-drafts'), '1 draft');
  const answered = (await keptWith('05-team-questions.mockup.html', 'Totals only')).at(-1);
  assert.deepEqual([answered.anchorId, answered.target, answered.text, answered.step], ['question-trend', { type: 'element', key: 'button[1]' }, 'Totals only', 'team-open']);
  // #acceptance-question-words: Next TBD to the next question, its Reply, an answer in words under it as a draft.
  await page.click('#hx-next-tbd');
  await page.waitForSelector('.hx-focused[data-question="question-file"]');
  assert.deepEqual(await shownAt(page), both('totals team-open export'));
  await page.click('.hx-focused .hx-choice[data-act="answer"]');
  await save(page, 'After the week, with its number');
  assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('#hx-threads .hx-question[data-question="question-file"] .hx-entry[data-thread] .hx-bubble')]
    .map(b => [b.querySelector('.hx-text').textContent, b.hasAttribute('data-draft')])), [['After the week, with its number', true]]);
  assert.equal(await page.textContent('#hx-drafts'), '2 drafts');
  assert.deepEqual((await oneControl(page)).handoff, 1, 'questions add no hand-off control');
  await page.close();

  // #acceptance-steps-phone: six steps on a phone; the bar scrolled sideways within itself, the last step tapped: both
  // frames at it within the screen's width, the page never scrolled sideways, each step a 44 px target.
  page = await open(phone, SIXES);
  const phoneBar = await page.evaluate(() => {
    const bar = document.getElementById('hx-steps');
    return { scrolls: bar.scrollWidth > bar.clientWidth, width: bar.getBoundingClientRect().width,
      first: bar.firstElementChild.id, targets: [...bar.querySelectorAll('.hx-step')].map(b => b.getBoundingClientRect().height >= 44) };
  });
  assert.deepEqual(phoneBar, { scrolls: true, width: phoneBar.width, first: 'hx-play', targets: Array(7).fill(true) });
  assert.ok(phoneBar.width <= 390, 'the bar takes the screen\'s width');
  await page.evaluate(() => { const bar = document.getElementById('hx-steps'); bar.scrollLeft = bar.scrollWidth; }); // the swipe
  await page.tap('.hx-step[data-step="downloaded"]');
  assert.deepEqual(await shownAt(page), both(SIX.join(' ')));
  assert.deepEqual(await pressed(page), ['downloaded']);
  const lastStep = await rect(page, '.hx-step[data-step="downloaded"]');
  assert.ok(lastStep.left >= 0 && lastStep.right <= 390, 'the last step within the screen');
  for (const frame of await page.evaluate(() => [...document.querySelectorAll('[data-mockup-frame]')].map(f => { const r = f.getBoundingClientRect(); return [r.left, r.right]; }))) {
    assert.ok(frame[0] >= 0 && frame[1] <= 390, 'each frame within the screen\'s width');
  }
  assert.ok(await noSideScroll(page) && await page.evaluate(() => window.scrollX === 0), 'the page has not scrolled sideways');
  await page.close();
  // #acceptance-question-phone: Next TBD, then an answer button: the panel ringed above a sheet holding the question with
  // that button chosen, within the screen's width, and the bottom toolbar shows the one Hand off.
  page = await open(phone, ASKS_PHONE);
  await page.tap('.hx-toolbar #hx-next-tbd');
  await page.waitForSelector('.hx-focused.hx-sheet[data-question="question-trend"]');
  await page.tap('.hx-sheet .hx-choice:has-text("Add a trend line")');
  await page.waitForFunction(() => document.querySelector('.hx-sheet .hx-choice[aria-pressed="true"]')?.textContent === 'Add a trend line');
  await page.waitForFunction(() => {
    const sheet = document.querySelector('.hx-sheet').getBoundingClientRect();
    const part = document.querySelector('.hx-asked');
    const r = part && part.getBoundingClientRect();
    return r && getComputedStyle(part).outlineStyle === 'solid' && r.top >= 0 && r.bottom <= sheet.top;
  }, null, { timeout: 4000 });
  const sheetBox = await rect(page, '.hx-sheet');
  assert.ok(sheetBox.left >= 0 && sheetBox.right <= 390, 'the sheet within the screen\'s width');
  assert.deepEqual(await page.evaluate(() => {
    const shown = el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
    const handoffs = [...document.querySelectorAll('button')].filter(b => shown(b) && /^Hand off/.test(b.textContent.trim()));
    const r = handoffs[0].getBoundingClientRect();
    return { count: handoffs.length, inToolbar: Boolean(handoffs[0].closest('.hx-toolbar')), onTop: document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) === handoffs[0] };
  }), { count: 1, inToolbar: true, onTop: true }, 'the bottom toolbar shows the one Hand off');
  assert.ok(await noSideScroll(page));
  await page.close();

  // Next and previous screen (mockups #next-screen) on Trips' three screens, in the order of its Screens index.
  const TRIPS = `http://127.0.0.1:${port}/specs/mockups/trips/`;
  const ARRIVALS = TRIPS + '01-arrivals.mockup.html', SEARCH = TRIPS + '02-search.mockup.html', CHECKOUT = TRIPS + '03-checkout.mockup.html';
  const SINGLE = `http://127.0.0.1:${port}/specs/mockups/single/01-only-screen.mockup.html`;
  const pagerOf = async page => {
    await page.waitForSelector('#hx-pager');
    return page.evaluate(() => {
      const box = el => { const r = el.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height }; };
      const control = el => ({ disabled: el.disabled, label: el.getAttribute('aria-label'), title: el.getAttribute('title') });
      const pager = document.getElementById('hx-pager');
      return { place: pager.querySelector('.hx-pager-place').textContent, previous: control(document.getElementById('hx-pager-previous')),
        next: control(document.getElementById('hx-pager-next')), inLine: Boolean(pager.closest('#hx-top-line')), box: box(pager),
        link: box(document.getElementById('hx-spec-link')), copy: box(document.getElementById('hx-range-copy')),
        row: box(pager.parentElement), buttons: [...pager.querySelectorAll('button')].map(box),
        view: document.getElementById('hx-mockup-view')?.hidden === false ? [...document.querySelectorAll('#hx-mockup-view button')].map(box) : null };
    });
  };
  const stays = async (page, url) => { await page.waitForTimeout(400); assert.equal(new URL(page.url()).pathname, new URL(url).pathname, 'the same mockup page still shows'); };
  // The Screens index on the spec page lists the set in the order the pager reads.
  page = await open(desktop, `http://127.0.0.1:${port}/specs/trips.spec.html`);
  await page.waitForSelector('#hx-screens');
  assert.deepEqual(await page.$$eval('#hx-screens .hx-screen', rows => rows.map(r => [r.querySelector('.hx-screen-name').textContent, r.href])),
    [['Arrivals', ARRIVALS], ['Search', SEARCH], ['Checkout', CHECKOUT]]);
  await page.close();
  // #acceptance-next-screen: on the middle screen, 2 of 3 between Previous and Next after the spec link, each naming its
  // screen and key in its tooltip; Next opens the screen after it, 3 of 3, with Next disabled, at the base this page
  // compares (#next-screen-press).
  page = await open(desktop, SEARCH);
  let shown = await pagerOf(page);
  assert.deepEqual([shown.place, shown.previous, shown.next], ['2 of 3',
    { disabled: false, label: 'Previous screen, Arrivals', title: 'Previous screen: Arrivals (←)' },
    { disabled: false, label: 'Next screen, Checkout', title: 'Next screen: Checkout (→)' }]);
  assert.ok(shown.inLine && shown.box.left >= shown.link.right && shown.box.right <= shown.copy.left, 'in the top line after the spec link, before the range');
  const lane = git('rev-parse', 'lane').toString().trim();
  await page.click('#hx-range-change');
  await page.fill('#hx-range-input', lane);
  await page.click('#hx-range-apply');
  await page.waitForFunction(lane => new URL(location.href).searchParams.get('base') === lane, lane);
  await page.click('#hx-pager-next');
  await page.waitForURL(url => url.pathname.endsWith('/03-checkout.mockup.html'));
  assert.equal(new URL(page.url()).searchParams.get('base'), lane, 'at the base the page compared');
  shown = await pagerOf(page);
  assert.deepEqual([shown.place, shown.previous.disabled, shown.next], ['3 of 3', false, { disabled: true, label: 'Next screen', title: null }]);
  assert.equal(await page.textContent('[data-anchor="desktop-card"] h2'), 'Checkout');
  await page.close();
  // #acceptance-previous-key: on the last screen the Left arrow key opens the one before it, 2 of 3; Right on the last
  // opens none, and a modifier key with an arrow changes no screen (#next-screen-keys).
  page = await open(desktop, CHECKOUT);
  await pagerOf(page);
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('Shift+ArrowLeft');
  await page.keyboard.press('Control+ArrowLeft');
  await stays(page, CHECKOUT);
  await page.keyboard.press('ArrowLeft');
  await page.waitForURL(SEARCH);
  assert.equal((await pagerOf(page)).place, '2 of 3');
  // #next-screen-keys: in the base picker and on a video the arrow keys keep their own work.
  await page.click('#hx-range-change');
  await page.focus('#hx-range-input');
  await page.keyboard.press('ArrowLeft');
  await page.click('#hx-range-change');
  await page.evaluate(() => {
    const video = document.querySelector('[data-anchor="desktop-card"]').appendChild(document.createElement('video'));
    video.tabIndex = 0;
    video.focus();
  });
  await page.keyboard.press('ArrowRight');
  await stays(page, SEARCH);
  await page.close();
  // #acceptance-next-screen-comment: Left in the open comment box moves in the comment, and the page stays.
  page = await open(desktop, SEARCH);
  await pagerOf(page);
  await page.click('#hx-mode');
  await page.click('[data-anchor="desktop-card"] button');
  await page.fill('.hx-composer textarea', 'Name the travellers');
  await page.keyboard.press('ArrowLeft');
  await stays(page, SEARCH);
  assert.equal(await page.inputValue('.hx-composer textarea'), 'Name the travellers', 'the comment as typed');
  // review-state #offline-leave, mockups #next-screen-press: text typed and not saved is saved as a draft as the page is
  // left, by any way out, into the page's outbox, which sends it first as the page opens again (#offline-reopen).
  const trips = join(specs, 'mockups/trips');
  const tripEvents = name => spoolEvents(name, 'human', trips);
  // A page come back to: its threads drawn from the service's answer, its waiting drafts sent.
  const cameBack = async (page, url) => {
    await page.waitForURL(url);
    await firstRead(page);
  };
  const firstOf = async (page, text) => (await threads(page)).find(t => t.messages[0] === text);
  const commentOn = async (page, text) => {
    if (await page.getAttribute('#hx-mode', 'aria-pressed') !== 'true') await page.click('#hx-mode');
    await page.click('[data-anchor="desktop-card"] button');
    await page.fill('.hx-compose .hx-composer textarea', text);
  };
  // By Left: a comment typed and left open, the focus outside its box.
  await page.click('.hx-mockup-slot[data-viewport="desktop"] .hx-mockup-label');
  assert.equal(await page.inputValue('.hx-composer textarea'), 'Name the travellers', 'the comment box stays open');
  await page.keyboard.press('ArrowLeft');
  await page.waitForURL(ARRIVALS);
  // By Next: a reply typed in the focused thread.
  await page.click('#hx-mode');
  await page.click('[data-anchor="desktop-card"] button');
  await save(page, 'Say the dates');
  await (await pinOf(page, 'Say the dates')).click();
  await page.fill('.hx-focused textarea', 'And the times');
  await page.click('#hx-pager-next');
  await cameBack(page, SEARCH);
  assert.equal((await firstOf(page, 'Name the travellers'))?.status, 'Draft', 'the comment left by Left shows as a draft on coming back');
  assert.deepEqual((await keptWith('02-search.mockup.html', 'Name the travellers', trips)).map(e => [e.event, e.anchorId]),
    [['comment', 'desktop-card']], 'the comment kept as a draft');
  await page.goto(ARRIVALS);
  await cameBack(page, ARRIVALS);
  const reply = (await keptWith('01-arrivals.mockup.html', 'And the times', trips)).at(-1);
  assert.equal(reply.event, 'reply', 'the reply left by Next kept as a draft');
  assert.equal(reply.threadId, tripEvents('01-arrivals.mockup.html').find(e => e.text === 'Say the dates').id);
  await page.close();
  // By the spec link: a comment typed, then the link to its spec pressed, then Back to the mockup page.
  page = await open(desktop, CHECKOUT);
  await commentOn(page, 'Show the fare');
  await page.click('#hx-spec-link');
  await page.waitForURL(url => url.pathname.endsWith('/trips.spec.html'));
  await page.goBack();
  await cameBack(page, CHECKOUT);
  assert.equal((await firstOf(page, 'Show the fare'))?.status, 'Draft', 'the comment left by the spec link shows as a draft');
  const fare = (await keptWith('03-checkout.mockup.html', 'Show the fare', trips)).find(e => e.event === 'comment');
  // By Previous: a reply typed in the Reply and reopen box a resolved thread shows when the agent's message is its last.
  execFileSync('sh', [resolve(root, 'skill/review-spec/scripts/emit-reply.sh'), join(trips, '03-checkout.mockup.html.review'), fare.id, fare.anchorId, JSON.stringify(fare.target), 'acknowledged', 'mockup edited', 'Added the fare']);
  await page.waitForFunction(() => document.getElementById('hx-threads').textContent.includes('Added the fare'), null, { timeout: 4000 });
  await (await pinOf(page, 'Show the fare')).click();
  await page.click('.hx-focused [data-act=resolve]');
  await page.fill('.hx-focused textarea[aria-label="Reply and reopen"]', 'And the seat');
  await page.click('#hx-pager-previous');
  await cameBack(page, SEARCH);
  await page.goBack();
  await cameBack(page, CHECKOUT);
  const reopened = (await keptWith('03-checkout.mockup.html', 'And the seat', trips)).at(-1);
  assert.deepEqual([reopened.event, reopened.threadId], ['reply', fare.id], 'the Reply and reopen left by Previous kept as a draft');
  await page.waitForFunction(() => document.getElementById('hx-threads').textContent.includes('And the seat'));
  assert.equal((await firstOf(page, 'Show the fare'))?.status, 'Draft', 'its thread reopened by the draft');
  await page.close();
  // An Edit box left as it opened saves nothing: Edit on the draft, then Next, then Back.
  page = await open(desktop, SEARCH);
  await (await pinOf(page, 'Name the travellers')).click();
  await page.click('.hx-focused [data-act=edit]');
  assert.equal(await page.inputValue('.hx-focused textarea[aria-label="Edit"]'), 'Name the travellers');
  await page.click('#hx-pager-next');
  await cameBack(page, CHECKOUT);
  await page.goBack();
  await cameBack(page, SEARCH);
  await page.waitForTimeout(1500); // a poll's slack for a draft the page would send
  assert.deepEqual(tripEvents('02-search.mockup.html').filter(e => e.event === 'edit'), [], 'no edit saved');
  assert.equal(await page.evaluate(() => Object.keys(localStorage).filter(k => k.startsWith('spec-chat:outbox:') && k.includes('02-search'))
    .flatMap(k => JSON.parse(localStorage.getItem(k))).length), 0, 'none waiting');
  assert.deepEqual((await firstOf(page, 'Name the travellers'))?.messages, ['Name the travellers'], 'the comment as it was');
  // By Back: a comment typed, then Back to the page before, then Forward to it again.
  await page.goto(ARRIVALS);
  await cameBack(page, ARRIVALS);
  await commentOn(page, 'Pick a seat');
  await page.goBack();
  await cameBack(page, SEARCH);
  await page.goForward();
  await cameBack(page, ARRIVALS);
  assert.equal((await firstOf(page, 'Pick a seat'))?.status, 'Draft', 'the comment left by Back shows as a draft');
  assert.deepEqual((await keptWith('01-arrivals.mockup.html', 'Pick a seat', trips)).filter(e => e.text === 'Pick a seat').map(e => e.event), ['comment']);
  await page.close();
  // #acceptance-next-screen-steps: Right after a step pressed in the step bar leaves the page and its step showing.
  page = await open(desktop, TEAM);
  assert.equal((await pagerOf(page)).place, '4 of 7');
  await page.click('.hx-step[data-step="team-open"]');
  await page.keyboard.press('ArrowRight');
  await stays(page, TEAM);
  assert.deepEqual(await pressed(page), ['team-open']);
  assert.deepEqual(await shownAt(page), both('totals team-open'));
  await page.close();
  // #acceptance-next-screen-one: a set of one shows the spec link and the range, no Previous, no Next, and no place;
  // #next-screen-live: a screen added to the set adds them, 1 of 2.
  page = await open(desktop, SINGLE);
  await page.waitForResponse(r => r.url().includes('/api/mockups?'));
  await page.waitForFunction(() => /^(Changes from |New since)/.test(document.getElementById('hx-range-copy')?.textContent || ''));
  assert.deepEqual(await page.evaluate(() => ({ pager: document.querySelectorAll('#hx-pager, .hx-pager-place').length,
    link: document.getElementById('hx-spec-link')?.textContent })), { pager: 0, link: 'Single spec' });
  await page.keyboard.press('ArrowRight');
  await stays(page, SINGLE);
  writeFileSync(join(specs, 'mockups/single/02-more.mockup.html'), screenPage('More'));
  await page.waitForSelector('#hx-pager', { timeout: 6000 });
  shown = await pagerOf(page);
  assert.deepEqual([shown.place, shown.previous.disabled, shown.next.title], ['1 of 2', true, 'Next screen: More (→)']);
  await page.close();
  // #acceptance-next-screen-phone: on a phone, Next on the first screen opens the second, 2 of 3; the range on a row of
  // its own under the spec link, Previous and Next at the end of the row under it, each a 44 px target with no tooltip,
  // within the screen's width (#next-screen-phone).
  const meetsRow = (a, b) => a.top < b.bottom && b.top < a.bottom;
  const phonePager = (shown, what) => {
    assert.ok(shown.copy.top >= shown.link.bottom - 0.5, what + ': the range under the spec link');
    assert.ok(shown.copy.width >= shown.row.width - 0.5, what + ': the range takes the whole row');
    assert.ok(shown.box.top >= shown.copy.bottom - 0.5 && !meetsRow(shown.box, shown.copy), what + ': Previous and Next on the row under the range');
    assert.ok(Math.abs(shown.box.right - shown.row.right) <= 0.5, what + ': at the end of that row');
    shown.buttons.forEach(b => assert.ok(b.width >= 44 && b.height >= 44, what + ': a 44 px target'));
    assert.ok(shown.box.left >= 0 && shown.box.right <= 390, what + ": within the screen's width");
  };
  page = await open(phone, ARRIVALS);
  await pagerOf(page);
  await page.tap('#hx-pager-next');
  await page.waitForURL(SEARCH);
  await page.waitForFunction(() => document.getElementById('hx-mockup-view'));
  shown = await pagerOf(page);
  assert.equal(shown.place, '2 of 3');
  assert.deepEqual([shown.previous.title, shown.next.title], [null, null], 'no tooltip on a phone');
  assert.equal(shown.view, null, 'unchanged since the base: no Before and After');
  phonePager(shown, 'without Before and After');
  assert.ok(await noSideScroll(page));
  await page.close();
  // With Before and After showing, they sit on that row first, Previous and Next after them at its end.
  page = await open(phone, PLAN);
  await page.waitForFunction(() => document.getElementById('hx-mockup-view')?.hidden === false);
  shown = await pagerOf(page);
  assert.equal(shown.place, '1 of 7');
  phonePager(shown, 'with Before and After');
  shown.view.forEach(b => {
    assert.ok(meetsRow(b, shown.box) && b.right <= shown.box.left, 'Before and After on the row, before Previous and Next');
    assert.ok(b.height >= 44 && b.left >= 0, 'Before and After each a 44 px target within the screen');
  });
  assert.ok(await noSideScroll(page));
  await page.close();

  assert.deepEqual(errors, [], 'no page errors');
} finally {
  await browser.close();
  server.kill();
}
console.log('runtime mockup page tests passed');
