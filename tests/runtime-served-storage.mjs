// served-pages #served-storage, comment-layer #ui-frame: a page with no origin keeps its fruit name and outbox in its
// frame's name. Each review service's items stay apart, and a page's write keeps of every other service only its
// reviewer (browser id and fruit name) and its unsent changes. A name the host gives its frame, before the page loads or
// after, is the host's for the rest of the page's life, even if the host clears it, and the changes the page queued
// still send from its memory until it reloads.
// Each page load below runs the runtime's own outbox and frame store, its fruit name chosen by the runtime at boot.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
assert.equal(readFileSync(resolve(root, 'docs/specs/.viz/runtime.js'), 'utf8'), runtime, 'runtime copies are identical');
const slice = (from, to) => {
  const a = runtime.indexOf(from), b = runtime.indexOf(to, a);
  assert.ok(a >= 0 && b > a, 'runtime holds ' + from);
  return runtime.slice(a, b);
};
const { handoffEvents, reviewerIdentity, nextStamp, eventName } = Function(slice('function draftIds(', '\nfunction offlineNotice(') +
  '\n; return { handoffEvents, reviewerIdentity, nextStamp, eventName };')();
const outboxCode = slice('const OUTBOX_PREFIX', '\nfunction forgetEvent(');

const SPEC = 'https://specs.example/lane/docs/specs/report.spec.html';
const OTHER = 'https://other.example/lane/docs/specs/report.spec.html'; // another service, the same path
const REVIEW_DIR = 'lane/docs/specs/report.spec.html.review';
const OUTBOX = 'spec-chat:outbox:' + REVIEW_DIR;
const REVIEWER = 'spec-chat:reviewer';

// A frame with no origin: no browser storage, only its name.
const noOrigin = name => ({ name, get localStorage() { throw new Error('no origin, no browser storage'); } });
// A review service that stores each event it is sent while online, and refuses the connection while offline.
const reviewService = () => ({ online: true, stored: [] });
const sent = service => service.stored.map(event => event.body.text);

// One page load in the frame: a page at its own address, or the layer loaded from its service on a host page. Its
// store is the one the runtime keeps its fruit name and outbox in.
function load(frame, address, { service = reviewService(), layerService = null } = {}) {
  const state = { reviewer: null, version: null, lastStamp: 0, outboxBlocked: false,
    transport: { postEvent: async entry => { if (!service.online) throw new Error('offline'); service.stored.push({ actor: 'human', name: entry.name, body: entry.body }); return 'stored'; } } };
  const none = () => {};
  const page = Function('window', 'OWN_ADDRESS', 'LAYER_SERVICE', 'REVIEW_DIR', 'REVIEWER_KEY', 'state', 'nextStamp', 'eventName',
    'layerRoute', 'ingest', 'forgetEvent', 'renderOffline', 'COMMENT_UI', 'uiHost', 'ui', 'PROTOTYPE_ROUTE',
    outboxCode + '\n; return { store: reviewStorage(), save, flushOutbox, outboxEntries };')(
    frame, new URL(address), layerService && new URL(layerService), REVIEW_DIR, REVIEWER, state, nextStamp, eventName, none, none, none, none,
    false, () => false, { dir: null }, null);
  return Object.assign(page, { state });
}
// A page opening: the runtime boots with its fruit name, then sends its waiting changes.
async function open(frame, address, options) {
  const page = load(frame, address, options);
  page.state.reviewer = reviewerIdentity(page.store, Math.random);
  await page.flushOutbox();
  return page;
}
const waiting = page => page.outboxEntries().map(entry => entry.body.text);
const kept = frame => JSON.parse(frame.name.slice('spec-chat:'.length));

// A frame with no name keeps the page's items in its name, across reloads and the service's other pages.
let frame = noOrigin('');
load(frame, SPEC).store.setItem(OUTBOX, '[1]');
assert.ok(frame.name.startsWith('spec-chat:'), 'kept in the frame name');
assert.equal(load(frame, SPEC).store.getItem(OUTBOX), '[1]', 'kept across a reload');
let page = load(frame, SPEC);
page.store.setItem(REVIEWER, 'mango');
assert.equal(page.store.getItem(OUTBOX), '[1]', 'a write keeps the page\'s other items');
assert.equal(load(frame, 'https://specs.example/other/docs/specs/notes.spec.html').store.getItem(REVIEWER), 'mango', 'the same service\'s other pages share its items');

// Under the prefix, anything but a plain object reads as no items: no throw, and no key written after it is lost.
for (const left of ['[]', 'null', '"text"', '7', 'true', '{broken', '']) {
  frame = noOrigin('spec-chat:' + left);
  page = load(frame, SPEC);
  assert.equal(page.store.getItem(OUTBOX), null, left + ' holds no items');
  page.store.setItem(OUTBOX, '[1]');
  page.store.setItem(REVIEWER, 'mango');
  assert.equal(load(frame, SPEC).store.getItem(OUTBOX), '[1]', left + ': the outbox is kept');
  assert.equal(load(frame, SPEC).store.getItem(REVIEWER), 'mango', left + ': the fruit name is kept beside it');
}

