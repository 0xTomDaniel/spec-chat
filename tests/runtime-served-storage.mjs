// review-service #served-storage, comment-layer #ui-frame: a page with no origin keeps its fruit name and outbox in its
// frame's name. The name is read at each use; a name the host gives its frame, before the page loads or after, stays the
// host's, and the changes the page queued before then still send; and each review service's items stay apart, so a frame
// moved to another service never sends it the first's.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
assert.equal(readFileSync(resolve(root, 'docs/specs/.viz/runtime.js'), 'utf8'), runtime, 'runtime copies are identical');
const from = runtime.indexOf('const FRAME_NAME'), to = runtime.indexOf('\nfunction outboxEntries(', from);
assert.ok(from >= 0 && to > from, 'runtime exposes the frame store');
// One page load in the frame: the frame store of a page at its own address, or of the layer loaded from its service.
const load = (frame, address, layerService = null) => Function('window', 'OWN_ADDRESS', 'LAYER_SERVICE',
  runtime.slice(from, to) + '; return frameStorage();')(frame, new URL(address), layerService && new URL(layerService));

const SPEC = 'https://specs.example/lane/docs/specs/report.spec.html';
const OTHER = 'https://other.example/lane/docs/specs/report.spec.html'; // another service, the same path
const OUTBOX = 'spec-chat:outbox:lane/docs/specs/report.spec.html.review';
const REVIEWER = 'spec-chat:reviewer';

// A frame with no name keeps the page's items in its name, across reloads.
let frame = { name: '' };
load(frame, SPEC).setItem(OUTBOX, '[1]');
assert.ok(frame.name.startsWith('spec-chat:'), 'kept in the frame name');
assert.equal(load(frame, SPEC).getItem(OUTBOX), '[1]', 'kept across a reload');

// Read at each use: a store sees what was written to the frame since it was made, and writing keeps it.
let page = load(frame, SPEC);
load(frame, SPEC).setItem(OUTBOX, '[2]');
assert.equal(page.getItem(OUTBOX), '[2]', 'reads the frame name now, not as it was');
page.setItem(REVIEWER, 'mango');
assert.equal(load(frame, SPEC).getItem(OUTBOX), '[2]', 'a write keeps what was written since the store was made');

// A name the host gave its frame stays the host's: the page keeps nothing there and never overwrites it.
frame = { name: 'host-target' };
page = load(frame, SPEC);
assert.equal(page.getItem(OUTBOX), null, 'the host name holds nothing of the page');
assert.throws(() => page.setItem(OUTBOX, '[1]'), 'a write to the host name is refused');
assert.equal(frame.name, 'host-target', 'the host name stays');
// Also when the host names its frame after the page loaded.
frame = { name: '' };
page = load(frame, SPEC);
page.setItem(OUTBOX, '[1]');
frame.name = 'host-target';
assert.equal(page.getItem(OUTBOX), '[1]', 'once the host names its frame, the page serves what it kept there from memory');
assert.throws(() => page.setItem(OUTBOX, '[2]'), 'a write after the host named its frame is refused');
assert.equal(frame.name, 'host-target', 'the name the host set after boot stays');
assert.equal(load(frame, SPEC).getItem(OUTBOX), null, 'a reload in that frame finds nothing of the page');

// Under the prefix, anything but a plain object reads as no items: no throw, and no key written after it is lost.
for (const kept of ['[]', 'null', '"text"', '7', 'true', '{broken', '']) {
  frame = { name: 'spec-chat:' + kept };
  page = load(frame, SPEC);
  assert.equal(page.getItem(OUTBOX), null, kept + ' holds no items');
  page.setItem(OUTBOX, '[1]');
  page.setItem(REVIEWER, 'mango');
  assert.equal(load(frame, SPEC).getItem(OUTBOX), '[1]', kept + ': the outbox is kept');
  assert.equal(load(frame, SPEC).getItem(REVIEWER), 'mango', kept + ': the fruit name is kept beside it');
}

