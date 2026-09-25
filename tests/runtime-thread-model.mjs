import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
const start = runtime.indexOf('function foldThreads(events)');
const end = runtime.indexOf('\n\nfunction ingest(events)', start);
assert.ok(start >= 0 && end > start, 'runtime exposes the pure thread-folding function');
const model = Function(runtime.slice(start, end) + '; return { foldThreads, resolvedThreadCollapsed, threadReplyAction, reviewHandoffState, handoffObservation, commentModeShortcut, threadDockEntries, acknowledgedReplyCount, isOpenTbd, openTbdMarkers, nextOpenTbd, tbdBlock, tbdHighlightBlocks, advanceTbd, renderTbdHighlight };')();
const { foldThreads, resolvedThreadCollapsed, threadReplyAction, reviewHandoffState, handoffObservation, commentModeShortcut, threadDockEntries, acknowledgedReplyCount, isOpenTbd, openTbdMarkers, nextOpenTbd, tbdBlock, tbdHighlightBlocks, advanceTbd, renderTbdHighlight } = model;

const event = (name, actor, body) => ({ name, actor, body: { actor, schemaVersion: 1, ...body } });
const events = [
  event('100-comment-root.json', 'human', { id: 'u-root', event: 'comment', anchorId: 'policy', target: null, text: 'Root' }),
  event('110-handoff-root.json', 'human', { id: 'h-root', event: 'handoff' }),
  event('120-reply-root.json', 'agent', { id: 'r-root', event: 'reply', respondsTo: 'u-root', status: 'acknowledged', text: 'Agent answer' }),
  event('130-reply-follow.json', 'human', { id: 'u-follow', event: 'reply', respondsTo: 'r-root', threadId: 'u-root', anchorId: 'policy', target: null, text: 'Follow-up' }),
  event('140-edit-follow.json', 'human', { id: 'e-follow', event: 'edit', supersedes: 'u-follow', threadId: 'u-root', anchorId: 'policy', target: null, text: 'Edited follow-up' }),
  event('150-handoff-follow.json', 'human', { id: 'h-follow', event: 'handoff' }),
  event('160-reply-stale.json', 'agent', { id: 'r-stale', event: 'reply', respondsTo: 'u-root', status: 'acknowledged', text: 'Late root answer' }),
];

let threads = foldThreads(events);
let thread = threads.get('u-root');
assert.equal(thread.status, 'pending', 'a reply to an older human message does not acknowledge the edited follow-up');
assert.equal(thread.latestHumanId, 'e-follow');
assert.equal(thread.messages[2].body.text, 'Edited follow-up');
assert.ok(!thread.messages.some(message => message.body.text === 'Follow-up'), 'superseded text is removed from the effective thread');
assert.equal(acknowledgedReplyCount(threads), 0, 'a human follow-up clears the acknowledged-reply count');

events.push(event('170-reply-edit.json', 'agent', { id: 'r-edit', event: 'reply', respondsTo: 'e-follow', status: 'acknowledged', text: 'Current answer' }));
threads = foldThreads(events);
thread = threads.get('u-root');
assert.equal(thread.status, 'acknowledged', 'replying to the latest edit acknowledges the thread');
assert.equal(thread.messages.at(-1).body.id, 'r-edit');
assert.equal(acknowledgedReplyCount(threads), 1, 'a current agent reply enters the acknowledged-reply count');

events.push(event('180-status-resolved.json', 'human', { id: 's-root', event: 'status', respondsTo: 'u-root', threadId: 'u-root', status: 'resolved' }));
thread = foldThreads(events).get('u-root');
assert.equal(thread.status, 'resolved');
assert.equal(acknowledgedReplyCount(foldThreads(events)), 0, 'resolving a thread clears the acknowledged-reply count');
const expandedResolved = new Set();
assert.equal(resolvedThreadCollapsed(thread, expandedResolved), true, 'resolved threads start collapsed');
expandedResolved.add(thread.id);
assert.equal(resolvedThreadCollapsed(thread, expandedResolved), false, 'a reopened resolved thread stays expanded');
assert.equal(resolvedThreadCollapsed({ ...thread, status: 'acknowledged' }, expandedResolved), false, 'non-resolved threads never collapse');
assert.deepEqual(threadReplyAction(thread), { label: 'Reply and reopen', message: thread.messages.at(-1) }, 'a resolved thread continues through an ordinary reply');
assert.deepEqual(threadReplyAction({ ...thread, status: 'acknowledged' }), { label: '↩ Reply', message: thread.messages.at(-1) }, 'an active thread keeps the ordinary reply action');

