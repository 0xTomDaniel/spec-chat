import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, readdirSync } from 'node:fs';
import { createServer } from 'node:net';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

// A screen's first round showing its directions (mockups #directions, #screen-index-directions; prompt-first-shaping
// #answer-buttons): the QA fixture's directions collections (tests/fixtures/qa reset.sh, spools.py PROTOTYPE_DIRECTIONS,
// DRAWN_DIRECTIONS) on the fixture service (serve.py), the prototype ones on the prototype collections' own, private on
// loopback, and its running prototype (prototype.py). Weekly report, a prototype mockup, names Team cards, its own route
// and the agent's pick, and Team table, the prototype's reports/weekly-table, each framed at the lane's prototype origin,
// <slug>-prototype.localhost at that service's port, and held across the two origins by the shell messages (shell
// #shell-msg-prototype); Team panel, drawn, draws Team table's own frames beside its own. The mockup page shows them
// side by side with one Desktop and Phone toggle, and Pick saves the reviewer's answer as an answer button does; the
// spec page's Screens index reads the count. On a desktop and a phone. Headless Chromium. Needs
// PLAYWRIGHT_CORE=<path to playwright-core>.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
assert.equal(readFileSync(resolve(root, 'docs/specs/.viz/runtime.js'), 'utf8'), runtime, 'runtime copies are identical');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_CORE || 'playwright-core');

const fixture = resolve(root, 'tests/fixtures/qa');
const SET = 'docs/specs/mockups/report-page/';
const PROTOTYPE = SET + '06-weekly-report.mockup.html';
const DRAWN = SET + '04-team-panel.mockup.html';
const SPEC = 'docs/specs/report-page.spec.html';
const freePort = () => new Promise(res => { const s = createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); });
const [port, prototypesPort, prototypePort] = [await freePort(), await freePort(), await freePort()];
const service = `http://127.0.0.1:${port}`, prototypes = `http://127.0.0.1:${prototypesPort}`;
const PROTOTYPE_COLLECTIONS = new Set(['directions', 'directions-pick', 'directions-pick-panel']); // spools.py PROTOTYPE_DIRECTIONS
const qaRoot = mkdtempSync(join(tmpdir(), 'directions-'));
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
  start([join(fixture, 'serve.py'), join(qaRoot, 'serve-prototype/registry.toml'), String(prototypesPort)]),
  start([join(fixture, 'prototype.py'), String(prototypePort)])];
process.on('exit', () => { for (const server of servers) try { process.kill(-server.pid); } catch {} });
await ready(`${service}/directions-drawn/${SPEC}`);
await ready(`${prototypes}/directions/${SPEC}`);
await ready(`http://127.0.0.1:${prototypePort}/`);

const site = join(qaRoot, 'site');
const comments = (collection, page) => {
  const dir = join(site, collection, page + '.review', 'human');
  let names = [];
  try { names = readdirSync(dir).filter(n => n.endsWith('.json')).sort(); } catch {}
  return names.map(n => JSON.parse(readFileSync(join(dir, n), 'utf8'))).filter(e => e.event === 'comment');
};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const until = async (check, what, ms = 8000) => {
  for (const end = Date.now() + ms; Date.now() < end; await sleep(100)) if (await check()) return;
  assert.fail('timed out: ' + what);
};
const browser = await chromium.launch();
const desktop = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
const errors = [];

