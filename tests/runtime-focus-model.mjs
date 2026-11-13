import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
const start = runtime.indexOf('function classifyAnchorSignatures(');
const end = runtime.indexOf('\n\nfunction anchorSignatures(', start);
assert.ok(start >= 0 && end > start, 'runtime exposes pure anchor classification');
const classifyAnchorSignatures = Function(runtime.slice(start, end) + '; return classifyAnchorSignatures;')();
const rootsStart = runtime.indexOf('function changedRootAnchors(');
const rootsEnd = runtime.indexOf('\n\nfunction ownAnchorSignature(', rootsStart);
assert.ok(rootsStart >= 0 && rootsEnd > rootsStart, 'runtime exposes pure changed-root classification');
const changedRootAnchors = Function(runtime.slice(rootsStart, rootsEnd) + '; return changedRootAnchors;')();

const current = new Map([
  ['unchanged', '<p>same</p>'],
  ['modified', '<p>after</p>'],
  ['added', '<p>new</p>'],
]);
const baseline = new Map([
  ['unchanged', '<p>same</p>'],
  ['modified', '<p>before</p>'],
]);
assert.deepEqual([...classifyAnchorSignatures(current, baseline)], [
  ['unchanged', 'unchanged'],
  ['modified', 'changed'],
  ['added', 'changed'],
], 'current anchors classify only from baseline signatures');
assert.deepEqual([...classifyAnchorSignatures(current, null).values()], ['changed', 'changed', 'changed'], 'a new spec is entirely changed');

const hierarchy = new Map([
  ['unchanged-parent', null],
  ['changed-child', 'unchanged-parent'],
  ['changed-parent', null],
  ['changed-descendant', 'changed-parent'],
]);
const hierarchyClassification = new Map([
  ['unchanged-parent', 'unchanged'],
  ['changed-child', 'changed'],
  ['changed-parent', 'changed'],
  ['changed-descendant', 'changed'],
]);
assert.deepEqual(
  [...changedRootAnchors(hierarchy, hierarchyClassification)],
  ['changed-child', 'changed-parent'],
  'focus boundaries mark changed blocks beneath unchanged context and collapse nested changed descendants',
);

assert.match(runtime, /if \(httpPage\) applyIssueFocus\(\);/, 'every HTTP page applies change focus by default, no focus=changes needed');
assert.doesNotMatch(runtime, /get\('focus'\) !== 'changes'\) return/, 'focus no longer requires the issue-focus URL');

const markStart = runtime.indexOf('function markIssueFocus(');
const markEnd = runtime.indexOf('\n\nasync function fetchBaseline(', markStart);
assert.ok(markStart >= 0 && markEnd > markStart, 'runtime exposes issue focus marking');
function plainOpen(classes) {
  const elements = Object.keys(classes).map(anchor => ({ dataset: { anchor }, parentElement: null }));
  const body = new Set(['hx-focus-active']);
  const document = {
    querySelectorAll: () => elements,
    body: { classList: { toggle: (name, on) => (on ? body.add(name) : body.delete(name)) } },
  };
  const markIssueFocus = Function('document', 'DOMParser', 'anchorSignatures', 'classifyAnchorSignatures', 'changedRootAnchors',
    runtime.slice(markStart, markEnd) + '; return markIssueFocus;')(
    document, class { parseFromString() { return null; } }, () => null,
    () => new Map(Object.entries(classes)), changedRootAnchors);
  markIssueFocus('', { html: '' });
  return { active: body.has('hx-focus-active'), elements };
}
const withChanges = plainOpen({ a: 'unchanged', b: 'changed' });
assert.equal(withChanges.active, true, 'plain open with changes highlights them');
assert.equal(withChanges.elements[1].dataset.hxFocusRoot, 'changed', 'plain open marks the changed root');
assert.equal(plainOpen({ a: 'unchanged', b: 'unchanged' }).active, false, 'plain open of a clean spec renders clear, no veil');
assert.match(runtime, /function ownAnchorSignature\(/, 'parent anchors compare their own heading and visual-island content');
assert.doesNotMatch(runtime, /body\.hx-focus-active \[data-hx-focus=unchanged\]\{opacity:/, 'focus never dims pins through ancestor opacity');
assert.doesNotMatch(runtime, /color-mix\(in srgb,currentColor/, 'focus recession never compounds inherited transparency');
assert.match(runtime, /element\.dataset\.hxFocusRoot = 'changed'/, 'focus marks classified changed roots in the rendered document');
assert.doesNotMatch(runtime, /data-hx-focus-root=changed\]\)\{outline:/, 'changed roots carry no outline; being unveiled is the only marker they need');
assert.doesNotMatch(runtime, /outline-color:#5eead4/, 'no dark-mode outline survives on changed roots');
assert.match(runtime, /AbortController/, 'focus bounds slow baseline reads');
assert.doesNotMatch(runtime, /await applyIssueFocus\(\)/, 'focus lookup never blocks chart and review boot');
assert.match(runtime, /fetch\('\/api\/baseline\?'/, 'focus reads its baseline from the review server');
assert.doesNotMatch(runtime, /fetch\(location\.pathname, \{ cache/, 'the page never downloads its own file again');
assert.doesNotMatch(runtime, /no-store/, 'no request bypasses the cache the navigation filled');
assert.match(runtime, /if \(httpPage\) state\.range\.loaded = anchorSignatures\(document\);\n\s*(\/\/.*\n\s*)*mountUI\(\);/,
  'anchors are read from the served DOM before the runtime changes it');
assert.equal((runtime.match(/markIssueFocus\(state\.range\.loaded, baseline\)/g) || []).length, 2,
  'default open and base changes compare the same loaded anchors');
assert.match(runtime, /sharedDocumentStyle \? '' : DOC_CSS/, 'a linked shared spec stylesheet owns document presentation');
assert.match(runtime, /sharedDocumentStyle \? '' : DOC_CSS\) \+ FOCUS_CSS \+ CSS/, 'focus styling loads with shared document styles');
const focusCss = runtime.slice(runtime.indexOf('const FOCUS_CSS = `'), runtime.indexOf('const CSS = `'));
assert.match(focusCss, /background:rgba\(0,0,0,calc\(\.5\*var\(--hx-veil,1\)\)\)[^}]*backdrop-filter:blur\(calc\(2\.5px\*var\(--hx-veil,1\)\)\)/, 'unchanged blocks use a blurred black veil whose alpha and blur both scale from --hx-veil');
assert.match(runtime, /id="hx-veil"[^>]*min="0"[^>]*max="100"/, 'the review panel exposes one diff visibility slider over the full range');
assert.match(runtime, /setProperty\('--hx-veil'/, 'the slider drives --hx-veil so one control governs the whole veil');
assert.doesNotMatch(focusCss, /blur\(2\.5px\)/, 'no unscaled blur remains');
assert.match(focusCss, /\[data-hx-focus=unchanged\] \.hx-pin[^}]*z-index:700/, 'pins remain above the veil');

console.log('runtime focus model tests passed');