const reopenedEvents = [...events, event('190-reply-reopen.json', 'human', { id: 'u-reopen', event: 'reply', respondsTo: 'r-edit', threadId: 'u-root', anchorId: 'policy', target: null, text: 'Reopened' })];
assert.equal(foldThreads(reopenedEvents).get('u-root').status, 'draft', 'replying to a resolved thread reopens it as a draft without a new status');

const shortcut = overrides => commentModeShortcut({ key: 'c', target: { tagName: 'BODY' }, ...overrides });
assert.equal(shortcut({}), true, 'bare C enters comment mode');
assert.equal(shortcut({ ctrlKey: true }), false, 'Ctrl+C remains available to the browser');
assert.equal(shortcut({ metaKey: true }), false, 'Command+C remains available to the browser');
assert.equal(shortcut({ altKey: true }), false, 'Alt+C does not enter comment mode');
assert.equal(shortcut({ shiftKey: true }), false, 'Shift+C does not enter comment mode');
assert.equal(shortcut({ repeat: true }), false, 'key repeat does not toggle comment mode repeatedly');
assert.equal(shortcut({ target: { tagName: 'INPUT' } }), false, 'typing in an input does not enter comment mode');
assert.equal(shortcut({ target: { tagName: 'DIV', isContentEditable: true } }), false, 'typing in editable content does not enter comment mode');

const dockEntries = threadDockEntries(new Map([
  ['first', { id: 'first' }],
  ['second', { id: 'second' }],
]));
assert.deepEqual(dockEntries.map(entry => [entry.thread.id, entry.number]), [['second', 2], ['first', 1]], 'the dock shows newest threads first while preserving pin numbers');

assert.equal(acknowledgedReplyCount(new Map([
  ['draft', { status: 'draft' }],
  ['pending', { status: 'pending' }],
  ['ack-one', { status: 'acknowledged' }],
  ['ack-two', { status: 'acknowledged' }],
  ['resolved', { status: 'resolved' }],
])), 2, 'the unread badge counts only acknowledged threads awaiting a human response');
assert.equal(acknowledgedReplyCount(new Map([['replied', { status: 'draft' }], ['resolved', { status: 'resolved' }]])), 0, 'replied-to and resolved threads leave the unread count');

