import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');

assert.match(runtime, /@media\(max-width:640px\)/, 'runtime defines a narrow review layout');
assert.match(runtime, /\.hx-panel\{width:100vw;height:100dvh;max-height:100dvh/, 'mobile review uses a dynamic full-viewport sheet');
assert.match(runtime, /\.hx-composer textarea\{min-height:120px;font-size:16px/, 'mobile composer avoids browser input zoom');
assert.match(runtime, /\.hx-dock-open,\.hx-dock-thread\{width:44px;height:44px/, 'mobile conversation controls meet the touch target floor');
assert.match(runtime, /openPanel\(!window\.matchMedia\('\(max-width: 640px\)'\)\.matches\)/, 'mobile comment mode exposes the document before target selection');
assert.match(runtime, /class="hx-mobile-handoff" id="hx-mobile-handoff"/, 'mobile toolbar exposes handoff beside comment mode');
assert.match(runtime, /mobileHandoff\.textContent = drafts \? 'Hand off \(' \+ drafts \+ '\)' : 'Hand off'/, 'mobile handoff renders the current draft count');
assert.match(runtime, /e\.message === 'Script error\.' && !e\.filename && !e\.lineno && !e\.colno && !e\.error/, 'fully opaque browser errors do not raise a fatal review overlay');
assert.match(runtime, /overlay\('error', e\.message/, 'attributable script errors remain visible');

console.log('runtime mobile contract tests passed');
