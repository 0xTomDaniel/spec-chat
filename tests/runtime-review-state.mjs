import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

// Review state in the page (review-state #model-fields, #model-fields-spot, #model-fields-stage, #model-author-assign, #model-author-shown,
// #model-order, #model-edit-history, #live-save, #live-handoff, #offline-notice): pure model checks.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
assert.equal(readFileSync(resolve(root, 'docs/specs/.viz/runtime.js'), 'utf8'), runtime, 'runtime copies are identical');
const start = runtime.indexOf('function foldThreads(events)');
const end = runtime.indexOf('\n\nfunction ingest(', start);
assert.ok(start >= 0 && end > start, 'runtime exposes the pure review-state model');
const model = Function(runtime.slice(start, end) + '; return { FRUITS, foldThreads, draftIds, handoffEvents, authorNames, messageAuthor, reviewHandoffState, reviewerIdentity, addressBrowser, earlierAuthor, commentSpot, spotFields, pictureOf, nextStamp, eventName, offlineNotice };')();
const { FRUITS, foldThreads, draftIds, handoffEvents, authorNames, messageAuthor, reviewHandoffState, reviewerIdentity, addressBrowser, earlierAuthor, commentSpot, spotFields, pictureOf, nextStamp, eventName, offlineNotice } = model;
// threads #stage-tag-source: the lane a comment recorded, read beside the stages a read tells.
const laneOf = Function(runtime.slice(runtime.indexOf('const laneOf ='), runtime.indexOf('\nconst threadLanes =')) + '; return laneOf;')();

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

// #model-fields, shell #shell-phone-id: a page with no storage takes the browser id its address passes.
assert.equal(addressBrowser('#browser=k3j9x0abc'), 'k3j9x0abc');
assert.equal(addressBrowser('#plans&browser=k3j9x0abc'), 'k3j9x0abc', 'beside other fragment parts');
assert.equal(addressBrowser('#plans'), null, 'an anchor alone passes none');
assert.equal(addressBrowser(''), null);
assert.equal(addressBrowser('#browser='), null);
assert.equal(addressBrowser('#browser=' + encodeURIComponent('<img src=x>')), null, 'an id is a plain token');
assert.equal(addressBrowser('#browser=' + 'a'.repeat(65)), null);
const frame = new Map();
const frameStorage = { getItem: k => frame.has(k) ? frame.get(k) : null, setItem: (k, v) => frame.set(k, String(v)) };
const fromAddress = reviewerIdentity(frameStorage, () => 0.5, 'shell-id-1');
assert.equal(fromAddress.browser, 'shell-id-1', 'the address id is the browser');
assert.ok(FRUITS.includes(fromAddress.author), 'a name is assigned until earlier events give one');
assert.equal(fromAddress.address, true, 'the page knows its id came from its address');
assert.deepEqual(reviewerIdentity(frameStorage, () => 0, 'shell-id-1'), fromAddress, 'the same id keeps the name kept for it');
assert.equal(reviewerIdentity(frameStorage, () => 0, 'shell-id-2').browser, 'shell-id-2', 'a kept reviewer of another id is not taken');
assert.equal(reviewerIdentity(throwing, () => 0, 'shell-id-3').browser, 'shell-id-3', 'with nothing to keep it in, the id still holds');
const noAddress = reviewerIdentity(frameStorage, () => 0.5);
assert.equal(noAddress.browser, 'shell-id-2', 'with no address id the page keeps the reviewer it holds');
assert.equal(noAddress.address, undefined);

