import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');

assert.match(runtime, /id = 'hx-reading'/, 'HTTP pages expose a reading view toggle');
assert.match(runtime, /params\.set\('view', 'reading'\)/, 'reading view requests the explicit route view');
assert.match(runtime, /item\.kind !== 'audience'/, 'runtime consumes only audience items for reading view');
assert.match(runtime, /item\.state === 'label' && item\.label === 'internals'/, 'internals are the only confident clauses dimmed');
assert.doesNotMatch(runtime, /appendJevMarker\(holder, audienceLabel/, 'confident audience labels stay invisible');
assert.match(runtime, /hx-reading-active \[data-hx-audience="internals"\].*color:#586069!important/s, 'internals use a readable light-theme color');
assert.match(runtime, /color:#b9c0ca!important/, 'internals use a readable dark-theme color');
assert.match(runtime, /clearGitFocusForReading/, 'reading view clears Git focus');
assert.match(runtime, /delete el\.dataset\.hxAudience/, 'turning reading view off clears labels');
assert.match(runtime, /item\.state !== 'label'|Jev unavailable/, 'reading view renders unsure and unavailable states');
assert.doesNotMatch(runtime, /data-hx-audience="internals"[^}]*display\s*:\s*none/, 'reading view never hides internals');
assert.doesNotMatch(runtime, /data-hx-audience="internals"[^}]*order\s*:/, 'reading view never reorders internals');

console.log('runtime reading tests passed');
