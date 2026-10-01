import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

// Review state in the page (review-state #model-fields, #model-author-assign, #model-author-shown,
// #model-order, #model-edit-history, #live-save, #live-handoff, #offline-notice): pure model checks.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
assert.equal(readFileSync(resolve(root, 'docs/specs/.viz/runtime.js'), 'utf8'), runtime, 'runtime copies are identical');
const start = runtime.indexOf('function foldThreads(events)');
const end = runtime.indexOf('\n\nfunction ingest(', start);
assert.ok(start >= 0 && end > start, 'runtime exposes the pure review-state model');
const model = Function(runtime.slice(start, end) + '; return { FRUITS, foldThreads, draftIds, handoffEvents, authorNames, messageAuthor, reviewHandoffState, reviewerIdentity, nextStamp, eventName, offlineNotice };')();
const { FRUITS, foldThreads, draftIds, handoffEvents, authorNames, messageAuthor, reviewHandoffState, reviewerIdentity, nextStamp, eventName, offlineNotice } = model;

const ev = (name, actor, body) => ({ name, actor, body: { actor, schemaVersion: 1, ...body } });
const mango = { browser: 'b-mango', author: 'Mango' };
const papaya = { browser: 'b-papaya', author: 'Papaya' };

// #model-author-assign: one fruit from the fixed list, kept in this browser; no prompt.
assert.deepEqual(FRUITS, ['Mango', 'Papaya', 'Lychee', 'Guava', 'Rambutan', 'Pineapple', 'Passion Fruit', 'Dragon Fruit', 'Durian', 'Jackfruit', 'Mangosteen', 'Starfruit', 'Soursop', 'Coconut', 'Banana', 'Tamarind', 'Longan', 'Feijoa', 'Cherimoya', 'Sapodilla', 'Kiwano', 'Pitanga', 'Jabuticaba', 'Salak']);
const store = new Map();
const storage = { getItem: k => store.has(k) ? store.get(k) : null, setItem: (k, v) => store.set(k, String(v)) };
const first = reviewerIdentity(storage, () => 0.99);
assert.equal(first.author, 'Salak', 'the name is drawn from the list');
assert.match(first.browser, /^[a-z0-9]{16,}$/, 'a random browser id');
assert.deepEqual(reviewerIdentity(storage, () => 0), first, 'the browser keeps its name and id');
store.set('spec-chat:reviewer', JSON.stringify({ browser: 'x', author: 'Nobody' }));
assert.ok(FRUITS.includes(reviewerIdentity(storage, () => 0).author), 'a stored name off the list is replaced');
const throwing = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
assert.ok(FRUITS.includes(reviewerIdentity(throwing, () => 0.1).author), 'storage refusal still yields a name');

// #live-save: the page names each event before sending; names sort by time and never repeat in a page.
const s1 = nextStamp(null, 1700000000000, () => 0.5);
assert.equal(s1, '1700000000000500000');
const s2 = nextStamp(s1, 1700000000000, () => 0.1);
assert.ok(BigInt(s2) > BigInt(s1), 'a same-millisecond save still sorts after the previous one');
assert.equal(nextStamp(s2, 1700000000001, () => 0), '1700000000001000000');
assert.equal(eventName({ event: 'comment', id: 'u1' }, s1), '1700000000000500000-comment-u1.json');

