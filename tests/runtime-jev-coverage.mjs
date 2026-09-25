import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
const start = runtime.indexOf('function coveragePair(');
const end = runtime.indexOf('\n\nfunction anchorElement(', start);
assert.ok(start >= 0 && end > start, 'runtime exposes pure coverage aggregation');
const { coverageGapFlags, coverageFlags } = Function(
  runtime.slice(start, end) + '; return { coverageGapFlags, coverageFlags };',
)();

const flags = coverageGapFlags([
  { kind: 'coverage', id: 'story-gap::criterion-verified', state: 'label', label: 'unrelated' },
  { kind: 'coverage', id: 'story-gap::criterion-unsure', state: 'unsure', label: null },
  { kind: 'coverage', id: 'story-covered::criterion-verified', state: 'label', label: 'verifies' },
  { kind: 'coverage', id: 'story-covered::criterion-gap', state: 'label', label: 'unrelated' },
]);
assert.deepEqual(flags, [
  { anchor: 'story-gap', side: 'story', state: 'unsure', label: 'unsure' },
  { anchor: 'criterion-unsure', side: 'criterion', state: 'unsure', label: 'unsure' },
  { anchor: 'criterion-gap', side: 'criterion', state: 'gap', label: 'No story backs this' },
]);
assert.deepEqual(coverageFlags([
  { kind: 'coverage', story: 'story', criterion: 'criterion', state: 'label', label: 'unrelated' },
]), [{ anchor: 'story', side: 'story', state: 'gap', label: 'No criterion covers this' },
    { anchor: 'criterion', side: 'criterion', state: 'gap', label: 'No story backs this' }]);
assert.match(runtime, /No criterion covers this/);
assert.match(runtime, /No story backs this/);

console.log('runtime Jev coverage tests passed');