// The route's frames of a prototype mockup page: at another origin than the page, so read through the browser.
const routeFrames = async tab => Promise.all((await tab.$$('.hx-dir iframe')).map(el => el.contentFrame()));
// A collection's page, on its service, connected; a prototype mockup's once every direction's frame runs its route.
const open = async (collection, page, on = desktop) => {
  const tab = await on.newPage();
  tab.on('pageerror', e => errors.push(String(e)));
  await tab.goto(`${PROTOTYPE_COLLECTIONS.has(collection) ? prototypes : service}/${collection}/${page}`);
  await tab.waitForFunction(() => /connected/.test(document.getElementById('hx-status')?.textContent || ''));
  if (page === PROTOTYPE) await until(async () => {
    for (const frame of await routeFrames(tab)) if (!frame || !await frame.evaluate(() => Boolean(document.querySelector('main'))).catch(() => false)) return false;
    return true;
  }, 'every direction runs its route');
  return tab;
};
// The directions as shown: each one's name, tags, and Pick as seen, its reason and gives-up line, and its frame showing,
// with where each sits; and the toggle's pressed viewport.
const shown = el => Boolean(el) && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
const directions = async tab => {
  const seen = await tab.evaluate(shownSource => {
  const shown = new Function('return ' + shownSource)();
  const box = el => { const r = el.getBoundingClientRect(); return { left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height }; };
  return {
    pressed: [...document.querySelectorAll('.hx-dirs-toggle button')].filter(b => b.getAttribute('aria-pressed') === 'true').map(b => b.textContent),
    toggle: box(document.querySelector('.hx-dirs-toggle')),
    dirs: [...document.querySelectorAll('.hx-dir')].map(dir => {
      const slots = [...dir.querySelectorAll('.hx-mockup-slot')].filter(shown);
      const frame = slots[0] && slots[0].querySelector('[data-mockup-frame]');
      return {
        name: dir.querySelector('.hx-dir-name').textContent,
        tags: [...dir.querySelectorAll('.hx-dir-tag')].filter(shown).map(t => t.textContent),
        pick: shown(dir.querySelector('.hx-dir-pick')),
        pickBox: shown(dir.querySelector('.hx-dir-pick')) ? box(dir.querySelector('.hx-dir-pick')) : null,
        why: dir.querySelector('.hx-dir-why')?.textContent || null,
        gives: dir.querySelector('.hx-dir-gives')?.textContent || null,
        head: box(dir.querySelector('.hx-dir-head')),
        givesBox: dir.querySelector('.hx-dir-gives') ? box(dir.querySelector('.hx-dir-gives')) : null,
        viewports: slots.map(s => s.dataset.viewport),
        frame: frame ? { ...box(frame), width: frame.tagName === 'IFRAME' ? null : frame.offsetWidth, at: null,
          direction: frame.dataset.direction || null, anchor: frame.dataset.anchor || null } : null,
      };
    }),
  };
  }, shown.toString());
  // A route's frame, at another origin, tells its own width and route.
  for (const [i, dir] of (await tab.$$('.hx-dir')).entries()) {
    const el = (await dir.evaluateHandle((d, shownSource) => {
      const shown = new Function('return ' + shownSource)();
      const slot = [...d.querySelectorAll('.hx-mockup-slot')].filter(shown)[0];
      return slot ? slot.querySelector('iframe') : null;
    }, shown.toString())).asElement();
    if (el) Object.assign(seen.dirs[i].frame, await (await el.contentFrame()).evaluate(() => ({ width: innerWidth, at: location.pathname })));
  }
  return seen;
};

