import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { cpSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

// Answer buttons (prompt-first-shaping #answer-buttons, #tdd-choice): pressing a data-spec-choice
// button outside comment mode saves one draft comment through the existing comment save, on the
// nearest anchored block, targeting that button, its answer as text; it opens no composer or panel;
// the chosen mark derives from the review state (reload, every reviewer's page); pressing the chosen
// button again saves nothing; comment mode's composer path is unchanged. #acceptance-choice-event:
// the stored answer is the same comment event a typed comment writes, and the hand-off lists it.
// The full runtime on the QA fixture's report spec, whose export-format TBD offers two answers,
// served by the real review-serve.py, in headless Chromium. Needs PLAYWRIGHT_CORE=<path to playwright-core>.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
assert.equal(readFileSync(resolve(root, 'docs/specs/.viz/runtime.js'), 'utf8'), runtime, 'runtime copies are identical');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_CORE || 'playwright-core');

// One copy of the report spec per scenario: each has its own spool.
const SCENARIOS = ['press', 'change', 'moved', 'early', 'phone', 'comment-mode', 'plain'];
const repo = mkdtempSync(join(tmpdir(), 'answer-buttons-'));
const specs = join(repo, 'docs/specs');
cpSync(resolve(root, 'tests/fixtures/qa/head/docs/specs'), specs, { recursive: true });
cpSync(resolve(root, 'docs/specs/.viz'), join(specs, '.viz'), { recursive: true });
cpSync(resolve(root, 'docs/specs/.style'), join(specs, '.style'), { recursive: true });
for (const name of SCENARIOS) cpSync(join(specs, 'report.spec.html'), join(specs, name + '.spec.html'));
const git = (...args) => execFileSync('git', ['-C', repo, ...args], { env: { ...process.env, GIT_CONFIG_GLOBAL: '/dev/null', GIT_AUTHOR_NAME: 't', GIT_AUTHOR_EMAIL: 't@x', GIT_COMMITTER_NAME: 't', GIT_COMMITTER_EMAIL: 't@x' } });
git('init', '-q', '-b', 'main');
git('add', '-A');
git('commit', '-qm', 'fixture');
const spool = name => {
  const dir = join(specs, name + '.spec.html.review/human');
  try { return readdirSync(dir).filter(n => n.endsWith('.json')).sort().map(n => JSON.parse(readFileSync(join(dir, n), 'utf8'))); } catch { return []; }
};
const comments = name => spool(name).filter(b => b.event === 'comment');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const until = async (check, what) => {
  for (const end = Date.now() + 4000; Date.now() < end; await sleep(50)) if (check()) return;
  assert.fail('timed out: ' + what);
};

const port = await new Promise(res => { const s = createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); });
const server = spawn('python3', [resolve(root, 'skill/review-spec/assets/review-serve.py'), join(repo, 'docs'), String(port)], {
  stdio: 'ignore', env: { ...process.env, HOME: join(repo, '.home'), XDG_STATE_HOME: join(repo, '.state'), XDG_CONFIG_HOME: join(repo, '.config'), HERDR_SOCKET_PATH: '' },
});
// Stop the server on any exit, also when a step throws before the finally below.
process.on('exit', () => server.kill());
const url = name => `http://127.0.0.1:${port}/specs/${name}.spec.html`;
for (let i = 0; ; i++) {
  try { if ((await fetch(url('press'))).ok) break; } catch {}
  if (i > 100) throw new Error('review-serve did not start');
  await sleep(100);
}

const ANSWERS = '[data-anchor="export-format"] button[data-spec-choice]';
const FIRST = 'CSV only (recommended)';
const SECOND = 'CSV and XLSX';
const SAVED = 'Comment saved as draft — hand off when ready';
const browser = await chromium.launch();
const errors = [];
const connected = page => page.waitForFunction(() => /connected/.test(document.getElementById('hx-status')?.textContent || ''));
const reviewer = async (name, options = { viewport: { width: 1280, height: 800 } }) => {
  const context = await browser.newContext(options);
  const page = await context.newPage();
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto(url(name));
  await connected(page);
  return { context, page };
};
const answer = (page, text) => page.getByRole('button', { name: text, exact: true });
const pressed = page => page.evaluate(s => [...document.querySelectorAll(s)].map(b => b.getAttribute('aria-pressed')), ANSWERS);
const showsPressed = (page, want) => page.waitForFunction(([s, want]) =>
  JSON.stringify([...document.querySelectorAll(s)].map(b => b.getAttribute('aria-pressed'))) === want, [ANSWERS, JSON.stringify(want)], { timeout: 4000 });
const surface = page => page.evaluate(() => ({
  composer: document.querySelectorAll('.hx-composer').length,
  panel: document.querySelector('.hx-panel').classList.contains('open'),
  toast: document.getElementById('hx-toast')?.textContent || null,
}));
const threads = page => page.evaluate(() => [...document.querySelectorAll('#hx-threads .hx-thread')].map(t => ({
  head: t.querySelector('.hx-anchor').textContent, status: t.querySelector('.hx-pill').textContent, text: t.querySelector('.hx-text').textContent,
})));

