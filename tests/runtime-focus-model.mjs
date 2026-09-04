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
assert.match(runtime, /element\.dataset\.hxFocusRoot = 'changed'/, 'focus marks classified changed roots in the rendered document');
assert.match(runtime, /\[data-hx-focus-root=changed\]\)\{outline:3px solid #087f73!important/, 'changed roots keep an explicit light-mode focus boundary across document styles');
assert.match(runtime, /\[data-hx-focus-root=changed\]\)\{outline-color:#5eead4!important\}/, 'changed roots keep an explicit dark-mode focus boundary');
assert.match(runtime, /AbortController/, 'focus bounds slow baseline reads');
assert.doesNotMatch(runtime, /await applyIssueFocus\(\)/, 'focus lookup never blocks chart and review boot');
assert.match(runtime, /fetch\('\/api\/baseline\?'/, 'focus reads its baseline from the review server');
assert.match(runtime, /sharedDocumentStyle \? '' : DOC_CSS/, 'a linked shared spec stylesheet owns document presentation');
assert.match(runtime, /sharedDocumentStyle \? '' : DOC_CSS\) \+ FOCUS_CSS \+ CSS/, 'focus styling is injected even when a shared spec stylesheet owns document presentation');
const docCss = runtime.slice(runtime.indexOf('const DOC_CSS = `'), runtime.indexOf('const FOCUS_CSS = `'));
assert.doesNotMatch(docCss, /hx-focus/, 'no focus rule lives in the document stylesheet that a shared spec stylesheet replaces');
const focusCss = runtime.slice(runtime.indexOf('const FOCUS_CSS = `'), runtime.indexOf('const CSS = `'));
assert.match(focusCss, /\[data-hx-focus=unchanged\]:not\(:has\(\[data-hx-focus=changed\]\)\)[^{]*::after\{content:"";position:absolute;inset:-3px;background:rgba\(0,0,0,\.5\)[^}]*backdrop-filter:blur\(2\.5px\)\}/, 'the outermost unchanged block sits under one translucent blurred dark layer');
assert.match(focusCss, /:not\(\[data-hx-focus=unchanged\]:not\(:has\(\[data-hx-focus=changed\]\)\) \*\)/, 'nested unchanged blocks never stack a second layer');
assert.match(focusCss, /tr\[data-hx-focus=unchanged\][^{]*> :is\(td,th\)::after\{content:"";position:absolute;inset:0;background:rgba\(0,0,0,\.5\)/, 'unchanged rows inside a changed table dim cell by cell');
assert.match(focusCss, /\[data-hx-focus=unchanged\] \.hx-pin[^{]*\{opacity:1;filter:none;z-index:700\}/, 'pins and badges float above the layer');
assert.doesNotMatch(focusCss, /color:#6b6e75/, 'focus no longer recolors unchanged text; the layer does the receding');
assert.match(focusCss, /prefers-color-scheme:dark\)\{[^`]*background:rgba\(0,0,0,\.6\)/, 'the dark scheme deepens the layer');

console.log('runtime focus model tests passed');
