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

assert.match(runtime, /new URLSearchParams\(location\.search\)\.get\('focus'\) !== 'changes'/, 'focus activates only through the issue-focus URL');
assert.match(runtime, /function ownAnchorSignature\(/, 'parent anchors compare their own heading and visual-island content');
assert.doesNotMatch(runtime, /body\.hx-focus-active \[data-hx-focus=unchanged\]\{opacity:/, 'focus never dims pins through ancestor opacity');
assert.doesNotMatch(runtime, /color-mix\(in srgb,currentColor/, 'focus recession never compounds inherited transparency');
assert.match(runtime, /:where\(body\.hx-focus-active \[data-hx-focus=unchanged\]\)\{color:#6b6e75!important;background-image:none!important;box-shadow:none!important\}/, 'unchanged light-mode surfaces resist custom color and decorative background overrides');
assert.match(runtime, /:not\(\[data-hx-focus=changed\] \*\)\)\{color:#6b6e75!important\}/, 'recession does not override descendants of a changed block');
assert.match(runtime, /:where\(body\.hx-focus-active \[data-hx-focus=changed\]\)\{color:#22242a\}/, 'changed blocks reset to full light-mode contrast inside unchanged parents');
assert.match(runtime, /element\.dataset\.hxFocusRoot = 'changed'/, 'focus marks classified changed roots in the rendered document');
assert.match(runtime, /\[data-hx-focus-root=changed\]\)\{outline:3px solid #087f73!important/, 'changed roots keep an explicit light-mode focus boundary across document styles');
assert.match(runtime, /:where\(body\.hx-focus-active \[data-hx-focus=unchanged\]\)\{color:#9fa1a7!important\}/, 'unchanged dark-mode text resists custom page overrides');
assert.match(runtime, /:where\(body\.hx-focus-active \[data-hx-focus=changed\]\)\{color:#e8e7e2\}/, 'changed blocks reset to full dark-mode contrast inside unchanged parents');
assert.match(runtime, /\[data-hx-focus-root=changed\]\)\{outline-color:#5eead4!important\}/, 'changed roots keep an explicit dark-mode focus boundary');
assert.match(runtime, /> :is\(\[data-render-target\],figure,img,svg,canvas\):not\(\[data-anchor\]\)/, 'visual recession never wraps an independently anchored visual block');
assert.match(runtime, /AbortController/, 'focus bounds slow baseline reads');
assert.doesNotMatch(runtime, /await applyIssueFocus\(\)/, 'focus lookup never blocks chart and review boot');
assert.match(runtime, /fetch\('\/api\/baseline\?'/, 'focus reads its baseline from the review server');

console.log('runtime focus model tests passed');
