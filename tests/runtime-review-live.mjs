import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { cpSync, mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { createServer } from 'node:net';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

// Shared review state between reviewers' pages (review-state #live, #offline, #model-fields,
// #model-author-shown, #model-edit-history): the full runtime on the QA fixture's report spec,
// served by the real review-serve.py, in headless Chromium with one browser context per reviewer.
// Needs PLAYWRIGHT_CORE=<path to playwright-core>.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
assert.equal(readFileSync(resolve(root, 'docs/specs/.viz/runtime.js'), 'utf8'), runtime, 'runtime copies are identical');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_CORE || 'playwright-core');

const repo = mkdtempSync(join(tmpdir(), 'review-live-'));
const specs = join(repo, 'docs/specs');
cpSync(resolve(root, 'tests/fixtures/qa/head/docs/specs'), specs, { recursive: true });
cpSync(resolve(root, 'docs/specs/.viz'), join(specs, '.viz'), { recursive: true });
cpSync(resolve(root, 'docs/specs/.style'), join(specs, '.style'), { recursive: true });
const git = (...args) => execFileSync('git', ['-C', repo, ...args], { env: { ...process.env, GIT_CONFIG_GLOBAL: '/dev/null', GIT_AUTHOR_NAME: 't', GIT_AUTHOR_EMAIL: 't@x', GIT_COMMITTER_NAME: 't', GIT_COMMITTER_EMAIL: 't@x' } });
git('init', '-q', '-b', 'main');
git('add', '-A');
git('commit', '-qm', 'fixture');
const human = join(specs, 'report.spec.html.review/human');
const spoolFiles = () => { try { return readdirSync(human).filter(n => n.endsWith('.json')).sort(); } catch { return []; } };
const spoolEvents = () => spoolFiles().map(name => ({ name, body: JSON.parse(readFileSync(join(human, name), 'utf8')) }));

const port = await new Promise(res => { const s = createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); });
const server = spawn('python3', [resolve(root, 'skill/review-spec/assets/review-serve.py'), join(repo, 'docs'), String(port)], {
  stdio: 'ignore', env: { ...process.env, HOME: join(repo, '.home'), XDG_STATE_HOME: join(repo, '.state'), XDG_CONFIG_HOME: join(repo, '.config'), HERDR_SOCKET_PATH: '' },
});
const URL = `http://127.0.0.1:${port}/specs/report.spec.html`;
for (let i = 0; ; i++) {
  try { if ((await fetch(URL)).ok) break; } catch {}
  if (i > 100) throw new Error('review-serve did not start');
  await new Promise(r => setTimeout(r, 100));
}
const TOTALS = 'p[data-anchor="report-totals"]';
const EXPORT = 'p[data-anchor="report-export"]';
const LIVE = { timeout: 2500 }; // #acceptance-live-comment: within 2 seconds, plus one poll's slack

const browser = await chromium.launch();
const errors = [];
const reviewer = async (preset = null) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  if (preset) await context.addInitScript(v => localStorage.setItem('spec-chat:reviewer', v), JSON.stringify(preset));
  const open = async () => {
    const page = await context.newPage();
    page.on('pageerror', e => errors.push(String(e)));
    await page.goto(URL);
    await page.waitForFunction(() => /connected/.test(document.getElementById('hx-status')?.textContent || ''));
    return page;
  };
  return { context, page: await open(), open };
};
const comment = async (page, selector, text) => {
  await page.click('#hx-mode');
  await page.click(selector);
  await page.fill('.hx-composer textarea', text);
  await page.click('.hx-composer [data-act=save]');
};
const threadsOf = page => page.evaluate(() => [...document.querySelectorAll('#hx-threads .hx-thread')].map(t => ({
  status: t.querySelector('.hx-pill')?.textContent,
  messages: [...t.querySelectorAll(':scope > .hx-msg')].map(m => ({ who: m.querySelector('.hx-who').textContent, text: m.querySelector('.hx-text')?.textContent })),
  history: [...t.querySelectorAll('.hx-history li')].map(li => li.textContent),
})));
const byText = (threads, text) => threads.filter(t => t.messages[0]?.text === text);
const waitThread = (page, text, extra = 'true') => page.waitForFunction(([text, extra]) =>
  [...document.querySelectorAll('#hx-threads .hx-thread')].some(t => { const m = t.querySelector(':scope > .hx-msg'); return m && m.querySelector('.hx-text')?.textContent === text && eval(extra); }), [text, extra], LIVE);