// Each service's items stay apart. A frame moved to another service's page at the same path finds none of the first
// service's outbox, so never sends it there; back at the first, it is still there; the service's other pages share them.
frame = { name: '' };
load(frame, SPEC).setItem(OUTBOX, '[1]');
load(frame, SPEC).setItem(REVIEWER, 'mango');
assert.equal(load(frame, OTHER).getItem(OUTBOX), null, 'another service at the same path finds no outbox');
assert.equal(load(frame, OTHER).getItem(REVIEWER), null, 'nor the first service\'s fruit name');
load(frame, OTHER).setItem(OUTBOX, '[9]');
assert.equal(load(frame, SPEC).getItem(OUTBOX), '[1]', 'back at the first service, its outbox is its own');
assert.equal(load(frame, OTHER).getItem(OUTBOX), '[9]', 'and the other service keeps its own');
assert.equal(load(frame, 'https://specs.example/other/docs/specs/notes.spec.html').getItem(REVIEWER), 'mango', 'the same service\'s other pages share its items');

// The layer's service is the one it was loaded from, whichever host page it is on.
frame = { name: '' };
load(frame, 'https://bb.example/pane', 'https://specs.example/').setItem(REVIEWER, 'papaya');
assert.equal(load(frame, 'https://wb.example/pane', 'https://specs.example/').getItem(REVIEWER), 'papaya', 'another host page of the same service');
assert.equal(load(frame, 'https://bb.example/pane', 'https://other.example/').getItem(REVIEWER), null, 'the same host page with another service');

// Outbox level: a page with no origin and its outbox, saving through the runtime's own save and drain. Its review
// service stores each event it is sent while online, and refuses the connection while offline.
const outboxFrom = runtime.indexOf('const OUTBOX_KEY'), outboxTo = runtime.indexOf('\n// The service refused the event outright', outboxFrom);
assert.ok(outboxFrom >= 0 && outboxTo > outboxFrom, 'runtime exposes the outbox');
const noOrigin = name => ({ name, get localStorage() { throw new Error('no origin, no browser storage'); } });
function loadOutbox(frame, address, service) {
  const state = { reviewer: { browser: 'b1', author: 'mango' }, version: null, lastStamp: 0, outboxBlocked: false,
    transport: { postEvent: async entry => { if (!service.online) throw new Error('offline'); service.stored.push(entry.body.text); return 'stored'; } } };
  const none = () => {};
  return Function('window', 'OWN_ADDRESS', 'LAYER_SERVICE', 'REVIEW_DIR', 'state', 'nextStamp', 'eventName', 'layerRoute', 'ingest', 'forgetEvent', 'renderOffline',
    runtime.slice(outboxFrom, outboxTo) + '; return { save, flushOutbox, outboxEntries };')(
    frame, new URL(address), null, 'lane/docs/specs/report.spec.html.review', state, last => last + 1, (event, stamp) => 'n' + stamp, none, none, none, none);
}
const waiting = page => page.outboxEntries().map(entry => entry.body.text);

// Changes queued in the frame name before the host names its frame still send, with every change saved after; the
// name stays the host's.
let service = { online: false, stored: [] };
frame = noOrigin('');
let outbox = loadOutbox(frame, SPEC, service);
outbox.save({ text: 'e1' }); await outbox.flushOutbox();
outbox.save({ text: 'e2' }); await outbox.flushOutbox();
assert.deepEqual(waiting(outbox), ['e1', 'e2'], 'offline, both changes wait');
assert.ok(frame.name.startsWith('spec-chat:'), 'in the frame name');
frame.name = 'host-target';
outbox.save({ text: 'e3' }); await outbox.flushOutbox();
assert.deepEqual(waiting(outbox), ['e1', 'e2', 'e3'], 'the next save keeps the changes queued before the host named its frame');
service.online = true;
await outbox.flushOutbox();
assert.deepEqual(service.stored, ['e1', 'e2', 'e3'], 'all three send, in order');
assert.deepEqual(waiting(outbox), [], 'and the outbox drains');
assert.equal(frame.name, 'host-target', 'the name stays the host\'s');

// Also with no save after the host names its frame: the queued changes send once the service is back.
service = { online: false, stored: [] };
frame = noOrigin('');
outbox = loadOutbox(frame, SPEC, service);
outbox.save({ text: 'e1' }); await outbox.flushOutbox();
outbox.save({ text: 'e2' }); await outbox.flushOutbox();
frame.name = 'host-target';
service.online = true;
await outbox.flushOutbox();
assert.deepEqual(service.stored, ['e1', 'e2'], 'both send');
assert.deepEqual(waiting(outbox), [], 'and the outbox drains');
assert.equal(frame.name, 'host-target', 'the name stays the host\'s');

console.log('runtime served storage tests passed');