// #model-author-assign: the name that browser's earlier events carry, from its first event by file name.
const earlier = [
  ev('090-comment-p.json', 'human', { id: 'p', event: 'comment', anchorId: 'x', browser: 'shell-id-1', author: 'Nobody' }),
  ev('100-comment-a.json', 'human', { id: 'a', event: 'comment', anchorId: 'x', browser: 'shell-id-1', author: 'Guava' }),
  ev('110-reply-r.json', 'agent', { id: 'r', event: 'reply', respondsTo: 'a', browser: 'shell-id-1', author: 'Longan' }),
  ev('120-reply-u.json', 'human', { id: 'u', event: 'reply', respondsTo: 'r', threadId: 'a', browser: 'shell-id-1', author: 'Feijoa' }),
  ev('130-comment-b.json', 'human', { id: 'b', event: 'comment', anchorId: 'x', ...papaya }),
];
assert.equal(earlierAuthor(earlier, 'shell-id-1'), 'Guava', 'the first event carrying a name from the list');
assert.equal(earlierAuthor(earlier, 'shell-id-9'), null, 'a browser with no earlier events has none');
{
  const ingestCode = runtime.slice(runtime.indexOf('function ingest('), runtime.indexOf('\nfunction jevDisplayLabel('));
  const noop = () => {};
  const page = { events: [], seenNames: new Set(), openReplies: new Set(), reviewer: { browser: 'shell-id-1', author: 'Salak', address: true } };
  const ingest = Function('state', 'LAYER_SERVICE', 'foldThreads', 'foldLayerThreads', 'authorNames', 'earlierAuthor', 'renderLayer', 'renderPanel', 'renderPins', 'renderBadges', 'renderChoices',
    'uiHost', 'COMMENT_UI', 'sendMarks', ingestCode + '; return ingest;')(page, null, foldThreads, foldThreads, authorNames, earlierAuthor, noop, noop, noop, noop, noop, () => false, false, noop);
  ingest([earlier[3]]);
  assert.equal(page.reviewer.author, 'Feijoa', 'the name its events carry so far');
  ingest(earlier);
  assert.equal(page.reviewer.author, 'Guava', 'after a reload, the name of its first event');
  const kept = { events: [], seenNames: new Set(), openReplies: new Set(), reviewer: { browser: 'shell-id-1', author: 'Salak' } };
  Function('state', 'LAYER_SERVICE', 'foldThreads', 'foldLayerThreads', 'authorNames', 'earlierAuthor', 'renderLayer', 'renderPanel', 'renderPins', 'renderBadges', 'renderChoices',
    'uiHost', 'COMMENT_UI', 'sendMarks', ingestCode + '; return ingest;')(kept, null, foldThreads, foldThreads, authorNames, earlierAuthor, noop, noop, noop, noop, noop, () => false, false, noop)(earlier);
  assert.equal(kept.reviewer.author, 'Salak', 'a page keeping its own reviewer keeps its own name');
}
{
  // Only a page with no storage reads its address.
  const pageCode = runtime.slice(runtime.indexOf('function pageReviewer('), runtime.indexOf('\n}\n', runtime.indexOf('function pageReviewer(')) + 2);
  const frameStore = { getItem: () => null, setItem: () => {} };
  const local = { getItem: () => null, setItem: () => {} };
  const reviewer = storage => Function('reviewStorage', 'frameStore', 'reviewerIdentity', 'addressBrowser', 'location', pageCode + '; return pageReviewer();')(
    () => storage, frameStore, reviewerIdentity, addressBrowser, { hash: '#browser=shell-id-7' });
  assert.equal(reviewer(frameStore).browser, 'shell-id-7', 'no storage: the address id');
  assert.notEqual(reviewer(local).browser, 'shell-id-7', 'browser storage: its own id, whatever the address passes');
}

// #model-fields-spot: the one save carries the step, moment, and box its caller gives, each only when well formed.
assert.deepEqual(commentSpot({ step: 'plan-chosen', moment: 83.4, box: { left: 0.25, top: 0.1, width: 0.5, height: 0.3, x: 1 } }),
  { step: 'plan-chosen', moment: 83.4, box: { left: 0.25, top: 0.1, width: 0.5, height: 0.3 } });
