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

// #acceptance-note-draft: a draft button only opens the existing composer with fixed text; nothing is written.
const composerStart = runtime.indexOf('function openComposer(');
const composerEnd = runtime.indexOf('\n\nconst label', composerStart);
assert.ok(composerStart >= 0 && composerEnd > composerStart, 'runtime exposes the comment composer');
const composerState = { transport: { postEvent: async event => posted.push(event) } };
const openComposer = Function('state', 'setCommentMode', 'openPanel', 'renderPanel', 'setTimeout',
  runtime.slice(composerStart, composerEnd) + '; return openComposer;')(composerState, () => {}, () => {}, () => {}, () => {});
posted.length = 0;
openComposer('story-a', null, null, 'Add an acceptance criterion that verifies this story.');
assert.deepEqual(composerState.composer, { kind: 'comment', anchorId: 'story-a', target: null, quote: null,
  text: 'Add an acceptance criterion that verifies this story.' });
openComposer('clause-a', { type: 'element', key: 'p:1' }, 'quote');
assert.equal(composerState.composer.text, '', 'ordinary comments still open empty');
assert.equal(posted.length, 0, 'opening a draft writes nothing');

// #acceptance-note-resolve: Resolve thread on a Looks resolved card posts what the card's own resolve control posts.
const resolveStart = runtime.indexOf('function threadResolveButtons(');
const resolveEnd = runtime.indexOf('\n\nfunction selectThread(', resolveStart);
assert.ok(resolveStart >= 0 && resolveEnd > resolveStart, 'runtime exposes the shared resolve path');
const resolveState = { expandedResolved: new Set(['t1']), transport: { postEvent: async event => posted.push(event) } };
const { threadResolveButtons, resolveThread } = Function('state', 'humanId', 'toast', 'refresh',
  runtime.slice(resolveStart, resolveEnd) + '; return { threadResolveButtons, resolveThread };')(
  resolveState, prefix => prefix + 'fixed', () => {}, () => {});
const acts = (status, looks) => threadResolveButtons({ id: 't1', status }, looks).map(b => b.act + ':' + b.label);
assert.deepEqual(acts('pending', true), ['jev-resolve:Resolve thread']);
assert.deepEqual(acts('acknowledged', true), ['jev-resolve:Resolve thread', 'resolve:✓ Resolve']);
assert.deepEqual(acts('acknowledged', false), ['resolve:✓ Resolve']);
assert.deepEqual(acts('resolved', true), []);
assert.deepEqual(acts('pending', false), []);
assert.match(runtime, /for \(const action of threadResolveButtons\(th, looksResolved\)\) \{[^}]*resolveThread\(th\)/,
  'every resolve button on a card runs the one resolve path');
posted.length = 0;
await resolveThread({ id: 't1' });
assert.equal(posted.length, 1);
assert.deepEqual({ ...posted[0], createdAt: null }, { id: 'sfixed', event: 'status', respondsTo: 't1', threadId: 't1', status: 'resolved',
  actor: 'human', createdAt: null, schemaVersion: 1 });
assert.equal(resolveState.expandedResolved.has('t1'), false);

console.log('runtime Jev contract tests passed');
