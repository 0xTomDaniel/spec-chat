import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');

const paramsStart = runtime.indexOf('function jevParams(');
const paramsEnd = runtime.indexOf('\n\nasync function fetchJev(', paramsStart);
assert.ok(paramsStart >= 0 && paramsEnd > paramsStart, 'runtime exposes Jev request parameters');
const jevParams = Function('location', 'URLSearchParams', 'state', runtime.slice(paramsStart, paramsEnd) + '; return jevParams;')(
  { pathname: '/docs/specs/example.spec.html', search: '?view=reading&extra=ignored' },
  URLSearchParams,
  { readingView: false },
);
const params = jevParams('base-123');
assert.equal(params.get('path'), 'docs/specs/example.spec.html');
assert.equal(params.get('base'), 'base-123');
assert.equal(params.has('view'), false);
assert.equal(params.has('extra'), false);

const fetchStart = runtime.indexOf('async function fetchJev(');
const fetchEnd = runtime.indexOf('\n\nfunction jevItem(', fetchStart);
assert.ok(fetchStart >= 0 && fetchEnd > fetchStart, 'runtime exposes Jev route consumer');
let requested = '';
const fakeFetch = async url => {
  requested = url;
  return { ok: true, json: async () => ({ jev: 'on', items: [
    { kind: 'type', id: 'change-type', state: 'label', label: 'behavioral', target: null, record: 'r1' },
    { kind: 'orphan', id: 'thread-1', state: 'label', label: 'one candidate', target: 'new-section', record: 'r2' },
  ] }) };
};
const fetchJev = Function('fetch', 'jevParams', 'location', 'URLSearchParams', runtime.slice(fetchStart, fetchEnd) + '; return fetchJev;')(
  fakeFetch,
  jevParams,
  { pathname: '/docs/specs/example.spec.html', search: '' },
  URLSearchParams,
);
const answer = await fetchJev('base-123');
assert.match(requested, /^\/api\/jev\\?/);
assert.deepEqual(answer, {
  jev: 'on',
  items: [
    { kind: 'type', id: 'change-type', state: 'label', label: 'behavioral', target: null, record: 'r1' },
    { kind: 'orphan', id: 'thread-1', state: 'label', label: 'one candidate', target: 'new-section', record: 'r2' },
  ],
});
assert.equal((runtime.match(/fetch\('\/api\/jev\?/g) || []).length, 1, 'all Jev display uses one request seam');
assert.match(runtime, /120000/, 'Jev fetch allows a cold provider request to finish');
assert.match(runtime, /resolvedHint\.label === 'resolved in spirit'/, 'unrelated resolved answers never display');
assert.match(runtime, /if \(!item\.id \|\| item\.state === 'none'\) continue/, 'explicit none answers render no suggestion');
assert.match(runtime, /if \(!gitFocus \|\| item\.kind !== 'type'\) continue/, 'type badges require Git focus');
assert.match(runtime, /if \(!gitFocus\) return;/, 'corpus flags require Git focus');
assert.doesNotMatch(runtime, /async function loadJev\(/, 'coverage shares the main Jev request');
assert.doesNotMatch(runtime, /const jevState/, 'coverage shares the main Jev state');
assert.match(runtime, /renderPanel\(\);\n  renderPins\(\);/, 'base changes clear thread and pin Jev displays');

const moveStart = runtime.indexOf('async function moveOrphan(');
const moveEnd = runtime.indexOf('\n\nconst chartInfoFor', moveStart);
assert.ok(moveStart >= 0 && moveEnd > moveStart, 'runtime exposes orphan move action');
const posted = [];
const moveState = {
  movingOrphans: new Set(),
  transport: { postEvent: async event => posted.push(event) },
  activeThread: 'thread-1',
};
const moveOrphan = Function('state', 'humanId', 'renderPanel', 'toast', 'refresh', 'renderPins', runtime.slice(moveStart, moveEnd) + '; return moveOrphan;')(
  moveState,
  prefix => prefix + 'fixed',
  () => {},
  () => {},
  async () => {},
  () => {},
);
await moveOrphan({ id: 'thread-1', ev: { body: { quote: 'old quote', text: 'Original note' } } }, 'new-section');
assert.equal(posted.length, 2);
assert.equal(posted[0].event, 'comment');
assert.equal(posted[0].anchorId, 'new-section');
assert.equal(posted[0].quote, 'old quote');
assert.equal(posted[0].text, 'Original note');
assert.equal(posted[1].event, 'status');
assert.equal(posted[1].status, 'resolved');

console.log('runtime Jev contract tests passed');
