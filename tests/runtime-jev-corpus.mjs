import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
const otherRuntime = readFileSync(resolve(root, 'docs/specs/.viz/runtime.js'), 'utf8');
assert.equal(runtime, otherRuntime, 'runtime copies stay byte-identical');

const flagsStart = runtime.indexOf('function corpusFlags(');
const targetStart = runtime.indexOf('function corpusTargetLink(', flagsStart);
const targetEnd = runtime.indexOf('\n\nfunction goToJevTarget(', targetStart);
assert.ok(flagsStart >= 0 && targetStart > flagsStart && targetEnd > targetStart, 'runtime exposes corpus helpers');
const { corpusFlags } = Function(runtime.slice(flagsStart, targetStart) + '; return { corpusFlags };')();
const { corpusTargetLink } = Function(runtime.slice(targetStart, targetEnd) + '; return { corpusTargetLink };')();

// Each label flag keeps the item it shows, which its buttons answer for.
const items = [
  { kind: 'corpus', id: 'draft', state: 'label', label: 'contradicts', target: 'non-goal-text', level: 'important' },
  { kind: 'corpus', id: 'draft', state: 'label', label: 'overlaps', target: 'compared-range#bar-flow' },
  { kind: 'corpus', id: 'part', state: 'label', label: 'partial-overlap', target: 'other#flow' },
  { kind: 'corpus', id: 'mixed', state: 'label', label: 'oversteps', target: 'other#flow', level: 'warning' },
  { kind: 'corpus', id: 'quiet', state: 'none', label: null, target: null },
  { kind: 'corpus', id: 'draft', state: 'label', label: 'unrelated', target: 'other' },
  { kind: 'corpus', id: 'draft', state: 'unavailable', label: null, target: 'other' },
  { kind: 'corpus', id: 'draft', state: 'unavailable', label: null, target: 'other' },
];
const flags = corpusFlags(items);
assert.deepEqual(flags, [
  { anchor: 'draft', state: 'label', label: 'Contradicts', level: 'important', target: 'non-goal-text', rule: null, item: items[0] },
  { anchor: 'draft', state: 'label', label: 'Overlaps', level: null, target: 'compared-range#bar-flow', rule: null, item: items[1] },
  { anchor: 'part', state: 'label', label: 'Partial overlap', level: null, target: 'other#flow', rule: null, item: items[2] },
  { anchor: 'mixed', state: 'label', label: 'Oversteps', level: 'warning', target: 'other#flow', rule: null, item: items[3] },
  { anchor: 'draft', state: 'unavailable', label: 'Jev unavailable', target: null },
]);
assert.ok(flags.slice(0, 4).every((flag, index) => flag.item === items[index]), 'the item itself, not a copy');
assert.deepEqual(corpusTargetLink('non-goal-text'), { text: '#non-goal-text', href: '#non-goal-text' });
assert.deepEqual(corpusTargetLink('compared-range#bar-flow'), {
  text: 'compared-range#bar-flow', href: '/compared-range#bar-flow',
});
assert.equal(corpusTargetLink(''), null);
assert.match(runtime, /Contradicts/);
assert.match(runtime, /Overlaps/);
assert.match(runtime, /Partial overlap/);
assert.match(runtime, /Oversteps/);

console.log('runtime Jev corpus tests passed');
