import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

// spec-page-design #acceptance-token-block, #token-block-rule, #design-set, #type-system-rule, #button-system-rule:
// the chrome CSS opens with the pinned design set, then the additions alone, and every chrome rule uses only them.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
const serve = readFileSync(resolve(root, 'skill/review-spec/assets/review-serve.py'), 'utf8');
const pinned = readFileSync(resolve(root, 'design-set.toml'), 'utf8').match(/^commit = "([0-9a-f]{40})"$/m)[1];

const start = runtime.indexOf('const CSS = `');
assert.ok(start >= 0, 'runtime defines the chrome CSS const');
const whole = runtime.slice(start + 'const CSS = `'.length, runtime.indexOf('`;', start));
const DARK = /@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*:root\s*\{([^}]*)\}/;
const tokens = body => Object.fromEntries([...body.matchAll(/(--[\w-]+)\s*:\s*([^;]+)/g)]
  .map(([, name, value]) => [name, value.trim().replace(/\s*,\s*/g, ',').replace(/'/g, '"')]));

// #token-block-rule: the set's stylesheet at the pinned commit opens the const (tests/design-set.py proves its bytes).
const set = whole.match(/^\s*\/\* design-set ([0-9a-f]{40}) begin\b[^\n]*\*\/\n([\s\S]*?)\/\* design-set end \*\/\n/);
assert.ok(set, 'chrome CSS opens with the design set block');
assert.equal(set[1], pinned, 'the design set block is the pinned commit');
const setCss = set[2].replace(/\/\*[\s\S]*?\*\//g, '');
const setLight = tokens(setCss.match(/^\s*:root\s*\{([^}]*)\}/)[1]);
const setDark = tokens(setCss.match(DARK)[1]);
const css = whole.slice(set[0].length);

// Then one :root block headed /* ui tokens */ holding the additions alone, and their one dark override.
const light = css.match(/^\/\* ui tokens \*\/\s*:root\{([^}]*)\}/);
assert.ok(light, 'the /* ui tokens */ :root block follows the design set');
const dark = css.match(DARK);
assert.ok(dark, 'the additions have one dark override');
assert.equal(css.match(/:root\s*\{/g).length, 2, 'only the additions and their dark override declare :root after the set');
// #token-additions: Spec Chat's own, the status names aliases over the set's tokens.
const ADDITIONS = {
  '--ui-draft': 'var(--ui-attention)', '--ui-draft-soft': 'var(--ui-attention-soft)',
  '--ui-ack': 'var(--ui-action)', '--ui-ack-soft': 'var(--ui-action-soft)',
  '--ui-resolved': 'var(--ui-pass)', '--ui-resolved-soft': 'var(--ui-pass-soft)', '--ui-error': 'var(--ui-fail)',
  '--ui-marker': '#767b85', '--ui-marker-important': '#d1242f', '--ui-marker-pass': '#1a7f37', '--ui-on-marker': '#ffffff',
  '--ui-marker-warning': '#e5534b', '--ui-pin-ring': 'rgba(176,84,14,0.25)', '--ui-shadow': 'rgba(51,51,51,0.14)',
};
const chromeLight = tokens(light[1]);
const chromeDark = tokens(dark[1]);
assert.deepEqual(chromeLight, ADDITIONS, 'the ui tokens block holds exactly the additions');
for (const name of Object.keys(chromeLight)) assert.ok(!(name in setLight), 'an addition never redefines a set token: ' + name);
for (const value of Object.values(chromeLight))
  for (const [, name] of value.matchAll(/var\((--[\w-]+)\)/g)) assert.ok(name in setLight, 'an alias names a set token: ' + name);
assert.deepEqual(Object.keys(chromeDark).sort(), ['--ui-pin-ring', '--ui-shadow'], 'dark overrides only the additions of their own');
// #token-on-marker, #token-marker-warning: marks keep their fill and glyph in both themes.
for (const name of ['--ui-on-marker', '--ui-marker-warning']) assert.ok(!(name in chromeDark), 'dark leaves ' + name);
// #token-dark-mode: the set brings its dark values; the chrome declares none of its own over them.
assert.ok(Object.keys(setDark).length > 10, 'the set carries its dark values');

// lane-hosting #index-tokens: the index serves the runtime's own block, holding no token value of its own.
assert.match(serve, /def _design_set\(\):/, 'the index takes the design set block from the runtime');
assert.doesNotMatch(serve, /--ui-[\w-]+\s*:/, 'the review server declares no token');

// Every chrome rule, outside the two token blocks, as { media, sel, decls }.
function parse(text) {
  const rules = [];
  let media = null;
  const re = /(@media[^{]*|@keyframes[^{]*)\{|([^{}]+)\{([^{}]*)\}|\}/g;
  for (const m of text.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(re)) {
    if (m[1]) media = m[1].replace(/\s+/g, '');
    else if (m[2] !== undefined) {
      if (m[2].trim() === ':root' || (media || '').startsWith('@keyframes')) continue;
      const decls = [];
      for (const d of m[3].split(/;(?![^(]*\))/)) {
        const i = d.indexOf(':');
        if (i > 0) decls.push([d.slice(0, i).trim(), d.slice(i + 1).replace('!important', '').trim()]);
      }
      rules.push({ media, sel: m[2].trim(), decls });
    } else media = null;
  }
  return rules;
}
const rules = parse(css);
assert.ok(rules.length > 100, 'parsed the chrome rules');
const strip = value => value.replace(/var\([^()]*\)|env\([^()]*\)/g, ' ');
const sizes = new Set([...Object.keys(setLight).filter(name => name.startsWith('--ui-text-')).map(name => `var(${name})`), 'inherit']);
const weights = new Set(Object.keys(setLight).filter(name => name.startsWith('--ui-weight-')).map(name => `var(${name})`));
assert.equal(weights.size, 3, 'the set has three weights');
const families = new Set(['var(--ui-font)', 'var(--ui-mono)', 'inherit']);
const NAMED = /\b(white|black|red|green|blue|gray|grey|orange|yellow|purple|silver|navy)\b/i;

// The open panel reserves its own width on the body: geometry, not spacing.
const panelWidth = rules.find(rule => rule.sel === '.hx-panel' && !rule.media).decls.find(([p]) => p === 'width')[1];
const problems = [];
const bad = (rule, prop, value, why) => problems.push(`${rule.sel} { ${prop}: ${value} } ${why}`);
for (const rule of rules) {
  for (const [prop, value] of rule.decls) {
    // #color-map: every color maps to a token; token-derived mixes and keywords stay allowed.
    if ((/#[0-9a-f]{3,8}\b|\b(rgba?|hsla?)\(/i.test(value) || NAMED.test(value))) bad(rule, prop, value, 'literal color');
    // #type-system-rule: the set's type steps and its three weights only.
    if (prop === 'font-size' && !sizes.has(value)) bad(rule, prop, value, 'off-scale font size');
    if (prop === 'font-weight' && !weights.has(value)) bad(rule, prop, value, 'weight outside the set');
    if (prop === 'font-family' && !families.has(value)) bad(rule, prop, value, 'font family outside the tokens');
    if (prop === 'font' && value !== 'inherit') {
      const m = value.match(/^(?:(var\(--ui-weight-\w+\))\s+)?(var\(--ui-text-\w+\))(?:\/[\d.]+)?\s+(var\(--ui-(?:font|mono)\))$/);
      if (!m || (m[1] && !weights.has(m[1])) || !sizes.has(m[2])) bad(rule, prop, value, 'font shorthand off the tokens');
    }
    // #spacing-rule: spacing uses only the index spacing scale.
    if (/^(padding|margin)(-\w+)?$|^(row-|column-)?gap$/.test(prop) && !(prop === 'padding-right' && value === panelWidth) && /(^|[^\w.-])-?\d*\.?\d+(px|em|rem|lh)\b/.test(strip(value).replace(/\b0px\b/g, '0')))
      bad(rule, prop, value, 'literal spacing');
    // Radii: tokens only; the pin keeps its teardrop, round dots stay 50%.
    if (/^border(-[\w]+)?-radius$/.test(prop) && !/^(0|50%|inherit|var\(--ui-radius(-sm|-pill)?\))$/.test(value)
      && !(rule.sel === '.hx-pin' && value === '50% 50% 50% 4px'))
      bad(rule, prop, value, 'literal radius');
  }
}
assert.deepEqual(problems, [], 'chrome rules use only tokens');

// #button-system-rule: every button shares the size; no rule shrinks one below it.
const BUTTONS = ['.hx-btn', '.hx-toolbar button', '.hx-range-change', '.hx-range-form button', '.hx-banner button',
  '.hx-disclosure', '.hx-rule-card-reject', '.hx-jev-pop-more'];
const SQUARE = ['.hx-panel-toggle', '.hx-panel-gear', '.hx-dock-open', '.hx-dock-thread', '.hx-disclosure'];
const desktop = rules.filter(rule => !rule.media);
const declared = (sel, props) => desktop.filter(rule => rule.sel.split(/,(?![^(]*\))/).map(s => s.trim()).includes(sel))
  .flatMap(rule => rule.decls).filter(([p]) => props.includes(p)).map(([, v]) => v);
for (const sel of BUTTONS)
  assert.ok(declared(sel, ['min-height', 'height']).includes('var(--ui-space-6)'), sel + ' has the 32px min-height');
for (const sel of SQUARE) {
  assert.ok(declared(sel, ['width', 'min-width']).includes('var(--ui-space-6)'), sel + ' is square: width');
  assert.ok(declared(sel, ['height', 'min-height']).includes('var(--ui-space-6)'), sel + ' is square: height');
}
for (const rule of desktop) {
  if (!/\.hx-btn$|\.hx-btn\.pri$/.test(rule.sel) || rule.sel === '.hx-btn' || rule.sel === '.hx-btn.pri') continue;
  for (const [prop, value] of rule.decls)
    if (/^(padding|font|font-size|min-height|height|border-radius)$/.test(prop))
      bad(rule, prop, value, 'resizes a shared button');
}
assert.deepEqual(problems, [], 'no context resizes a button');

// #acceptance-button-system-phone, #acceptance-no-content-change-phone: the phone block keeps every button at its
// 44px mobile size, with color, font, and radius held to the tokens by the checks above, and restyles only chrome.
// The phone block sits at the set's phone width, the one media queries cannot read as a token.
const phone = rules.filter(rule => rule.media === `@media(max-width:${setLight['--ui-phone-width']})`);
assert.ok(phone.length > 10, 'parsed the phone block');
const parts = rule => rule.sel.split(/,(?![^(]*\))/).map(s => s.trim());
for (const rule of phone) {
  for (const sel of parts(rule)) assert.match(sel, /\.hx-|\bhx-/, 'phone rule restyles only chrome: ' + sel);
  if (!parts(rule).some(sel => BUTTONS.includes(sel) || SQUARE.includes(sel))) continue;
  for (const [prop, value] of rule.decls)
    if (/^(min-)?(height|width)$/.test(prop)) assert.equal(value, '44px', rule.sel + ' ' + prop + ' is the mobile size');
}

// #button-system-rule, #acceptance-focus-set: every chrome focus ring is the set's ring, and one rule gives it to every
// control in the toolbar and the review panel, Accept spec among them; lane-hosting #acceptance-index-focus: the index
// shows the same ring.
const RING = { outline: 'var(--ui-focus-width) solid var(--ui-focus)', 'outline-offset': 'var(--ui-focus-offset)' };
const rings = rules.filter(rule => rule.decls.some(([p, v]) => p === 'outline' && v.includes('var(--ui-focus)')));
assert.ok(rings.length > 0, 'the chrome has a focus ring');
for (const rule of rings)
  assert.deepEqual(Object.fromEntries(rule.decls.filter(([p]) => p.startsWith('outline'))), RING, 'the set ring: ' + rule.sel);
for (const rule of rules)
  for (const [prop, value] of rule.decls)
    if (/^outline(-style|-width)?$/.test(prop) && /^(none|0)$/.test(value)) bad(rule, prop, value, 'removes the focus ring');
assert.deepEqual(problems, [], 'no chrome rule removes the focus ring');
const ringed = rings.flatMap(parts);
for (const holder of ['.hx-toolbar', '.hx-panel', '.hx-thread-dock', '.hx-jev-pop', '.hx-range-bar', '.hx-layer'])
  assert.ok(ringed.some(sel => new RegExp(`:is\\([^)]*\\${holder}\\b[^)]*\\) :focus-visible$`).test(sel)),
    'every control in ' + holder + ' shows the set ring');
for (const control of ['.hx-pin', '.hx-jev-marker', '.hx-service-index-link'])
  assert.ok(ringed.some(sel => new RegExp(`:is\\([^)]*\\${control}\\b[^)]*\\):focus-visible$`).test(sel)), control + ' shows the set ring');
assert.match(serve, /a:focus-visible, summary:focus-visible \{ outline: var\(--ui-focus-width\) solid var\(--ui-focus\); outline-offset: var\(--ui-focus-offset\);/,
  'the index shows the same ring');
console.log('ok runtime chrome tokens');
