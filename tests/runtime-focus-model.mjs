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

assert.match(runtime, /if \(httpPage\) applyIssueFocus\(\);/, 'every HTTP page diffs, with or without focus=changes');
assert.doesNotMatch(runtime, /function loadRangeBar\(/, 'no bar-only load path skips highlighting');

function extract(name, next) {
  const from = runtime.indexOf('function ' + name + '(');
  const to = runtime.indexOf('\n\nfunction ' + next + '(', from);
  assert.ok(from >= 0 && to > from, 'runtime exposes ' + name);
  return runtime.slice(from, to);
}
const sha256Hex = Function(extract('sha256Hex', 'reviewedTime') + '; return sha256Hex;')();
const { createHash } = await import('node:crypto');
for (const size of [0, 1, 55, 56, 63, 64, 65, 1000, 150000]) {
  const bytes = new Uint8Array(size).map((_, i) => (i * 131 + 7) & 255);
  assert.equal(sha256Hex(bytes), createHash('sha256').update(bytes).digest('hex'), 'sha256Hex matches node for ' + size + ' bytes');
}
const rangeText = Function(
  extract('reviewedTime', 'comparesLastReviewed') + '\n' + extract('comparesLastReviewed', 'rangeBarText') + '\n'
  + extract('rangeBarText', 'baselineParams') + '; return { rangeBarText, reviewedTime };',
)();
const at = '2026-09-25T14:05:00Z';
const local = new Date(at);
const pad = n => String(n).padStart(2, '0');
const stamp = local.getFullYear() + '-' + pad(local.getMonth() + 1) + '-' + pad(local.getDate()) + ' ' + pad(local.getHours()) + ':' + pad(local.getMinutes());
assert.equal(rangeText.reviewedTime(at), stamp);
assert.equal(
  rangeText.rangeBarText({ base: 'reviewed', head: 'abcdef1234', headDate: '2026-09-25', dirty: false, reviewed: { sha256: 'x', at } }),
  'Changes from last reviewed ' + stamp + ' to abcdef1 2026-09-25',
  'bar names the last reviewed version and its time',
);
assert.equal(
  rangeText.rangeBarText({ base: '1234567890', baseDate: '2026-09-01', head: 'abcdef1234', headDate: '2026-09-25', dirty: true, reviewed: { sha256: 'x', at } }),
  'Changes from 1234567 2026-09-01 to working copy of abcdef1 2026-09-25',
  'a picked commit keeps the commit bar',
);
assert.match(runtime, /if \(state\.range\.shownSha256\) review\.specSha256 = state\.range\.shownSha256;/, 'hand-offs carry the shown bytes SHA-256');
assert.match(runtime, /if \(reviewed\) url\.searchParams\.delete\('base'\);/, 'choosing Last reviewed drops base from the URL');
assert.match(runtime, /label\.textContent = 'Last reviewed';/, 'the picker lists Last reviewed');
assert.match(runtime, /function ownAnchorSignature\(/, 'parent anchors compare their own heading and visual-island content');
assert.doesNotMatch(runtime, /body\.hx-focus-active \[data-hx-focus=unchanged\]\{opacity:/, 'focus never dims pins through ancestor opacity');
assert.doesNotMatch(runtime, /color-mix\(in srgb,currentColor/, 'focus recession never compounds inherited transparency');
assert.match(runtime, /element\.dataset\.hxFocusRoot = 'changed'/, 'focus marks classified changed roots in the rendered document');
assert.doesNotMatch(runtime, /data-hx-focus-root=changed\]\)\{outline:/, 'changed roots carry no outline; being unveiled is the only marker they need');
assert.doesNotMatch(runtime, /outline-color:#5eead4/, 'no dark-mode outline survives on changed roots');
assert.match(runtime, /AbortController/, 'focus bounds slow baseline reads');
assert.doesNotMatch(runtime, /await applyIssueFocus\(\)/, 'focus lookup never blocks chart and review boot');
assert.match(runtime, /fetch\('\/api\/baseline\?'/, 'focus reads its baseline from the review server');
assert.match(runtime, /sharedDocumentStyle \? '' : DOC_CSS/, 'a linked shared spec stylesheet owns document presentation');
assert.match(runtime, /sharedDocumentStyle \? '' : DOC_CSS\) \+ FOCUS_CSS \+ CSS/, 'focus styling loads with shared document styles');
const focusCss = runtime.slice(runtime.indexOf('const FOCUS_CSS = `'), runtime.indexOf('const CSS = `'));
assert.match(focusCss, /background:rgba\(0,0,0,calc\(\.5\*var\(--hx-veil,1\)\)\)[^}]*backdrop-filter:blur\(calc\(2\.5px\*var\(--hx-veil,1\)\)\)/, 'unchanged blocks use a blurred black veil whose alpha and blur both scale from --hx-veil');
assert.match(runtime, /id="hx-veil"[^>]*min="0"[^>]*max="100"/, 'the review panel exposes one diff visibility slider over the full range');
assert.match(runtime, /setProperty\('--hx-veil'/, 'the slider drives --hx-veil so one control governs the whole veil');
assert.doesNotMatch(focusCss, /blur\(2\.5px\)/, 'no unscaled blur remains');
assert.match(focusCss, /\[data-hx-focus=unchanged\] \.hx-pin[^}]*z-index:700/, 'pins remain above the veil');

console.log('runtime focus model tests passed');