// A name the host gave its frame before the page loaded is the host's: the page keeps its items in memory, never there.
frame = noOrigin('host-target');
page = load(frame, SPEC);
assert.equal(page.store.getItem(OUTBOX), null, 'the host name holds nothing of the page');
page.store.setItem(OUTBOX, '[1]');
assert.equal(frame.name, 'host-target', 'the host name stays');
assert.equal(page.store.getItem(OUTBOX), '[1]', 'the page keeps the item in its memory');
assert.equal(load(frame, SPEC).store.getItem(OUTBOX), null, 'a reload in that frame finds nothing of the page');

// Also when the host names its frame after the page loaded, and when it clears the name again, whether or not the
// page used its store while the host's name stood: the name stays the host's until the page reloads.
for (const seen of [true, false]) {
  frame = noOrigin('');
  page = load(frame, SPEC);
  page.store.setItem(OUTBOX, '[1]');
  frame.name = 'host-target';
  if (seen) assert.equal(page.store.getItem(OUTBOX), '[1]', 'once the host names its frame, the page serves its items from memory');
  frame.name = '';
  assert.equal(page.store.getItem(OUTBOX), '[1]', 'cleared by the host, the name holds what the page queued: seen ' + seen);
  page.store.setItem(OUTBOX, '[2]');
  assert.equal(frame.name, '', 'the page writes nothing to the name the host cleared: seen ' + seen);
  assert.equal(page.store.getItem(OUTBOX), '[2]', 'its memory keeps the write: seen ' + seen);
}

// Each service's items stay apart: another service's page at the same path finds none of the first's; once it
// writes, the frame keeps of the first only its reviewer and its unsent changes, and nothing of a service with neither.
const mango = JSON.stringify({ browser: 'b-mango', author: 'Mango' }), papaya = JSON.stringify({ browser: 'b-papaya', author: 'Papaya' });
frame = noOrigin('');
page = load(frame, SPEC);
page.store.setItem(OUTBOX, '[1]');
page.store.setItem(OUTBOX.replace('report', 'notes'), '[]');
page.store.setItem(REVIEWER, mango);
assert.equal(load(frame, OTHER).store.getItem(OUTBOX), null, 'another service at the same path finds no outbox');
assert.equal(load(frame, OTHER).store.getItem(REVIEWER), null, 'nor the first service\'s browser id and fruit name');
load(frame, OTHER).store.setItem(REVIEWER, papaya);
const firstKept = { [OUTBOX]: '[1]', [REVIEWER]: mango };
assert.deepEqual(kept(frame), { 'https://specs.example': firstKept, 'https://other.example': { [REVIEWER]: papaya } },
  'once the other service writes, the first keeps only its reviewer and its unsent changes');
const THIRD = 'https://third.example/lane/docs/specs/report.spec.html';
load(frame, THIRD).store.setItem(OUTBOX, '[]');
assert.deepEqual(kept(frame)['https://specs.example'], firstKept, 'kept alike through a third service\'s write');
load(frame, SPEC).store.setItem(OUTBOX, '[]');
load(frame, OTHER).store.setItem(REVIEWER, papaya);
assert.deepEqual(kept(frame), { 'https://specs.example': { [REVIEWER]: mango }, 'https://other.example': { [REVIEWER]: papaya } },
  'with no unsent changes, a service keeps its reviewer, and a service with neither leaves nothing');

// The layer's service is the one it was loaded from, whichever host page it is on.
frame = noOrigin('');
load(frame, 'https://bb.example/pane', { layerService: 'https://specs.example/' }).store.setItem(REVIEWER, 'papaya');
assert.equal(load(frame, 'https://wb.example/pane', { layerService: 'https://specs.example/' }).store.getItem(REVIEWER), 'papaya', 'another host page of the same service');
assert.equal(load(frame, 'https://bb.example/pane', { layerService: 'https://other.example/' }).store.getItem(REVIEWER), null, 'the same host page with another service');

// served-pages #acceptance-served-storage-service: offline, a comment saved on the first service's spec waits in the
// frame; the second service's spec, opened in that frame with its service reachable, shows no draft of it and is
// never sent it.
const first = reviewService(), second = reviewService();
frame = noOrigin('');
first.online = false;
page = await open(frame, SPEC, { service: first });
page.save({ event: 'comment', id: 'c1', text: 'Saved offline in the host frame.' }); await page.flushOutbox();
assert.deepEqual(waiting(page), ['Saved offline in the host frame.'], 'offline, the comment waits');
const saver = page.state.reviewer;
page = await open(frame, OTHER, { service: second });
assert.deepEqual(waiting(page), [], 'the second spec holds no draft of the comment');
assert.notEqual(page.state.reviewer.browser, saver.browser, 'nor the first service\'s browser id');
assert.deepEqual(second.stored, [], 'and its service is never sent it');
const left = kept(frame)['https://specs.example'];
assert.deepEqual(Object.keys(left).sort(), [OUTBOX, REVIEWER].sort(), 'the frame keeps of the first service only its reviewer and its unsent comment');
assert.deepEqual(JSON.parse(left[REVIEWER]), saver, 'and its reviewer, the browser id and fruit name it was saved under');