// #acceptance-directions: on a desktop, both directions' routes running side by side at desktop width, each under its
// name and the line saying what it gives up, the agent's pick reading Agent's pick with its reason, Desktop pressed.
{
  const tab = await open('directions', PROTOTYPE);
  const seen = await directions(tab);
  assert.deepEqual(seen.pressed, ['Desktop']);
  assert.deepEqual(seen.dirs.map(d => [d.name, d.tags, d.pick, d.viewports, d.frame.width, d.frame.at]), [
    ['Team cards', ["Agent's pick"], false, ['desktop'], 1440, '/reports/weekly'],
    ['Team table', [], true, ['desktop'], 1440, '/reports/weekly-table']]);
  const [cards, table] = seen.dirs;
  assert.equal(cards.why, 'Every team\'s total reads at a glance on a phone, and a press opens its weeks.');
  assert.equal(table.why, null, 'only the agent\'s pick gives a reason');
  assert.deepEqual([cards.gives, table.gives], ['Gives up: a team\'s earlier weeks take a press to see.',
    'Gives up: on a phone the three weeks squeeze into narrow columns.']);
  assert.ok(cards.frame.right <= table.frame.left && Math.abs(cards.frame.top - table.frame.top) < 1, 'side by side, the frames level');
  for (const d of seen.dirs) assert.ok(d.head.bottom <= d.givesBox.top && d.givesBox.bottom <= d.frame.top, d.name + ' under its name and gives-up line');
  assert.ok(seen.toggle.bottom <= cards.head.top, 'the toggle above them');
  assert.ok(table.frame.right <= 1440 && await tab.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'within the window');
  // #directions-pick-only: the pick's frames alone are held and walked through; the step bar is the pick's.
  assert.deepEqual(await tab.evaluate(() => [...document.querySelectorAll('#hx-steps .hx-step[data-step]')].map(b => b.dataset.step)), ['totals', 'team-open', 'export']);
  assert.equal(await tab.locator('.hx-mockup-slot iframe[data-anchor]').count(), 2, 'only the pick\'s two frames take a comment');
  // shell #shell-msg-prototype: the other direction's route is held and sizes its frame by the same messages, so its
  // content grown past the screen grows its frame.
  const OTHER = '.hx-dir-other .hx-mockup-slot[data-viewport="desktop"] iframe';
  await (await (await tab.$(OTHER)).contentFrame()).evaluate(() => { document.body.appendChild(document.createElement('section')).style.height = '1500px'; });
  await until(() => tab.evaluate(s => parseFloat(document.querySelector(s).style.height) > 1500, OTHER), 'the other direction\'s frame grows to its content');

  // #acceptance-directions-toggle: Phone in the toggle shows both directions' routes at phone width, side by side.
  await tab.click('.hx-dirs-toggle button:has-text("Phone")');
  const phoned = await directions(tab);
  assert.deepEqual(phoned.pressed, ['Phone']);
  assert.deepEqual(phoned.dirs.map(d => [d.name, d.viewports, d.frame.width]), [['Team cards', ['phone'], 390], ['Team table', ['phone'], 390]]);
  assert.ok(phoned.dirs[0].frame.right <= phoned.dirs[1].frame.left && Math.abs(phoned.dirs[0].frame.top - phoned.dirs[1].frame.top) < 1, 'side by side');
  await tab.click('.hx-dirs-toggle button:has-text("Desktop")');
  assert.deepEqual((await directions(tab)).dirs.map(d => d.viewports[0]), ['desktop', 'desktop']);
  await tab.close();
}

// #directions-page, #prototype-steps: the pick's walkthrough steps the frames the toggle shows, each step's motion
// ending in its own time, as with no directions; the frame the toggle hides, its route at another origin, which the
// browser does not draw, reaches the step showing once the toggle shows it.
{
  const tab = await open('directions', PROTOTYPE);
  const pick = async viewport => (await tab.$(`.hx-dir:not(.hx-dir-other) .hx-mockup-slot[data-viewport="${viewport}"] iframe`)).contentFrame();
  const showing = async viewport => (await pick(viewport)).evaluate(() => ({
    panel: !document.getElementById('team-panel').hidden && getComputedStyle(document.getElementById('team-panel')).opacity === '1',
    dialog: !document.getElementById('export-dialog').hidden,
  })).catch(() => null); // a route loading again has no document to read yet
  const steps = () => tab.evaluate(() => [...document.querySelectorAll('#hx-steps .hx-step[data-step]')]
    .filter(b => b.getAttribute('aria-pressed') === 'true').map(b => b.dataset.step).join());
  const started = Date.now();
  await tab.click('#hx-play');
  await until(async () => await steps() === 'export'
    && await tab.evaluate(() => document.getElementById('hx-play').getAttribute('aria-pressed') === 'false'), 'Play walks every step and stops', 15000);
  assert.ok(Date.now() - started < 12000, 'no step waits on the hidden frame: ' + (Date.now() - started) + ' ms');
  assert.deepEqual(await showing('desktop'), { panel: true, dialog: true }, 'the shown frame at the last step');
  await tab.click('.hx-dirs-toggle button:has-text("Phone")');
  await until(async () => JSON.stringify(await showing('phone')) === '{"panel":true,"dialog":true}', 'the phone frame reaches the last step as the toggle shows it');
  // A step before the one showing, pressed at Phone, and the desktop frame follows once shown.
  await tab.click('#hx-steps .hx-step[data-step="team-open"]');
  await until(async () => JSON.stringify(await showing('phone')) === '{"panel":true,"dialog":false}', 'the phone frame at the second step');
  await tab.click('.hx-dirs-toggle button:has-text("Desktop")');
  await until(async () => JSON.stringify(await showing('desktop')) === '{"panel":true,"dialog":false}', 'the desktop frame at the second step as the toggle shows it');
  await tab.close();
}

