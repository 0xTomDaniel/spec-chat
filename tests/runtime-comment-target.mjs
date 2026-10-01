import assert from 'node:assert/strict';
import { cpSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

// In comment mode every leaf anchored block is a button: button role, a Tab stop, named by its
// text; Enter and Space open the composer on it like a click on the block; text selection still
// comments on the selected text; leaving comment mode restores each block's own role and Tab order
// (prompt-first-shaping #comment-targets, #tdd-block-target). Runs the full runtime on the QA
// fixture's report spec in headless Chromium. Needs PLAYWRIGHT_CORE=<path to playwright-core>.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
assert.equal(readFileSync(resolve(root, 'docs/specs/.viz/runtime.js'), 'utf8'), runtime, 'runtime copies are identical');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_CORE || 'playwright-core');

const site = mkdtempSync(join(tmpdir(), 'comment-target-'));
const specs = join(site, 'docs/specs');
cpSync(resolve(root, 'tests/fixtures/qa/head/docs/specs'), specs, { recursive: true });
cpSync(resolve(root, 'docs/specs/.viz'), join(specs, '.viz'), { recursive: true });
cpSync(resolve(root, 'docs/specs/.style'), join(specs, '.style'), { recursive: true });
const TOTALS = 'p[data-anchor="report-totals"]';

const browser = await chromium.launch();
try {
  const open = async () => {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));
    await page.goto('file://' + join(specs, 'report.spec.html'));
    await page.waitForSelector('#hx-mode');
    // a block owning its own role and Tab order before comment mode
    await page.evaluate(() => {
      const own = document.querySelector('[data-anchor="context"]');
      own.setAttribute('role', 'note');
      own.setAttribute('tabindex', '-1');
    });
    return { page, errors };
  };
  const tabTo = async (page, selector) => {
    for (let i = 0; i < 200; i++) {
      await page.keyboard.press('Tab');
      if (await page.evaluate(s => document.activeElement === document.querySelector(s), selector)) return;
    }
    assert.fail('Tab never reached ' + selector);
  };
  const composer = async page => {
    // openComposer focuses the text box on the next task
    await page.waitForFunction(() => document.activeElement && document.activeElement.matches('.hx-composer textarea'), null, { timeout: 2000 }).catch(() => {});
    return page.evaluate(() => {
    const box = document.querySelector('.hx-composer');
    return box && { head: box.closest('.hx-thread').querySelector('.hx-anchor').textContent, focused: document.activeElement === box.querySelector('textarea') };
    });
  };

  // Reading view: no buttons, no Tab stops on blocks.
  {
    const { page } = await open();
    const reading = await page.evaluate(s => {
      const p = document.querySelector(s);
      return { role: p.getAttribute('role'), tabindex: p.getAttribute('tabindex') };
    }, TOTALS);
    assert.deepEqual(reading, { role: null, tabindex: null }, 'reading view adds no button role or Tab stop');

    await page.click('#hx-mode');
    const on = await page.evaluate(() => {
      const leaves = [...document.querySelectorAll('[data-anchor]')].filter(b => !b.querySelector('[data-anchor]'));
      const parents = [...document.querySelectorAll('[data-anchor]')].filter(b => b.querySelector('[data-anchor]'));
      return {
        leaves: leaves.length,
        buttons: leaves.filter(b => b.getAttribute('role') === 'button' && b.tabIndex === 0).length,
        parentButtons: parents.filter(b => b.getAttribute('role') === 'button').length,
      };
    });
    assert.ok(on.leaves > 5, 'fixture has leaf anchored blocks');
    assert.equal(on.buttons, on.leaves, 'comment mode makes every leaf anchored block a button Tab stop');
    assert.equal(on.parentButtons, 0, 'a block holding anchored blocks is not a button');
    const name = await page.getByRole('button', { name: 'The report page shows totals per week.' }).count();
    assert.equal(name, 1, 'the block button is named by its own text');

    // Off: each block back to its own role and Tab order.
    await page.click('#hx-mode');
    const off = await page.evaluate(s => {
      const p = document.querySelector(s), own = document.querySelector('[data-anchor="context"]');
      return {
        buttons: document.querySelectorAll('[data-anchor][role="button"]').length,
        tabStops: document.querySelectorAll('[data-anchor][tabindex="0"]').length,
        p: [p.getAttribute('role'), p.getAttribute('tabindex')],
        own: [own.getAttribute('role'), own.getAttribute('tabindex')],
      };
    }, TOTALS);
    assert.deepEqual(off, { buttons: 0, tabStops: 0, p: [null, null], own: ['note', '-1'] }, 'comment mode off restores own role and Tab order');
    await page.keyboard.press('Tab');
    const focus = await page.evaluate(() => ({ inSpec: Boolean(document.activeElement.closest('[data-anchor]')), tag: document.activeElement.tagName }));
    assert.ok(!focus.inSpec, 'Tab after comment mode off lands on a page control, not a spec block (' + focus.tag + ')');
    await page.close();
  }

  // Enter and Space open the composer on the focused block, text box focused.
  for (const key of ['Enter', ' ']) {
    const { page, errors } = await open();
    await page.click('#hx-mode');
    await tabTo(page, TOTALS);
    await page.keyboard.press(key);
    const box = await composer(page);
    assert.ok(box, (key === ' ' ? 'Space' : key) + ' opens the composer');
    assert.match(box.head, /^#report-totals$/, 'composer is headed #report-totals with no inner target');
    assert.ok(box.focused, 'composer text box is focused');
    assert.deepEqual(errors, [], 'no page errors');
    await page.close();
  }

  // Text selection inside a block still works and comments on the selected text.
  {
    const { page } = await open();
    await page.click('#hx-mode');
    await page.locator(TOTALS).scrollIntoViewIfNeeded();
    const p = await page.locator(TOTALS).boundingBox();
    await page.mouse.move(p.x + 2, p.y + p.height / 2);
    await page.mouse.down();
    await page.mouse.move(p.x + 120, p.y + p.height / 2, { steps: 8 });
    await page.mouse.up();
    const box = await composer(page);
    assert.ok(box, 'selecting text in a block opens the composer');
    assert.match(box.head, /^#report-totals › The report/, 'composer targets the selected text');
    await page.close();
  }
} finally {
  await browser.close();
  rmSync(site, { recursive: true, force: true });
}

console.log('runtime comment target tests passed');