try {
  // #acceptance-choice-press, -again, -thread, -handoff, -event
  {
    const { context, page } = await reviewer('press');
    await answer(page, FIRST).click();
    await until(() => comments('press').length === 1, 'pressing an answer saves one comment');
    const [stored] = comments('press');
    assert.deepEqual(await pressed(page), ['true', 'false'], 'the pressed answer shows as chosen, the other is a toggle not chosen');
    assert.deepEqual(await surface(page), { composer: 0, panel: false, toast: SAVED }, 'no composer or panel; the saved-as-draft notice confirms');
    const mark = await page.evaluate(s => getComputedStyle(document.querySelector(s + '[aria-pressed="true"]'), '::before').content, ANSWERS);
    assert.match(mark, /✓/, 'chosen is marked by more than color');
    assert.deepEqual({ anchorId: stored.anchorId, target: stored.target, text: stored.text, quote: stored.quote },
      { anchorId: 'export-format', target: { type: 'element', key: 'button[1]' }, text: 'CSV only', quote: FIRST },
      'one comment on the nearest anchor, targeting that button, its answer as text');

    await answer(page, FIRST).click();
    await sleep(400);
    assert.equal(comments('press').length, 1, 'pressing the chosen button again saves nothing');
    assert.deepEqual(await pressed(page), ['true', 'false']);

    await page.click('#hx-dock-open');
    assert.deepEqual(await threads(page), [{ head: '#export-format › button[1]', status: 'Draft', text: 'CSV only' }], 'one draft thread headed with the anchor and that button');
    assert.equal(await page.textContent('#hx-drafts'), '1 draft');

    await page.click('#hx-handoff');
    await page.waitForFunction(() => document.getElementById('hx-toast')?.textContent === 'Handed off 1 comment, agent notified');
    assert.deepEqual(await pressed(page), ['true', 'false'], 'the answer stays chosen after hand-off');
    await until(() => spool('press').some(b => b.event === 'handoff'), 'the hand-off is stored');
    assert.deepEqual(spool('press').find(b => b.event === 'handoff').events, [stored.id], 'the hand-off sends the answer comment');

    // The same comment event a typed comment writes.
    await page.getByRole('button', { name: 'Comment', exact: true }).click(); // the toolbar's Comment button, by its name
    await page.click('p[data-anchor="report-totals"]');
    await page.fill('.hx-composer textarea', 'typed');
    await page.click('.hx-composer [data-act=save]');
    await until(() => comments('press').length === 2, 'the typed comment is stored');
    const typed = comments('press').find(b => b.text === 'typed');
    assert.deepEqual(Object.keys(stored).sort(), Object.keys(typed).sort(), 'an answer has exactly the fields of a typed comment');
    for (const field of ['event', 'actor', 'schemaVersion', 'browser', 'author', 'version']) assert.equal(stored[field], typed[field], field);
    await context.close();
  }

  // #acceptance-choice-change, -reload; #choice-chosen-derived: every reviewer's page.
  {
    const a = await reviewer('change');
    await answer(a.page, FIRST).click();
    await answer(a.page, SECOND).click();
    assert.deepEqual(await pressed(a.page), ['false', 'true'], 'the chosen mark moves to the newer answer');
    await until(() => comments('change').length === 2, 'each answer is its own comment');
    assert.deepEqual(comments('change').map(c => c.text), ['CSV only', 'CSV and XLSX']);
    assert.deepEqual(comments('change').map(c => c.target.key), ['button[1]', 'button[2]']);

    await a.page.reload();
    await connected(a.page);
    await showsPressed(a.page, ['false', 'true']);

    const b = await reviewer('change');
    await showsPressed(b.page, ['false', 'true']);
    await answer(a.page, SECOND).click();
    await sleep(400);
    assert.equal(comments('change').length, 2, 'the chosen answer pressed again saves nothing');
    await answer(a.page, FIRST).click();
    await showsPressed(a.page, ['true', 'false']);
    await showsPressed(b.page, ['true', 'false']);
    await a.context.close();
    await b.context.close();
  }

  // #choice-chosen, #choice-change through an agent edit: an answer inserted before the chosen one shifts
  // its positional key; the chosen mark follows the service's place, where the pin sits.
  {
    const { context, page } = await reviewer('moved');
    await answer(page, FIRST).click();
    await until(() => comments('moved').length === 1, 'the answer is stored');
    const file = join(specs, 'moved.spec.html');
    writeFileSync(file, readFileSync(file, 'utf8').replace('<button type="button" data-spec-choice="CSV only">', '<button type="button" data-spec-choice="XLSX only">XLSX only</button> $&'));
    await page.reload();
    await connected(page);
    await showsPressed(page, ['false', 'true', 'false']);
    assert.equal(await page.getAttribute('[data-anchor="export-format"] .hx-pin', 'aria-label'), '#export-format › button[2]', 'the pin stays on CSV only');
    await answer(page, FIRST).click();
    await sleep(400);
    assert.equal(comments('moved').length, 1, 'the moved chosen answer pressed again saves nothing');
    await answer(page, 'XLSX only').click();
    await until(() => comments('moved').length === 2, 'the inserted answer saves');
    assert.deepEqual(comments('moved')[1].target, { type: 'element', key: 'button[1]' });
    await showsPressed(page, ['true', 'false', 'false']);
    await context.close();
  }

  // #choice-change: a press before the review state loads cannot know the chosen answer, so it saves nothing.
  {
    const a = await reviewer('early');
    await answer(a.page, FIRST).click();
    await until(() => comments('early').length === 1, 'the first answer is stored');
    await a.context.close();
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const page = await context.newPage();
    page.on('pageerror', e => errors.push(String(e)));
    let release;
    const held = new Promise(r => { release = r; });
    await page.route(u => u.pathname === '/api/events', async route => { await held; await route.continue(); });
    const loading = page.waitForRequest(r => new URL(r.url()).pathname === '/api/events');
    await page.goto(url('early'));
    await loading;
    await answer(page, FIRST).click();
    release();
    await connected(page);
    await showsPressed(page, ['true', 'false']);
    await sleep(400);
    assert.equal(comments('early').length, 1, 'an early press saves no duplicate');
    await context.close();
  }

  // #acceptance-choice-phone
  {
    const { context, page } = await reviewer('phone', { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    await answer(page, FIRST).tap();
    assert.deepEqual(await pressed(page), ['true', 'false']);
    assert.deepEqual(await surface(page), { composer: 0, panel: false, toast: SAVED }, 'no full-viewport composer or panel on a phone');
    assert.equal(await page.textContent('#hx-mobile-handoff'), 'Hand off (1)');
    await context.close();
  }

  // #acceptance-choice-comment-mode: an ordinary comment target; words added there make an answer too.
  {
    const { context, page } = await reviewer('comment-mode');
    await page.getByRole('button', { name: 'Comment', exact: true }).click(); // the toolbar's Comment button, by its name
    await answer(page, SECOND).click();
    await page.waitForFunction(() => document.activeElement?.matches('.hx-composer textarea'));
    assert.equal(await page.textContent('.hx-thread.active .hx-anchor'), '#export-format › button[2]', 'the composer is on that button');
    assert.deepEqual(await pressed(page), ['false', 'false'], 'opening the composer chooses nothing');
    await sleep(300);
    assert.equal(comments('comment-mode').length, 0, 'opening the composer saves nothing');
    await page.fill('.hx-composer textarea', 'CSV and XLSX, XLSX with one sheet per week');
    await page.click('.hx-composer [data-act=save]');
    await showsPressed(page, ['false', 'true']);
    await until(() => comments('comment-mode').length === 1, 'the composed answer is stored');
    assert.deepEqual(comments('comment-mode')[0].target, { type: 'element', key: 'button[2]' });
    await context.close();
  }

  // #choice-plain: an empty value, or no enclosing anchored block (none, or only the button's own), keeps the button's own behavior.
  {
    const { context, page } = await reviewer('plain');
    await page.evaluate(() => {
      window.own = [];
      const make = (value, text) => {
        const b = Object.assign(document.createElement('button'), { type: 'button', textContent: text });
        b.setAttribute('data-spec-choice', value);
        b.addEventListener('click', () => window.own.push(text));
        return b;
      };
      const self = make('Self', 'Self');
      self.dataset.anchor = 'self-answer';
      document.querySelector('[data-anchor="report-totals"]').append(' ', make('', 'Empty'), ' ', make('   ', 'Blank'), ' ', self);
      document.querySelector('article').prepend(make('Outside', 'Outside'));
    });
    for (const text of ['Empty', 'Blank', 'Self', 'Outside']) await answer(page, text).click();
    await sleep(400);
    assert.deepEqual(await page.evaluate(() => window.own), ['Empty', 'Blank', 'Self', 'Outside'], 'their own handlers run');
    assert.equal(comments('plain').length, 0, 'they save nothing');
    assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('[data-spec-choice]')].filter(b => !b.closest('[data-anchor="export-format"]')).map(b => b.getAttribute('aria-pressed'))),
      [null, null, null, null], 'they get no chosen state');
    assert.equal((await surface(page)).toast, null, 'no saved notice');
    await context.close();
  }
  assert.deepEqual(errors, [], 'no page errors');
} finally {
  await browser.close();
  server.kill();
  rmSync(repo, { recursive: true, force: true });
}

console.log('runtime answer buttons tests passed');
