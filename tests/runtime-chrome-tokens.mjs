import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

// spec-page-design #acceptance-token-block, #token-block-rule, #type-system-rule, #button-system-rule:
// the chrome CSS opens with the index tokens plus the additions, and every chrome rule uses only them.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
const serve = readFileSync(resolve(root, 'skill/review-spec/assets/review-serve.py'), 'utf8');

const start = runtime.indexOf('const CSS = `');
assert.ok(start >= 0, 'runtime defines the chrome CSS const');
const css = runtime.slice(start + 'const CSS = `'.length, runtime.indexOf('`;', start));
const DARK = /@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*:root\s*\{([^}]*)\}/;
const tokens = body => Object.fromEntries([...body.matchAll(/(--[\w-]+)\s*:\s*([^;]+)/g)]
  .map(([, name, value]) => [name, value.trim().replace(/\s*,\s*/g, ',').replace(/'/g, '"')]));

// #token-block-rule: one :root block headed /* ui tokens */ opens the const.
const light = css.match(/^\s*\/\* ui tokens \*\/\s*:root\{([^}]*)\}/);
assert.ok(light, 'chrome CSS opens with the /* ui tokens */ :root block');
const dark = css.match(DARK);
assert.ok(dark, 'chrome CSS has one dark token override');
assert.equal(css.match(/:root\s*\{/g).length, 2, 'only the token block and its dark override declare :root');

// #acceptance-token-block: exactly the index tokens plus the additions, the shared ones with the index values.
const indexStyle = serve.slice(serve.indexOf('/* ui tokens */'));
const indexLight = tokens(indexStyle.match(/^\/\* ui tokens \*\/\s*:root\s*\{([^}]*)\}/)[1]);
const indexDark = tokens(indexStyle.match(DARK)[1]);
const ADDITIONS = ['--ui-ack', '--ui-ack-soft', '--ui-error', '--ui-marker', '--ui-marker-important',
  '--ui-marker-pass', '--ui-pin-ring', '--ui-shadow'];
const chromeLight = tokens(light[1]);
const chromeDark = tokens(dark[1]);
assert.deepEqual(Object.keys(chromeLight).sort(), [...Object.keys(indexLight), ...ADDITIONS].sort(),
  'chrome tokens are the index tokens plus the additions');
for (const [name, value] of Object.entries(indexLight)) assert.equal(chromeLight[name], value, 'light ' + name);
for (const [name, value] of Object.entries(indexDark)) assert.equal(chromeDark[name], value, 'dark ' + name);
for (const name of Object.keys(chromeDark)) assert.ok(name in chromeLight, 'dark overrides a declared token: ' + name);

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
const sizes = new Set(['var(--ui-text-xs)', 'var(--ui-text-sm)', 'var(--ui-text-md)', 'var(--ui-text-lg)', 'inherit']);
const families = new Set(['var(--ui-font)', 'var(--ui-mono)', 'inherit']);
const NAMED = /\b(white|black|red|green|blue|gray|grey|orange|yellow|purple|silver|navy)\b/i;

// The open panel reserves its own width on the body: geometry, not spacing.
const panelWidth = rules.find(rule => rule.sel === '.hx-panel' && !rule.media).decls.find(([p]) => p === 'width')[1];
// Known gap, not a pattern: marks paint on the document, which stays light under a dark OS with the shared
// stylesheet, so the glyph white and the light red Warning dot (jev-suggestions #acceptance-warning-color)
// need one value in both themes, and the token set has none. Nothing else may join this list.
const MARK_LITERALS = [['.hx-jev-marker', 'color', '#ffffff'], ['.hx-jev-marker[data-warning=true]', 'background', '#e5534b']];
const markLiteral = (rule, prop, value) => !rule.media && MARK_LITERALS.some(([s, p, v]) => s === rule.sel && p === prop && v === value);
const problems = [];
const bad = (rule, prop, value, why) => problems.push(`${rule.sel} { ${prop}: ${value} } ${why}`);
for (const rule of rules) {
  for (const [prop, value] of rule.decls) {
    // #color-map: every color maps to a token; token-derived mixes and keywords stay allowed.
    if ((/#[0-9a-f]{3,8}\b|\b(rgba?|hsla?)\(/i.test(value) || NAMED.test(value)) && !markLiteral(rule, prop, value)) bad(rule, prop, value, 'literal color');
    // #type-system-rule: index type scale, weights 400 and 600 only.
    if (prop === 'font-size' && !sizes.has(value)) bad(rule, prop, value, 'off-scale font size');
    if (prop === 'font-weight' && !['400', '600'].includes(value)) bad(rule, prop, value, 'weight outside 400/600');
    if (prop === 'font-family' && !families.has(value)) bad(rule, prop, value, 'font family outside the tokens');
    if (prop === 'font' && value !== 'inherit') {
      const m = value.match(/^(?:(\d+)\s+)?(var\(--ui-text-\w+\))(?:\/[\d.]+)?\s+(var\(--ui-(?:font|mono)\))$/);
      if (!m || (m[1] && !['400', '600'].includes(m[1]))) bad(rule, prop, value, 'font shorthand off the tokens');
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
console.log('ok runtime chrome tokens');