// #acceptance-directions-pick: Pick on the direction that is not the agent's pick: it reads Your pick, the agent's pick
// offers Pick, and the saved-as-draft notice shows. The answer is a comment on the directions list, its text the
// direction's name, its target that direction's item, as an answer button's (prompt-first-shaping #choice-save).
{
  const tab = await open('directions-pick', PROTOTYPE);
  await tab.click('.hx-dir-pick[aria-label="Pick Team table"]');
  await tab.waitForFunction(() => document.getElementById('hx-toast')?.textContent === 'Comment saved as draft — hand off when ready');
  await until(async () => (await directions(tab)).dirs[1].tags.includes('Your pick'), 'Team table reads Your pick');
  const seen = await directions(tab);
  assert.deepEqual(seen.dirs.map(d => [d.name, d.tags, d.pick]), [['Team cards', ["Agent's pick"], true], ['Team table', ['Your pick'], false]]);
  assert.equal(await tab.evaluate(() => document.querySelector('.hx-panel').classList.contains('open')), false, 'no panel opens');
  await until(() => comments('directions-pick', PROTOTYPE).length === 1, 'the answer saved');
  const [answer] = comments('directions-pick', PROTOTYPE);
  assert.deepEqual([answer.anchorId, answer.text, answer.target, answer.quote, answer.step], ['directions', 'Team table', { type: 'element', key: 'li[2]' }, 'Team table', undefined]);
  // The newest answer stands (threads #supersede): Pick on the agent's pick moves Your pick back to it.
  await tab.click('.hx-dir-pick[aria-label="Pick Team cards"]');
  await until(async () => (await directions(tab)).dirs[0].tags.includes('Your pick'), 'Team cards reads Your pick');
  assert.deepEqual((await directions(tab)).dirs.map(d => [d.tags, d.pick]), [[["Agent's pick", 'Your pick'], false], [[], true]]);
  await until(() => comments('directions-pick', PROTOTYPE).length === 2, 'the second answer saved');
  // The pick derives from the review state: it survives a reload.
  await tab.reload();
  await tab.waitForFunction(() => /connected/.test(document.getElementById('hx-status')?.textContent || ''));
  await until(async () => (await directions(tab)).dirs[0].tags.includes('Your pick'), 'Your pick after a reload');
  await tab.close();
}

// #acceptance-directions-pick-panel: Pick on Team table, then the review panel: the reviewer's answer naming it as a
// draft, on the directions, and the drafts note reads 1 draft.
{
  const tab = await open('directions-pick-panel', PROTOTYPE);
  await tab.click('.hx-dir-pick[aria-label="Pick Team table"]');
  await tab.waitForFunction(() => document.getElementById('hx-toast')?.textContent === 'Comment saved as draft — hand off when ready');
  await tab.click('.hx-dock-open');
  await tab.waitForFunction(() => document.querySelector('.hx-panel').classList.contains('open'));
  const entries = await tab.evaluate(() => [...document.querySelectorAll('#hx-threads .hx-entry[data-thread]')].map(t => ({
    chip: t.querySelector('.hx-chip')?.textContent, text: t.querySelector('.hx-msg .hx-bubble .hx-text')?.textContent,
    draft: t.querySelector('.hx-bubble')?.hasAttribute('data-draft'),
  })));
  assert.deepEqual(entries, [{ chip: 'Directions · Pick', text: 'Team table', draft: true }]);
  assert.equal(await tab.textContent('#hx-drafts'), '1 draft');
  await tab.close();
}

