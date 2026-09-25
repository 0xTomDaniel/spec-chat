import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

// Every Jev text must reach 4.5:1 against the background it actually renders on
// (spec #shared-never-hide, #acceptance-inert). Colors come from the runtime CSS.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
const sharedCss = readFileSync(resolve(root, 'docs/specs/.style/spec.css'), 'utf8');
const literal = name => {
  const start = runtime.indexOf('const ' + name + ' = `');
  const end = runtime.indexOf('`;', start + name.length + 10);
  assert.ok(start >= 0 && end > start, 'runtime defines ' + name);
  return runtime.slice(runtime.indexOf('`', start) + 1, end);
};
const readingStart = runtime.indexOf("style.textContent = `.hx-reading-active");
const readingEnd = runtime.indexOf(';\n  document.head.appendChild(style);', readingStart);
assert.ok(readingStart >= 0 && readingEnd > readingStart, 'runtime defines reading view style');
// Evaluate the reading style as the runtime builds it, with and without the shared stylesheet link.
const readingCssFor = shared => Function('document', 'return ' + runtime.slice(readingStart + 'style.textContent = '.length, readingEnd))(
  { querySelector: () => (shared ? {} : null) });

function parse(css) {
  const rules = [];
  let media = null;
  const re = /(@media[^{]*)\{|([^{}]+)\{([^{}]*)\}|\}/g;
  for (const m of css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(re)) {
    if (m[1]) media = m[1].replace(/\s+/g, '');
    else if (m[2] !== undefined) {
      const decls = {};
      for (const d of m[3].split(';')) {
        const i = d.indexOf(':');
        if (i > 0) decls[d.slice(0, i).trim()] = d.slice(i + 1).replace('!important', '').trim();
      }
      for (const sel of m[2].split(/,(?![^(]*\))/)) rules.push({ media, sel: sel.trim(), decls });
    } else media = null;
  }
  return rules;
}
const DARK = '@media(prefers-color-scheme:dark)';
const docRules = parse(literal('DOC_CSS'));
const sharedRules = parse(literal('FOCUS_CSS') + literal('CSS') + readingCssFor(true));
const overlayRules = parse(literal('FOCUS_CSS') + literal('CSS') + readingCssFor(false));
function decl(rules, sel, prop, dark) {
  let value = null;
  for (const r of rules) {
    if (r.sel !== sel || !(prop in r.decls)) continue;
    if (r.media === null || (dark && r.media === DARK)) value = r.decls[prop];
  }
  return value;
}

const hex = v => { const m = String(v).match(/#([0-9a-f]{6})\b/i); return m ? [0, 2, 4].map(i => parseInt(m[1].slice(i, i + 2), 16)) : null; };
const mix = (fg, bg, a) => fg.map((c, i) => c * a + bg[i] * (1 - a));
const lum = rgb => { const c = rgb.map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; };
const ratio = (a, b) => { const [lo, hi] = [lum(a), lum(b)].sort((x, y) => x - y); return (hi + 0.05) / (lo + 0.05); };

// Page backgrounds the runtime renders on: the shared spec stylesheet (light only)
// and the runtime document CSS in light and dark.
const paper = hex(sharedCss.match(/--spec-paper:\s*(#[0-9a-f]{6})/i)[1]);
// Lightest clause text the shared stylesheet uses.
const sharedBody = hex(sharedCss.match(/--spec-muted:\s*(#[0-9a-f]{6})/i)[1]);
const pages = [
  { name: 'shared light', rules: sharedRules, dark: false, bg: paper, text: sharedBody },
  { name: 'shared, dark OS', rules: sharedRules, dark: true, bg: paper, text: sharedBody },
  { name: 'runtime light', rules: docRules.concat(overlayRules), dark: false, bg: hex(decl(docRules, ':where(body)', 'background', false)), text: hex(decl(docRules, ':where(body)', 'color', false)) },
  { name: 'runtime dark', rules: docRules.concat(overlayRules), dark: true, bg: hex(decl(docRules, ':where(body)', 'background', true)), text: hex(decl(docRules, ':where(body)', 'color', true)) },
];
const COSMETIC = '[data-hx-jev-type=cosmetic]';
const opacityOf = (rules, sel, dark) => {
  const values = [sel, 'body.hx-focus-active ' + sel].map(s => decl(rules, s, 'opacity', dark)).filter(v => v !== null);
  return values.length ? Math.min(...values.map(Number)) : 1;
};
const colorOf = (page, sels, prop) => {
  let value = null;
  for (const s of sels) value = decl(page.rules, s, prop, page.dark) ?? value;
  return value;
};

const markers = [
  ['.hx-jev-badge[data-state=label]'], ['.hx-jev-badge[data-state=unsure]'], ['.hx-jev-badge[data-state=unavailable]'],
  ['.hx-jev-corpus'], ['.hx-jev-corpus', '.hx-jev-corpus[data-state=unsure]'],
  ['.hx-jev-coverage'], ['.hx-jev-coverage', '.hx-jev-coverage[data-state=unsure]'], ['.hx-jev-coverage', '.hx-jev-coverage[data-state=unavailable]'],
  ['.hx-jev-note'],
];
const panelLabels = [
  ['.hx-jev-thread-label'], ['.hx-jev-thread-label', '.hx-jev-thread-label[data-state=unsure]'],
  ['.hx-jev-thread-label', '.hx-jev-thread-label[data-state=unavailable]'], ['.hx-pin-jev'], ['.hx-orphan-hint'],
];
const failures = [];
const check = (name, fg, bg) => {
  const r = ratio(fg, bg);
  if (r < 4.5) failures.push(name + ' ' + r.toFixed(2));
};
for (const page of pages) {
  const a = opacityOf(page.rules, COSMETIC, page.dark);
  const cosmetic = hex(colorOf(page, ['body ' + COSMETIC, COSMETIC].reverse(), 'color')) || page.text;
  // Dimmed cosmetic clause text, composited through any opacity on the clause.
  check(page.name + ': cosmetic text', mix(cosmetic, page.bg, a), page.bg);
  // Markers render in the page flow, including inside a dimmed cosmetic clause.
  for (const sels of markers) {
    const fg = hex(colorOf(page, sels, 'color'));
    const bg = hex(colorOf(page, sels, 'background')) || page.bg;
    assert.ok(fg, 'color for ' + sels.at(-1));
    check(page.name + ': ' + sels.at(-1), fg, bg);
    check(page.name + ': ' + sels.at(-1) + ' in cosmetic clause', mix(fg, page.bg, a), mix(bg, page.bg, a));
  }
  for (const sels of panelLabels) {
    const fg = hex(colorOf(page, sels, 'color'));
    const bg = hex(colorOf(page, sels, 'background'));
    assert.ok(fg && bg, 'panel label ' + sels.at(-1) + ' owns its background');
    check(page.name + ': ' + sels.at(-1), fg, bg);
  }
  // Reading view internals, where the dark rule applies only on runtime-owned pages.
  const reading = '.hx-reading-active [data-hx-audience="internals"]';
  const internals = hex(decl(page.rules, reading, 'color', page.dark));
  check(page.name + ': reading internals', internals, page.bg);
  // Chips inside a dimmed internals clause keep their own color unless the inherit rule reaches them.
  const inherit = page.rules.filter(r => r.sel.startsWith(reading + ' :is(') && r.decls.color === 'inherit');
  assert.ok(inherit.length, 'reading view defines the internals inherit rule');
  for (const sels of markers.filter(s => s[0] !== '.hx-jev-note')) {
    const cls = sels[0].match(/^\.[\w-]+/)[0];
    const reached = inherit.some(r => !new RegExp(':not\\([^)]*\\' + cls + '\\b').test(r.sel));
    const fg = reached ? internals : hex(colorOf(page, sels, 'color'));
    const bg = hex(colorOf(page, sels, 'background')) || page.bg;
    check(page.name + ': ' + sels.at(-1) + ' in reading internals', fg, bg);
  }
  // Git focus veil over unchanged blocks; chips must be lifted above it like .hx-pin and .hx-badge.
  const veilSel = 'body.hx-focus-active [data-hx-focus=unchanged]:not(:has([data-hx-focus=changed])):not([data-hx-focus=unchanged]:not(:has([data-hx-focus=changed])) *):not(tr):not(td):not(th):not(script):not(style)::after';
  const veil = Number(String(decl(page.rules, veilSel, 'background', page.dark)).match(/calc\(([\d.]+)\*/)[1]);
  assert.ok(veil > 0, 'focus veil alpha');
  for (const sels of markers.filter(s => s[0] !== '.hx-jev-note')) {
    const cls = sels[0].match(/^\.[\w-]+/)[0];
    const lift = 'body.hx-focus-active [data-hx-focus=unchanged] ' + cls;
    const lifted = decl(page.rules, lift, 'z-index', page.dark) === '700' && decl(page.rules, lift, 'position', page.dark) === 'relative' &&
      decl(page.rules, lift, 'opacity', page.dark) === '1' && decl(page.rules, lift, 'filter', page.dark) === 'none';
    const a = lifted ? 0 : veil;
    const fg = hex(colorOf(page, sels, 'color'));
    const bg = hex(colorOf(page, sels, 'background')) || page.bg;
    check(page.name + ': ' + sels.at(-1) + ' under focus veil', mix([0, 0, 0], fg, a), mix([0, 0, 0], bg, a));
  }
}
assert.deepEqual(failures, [], 'Jev text below 4.5:1');
assert.doesNotMatch(runtime, /\[data-hx-jev-type=cosmetic\]\{[^}]*opacity/, 'cosmetic dimming never fades markers');

console.log('runtime Jev contrast tests passed');