// served-pages #acceptance-served-storage-return: the first service's spec, opened again in that frame with its
// service reachable, is the same reviewer and sends the comment under the browser id it was saved under, so its
// hand-off lists it.
first.online = true;
page = load(frame, SPEC, { service: first });
assert.deepEqual(waiting(page), ['Saved offline in the host frame.'], 'back at the first spec, the comment is its draft');
page.state.reviewer = reviewerIdentity(page.store, Math.random);
assert.deepEqual(page.state.reviewer, saver, 'the page is the reviewer the comment was saved by');
await page.flushOutbox();
assert.deepEqual(sent(first), ['Saved offline in the host frame.'], 'its service stores the comment');
assert.deepEqual(second.stored, [], 'only its own service');
assert.deepEqual(waiting(page), [], 'and the outbox drains');
assert.deepEqual(handoffEvents(first.stored, page.state.reviewer.browser), ['c1'], 'its hand-off lists the comment');

// served-pages #acceptance-served-storage-stored: a comment saved and stored on the first service's spec; the second
// service's spec, opened in that frame, saves a comment there, so its page writes the frame; the first service's spec,
// opened again, is the same reviewer, and its hand-off lists the comment it stored before.
{
  const first = reviewService(), second = reviewService();
  frame = noOrigin('');
  page = await open(frame, SPEC, { service: first });
  page.save({ event: 'comment', id: 's1', text: 'Saved on the first service.' }); await page.flushOutbox();
  assert.deepEqual(sent(first), ['Saved on the first service.'], 'the first service stores the comment');
  const saver = page.state.reviewer;
  page = await open(frame, OTHER, { service: second });
  page.save({ event: 'comment', id: 's2', text: 'Saved on the second service.' }); await page.flushOutbox();
  assert.deepEqual(sent(second), ['Saved on the second service.'], 'the second service stores its own comment');
  assert.notEqual(page.state.reviewer.browser, saver.browser, 'under the second service\'s own reviewer');
  page = await open(frame, SPEC, { service: first });
  assert.deepEqual(page.state.reviewer, saver, 'back at the first spec, the same reviewer');
  assert.deepEqual(handoffEvents(first.stored, page.state.reviewer.browser), ['s1'], 'its hand-off lists the stored comment');
}

// served-pages #acceptance-served-storage-host-named: the host names the frame and clears its name once the page has
// opened; offline, a comment saved after both waits in the page's memory and sends once the browser is back online.
// Also a comment saved before the host named the frame, and one between, whether or not the page read the name while
// the host's stood.
for (const seen of [true, false]) {
  const service = reviewService();
  frame = noOrigin('');
  page = await open(frame, SPEC, { service });
  service.online = false;
  page.save({ text: 'before' }); await page.flushOutbox();
  frame.name = 'host-target';
  if (seen) { page.save({ text: 'between' }); await page.flushOutbox(); }
  frame.name = '';
  page.save({ text: 'Saved offline in the host frame.' }); await page.flushOutbox();
  const queued = seen ? ['before', 'between', 'Saved offline in the host frame.'] : ['before', 'Saved offline in the host frame.'];
  assert.deepEqual(waiting(page), queued, 'offline, every comment waits: seen ' + seen);
  assert.equal(frame.name, '', 'the page keeps nothing in the name the host cleared: seen ' + seen);
  service.online = true;
  await page.flushOutbox();
  assert.deepEqual(sent(service), queued, 'back online, all send in order: seen ' + seen);
  assert.deepEqual(waiting(page), [], 'and the outbox drains: seen ' + seen);
  assert.equal(frame.name, '', 'the name stays the host\'s: seen ' + seen);
}

// Also with no save after the host names its frame: the queued changes send once the service is back, and a reload in
// the host's frame finds nothing of the page.
const service = reviewService();
frame = noOrigin('');
page = await open(frame, SPEC, { service });
service.online = false;
page.save({ text: 'e1' }); await page.flushOutbox();
page.save({ text: 'e2' }); await page.flushOutbox();
frame.name = 'host-target';
service.online = true;
await page.flushOutbox();
assert.deepEqual(sent(service), ['e1', 'e2'], 'both send');
assert.equal(frame.name, 'host-target', 'the name stays the host\'s');
assert.equal(load(frame, SPEC).store.getItem(REVIEWER), null, 'a reload in the host\'s frame finds nothing of the page');

console.log('runtime served storage tests passed');