// #directions-page, shell #shell-msg-show: a thread's chip arrives at its spot in the frame the toggle hides, that frame
// shown and at the thread's step before it is asked. Another reviewer's thread on the export dialog at the last step,
// the dialog a part only the phone shows, the toggle on Desktop and the phone frame left at the first step: the chip
// switches the toggle to Phone, the phone frame at the last step, its dialog shown and in view, Phone still pressed once
// there. An arrival no frame shows, a thread on the team panel at the first step, where neither frame shows it, the
// toggle on Desktop again: the chip switches the toggle to Phone to ask its frame, then back to Desktop; once more with
// the reviewer pressing Phone as the chip switches the toggle to it, the press stands, the toggle on Phone past the 2 s a
// frame's answer waits at most. One arrival at a time: a second such thread's chip pressed as the first's switches the
// toggle to Phone, the reviewer on Desktop, the first's switch goes back and the second asks both frames, the toggle
// ending on Desktop; an address naming the phone frame as a chip asks the desktop frame shows Phone, and it stands.
{
  const { mkdirSync, writeFileSync } = await import('node:fs');
  const human = join(site, 'directions-pick-panel', PROTOTYPE + '.review', 'human');
  mkdirSync(human, { recursive: true });
  const ns = BigInt(Date.now()) * 1000000n;
  const event = (at, body) => writeFileSync(join(human, `${ns + BigInt(at)}-${body.event}-${body.id}.json`), JSON.stringify({
    createdAt: new Date().toISOString(), schemaVersion: 1, actor: 'human', browser: 'qa-browser-mango', author: 'Mango', ...body }));
  event(1, { id: 'qa-phone-dialog', event: 'comment', anchorId: 'export-dialog', target: null, quote: null,
    text: 'Name the file a phone downloads.', step: 'export' });
  event(2, { id: 'qa-no-frame', event: 'comment', anchorId: 'team-panel', target: null, quote: null,
    text: 'Show a team before any card is pressed.', step: 'totals' });
  event(3, { id: 'qa-no-frame-again', event: 'comment', anchorId: 'team-panel', target: null, quote: null,
    text: 'Say which team the panel opens on.', step: 'totals' });
  event(4, { id: 'qa-phone-dialog-handoff', event: 'handoff', events: ['qa-phone-dialog', 'qa-no-frame', 'qa-no-frame-again'] });
  const tab = await open('directions-pick-panel', PROTOTYPE);
  const pick = async viewport => (await tab.$(`.hx-dir:not(.hx-dir-other) .hx-mockup-slot[data-viewport="${viewport}"] iframe`)).contentFrame();
  for (const viewport of ['desktop', 'phone']) await (await pick(viewport)).evaluate(() => {
    document.head.appendChild(document.createElement('style')).textContent = '@media (min-width:641px){#export-dialog{display:none!important}}';
  });
  assert.deepEqual((await directions(tab)).pressed, ['Desktop']);
  await tab.click('.hx-dock-open');
  await tab.locator('#hx-threads .hx-entry[data-thread]', { hasText: 'Name the file a phone downloads.' }).locator(':scope > .hx-chip').click();
  await until(async () => (await directions(tab)).pressed[0] === 'Phone', 'the chip switches the toggle to Phone');
  const dialog = async () => (await pick('phone')).evaluate(() => {
    const el = document.getElementById('export-dialog');
    const r = el.getBoundingClientRect();
    return el.hidden || !el.getClientRects().length ? null : { top: r.top, bottom: r.bottom };
  }).catch(() => null);
  await until(async () => Boolean(await dialog()), 'the phone frame at the last step, its dialog shown');
  await until(async () => {
    const [inner, frame] = [await dialog(), await tab.evaluate(() => {
      const f = document.querySelector('.hx-dir:not(.hx-dir-other) .hx-mockup-slot[data-viewport="phone"] iframe');
      const r = f.getBoundingClientRect();
      return { top: r.top, zoom: r.width / f.offsetWidth, height: innerHeight };
    })];
    return inner && frame.top + inner.top * frame.zoom >= 0 && frame.top + inner.bottom * frame.zoom <= frame.height;
  }, 'the dialog in view');
  assert.deepEqual(await tab.evaluate(() => [...document.querySelectorAll('#hx-steps .hx-step[data-step]')]
    .filter(b => b.getAttribute('aria-pressed') === 'true').map(b => b.dataset.step)), ['export']);
  assert.deepEqual((await directions(tab)).pressed, ['Phone'], 'the toggle stays on Phone, which shows the spot');
  await tab.locator('.hx-dirs-toggle button', { hasText: 'Desktop' }).click();
  await tab.evaluate(() => { // each viewport the toggle presses in turn
    const toggle = document.querySelector('.hx-dirs-toggle');
    const pressed = () => toggle.querySelector('button[aria-pressed=true]')?.textContent;
    window.qaPressed = [pressed()];
    new MutationObserver(() => pressed() !== window.qaPressed.at(-1) && window.qaPressed.push(pressed()))
      .observe(toggle, { attributes: true, subtree: true, attributeFilter: ['aria-pressed'] });
  });
  await tab.locator('#hx-threads .hx-entry[data-thread]', { hasText: 'Show a team before any card is pressed.' }).locator(':scope > .hx-chip').click();
  await until(async () => (await tab.evaluate(() => window.qaPressed)).join() === 'Desktop,Phone,Desktop',
    'the chip asks the phone frame, then the toggle goes back to Desktop');
  assert.deepEqual((await directions(tab)).pressed, ['Desktop']);
  const asPhone = press => tab.evaluate(press => { // press, in the page, as the toggle switches to Phone
    const phone = [...document.querySelectorAll('.hx-dirs-toggle button')].find(b => b.textContent === 'Phone');
    const chip = text => [...document.querySelectorAll('#hx-threads .hx-entry[data-thread]')]
      .find(entry => entry.textContent.includes(text)).querySelector(':scope > .hx-chip');
    window.qaPressed = ['Desktop'];
    const on = new MutationObserver(() => phone.getAttribute('aria-pressed') === 'true'
      && (on.disconnect(), (press === 'Phone' ? phone : chip(press)).click()));
    on.observe(phone, { attributes: true, attributeFilter: ['aria-pressed'] });
  }, press);
  const noFrame = () => tab.locator('#hx-threads .hx-entry[data-thread]', { hasText: 'Show a team before any card is pressed.' }).locator(':scope > .hx-chip').click();
  await asPhone('Phone');
  await noFrame();
  await sleep(3000);
  assert.equal(await tab.evaluate(() => window.qaPressed.join()), 'Desktop,Phone', 'the reviewer\'s press of Phone stands');
  assert.deepEqual((await directions(tab)).pressed, ['Phone']);
  await tab.locator('.hx-dirs-toggle button', { hasText: 'Desktop' }).click();
  await asPhone('Say which team the panel opens on.');
  await noFrame();
  await until(async () => (await tab.evaluate(() => window.qaPressed)).join() === 'Desktop,Phone,Desktop,Phone,Desktop',
    'the second chip puts the first\'s switch back, asks the phone frame, then the toggle goes back to Desktop');
  assert.deepEqual((await directions(tab)).pressed, ['Desktop'], 'the toggle ends where the reviewer left it');
  await tab.evaluate(() => { // the chip asks the desktop frame first; the address arrives meanwhile
    window.qaPressed = ['Desktop'];
    [...document.querySelectorAll('#hx-threads .hx-entry[data-thread]')]
      .find(entry => entry.textContent.includes('Show a team before any card is pressed.')).querySelector(':scope > .hx-chip').click();
    location.hash = 'phone';
  });
  await sleep(3000);
  assert.equal(await tab.evaluate(() => window.qaPressed.join()), 'Desktop,Phone', 'the address\'s Phone stands');
  assert.deepEqual((await directions(tab)).pressed, ['Phone']);
  await tab.close();
}