assert.deepEqual(commentSpot({ step: '', moment: -1, box: { left: 0, top: 0, width: 2, height: 1 } }), { step: null, moment: null, box: null });
assert.deepEqual(commentSpot({ step: ['s'], moment: '4', box: [0, 0, 1, 1] }), { step: null, moment: null, box: null });
assert.deepEqual(spotFields({ moment: 0, step: 7 }), { moment: 0 });
{
  const saveCode = runtime.slice(runtime.indexOf('function saveMessage('), runtime.indexOf('\nfunction startEdit('));
  const editCode = runtime.slice(runtime.indexOf('function startEdit('), runtime.indexOf('\nfunction renderThreadDock('));
  const saved = [];
  let ids = 0;
  const page = { composer: null };
  const saving = (pageState, hosted = null) => Function('save', 'toast', 'refresh', 'humanId', 'spotFields', 'pictureOf', 'laneOf', 'state', 'renderPanel',
    'LAYER_SERVICE', 'uiHost', 'layerRoute', 'layer', 'layerItems', saveCode + editCode + '; return { saveMessage, startEdit };')(
    body => saved.push(body), () => {}, () => {}, p => p + ++ids, spotFields, pictureOf, laneOf,
    Object.assign(pageState, { threads: pageState.threads || new Map() }), () => {}, hosted && 'https://review.example/',
    () => false, (body, route) => route || hosted.threads[body.threadId], hosted && hosted.layer, () => hosted.items);
  const { saveMessage, startEdit } = saving(page);
  const box = { left: 0.25, top: 0.1, width: 0.5, height: 0.3 };
  saveMessage({ kind: 'comment', anchorId: 'clip', target: null, moment: 83.4, box, step: 'plan-chosen' }, 'Here.');
  assert.deepEqual([saved[0].step, saved[0].moment, saved[0].box], ['plan-chosen', 83.4, box], 'a comment carries its spot');
  saveMessage({ kind: 'comment', anchorId: 'clip', target: null }, 'No spot.');
  assert.deepEqual(['step', 'moment', 'box'].filter(k => k in saved[1]), [], 'a comment with no spot carries no spot field');
  saveMessage({ kind: 'comment', anchorId: 'clip', target: null, moment: 'soon' }, 'Malformed.');
  assert.equal('moment' in saved[2], false, 'a malformed field is not saved');
  saveMessage({ kind: 'reply', anchorId: 'clip', target: null, respondsTo: 'r1', threadId: 'u1', moment: 4 }, 'Reply.');
  assert.equal('moment' in saved[3], false, 'a reply joins its thread by reference and carries no spot');
  const root = { actor: 'human', name: '1-comment-u1.json', body: saved[0] };
  startEdit({ id: saved[0].id, ev: root }, root);
  saveMessage(page.composer, 'Edited.');
  assert.deepEqual([saved[4].event, saved[4].step, saved[4].moment, saved[4].box], ['edit', 'plan-chosen', 83.4, box], 'an edit repeats its spot');
  const threads = foldThreads([root, { actor: 'human', name: '2-edit-e.json', body: saved[4] }]);
  assert.deepEqual(threads.get(saved[0].id).spot, { step: 'plan-chosen', moment: 83.4, box }, 'the fold reads the root comment\'s spot');
  assert.deepEqual(saved.map(body => 'stage' in body), [false, false, false, false, false], 'told no stage, no message carries one');

  // #model-fields-stage, threads #stage-tag-fixed, #stage-tag-source: a comment and a reply carry the stage its service
  // last told for the review's lane, an edit none; a later stage only the messages written after it.
  saved.length = 0;
  const lanePage = { composer: null, lanes: { lane: 'abc1', stages: { abc1: 'Spec review', abc2: 'QA' } } };
  const onLane = saving(lanePage);
  onLane.saveMessage({ kind: 'comment', anchorId: 'x', target: null }, 'Comment.');
  onLane.saveMessage({ kind: 'reply', anchorId: 'x', target: null, respondsTo: 'u1', threadId: 'u1' }, 'Reply.');
  onLane.startEdit({ id: saved[0].id, ev: { body: saved[0] } }, { body: saved[0] });
  onLane.saveMessage(lanePage.composer, 'Edited.');
  lanePage.lanes = { lane: 'abc1', stages: { abc1: 'Code review' } };
  onLane.saveMessage({ kind: 'comment', anchorId: 'x', target: null }, 'Later.');
  lanePage.lanes = { lane: null, stages: { abc2: 'QA' } };
  onLane.saveMessage({ kind: 'comment', anchorId: 'x', target: null }, 'No lane.');
  assert.deepEqual(saved.map(body => [body.event, body.stage ?? null]), [['comment', 'Spec review'], ['reply', 'Spec review'],
    ['edit', null], ['comment', 'Code review'], ['comment', null]]);

  // On a host page: the lane its item's anchor names, else its scope's when the read names one; a reply its thread's.
  saved.length = 0;
  const items = [{ scope: 'ops', subject: 'run:1', lane: 'abc2' }, { scope: 'ops', subject: 'run:2', lane: null },
    { scope: 'abc1', subject: 'turn:3', lane: null }];
  const told = new Map([['ops', { lane: null, stages: { abc2: 'QA' } }], ['abc1', { lane: 'abc1', stages: { abc1: 'Spec review' } }]]);
  const onHost = saving({ composer: null, lanes: { lane: null, stages: {} } },
    { items, layer: { lanes: told }, threads: { t1: { scope: 'ops', subject: 'run:1' } } });
  for (const item of items) onHost.saveMessage({ kind: 'comment', anchorId: '', target: null, route: { scope: item.scope, subject: item.subject } }, 'On it.');
  onHost.saveMessage({ kind: 'comment', anchorId: '', target: null, route: { scope: 'gone', subject: 'run:9' } }, 'Unread scope.');
  onHost.saveMessage({ kind: 'reply', anchorId: '', target: null, respondsTo: 't1', threadId: 't1' }, 'Reply.');
  assert.deepEqual(saved.map(body => body.stage ?? null), ['QA', null, 'Spec review', null, 'QA']);
  // #model-fields-stage: a comment with a lane records it, the lane it took its stage from; a reply records none.
  assert.deepEqual(saved.map(body => body.lane ?? null), ['abc2', null, 'abc1', null, null]);

  // threads #stage-tag-source: a reply has its thread's lane, the one its comment recorded, whatever the lane its page
  // or item names now; with none recorded, the page's or item's, as before.
  saved.length = 0;
  const replyPage = { composer: null, lanes: { lane: 'abc1', stages: { abc1: 'Merge ask', abc2: 'QA' } },
    threads: new Map([['t2', { ev: { body: { lane: 'abc2' } } }], ['t3', { ev: { body: { lane: '../x' } } }]]) };
  const onReply = saving(replyPage);
  for (const threadId of ['t2', 't3', 'gone']) onReply.saveMessage({ kind: 'reply', anchorId: 'x', target: null, respondsTo: threadId, threadId }, 'Reply.');
  assert.deepEqual(saved.map(body => [body.stage ?? null, body.lane ?? null]), [['QA', null], ['Merge ask', null], ['Merge ask', null]]);
  assert.equal(laneOf({ lane: 'qa-20' }), 'qa-20');
  assert.deepEqual([laneOf({}), laneOf({ lane: 'QA 20' }), laneOf({ lane: 7 })], [null, null, null]);

  // threads #box-picture-what: a comment on a box carries the picture its page made, a PNG, JPEG, or WebP image of at
  // most 1 MB in base64, as the pick carried it; any other picture, and a reply, carry none.
  saved.length = 0;
  const png = { type: 'image/png', data: Buffer.from('\x89PNG\r\n\x1a\nbytes').toString('base64') };
  const mb = { type: 'image/jpeg', data: 'A'.repeat(1398102) + '==' }; // 1 MB, 1048576 bytes
  const big = { type: 'image/webp', data: 'A'.repeat(1398103) + '=' }; // one byte more
  assert.deepEqual(pictureOf(png), png);
  assert.deepEqual(pictureOf(mb), mb, 'at most 1 MB');
  for (const bad of [big, { type: 'image/gif', data: png.data }, { type: 'image/png', data: 'not base64!' }, { type: 'image/png', data: '' },
    { type: 'image/png', data: 'abc' }, png.data, null, []]) assert.equal(pictureOf(bad), null, JSON.stringify(bad).slice(0, 60));
  const onBox = saving({ composer: null, lanes: { lane: null, stages: {} } });
  onBox.saveMessage({ kind: 'comment', anchorId: 'x', target: null, box: { left: 0, top: 0, width: 1, height: 1 }, picture: png }, 'Boxed.');
  onBox.saveMessage({ kind: 'comment', anchorId: 'x', target: null, box: { left: 0, top: 0, width: 1, height: 1 }, picture: big }, 'Too big.');
  onBox.saveMessage({ kind: 'reply', anchorId: 'x', target: null, respondsTo: 'u', threadId: 'u', picture: png }, 'Reply.');
  assert.deepEqual(saved.map(body => body.picture ?? null), [png, null, null]);
  assert.deepEqual(saved[1].box, { left: 0, top: 0, width: 1, height: 1 }, 'a picture left out leaves the comment and its box');
}

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
  const transport = Function('REVIEW_DIR', 'EMBED_REVIEW_DIR', 'fetch', 'apiPath', 'COMMENT_UI', 'ui', runtime.slice(httpStart, httpEnd) + '; return httpTransport();')(
    'd', false, async () => ({ ok: status < 300, status }), path => '/api/' + path, false, { dir: null });
  const got = await transport.postEvent({ name: 'n', body: {} }).catch(() => 'kept');
  assert.equal(got, outcome, 'status ' + status);
}

