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

assert.match(runtime, /new URLSearchParams\(location\.search\)\.get\('focus'\) !== 'changes'/, 'focus activates only through the issue-focus URL');
assert.match(runtime, /function ownAnchorSignature\(/, 'parent anchors compare their own heading and visual-island content');
assert.doesNotMatch(runtime, /body\.hx-focus-active \[data-hx-focus=unchanged\]\{opacity:/, 'focus never dims pins through ancestor opacity');
assert.match(runtime, /body\.hx-focus-active \[data-hx-focus=unchanged\] > :not\(\[data-anchor\]\)/, 'unchanged direct spec content visually recedes without dimming nested review controls');
assert.match(runtime, /AbortController/, 'focus bounds slow baseline reads');
assert.doesNotMatch(runtime, /await applyIssueFocus\(\)/, 'focus lookup never blocks chart and review boot');
assert.match(runtime, /fetch\('\/api\/baseline\?'/, 'focus reads its baseline from the review server');

console.log('runtime focus model tests passed');