// #acceptance-directions-phone: on a phone, the directions' routes one above the other at phone width, each within the
// screen's width under its name and gives-up line, Phone pressed, and Pick a 44 px target; one hand-off control
// (prompt-first-shaping #acceptance-one-handoff-rule), and no sideways scroll.
{
  const tab = await open('directions', PROTOTYPE, phone);
  const seen = await directions(tab);
  assert.deepEqual(seen.pressed, ['Phone']);
  assert.deepEqual(seen.dirs.map(d => [d.name, d.viewports, d.frame.width]), [['Team cards', ['phone'], 390], ['Team table', ['phone'], 390]]);
  const [cards, table] = seen.dirs;
  assert.ok(cards.frame.bottom <= table.head.top, 'one above the other');
  for (const d of seen.dirs) {
    assert.ok(d.frame.left >= 0 && d.frame.right <= 390, d.name + ' within the screen\'s width');
    assert.ok(d.head.bottom <= d.givesBox.top && d.givesBox.bottom <= d.frame.top, d.name + ' under its name and gives-up line');
  }
  assert.ok(table.pickBox.height >= 44 && table.pickBox.width >= 44, 'Pick a 44 px target');
  assert.ok(await tab.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'no sideways scroll');
  const handoffs = await tab.evaluate(shownSource => {
    const shown = new Function('return ' + shownSource)();
    return [...document.querySelectorAll('button')].filter(shown).filter(b => /^Hand off/.test(b.textContent.trim())).length;
  }, shown.toString());
  assert.equal(handoffs, 1, 'the directions add no hand-off control');
  await tab.close();
}