// #offline-outbox: the page keeps the events it last loaded and reads them while the service cannot list. A page the
// service reviews no spool for, such as a mockup outside its row's set, is answered 400 with an error object; an earlier
// page kept such an answer as its last load. Every read below fails, so the page shows the events it kept beside its
// waiting ones: what holds no event list is never kept and reads as no events, and each failing read shows one status
// line and rejects nothing.
{
  const LOADED = 'spec-chat:events:d';
  const waitingEvent = { actor: 'human', name: '1-comment-w.json', body: { id: 'w', event: 'comment' } };
  const page = (keptCopy, answer) => {
    const kept = new Map(keptCopy === undefined ? [] : [[LOADED, keptCopy]]);
    const window = { localStorage: { getItem: k => kept.has(k) ? kept.get(k) : null, setItem: (k, v) => kept.set(k, String(v)) } };
    const shown = { ingested: [], lines: [] };
    const state = { storedCount: -1, eventsRendered: false, events: [], threads: new Map() };
    const fetch = async () => typeof answer === 'function' ? answer() : answer;
    const element = { textContent: '' };
    const none = () => {};
    const run = Function('window', 'fetch', 'state', 'REVIEW_DIR', 'EMBED_REVIEW_DIR', 'LAYER_SERVICE', 'LOADED_KEY', 'ingest', 'status',
      'outboxEvents', 'flushOutbox', 'requestMockups', 'renderOffline', 'document', 'location', 'handoffObservation', 'handoffAgentText',
      'shell', 'uiHost', 'COMMENT_UI', 'ui', 'sendMarks', 'refreshLayer',
      runtime.slice(runtime.indexOf('function eventList('), runtime.indexOf('\nfunction classifyAnchorSignatures(')) +
      runtime.slice(runtime.indexOf('function keepLoaded('), runtime.indexOf('\nfunction renderOffline(')) +
      runtime.slice(runtime.indexOf('async function refresh()'), runtime.indexOf('\nasync function watchSpec(')) +
      '; state.transport = httpTransport(); return { refresh, loadedEvents };')(
      window, fetch, state, 'd', false, null, LOADED, events => shown.ingested.push(events), line => shown.lines.push(line),
      () => [waitingEvent], none, none, none, { getElementById: () => element }, { hash: '' }, none, () => '',
      { held: false }, () => false, false, { dir: null }, none, none);
    return Object.assign(run, { kept, shown });
  };
  const answered = (status, body) => ({ ok: status < 300, status, headers: new Map(), json: async () => body });
  const offline = () => { throw new TypeError('Load failed'); };

  for (const [keptCopy, answer, line] of [
    [undefined, answered(400, { error: 'bad dir' }), 'event sync failed: review service answered 400'],
    [undefined, answered(200, { error: 'bad dir' }), 'event sync failed: review service answered no event list'],
    [JSON.stringify({ error: 'bad dir' }), answered(400, { error: 'bad dir' }), 'event sync failed: review service answered 400'],
    [JSON.stringify({ error: 'bad dir' }), offline, 'event sync failed: Load failed'],
  ]) {
    const name = (keptCopy || 'nothing') + ' kept, ' + line;
    const p = page(keptCopy, answer);
    for (let tick = 0; tick < 3; tick++) await p.refresh(); // each tick rejecting would throw here
    assert.deepEqual(p.shown.lines, [line, line, line], name + ': one status line each read');
    assert.deepEqual(p.shown.ingested, [[waitingEvent], [waitingEvent], [waitingEvent]], name + ': only the waiting event shows');
    assert.equal(p.kept.get(LOADED), keptCopy, name + ': nothing is kept from the answer');
  }

  for (const keptCopy of ['{"error":"bad dir"}', '{}', 'null', '"text"', '7', 'true', '{broken']) {
    assert.deepEqual(page(keptCopy).loadedEvents(), [], keptCopy + ' reads as no events');
  }

  const listed = [{ actor: 'agent', name: '2-reply-r.json', body: { id: 'r', event: 'reply' } }];
  const p = page(JSON.stringify({ error: 'bad dir' }), answered(200, listed));
  await p.refresh();
  assert.deepEqual(JSON.parse(p.kept.get(LOADED)), listed, 'an event list is kept as the last load');
  assert.deepEqual(p.shown.ingested, [listed.concat([waitingEvent])], 'and shows beside the waiting event');
  assert.deepEqual(p.loadedEvents(), listed, 'and reads back');
}

// #anchoring-states: the service place wins; an element mark sits on the key place.py gives it now. On a page that is no
// prototype mockup (PROTOTYPE_ROUTE null).
const placeStart = runtime.indexOf('function placedMark(e)');
const placedMarkOn = route => Function('PROTOTYPE_ROUTE', runtime.slice(placeStart, runtime.indexOf('\n}\n', placeStart) + 2) + '; return placedMark;')(route);
const placedMark = placedMarkOn(null);
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
// mockups #prototype-marks: on a prototype mockup's page a mark resolves on the route as it now runs, never by the place
// the service gives it in the mockup file.
assert.equal(placedMarkOn('/report')({ body: elementMark, place: { anchorId: 'b', state: 'gone' } }), elementMark, 'a prototype mark as written');

console.log('runtime review-state tests passed');