assert.deepEqual(reviewHandoffState(new Map([['draft', { status: 'draft' }]])), { drafts: 1, finish: false, tbd: false, enabled: true }, 'drafts enable an ordinary hand-off');
assert.deepEqual(reviewHandoffState(new Map([['pending', { status: 'pending' }], ['resolved', { status: 'resolved' }]])), { drafts: 0, finish: false, tbd: false, enabled: false }, 'unsettled threads cannot accept the spec');
assert.deepEqual(reviewHandoffState(new Map([['resolved', { status: 'resolved' }]])), { drafts: 0, finish: true, tbd: false, enabled: true }, 'a clean resolved review enables Accept spec');
assert.deepEqual(reviewHandoffState(new Map()), { drafts: 0, finish: true, tbd: false, enabled: true }, 'a review with no threads may accept the spec explicitly');
assert.deepEqual(reviewHandoffState(new Map(), true), { drafts: 0, finish: false, tbd: true, enabled: true }, 'a material TBD blocks Accept spec and enables the TBD jump');
assert.deepEqual(reviewHandoffState(new Map([['resolved', { status: 'resolved' }]]), true), { drafts: 0, finish: false, tbd: true, enabled: true }, 'a TBD alone blocks an otherwise resolved review');
assert.deepEqual(reviewHandoffState(new Map([['draft', { status: 'draft' }]]), true), { drafts: 1, finish: false, tbd: false, enabled: true }, 'drafts still hand off despite a TBD');
assert.deepEqual(reviewHandoffState(new Map([['pending', { status: 'pending' }]]), true), { drafts: 0, finish: false, tbd: false, enabled: false }, 'unsettled threads stay disabled despite a TBD');
assert.match(runtime, /handoffState\.tbd \? 'TBD open'/, 'handoff controls read TBD open when only a TBD blocks Accept spec');
const marker = value => ({ getAttribute: name => name === 'data-spec-tbd' ? value : null });
assert.equal(isOpenTbd(''), true, 'a bare data-spec-tbd marker is open');
assert.equal(isOpenTbd('open'), true, 'any value other than later is open');
assert.equal(isOpenTbd('later'), false, 'data-spec-tbd="later" is not open');
const bare = marker(''), later = marker('later'), named = marker('question'), later2 = marker('later');
assert.deepEqual(openTbdMarkers([later, later2]), [], 'only later TBDs leave nothing open');
assert.deepEqual(reviewHandoffState(new Map([['resolved', { status: 'resolved' }]]), openTbdMarkers([later, later2]).length > 0), { drafts: 0, finish: true, tbd: false, enabled: true }, 'later TBDs do not block spec acceptance');
assert.deepEqual(openTbdMarkers([bare, later, named]), [bare, named], 'open TBDs keep document order and skip later markers');
assert.equal(nextOpenTbd([bare, named], null), bare, 'first activation focuses the first open TBD');
assert.equal(nextOpenTbd([bare, named], bare), named, 'next activation focuses the next open TBD in document order');
assert.equal(nextOpenTbd([bare, named], named), bare, 'activation wraps after the last open TBD');
assert.equal(nextOpenTbd([bare, named], later), bare, 'a stale or later last target restarts at the first open TBD');
assert.equal(nextOpenTbd([], null), null, 'no open TBD yields no jump target');
const block = { id: 'block' };
assert.equal(tbdBlock({ closest: sel => sel === '[data-anchor]' ? block : null }), block, 'the highlighted block is the nearest anchored block');
const loose = { closest: () => null };
assert.equal(tbdBlock(loose), loose, 'an unanchored marker highlights itself');
const cycle = { lastTbd: null };
assert.deepEqual([1, 2, 3, 4].map(() => advanceTbd(cycle, [bare, named])), [bare, named, bare, named], 'repeated activation cycles open TBDs in document order and wraps');
assert.equal(cycle.lastTbd, named, 'activation records the focused TBD for the next cycle step');
cycle.lastTbd = later;
assert.equal(advanceTbd(cycle, openTbdMarkers([bare, later, named])), bare, 'a later TBD is never a cycle target');
assert.match(runtime, /if \(action\.tbd\) return jumpToTbd\(advanceTbd\(state, openTbds\)\);/, 'the TBD handoff jumps to the next open TBD before posting any event');
const classList = () => { const set = new Set(); return { set, add: c => set.add(c), remove: c => set.delete(c), contains: c => set.has(c) }; };
const blockOf = () => { const el = { classList: classList() }; return el; };
const openBlock = blockOf(), otherOpenBlock = blockOf(), laterBlock = blockOf();
const inBlock = (b, value) => ({ ...marker(value), closest: sel => sel === '[data-anchor]' ? b : null });
const openInBlock = inBlock(openBlock, ''), secondInBlock = inBlock(openBlock, 'q'), otherOpen = inBlock(otherOpenBlock, ''), laterIn = inBlock(laterBlock, 'later');
const opens = openTbdMarkers([openInBlock, laterIn, secondInBlock, otherOpen]);
const tbdState = { drafts: 0, finish: false, tbd: true, enabled: true };
assert.deepEqual(tbdHighlightBlocks(tbdState, opens), [openBlock, otherOpenBlock], 'each block holding an open TBD is highlighted once; later blocks are not');
assert.deepEqual(tbdHighlightBlocks({ drafts: 1, finish: false, tbd: false, enabled: true }, opens), [], 'no highlight unless TBD open is the action');
const fakeRoot = blocks => ({ querySelectorAll: sel => sel === '.hx-tbd-open' ? blocks.filter(b => b.classList.contains('hx-tbd-open')) : [] });
const allBlocks = [openBlock, otherOpenBlock, laterBlock];
laterBlock.classList.add('hx-tbd-open');
renderTbdHighlight(fakeRoot(allBlocks), tbdHighlightBlocks(tbdState, opens));
assert.deepEqual(allBlocks.map(b => b.classList.contains('hx-tbd-open')), [true, true, false], 'render highlights only open TBD blocks and clears stale highlights');
renderTbdHighlight(fakeRoot(allBlocks), tbdHighlightBlocks({ drafts: 1, finish: false, tbd: false, enabled: true }, opens));
assert.deepEqual(allBlocks.map(b => b.classList.contains('hx-tbd-open')), [false, false, false], 'highlight clears when TBD open is no longer the action');
assert.match(runtime, /renderTbdHighlight\(document, tbdHighlightBlocks\(handoffState, openTbds\)\);/, 'the panel renders the gated open TBD highlight');
assert.equal((runtime.match(/const openTbds = openTbdMarkers\(document\.querySelectorAll\('\[data-spec-tbd\]'\)\);\n\s+const \w+ = reviewHandoffState\(state\.threads, openTbds\.length > 0\);/g) || []).length, 2, 'render and activation gate eligibility on open TBD markers only');
const tbdCss = runtime.match(/\n\.hx-tbd-open\{([^}]*)\}/);
assert.ok(tbdCss && /outline:/.test(tbdCss[1]), 'open TBD blocks have a visible outline highlight');
assert.doesNotMatch(tbdCss[1], /background|border-radius/, 'the highlight does not restyle author block background or radius');
assert.match(runtime, /body\.hx-focus-active \.hx-tbd-open\{position:relative;z-index:3\}/, 'under focus the highlighted block rises above an ancestor veil (z-index 2)');
assert.match(runtime, /body\.hx-focus-active \.hx-tbd-open\[data-hx-focus=unchanged\]::after,body\.hx-focus-active tr\.hx-tbd-open\[data-hx-focus=unchanged\] > :is\(td,th\)::after\{display:none!important\}/, 'an unchanged highlighted block drops its own veil');