// #acceptance-directions-drawn: a drawn mockup's directions, each one's desktop frame side by side under its name and
// gives-up line, the agent's pick with its reason; the other direction's own frames, which take no comment, naming no
// anchor, and the pick's walkthrough.
{
  const tab = await open('directions-drawn', DRAWN);
  const seen = await directions(tab);
  assert.deepEqual(seen.pressed, ['Desktop']);
  assert.deepEqual(seen.dirs.map(d => [d.name, d.tags, d.pick, d.viewports, d.frame.direction, d.frame.anchor]), [
    ['Team cards', ["Agent's pick"], false, ['desktop'], null, 'desktop'],
    ['Team table', [], true, ['desktop'], 'table', null]]);
  assert.equal(await tab.locator('.hx-dir-other [data-anchor]').count(), 0, 'no part of another direction names an anchor');
  const [cards, table] = seen.dirs;
  assert.equal(cards.why, 'Every team\'s total reads at a glance on a phone, and a press opens its weeks.');
  assert.ok(cards.frame.right <= table.frame.left && Math.abs(cards.frame.top - table.frame.top) < 1, 'side by side, level');
  for (const d of seen.dirs) assert.ok(d.head.bottom <= d.givesBox.top && d.givesBox.bottom <= d.frame.top, d.name + ' under its name and gives-up line');
  // Pressing a part a step names moves the pick's frames alone; the other direction's frames keep no step.
  await tab.locator('[data-mockup-frame="desktop"]:not([data-direction]) [data-anchor="desktop-open"]').click();
  await tab.waitForFunction(() => document.querySelector('#hx-steps .hx-step[data-step="team-open"]')?.getAttribute('aria-pressed') === 'true');
  assert.deepEqual(await tab.evaluate(() => [...document.querySelectorAll('[data-mockup-frame]')].map(f => [f.dataset.direction || null, f.dataset.mockupFrame, f.getAttribute('data-at')])),
    [[null, 'desktop', 'totals team-open'], [null, 'phone', 'totals team-open'], ['table', 'desktop', null], ['table', 'phone', null]]);
  // In comment mode the other direction takes no comment; the pick's frame does.
  await tab.click('#hx-mode');
  await tab.locator('.hx-dir-other [data-mockup-frame="desktop"] td').first().click();
  assert.equal(await tab.locator('.hx-composer').count(), 0, 'no composer on another direction');
  await tab.locator('[data-anchor="desktop-bar"] h1').click();
  await tab.waitForSelector('.hx-composer');
  await tab.close();
  // By keyboard (#directions-pick-only): in comment mode Tab stops at the pick's parts, each a comment target Enter
  // comments on, and in another direction at its Pick alone, never at a part of its frames; the toggle stays a button.
  const keys = await open('directions-drawn', DRAWN);
  await keys.click('#hx-mode');
  await keys.waitForFunction(() => document.body.classList.contains('hx-comment'));
  assert.equal(await keys.locator('.hx-dir-other [data-hx-target], .hx-dir-other [tabindex="0"]').count(), 0, 'no part of another direction is a Tab stop');
  await keys.focus('.hx-dirs-toggle button[data-viewport="desktop"]');
  const stops = [];
  for (let i = 0; i < 80; i++) {
    await keys.keyboard.press('Tab');
    const stop = await keys.evaluate(() => {
      const el = document.activeElement;
      return { other: Boolean(el.closest('.hx-dir-other')), anchor: el.dataset.anchor || null, target: el.hasAttribute('data-hx-target'),
        label: el.getAttribute('aria-label') || el.textContent.trim().slice(0, 40) };
    });
    if (stops.some(s => s.label === stop.label && s.anchor === stop.anchor)) break;
    stops.push(stop);
  }
  assert.deepEqual(stops.filter(s => s.other).map(s => s.label), ['Pick Team table'], 'another direction: its Pick alone');
  const targets = stops.filter(s => s.target).map(s => s.anchor);
  assert.ok(targets.includes('desktop-bar') && targets.every(anchor => /^desktop-/.test(anchor)), 'the comment targets: the pick\'s shown frame\'s parts, ' + targets);
  await keys.focus('[data-anchor="desktop-bar"]');
  await keys.keyboard.press('Enter');
  await keys.waitForSelector('.hx-composer');
  await keys.keyboard.press('Escape');
  await keys.click('#hx-mode');
  await keys.waitForFunction(() => document.body.classList.contains('hx-comment'));
  await keys.focus('.hx-dirs-toggle button[data-viewport="phone"]');
  await keys.keyboard.press('Enter');
  assert.deepEqual((await directions(keys)).pressed, ['Phone'], 'the toggle a button in comment mode');
  assert.equal(await keys.locator('.hx-composer').count(), 0, 'the toggle takes no comment');
  await keys.close();
  // The reader's place is a part the toggle shows, and coming back to it shows it at its viewport: a phone frame's part
  // kept across a reload opens at Phone.
  const kept = await open('directions-drawn', DRAWN);
  await kept.click('.hx-dirs-toggle button:has-text("Phone")');
  await kept.evaluate(() => {
    const part = document.querySelector('[data-anchor="phone-cards"]');
    const top = parseFloat(document.documentElement.style.getPropertyValue('--hx-top-space')) || 0;
    window.scrollTo(0, part.getBoundingClientRect().top + window.scrollY - top + 4);
  });
  const before = await kept.evaluate(() => document.querySelector('[data-anchor="phone-cards"]').getBoundingClientRect().top);
  await kept.reload();
  await kept.waitForFunction(() => /connected/.test(document.getElementById('hx-status')?.textContent || ''));
  await until(async () => (await directions(kept)).pressed[0] === 'Phone', 'the kept place opens at Phone');
  await until(() => kept.evaluate(top => Math.abs(document.querySelector('[data-anchor="phone-cards"]').getBoundingClientRect().top - top) < 2, before),
    'at the kept part');
  await kept.close();
  // A part the address names, as a See mockup chip opens it, shows at its viewport: a phone frame's part at Phone.
  const arrived = await open('directions-drawn', DRAWN + '#phone-cards');
  await until(async () => (await directions(arrived)).pressed[0] === 'Phone', 'the addressed part at Phone');
  await arrived.close();
}