try {
  const a = await reviewer();
  const b = await reviewer();

  // #acceptance-no-presence: before anyone saves, no list of viewers and no other reviewer's selection.
  const presence = await b.page.evaluate(() => Boolean(/view(ing|ers)|online now|cursor/i.test(document.querySelector('.hx-panel, .hx-toolbar')?.textContent || '') || document.querySelectorAll('[class*=presence],[class*=cursor-]').length));
  assert.equal(presence, false, 'no presence surface');

  // #acceptance-live-comment and #model-fields
  await comment(a.page, TOTALS, 'first from A');
  await waitThread(b.page, 'first from A');
  const [stored] = spoolEvents();
  const nameA = stored.body.author;
  assert.match(stored.name, /^\d{19}-comment-[A-Za-z0-9]+\.json$/, 'the page names the file');
  assert.ok(nameA && stored.body.browser, 'a human event carries browser and author');
  assert.equal(stored.body.version, createHash('sha256').update(readFileSync(join(specs, 'report.spec.html'))).digest('hex'), 'version is the shown spec hash');
  assert.deepEqual(byText(await threadsOf(b.page), 'first from A').map(t => t.messages[0].who), [nameA], "B's page shows A's fruit name");

  // #acceptance-name: no prompt; after reload, A's next message shows the same fruit.
  await a.page.close();
  a.page = await a.open();
  await comment(a.page, EXPORT, 'second from A');
  await waitThread(a.page, 'second from A');
  assert.equal(new Set(spoolEvents().map(e => e.body.author)).size, 1, 'one fruit for this browser across reloads');
  assert.equal(new Set(spoolEvents().map(e => e.body.browser)).size, 1);

  // #acceptance-handoff-own
  await comment(b.page, TOTALS, 'draft from B');
  await waitThread(a.page, 'draft from B');
  await a.page.click('#hx-handoff');
  await waitThread(b.page, 'first from A', "t.querySelector('.hx-pill').textContent === 'pending'");
  for (const page of [a.page, b.page]) {
    await waitThread(page, 'second from A', "t.querySelector('.hx-pill').textContent === 'pending'");
    assert.deepEqual(byText(await threadsOf(page), 'draft from B').map(t => t.status), ['draft'], "B's draft stays a draft");
  }
  const handoff = spoolEvents().find(e => e.body.event === 'handoff').body;
  assert.equal(handoff.events.length, 2, "A's hand-off lists A's two drafts only");

  // #acceptance-edit-race: both edit A's pending message; the later save shows; history lists both.
  for (const page of [a.page, b.page]) await page.locator('.hx-thread', { hasText: 'first from A' }).locator('[data-act=edit]').click();
  for (const [page, text] of [[a.page, 'edit by A'], [b.page, 'edit by B']]) {
    await page.fill('.hx-thread.active .hx-composer textarea', text);
    await page.click('.hx-thread.active .hx-composer [data-act=save]');
    await page.waitForTimeout(20);
  }
  for (const page of [a.page, b.page]) {
    await waitThread(page, 'edit by B', "t.querySelectorAll('.hx-history li').length === 2");
    const [raced] = byText(await threadsOf(page), 'edit by B');
    assert.equal(raced.messages.length, 1);
    assert.equal(raced.history.length, 2);
    assert.equal(raced.history[0], nameA + ' edit by A');
    const nameB = spoolEvents().find(e => e.body.text === 'edit by B').body.author;
    assert.equal(raced.history[1], (nameB === nameA ? nameA + ' 2' : nameB) + ' edit by B', "B's edit shows B's name");
  }

  // #acceptance-lost-response: the first response is lost; the resend stores nothing new.
  let dropped = false;
  await a.page.route('**/api/events?*name=*', async route => {
    if (dropped) return route.continue();
    dropped = true;
    await route.fetch();
    await route.abort('failed');
  });
  await comment(a.page, TOTALS, 'lost response');
  await a.page.waitForFunction(() => localStorage.getItem('spec-chat:outbox:specs/report.spec.html.review') === '[]', null, { timeout: 5000 });
  await a.page.unroute('**/api/events?*name=*');
  assert.ok(dropped);
  assert.equal(spoolEvents().filter(e => e.body.text === 'lost response').length, 1, 'stored once');
  await waitThread(b.page, 'lost response');
  assert.equal(byText(await threadsOf(b.page), 'lost response').length, 1, 'shown once');

  // #offline-notice: a save that never answers stalls no read; it times out, waits, and is sent again.
  let hung = 0;
  await a.page.route('**/api/events?*name=*', route => { if (hung++ === 0) return; return route.continue(); });
  await comment(a.page, TOTALS, 'hung save');
  await a.page.waitForTimeout(500);
  await comment(b.page, EXPORT, 'read past a hung save');
  await waitThread(a.page, 'read past a hung save');
  assert.equal(spoolEvents().filter(e => e.body.text === 'hung save').length, 0, 'the hung save is not stored yet');
  await a.page.waitForFunction(() => document.getElementById('hx-offline').textContent === 'Offline: 1 change waiting', null, { timeout: 12000 });
  await a.page.waitForFunction(() => !document.getElementById('hx-offline').textContent, null, { timeout: 5000 });
  await a.page.unroute('**/api/events?*name=*');
  assert.ok(hung >= 2, 'the stuck event is sent again');
  assert.equal(spoolEvents().filter(e => e.body.text === 'hung save').length, 1, 'stored once after the retry');
  await waitThread(b.page, 'hung save');

  // #acceptance-offline-save, #acceptance-offline-reconnect
  await a.context.setOffline(true);
  await comment(a.page, EXPORT, 'offline from A');
  await a.page.waitForFunction(() => document.getElementById('hx-offline').textContent === 'Offline: 1 change waiting', null, { timeout: 3000 });
  assert.deepEqual(byText(await threadsOf(a.page), 'offline from A').map(t => t.status), ['draft']);
  await comment(b.page, EXPORT, 'meanwhile from B');
  await b.page.waitForTimeout(300);
  await a.context.setOffline(false);
  await a.page.waitForFunction(() => !document.getElementById('hx-offline').textContent, null, LIVE);
  for (const page of [a.page, b.page]) {
    await waitThread(page, 'offline from A');
    await waitThread(page, 'meanwhile from B');
    const all = await threadsOf(page);
    assert.equal(byText(all, 'offline from A').length, 1);
    assert.equal(byText(all, 'meanwhile from B').length, 1);
  }

  // #acceptance-offline-closed
  await a.context.setOffline(true);
  await comment(a.page, TOTALS, 'closed while offline');
  await a.page.waitForFunction(() => document.getElementById('hx-offline').textContent === 'Offline: 1 change waiting', null, { timeout: 3000 });
  await a.page.close();
  await a.context.setOffline(false);
  a.page = await a.open();
  await waitThread(a.page, 'closed while offline', "t.querySelector('.hx-pill').textContent === 'draft'");
  await waitThread(b.page, 'closed while offline');
  assert.equal(spoolEvents().filter(e => e.body.text === 'closed while offline').length, 1);
  await a.context.close();
  await b.context.close();

  // #acceptance-name-collision: two browsers given the same fruit.
  const used = new Set(spoolEvents().map(e => e.body.author));
  const fruit = ['Guava', 'Salak', 'Longan'].find(name => !used.has(name));
  const c = await reviewer({ browser: 'collisionone0000000', author: fruit });
  const d = await reviewer({ browser: 'collisiontwo0000000', author: fruit });
  await comment(c.page, TOTALS, 'guava first');
  await waitThread(d.page, 'guava first');
  await comment(d.page, TOTALS, 'guava second');
  for (const page of [c.page, d.page]) {
    await waitThread(page, 'guava second');
    const all = await threadsOf(page);
    assert.equal(byText(all, 'guava first')[0].messages[0].who, fruit);
    assert.equal(byText(all, 'guava second')[0].messages[0].who, fruit + ' 2');
  }
  assert.deepEqual(errors, [], 'no page errors');
} finally {
  await browser.close();
  server.kill();
  rmSync(repo, { recursive: true, force: true });
}

console.log('runtime review live tests passed');
