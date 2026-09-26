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

assert.deepEqual(corpusFlags([
  { kind: 'corpus', id: 'draft', state: 'label', label: 'contradicts', target: 'non-goal-text' },
  { kind: 'corpus', id: 'draft', state: 'label', label: 'overlaps', target: 'compared-range#bar-flow' },
  { kind: 'corpus', id: 'unclear', state: 'unsure', label: null, target: null },
  { kind: 'corpus', id: 'unclear', state: 'unsure', label: null, target: null },
  { kind: 'corpus', id: 'mixed', state: 'unsure', label: null, target: null },
  { kind: 'corpus', id: 'mixed', state: 'label', label: 'overlaps', target: 'other#flow' },
  { kind: 'corpus', id: 'draft', state: 'label', label: 'unrelated', target: 'other' },
  { kind: 'corpus', id: 'draft', state: 'unavailable', label: null, target: 'other' },
  { kind: 'corpus', id: 'draft', state: 'unavailable', label: null, target: 'other' },
]), [
  { anchor: 'draft', state: 'label', label: 'Contradicts', target: 'non-goal-text' },
  { anchor: 'draft', state: 'label', label: 'Overlaps', target: 'compared-range#bar-flow' },
  { anchor: 'unclear', state: 'unsure', label: 'unsure', target: null },
  { anchor: 'mixed', state: 'label', label: 'Overlaps', target: 'other#flow' },
  { anchor: 'draft', state: 'unavailable', label: 'Jev unavailable', target: null },
]);
assert.deepEqual(corpusTargetLink('non-goal-text'), { text: '#non-goal-text', href: '#non-goal-text' });
assert.deepEqual(corpusTargetLink('compared-range#bar-flow'), {
  text: 'compared-range#bar-flow', href: '/compared-range#bar-flow',
});
assert.equal(corpusTargetLink(''), null);
assert.match(runtime, /Contradicts/);
assert.match(runtime, /Overlaps/);
assert.match(runtime, /Oversteps/);

console.log('runtime Jev corpus tests passed');
