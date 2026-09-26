import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
const slice = (from, to) => {
  const start = runtime.indexOf(from);
  const end = runtime.indexOf(to, start);
  assert.ok(start >= 0 && end > start, 'runtime exposes ' + from);
  return runtime.slice(start, end);
};

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
    { kind: 'type', id: 'change-type', state: 'label', label: 'behavioral', target: null, record: 'r1', level: 'warning', agent_level: 'warning' },
    { kind: 'orphan', id: 'thread-1', state: 'label', label: 'one candidate', target: 'new-section', record: 'r2' },
    { kind: 'lane', id: 'rule', state: 'label', label: 'contradicts', side: 'first', other: 'ann2', target: 'ann2/y.spec.html#b', record: 'r3', level: 'important', agent_level: 'important' },
  ], levels: { behavioral: { human: 'warning', agent: 'warning' }, contradicts: { human: 'important', agent: 'important' } } }) };
};
const fetchJev = Function('fetch', 'jevParams', 'location', 'URLSearchParams', runtime.slice(fetchStart, fetchEnd) + '; return fetchJev;')(
  fakeFetch,
  jevParams,
  { pathname: '/docs/specs/example.spec.html', search: '' },
  URLSearchParams,
);
const answer = await fetchJev('base-123');
assert.match(requested, /^\/api\/jev\\?/);
// The browser keeps only the human level; agent_level is for the agent read (#markers-levels-source).
assert.deepEqual(answer, {
  jev: 'on',
  items: [
    { kind: 'type', id: 'change-type', state: 'label', label: 'behavioral', target: null, record: 'r1', level: 'warning', side: null, other: null },
    { kind: 'orphan', id: 'thread-1', state: 'label', label: 'one candidate', target: 'new-section', record: 'r2', level: null, side: null, other: null },
    { kind: 'lane', id: 'rule', state: 'label', label: 'contradicts', target: 'ann2/y.spec.html#b', record: 'r3', level: 'important', side: 'first', other: 'ann2' },
  ],
  levels: { behavioral: { human: 'warning', agent: 'warning' }, contradicts: { human: 'important', agent: 'important' } },
});
assert.equal((runtime.match(/fetch\('\/api\/jev\?/g) || []).length, 1, 'all Jev display uses one request seam');
assert.match(runtime, /120000/, 'Jev fetch allows a cold provider request to finish');
assert.match(runtime, /hint\.label === 'resolved in spirit'/, 'unrelated resolved answers never display');
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

// #acceptance-note-resolve: Resolve all (n) resolves every Looks resolved thread with the card's own resolve event.
const resolveStart = runtime.indexOf('async function resolveThreads(');
const resolveEnd = runtime.indexOf('\n\nfunction selectThread(', resolveStart);
assert.ok(resolveStart >= 0 && resolveEnd > resolveStart, 'runtime exposes the shared resolve path');
let refreshed = 0;
const resolveState = { expandedResolved: new Set(['t1', 't3']), transport: { postEvent: async event => posted.push(event) } };
const { resolveThreads, resolveThread } = Function('state', 'humanId', 'toast', 'refresh',
  runtime.slice(resolveStart, resolveEnd) + '; return { resolveThreads, resolveThread };')(
  resolveState, prefix => prefix + 'fixed', () => {}, () => { refreshed++; });
const resolvedEvent = id => ({ id: 'sfixed', event: 'status', respondsTo: id, threadId: id, status: 'resolved', actor: 'human', createdAt: null, schemaVersion: 1 });
posted.length = 0;
await resolveThread({ id: 't1' });
assert.deepEqual(posted.map(e => ({ ...e, createdAt: null })), [resolvedEvent('t1')]);
posted.length = 0;
await resolveThreads([{ id: 't2' }, { id: 't3' }]);
assert.deepEqual(posted.map(e => ({ ...e, createdAt: null })), [resolvedEvent('t2'), resolvedEvent('t3')], 'one resolve event per hinted thread, nothing else');
assert.equal(resolveState.expandedResolved.size, 0);
assert.equal(refreshed, 2, 'one refresh per click');
const panel = slice('function renderPanel(', '\n\n// A card');
assert.doesNotMatch(runtime, /Resolve thread|jev-resolve/, 'no card has its own Resolve thread button');
assert.match(panel, /if \(th\.status === 'acknowledged'\) \{[\s\S]*?'✓ Resolve'[\s\S]*?resolveThread\(th\)/, "each card's own resolve control is unchanged");
assert.match(panel, /const hinted = threads\.filter\(looksResolved\);\n\s+if \(hinted\.length\) \{[\s\S]*?'Resolve all \(' \+ hinted\.length \+ '\)'[\s\S]*?resolveThreads\(hinted\)[\s\S]*?wrap\.appendChild\(all\);\n  \}\n  const handoffState/,
  'Resolve all (n) sits after every card, only when a thread looks resolved');

// #acceptance-resolved-tag: the tag shows only while the thread is open; once resolved its own indicator replaces it.
const looksStart = runtime.indexOf('function looksResolved(');
const looksResolved = Function('jevItem', runtime.slice(looksStart, runtime.indexOf('\n}\n', looksStart) + 2) + '; return looksResolved;')(
  (kind, id) => kind === 'resolved' && id === 'hinted' ? { kind, id, state: 'label', label: 'resolved in spirit' } : null);
assert.equal(looksResolved({ id: 'hinted', status: 'pending' }), true);
assert.equal(looksResolved({ id: 'hinted', status: 'acknowledged' }), true);
assert.equal(looksResolved({ id: 'hinted', status: 'resolved' }), false);
assert.equal(looksResolved({ id: 'other', status: 'pending' }), false);
assert.match(panel, /\(looksResolved\(th\) \? '<span class="hx-jev-thread-label">Looks resolved<\/span>' : ''\)/);
assert.match(panel, /const resolvedHint = th\.status === 'resolved' \? null : jevItem\('resolved', th\.id\);/, 'a resolved card shows no resolved-in-spirit Jev label');
assert.match(slice('function renderPins(', '\n\nfunction renderBadges('), /const hinted = looksResolved\(th\);/, 'the pin tag follows the card tag');

console.log('runtime Jev contract tests passed');