// #acceptance-directions-accept: a spec with no open TBD, its threads resolved, whose mockup holds two directions with no
// Pick pressed: the panel offers Accept spec.
{
  const tab = await open('directions-drawn', SPEC);
  await tab.waitForFunction(() => document.getElementById('hx-handoff')?.textContent === 'Accept spec', null, { timeout: 8000 });
  await tab.close();
}

// #acceptance-screen-index-directions: in the Screens index the screen showing two directions reads 2 directions, and
// every other screen reads none.
for (const on of [desktop, phone]) {
  const tab = await open('directions', SPEC, on);
  await tab.waitForFunction(() => document.querySelectorAll('#hx-screens .hx-screen').length === 4);
  const rows = await tab.evaluate(() => [...document.querySelectorAll('#hx-screens .hx-screen')].map(row => [
    row.querySelector('.hx-screen-name').textContent, row.querySelector('.hx-screen-dirs')?.textContent || null,
    row.getAttribute('aria-label').includes('2 directions')]));
  assert.deepEqual(rows, [['Report page', null, false], ['Team page', null, false], ['Export dialog', null, false],
    ['Weekly report', '2 directions', true]]);
  assert.ok(await tab.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'no sideways scroll');
  await tab.close();
}

assert.deepEqual(errors, []);
await browser.close();
console.log('ok runtime-directions');
process.exit(0);
