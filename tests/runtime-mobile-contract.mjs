import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');

assert.match(runtime, /@media\(max-width:640px\)/, 'runtime defines a narrow review layout');
assert.match(runtime, /\.hx-panel\{width:100vw;height:100dvh;max-height:100dvh/, 'mobile review uses a dynamic full-viewport sheet');
assert.match(runtime, /@media\(max-width:640px\)\{[^]*\.hx-panel\{[^}]*box-sizing:border-box;padding-bottom:calc\(var\(--hx-dock-space,0px\) \+ 10px \+ env\(safe-area-inset-bottom\)\)/, 'mobile sidebar content ends above the fixed dock');
assert.match(runtime, /new ResizeObserver\(\(\) => document\.documentElement\.style\.setProperty\('--hx-dock-space', bar\.offsetHeight \+ 'px'\)\)\.observe\(bar\)/, 'dock height tracks the rendered toolbar');
assert.match(runtime, /\.hx-panel\{[^}]*display:none;/, 'closed panel cannot widen or intercept the mobile page');
assert.match(runtime, /\.hx-panel\.open\{display:flex;/, 'open panel restores layout and interaction');
assert.match(runtime, /article\.spec pre\{white-space:pre-wrap;overflow-wrap:anywhere\}/, 'standalone doctrine and code blocks cannot widen mobile specs');
assert.match(runtime, /Math\.max\(0, Math\.min\(pos\.left, holder\.clientWidth - pinSize\)\)/, 'mobile pins are clamped inside their anchored holder');
assert.match(runtime, /if \(marker\.getBoundingClientRect\(\)\.right > document\.documentElement\.clientWidth\) marker\.dataset\.inset = 'true'/, 'Jev markers move inside the column edge when the margin cannot hold them');
assert.match(runtime, /\.hx-jev-marker\[data-inset=true\]\{left:auto;right:0\}/, 'inset Jev markers sit at the content column edge');
assert.match(runtime, /@media\(max-width:640px\)\{[^]*\.hx-jev-pop\{left:12px;right:12px;top:auto;bottom:calc\(10px \+ var\(--hx-dock-space,64px\)/, 'mobile Jev popover is a sheet above the review controls');
assert.match(runtime, /\.hx-composer textarea\{min-height:120px;font-size:16px/, 'mobile composer avoids browser input zoom');
assert.match(runtime, /\.hx-dock-open,\.hx-dock-thread\{width:44px;height:44px/, 'mobile conversation controls meet the touch target floor');
assert.match(runtime, /openPanel\(!window\.matchMedia\('\(max-width: 640px\)'\)\.matches\)/, 'mobile comment mode exposes the document before target selection');
assert.match(runtime, /class="hx-mobile-handoff" id="hx-mobile-handoff"/, 'mobile toolbar exposes handoff beside comment mode');
assert.match(runtime, /mobileHandoff\.textContent = handoffState\.finish \? 'Accept spec' : handoffState\.tbd \? 'TBD open' : drafts \? 'Hand off \(' \+ drafts \+ '\)' : 'Hand off'/, 'mobile handoff renders the current draft count or Accept spec');
assert.match(runtime, /e\.message === 'Script error\.' && !e\.filename && !e\.lineno && !e\.colno && !e\.error/, 'fully opaque browser errors do not raise a fatal review overlay');
assert.match(runtime, /overlay\('error', e\.message/, 'attributable script errors remain visible');
assert.match(runtime, /if \(state\.handoffPosting \|\| !action\.enabled\) return/, 'handoff and Accept spec latch against duplicate submission');
assert.match(runtime, /hx-service-index-link/, 'HTTP detail pages expose a visible service index link');
assert.ok(runtime.includes("new URL('/', location.href).href"), 'service index link resolves to the configured service root');
assert.match(runtime, /Back to Spec Chat index/, 'service index link has an accessible visible label');
assert.match(runtime, /automatic wake did not occur; send a new chat message to resume/, 'queued handoff gives an explicit manual-resume instruction');

console.log('runtime mobile contract tests passed');