const waitingHandoff = [event('300-handoff.json', 'human', { id: 'h-wait', event: 'handoff', createdAt: '2026-08-31T12:00:00.000Z' })];
assert.equal(handoffObservation(waitingHandoff, Date.parse('2026-08-31T12:00:10.000Z')), 'waiting', 'a fresh unacknowledged hand-off is waiting');
assert.equal(handoffObservation(waitingHandoff, Date.parse('2026-08-31T12:00:31.000Z')), 'queued', 'an unacknowledged hand-off becomes truthfully queued after 30 seconds');
assert.equal(handoffObservation([...waitingHandoff, event('310-reply.json', 'agent', { id: 'r-wait', event: 'reply', respondsTo: 'u-root', createdAt: '2026-08-31T12:00:12.000Z' })], Date.parse('2026-08-31T12:00:31.000Z')), null, 'a later agent event clears the waiting observation');

assert.match(runtime, /\.hx-thread-dock\{/, 'collapsed review uses a compact conversation dock');
assert.match(runtime, /\.hx-panel\{[^}]*display:none;/, 'the closed sidebar leaves document layout entirely');
assert.match(runtime, /setAttribute\('aria-label', 'Review conversations'\)/, 'the thread dock has an accessible navigation label');
assert.match(runtime, /Collapse review sidebar/, 'the open sidebar exposes a collapse control');
assert.match(runtime, /\.hx-dock-thread\[data-s=acknowledged\]\{border-color:#315fbd;color:#264f9e;background:#edf2ff\}/, 'acknowledged dock threads use the blue status palette');
assert.match(runtime, /\.hx-pin\[data-s=acknowledged\]\{background:#315fbd\}/, 'acknowledged page pins use the blue status palette');
assert.match(runtime, /class="hx-unread-badge" id="hx-unread-badge"/, 'the chat launcher includes an acknowledged-reply badge');
assert.match(runtime, /acknowledged > 99 \? '99\+'/, 'large unread counts stay compact');

const rootEdit = [
  event('200-comment-root-edit.json', 'human', { id: 'u-edit-root', event: 'comment', anchorId: 'copy', target: null, text: 'Original root' }),
  event('210-edit-root.json', 'human', { id: 'e-edit-root', event: 'edit', supersedes: 'u-edit-root', threadId: 'u-edit-root', anchorId: 'copy', target: null, text: 'Edited root' }),
  event('220-handoff-root-edit.json', 'human', { id: 'h-edit-root', event: 'handoff' }),
];
const editedRootThread = foldThreads(rootEdit).get('u-edit-root');
assert.equal(editedRootThread.id, 'u-edit-root', 'editing a root preserves the stable thread id');
assert.equal(editedRootThread.ev.body.id, 'e-edit-root');
assert.equal(editedRootThread.status, 'pending');

console.log('runtime thread model tests passed');
