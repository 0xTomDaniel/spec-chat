import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

// While any Jev item is pending the page reads /api/jev again every 2 s for the same base and shows each
// answer as it arrives; it stops when nothing is pending, the base changes, or the page is hidden, and resumes
// when shown (jev-suggestions #fast-marks-background, #state-pending, #acceptance-background).
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
const slice = (from, to) => {
  const a = runtime.indexOf(from), b = runtime.indexOf(to, a);
  assert.ok(a >= 0 && b > a, 'runtime exposes ' + from);
  return runtime.slice(a, b);
};
const code = slice('function jevParams(', '\nfunction jevItem(') + slice('function clearJev(', '\n// One evidence read per page');

function harness(responses) {
  let now = 0, next = 1;
  const timers = new Map();
  const listeners = {};
  const fetched = [];
  const renders = [];
  const env = {
    state: { readingView: false, jev: { status: 'idle', items: [], levels: {}, base: null, request: 0 } },
    location: { protocol: 'http:', pathname: '/docs/specs/a.spec.html' },
    document: { hidden: false, addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); } },
    setTimeout: (fn, ms) => { timers.set(next, { at: now + ms, fn }); return next++; },
    clearTimeout: id => { timers.delete(id); },
    fetch: async url => {
      fetched.push({ at: now, base: new URL(url, 'http://x').searchParams.get('base') });
      const body = responses.shift();
      if (body instanceof Error) throw body;
      return { ok: true, json: async () => body };
    },
  };
  env.renderJev = () => renders.push({ at: now, items: env.state.jev.items.map(item => item.state + ':' + item.label) });
  const api = new Function(...Object.keys(env), 'renderPanel', 'renderPins',
    code + '; return { requestJev, clearJev };')(...Object.values(env), () => {}, () => {});
  const settle = () => new Promise(done => setImmediate(done));
  return {
    ...api, env, fetched, renders, listeners,
    async advance(ms) {
      const end = now + ms;
      for (;;) {
        const due = [...timers.entries()].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        timers.delete(due[0]);
        now = due[1].at;
        due[1].fn();
        await settle();
      }
      now = end;
      await settle();
    },
    settle,
  };
}

const item = (id, state, label = null) => ({ kind: 'type', id, state, label, target: null, record: null, level: null });
const answer = items => ({ jev: 'on', items, levels: { scope: 'warning' } });

{ // A mark arrives without a reload; unchanged re-reads render nothing; with nothing pending, no more reads.
  const page = harness([
    answer([item('a', 'pending'), item('b', 'pending')]),
    answer([item('a', 'pending'), item('b', 'pending')]),
    answer([item('a', 'label', 'scope'), item('b', 'pending')]),
    answer([item('a', 'label', 'scope'), item('b', 'label', 'scope')]),
  ]);
  await page.requestJev('base1');
  await page.settle();
  assert.equal(page.fetched.length, 1);
  const rendered = page.renders.length;
  await page.advance(2000);
  assert.deepEqual(page.fetched.map(read => read.base), ['base1', 'base1'], 'same base again after 2 s');
  assert.equal(page.renders.length, rendered, 'an unchanged re-read changes nothing on the page');
  await page.advance(2000);
  assert.deepEqual(page.renders.at(-1), { at: 4000, items: ['label:scope', 'pending:null'] }, 'the answer shows as it arrives');
  await page.advance(2000);
  assert.deepEqual(page.renders.at(-1).items, ['label:scope', 'label:scope']);
  await page.advance(60000);
  assert.equal(page.fetched.length, 4, 'no /api/jev request once nothing is pending');
}

{ // Hidden pages stop asking and resume when shown.
  const page = harness([answer([item('a', 'pending')]), answer([item('a', 'label', 'scope')])]);
  await page.requestJev('base1');
  await page.settle();
  page.env.document.hidden = true;
  page.listeners.visibilitychange.forEach(fn => fn());
  await page.advance(10000);
  assert.equal(page.fetched.length, 1, 'no request while hidden');
  page.env.document.hidden = false;
  page.listeners.visibilitychange.forEach(fn => fn());
  await page.advance(2000);
  assert.equal(page.fetched.length, 2, 'showing the page resumes');
  assert.deepEqual(page.renders.at(-1).items, ['label:scope']);
}

{ // A base change stops the old base's re-reads.
  const page = harness([answer([item('a', 'pending')]), answer([item('a', 'label', 'scope')])]);
  await page.requestJev('base1');
  await page.settle();
  page.clearJev();
  await page.requestJev('base2');
  await page.settle();
  await page.advance(10000);
  assert.deepEqual(page.fetched.map(read => read.base), ['base1', 'base2']);
}

{ // A failed re-read keeps what shows and tries again.
  const page = harness([answer([item('a', 'pending')]), new Error('offline'), answer([item('a', 'label', 'scope')])]);
  await page.requestJev('base1');
  await page.settle();
  await page.advance(2000);
  assert.equal(page.env.state.jev.status, 'on');
  assert.deepEqual(page.env.state.jev.items.map(i => i.state), ['pending']);
  await page.advance(2000);
  assert.deepEqual(page.renders.at(-1).items, ['label:scope']);
  assert.equal(page.fetched.length, 3);
}

console.log('runtime Jev background tests passed');