// #live-handoff: a hand-off covers only the ids it lists; a hand-off without events covers every draft before it.
const spool = [
  ev('100-comment-a.json', 'human', { id: 'a', event: 'comment', anchorId: 'x', text: 'A', ...mango }),
  ev('110-comment-b.json', 'human', { id: 'b', event: 'comment', anchorId: 'x', text: 'B', ...papaya }),
  ev('120-handoff-h1.json', 'human', { id: 'h1', event: 'handoff', events: ['a'], ...mango }),
];
let threads = foldThreads(spool);
assert.equal(threads.get('a').status, 'pending', "Mango's hand-off makes Mango's thread pending");
assert.equal(threads.get('b').status, 'draft', "Papaya's draft stays a draft");
assert.deepEqual([...draftIds(spool)], ['b']);
assert.deepEqual(handoffEvents(spool, 'b-papaya'), ['b'], "Papaya's hand-off lists Papaya's drafts");
assert.deepEqual(handoffEvents(spool, 'b-mango'), [], 'Mango has no draft left');
assert.equal(reviewHandoffState(threads, 0, 'b-mango').drafts, 0, "another reviewer's draft does not enable my hand-off");
assert.equal(reviewHandoffState(threads, 0, 'b-mango').enabled, false);
assert.equal(reviewHandoffState(threads, 0, 'b-papaya').drafts, 1);
const legacy = [...spool, ev('130-handoff-old.json', 'human', { id: 'old', event: 'handoff' })];
assert.equal(foldThreads(legacy).get('b').status, 'pending', 'a hand-off without events hands off every draft before it');
const older = [ev('090-comment-o.json', 'human', { id: 'o', event: 'comment', anchorId: 'x', text: 'before authors' }), ...spool];
assert.deepEqual(handoffEvents(older, 'b-mango'), ['o'], 'a draft from before browser ids is handed off by whichever page hands off next');
assert.equal(reviewHandoffState(foldThreads(older), 0, 'b-mango').drafts, 1);

// #model-author-shown: browsers sharing a fruit are numbered by their first event's file name.
const twins = [
  ev('200-comment-c.json', 'human', { id: 'c', event: 'comment', anchorId: 'x', text: 'C', browser: 'b2', author: 'Mango' }),
  ev('100-comment-d.json', 'human', { id: 'd', event: 'comment', anchorId: 'x', text: 'D', browser: 'b1', author: 'Mango' }),
  ev('300-reply-e.json', 'human', { id: 'e', event: 'reply', respondsTo: 'd', threadId: 'd', anchorId: 'x', text: 'E', browser: 'b2', author: 'Mango' }),
].sort((x, y) => x.name < y.name ? -1 : 1);
const names = authorNames(twins);
assert.equal(messageAuthor(names, twins[0]), 'Mango', 'the earlier commenter shows the name alone');
assert.equal(messageAuthor(names, twins[1]), 'Mango 2', 'the later shows the name followed by 2');
assert.equal(messageAuthor(names, twins[2]), 'Mango 2', 'every message of that browser keeps its shown name');
assert.equal(messageAuthor(names, ev('1-reply-r.json', 'agent', { id: 'r', event: 'reply' })), 'Agent');
assert.equal(messageAuthor(names, ev('1-comment-o.json', 'human', { id: 'o', event: 'comment' })), 'Reviewer', 'events from before authors');

// #model-order: meaning follows references, not file names; a skewed clock does not drop a message.
const skewed = [
  ev('050-reply-late.json', 'human', { id: 'u2', event: 'reply', respondsTo: 'r1', threadId: 'u1', anchorId: 'x', text: 'follow-up', ...mango }),
  ev('100-comment-u1.json', 'human', { id: 'u1', event: 'comment', anchorId: 'x', text: 'root', ...mango }),
  ev('110-handoff-h.json', 'human', { id: 'h', event: 'handoff', events: ['u1'], ...mango }),
  ev('120-reply-r1.json', 'agent', { id: 'r1', event: 'reply', respondsTo: 'u1', status: 'acknowledged', text: 'ok' }),
];
threads = foldThreads(skewed);
assert.deepEqual(threads.get('u1').messages.map(m => m.body.id), ['u1', 'r1', 'u2'], 'the early-named reply follows what it responds to');
assert.equal(threads.get('u1').status, 'draft', 'the follow-up is the newest human message');
assert.equal(threads.size, 1);

// #model-effective, #model-edit-history: the last edit by file name shows; history lists every edit with its author.
const race = [
  ev('100-comment-m.json', 'human', { id: 'm', event: 'comment', anchorId: 'x', text: 'orig', ...mango }),
  ev('110-handoff-h.json', 'human', { id: 'h', event: 'handoff', events: ['m'], ...mango }),
  ev('130-edit-e2.json', 'human', { id: 'e2', event: 'edit', supersedes: 'm', threadId: 'm', anchorId: 'x', text: 'Papaya text', ...papaya }),
  ev('120-edit-e1.json', 'human', { id: 'e1', event: 'edit', supersedes: 'm', threadId: 'm', anchorId: 'x', text: 'Mango text', ...mango }),
].sort((x, y) => x.name < y.name ? -1 : 1);
const raceThread = foldThreads(race).get('m');
assert.equal(raceThread.messages.length, 1);
assert.equal(raceThread.messages[0].body.text, 'Papaya text', 'the later save wins on every page');
const history = raceThread.history.get(0);
assert.equal(history.original.body.id, 'm');
assert.deepEqual(history.edits.map(e => [e.body.author, e.body.text]), [['Mango', 'Mango text'], ['Papaya', 'Papaya text']]);
assert.equal(messageAuthor(authorNames(race), history.original), 'Mango', 'the message keeps its writer');
const backwards = foldThreads([...race].reverse().sort((x, y) => x.name < y.name ? -1 : 1)).get('m');
assert.equal(backwards.messages[0].body.text, 'Papaya text', 'input order does not matter once sorted');
const chain = [
  ev('100-comment-m.json', 'human', { id: 'm', event: 'comment', anchorId: 'x', text: 'orig', ...mango }),
  ev('090-edit-e2.json', 'human', { id: 'e2', event: 'edit', supersedes: 'e1', threadId: 'm', anchorId: 'x', text: 'second', ...mango }),
  ev('110-edit-e1.json', 'human', { id: 'e1', event: 'edit', supersedes: 'm', threadId: 'm', anchorId: 'x', text: 'first', ...mango }),
].sort((x, y) => x.name < y.name ? -1 : 1);
assert.equal(foldThreads(chain).get('m').messages[0].body.text, 'second', 'an edit of an edit wins whatever its clock');

// #offline-notice
assert.equal(offlineNotice(0), '');
assert.equal(offlineNotice(1), 'Offline: 1 change waiting');
assert.equal(offlineNotice(3), 'Offline: 3 changes waiting');

// #offline-outbox: only a validation refusal (400, 403, 409) drops an event; any other failure keeps it to resend.
const httpStart = runtime.indexOf('function httpTransport()');
const httpEnd = runtime.indexOf('\n}\n', httpStart) + 2;
for (const [status, outcome] of [[200, 'stored'], [400, 'refused'], [403, 'refused'], [409, 'refused'], [404, 'kept'], [500, 'kept'], [503, 'kept']]) {
  const transport = Function('REVIEW_DIR', 'EMBED_REVIEW_DIR', 'fetch', runtime.slice(httpStart, httpEnd) + '; return httpTransport();')(
    'd', false, async () => ({ ok: status < 300, status }));
  const got = await transport.postEvent({ name: 'n', body: {} }).catch(() => 'kept');
  assert.equal(got, outcome, 'status ' + status);
}

// #anchoring-states: the service place wins; an element mark sits on the key place.py gives it now.
const placeStart = runtime.indexOf('function placedMark(e)');
const placedMark = Function(runtime.slice(placeStart, runtime.indexOf('\n}\n', placeStart) + 2) + '; return placedMark;')();
const elementMark = { anchorId: 'b', target: { type: 'element', key: 'p[1]' } };
assert.deepEqual(placedMark({ body: elementMark, place: { anchorId: 'b', state: 'kept', key: 'p[2]' } }).target, { type: 'element', key: 'p[2]' },
  'a paragraph inserted before the marked one moves the pin with it');
assert.equal(placedMark({ body: elementMark, place: { anchorId: 'b', state: 'kept', key: null } }).target, null, 'no current key: the block');
assert.deepEqual(placedMark({ body: { anchorId: 'b', target: { type: 'text', key: 'old' } }, place: { anchorId: 'b', state: 'changed', quote: 'new' } }).target,
  { type: 'text', key: 'new' });
assert.equal(placedMark({ body: elementMark, place: { anchorId: 'b', state: 'gone' } }).target, null);
const datum = { anchorId: 'c', target: { type: 'datum', key: 'Q1' } };
assert.deepEqual(placedMark({ body: datum, place: { anchorId: 'c', state: 'kept' } }).target, datum.target, 'a chart mark keeps its own target');
assert.equal(placedMark({ body: elementMark }), elementMark, 'no place: the event as written');

console.log('runtime review-state tests passed');
