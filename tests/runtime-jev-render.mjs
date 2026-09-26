import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
const slice = (from, to) => {
  const start = runtime.indexOf(from);
  const end = runtime.indexOf(to, start);
  assert.ok(start >= 0 && end > start, 'runtime exposes ' + from);
  return runtime.slice(start, end);
};

// Minimal DOM: enough for renderJev, its markers, and the shared popover.
class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.parent = null; this.dataset = {}; this.attrs = {};
    this.ownText = ''; this.className = ''; this.listeners = {}; this.style = {}; this.hidden = false;
  }
  appendChild(child) { if (child.parent) child.remove(); child.parent = this; this.children.push(child); return child; }
  insertBefore(child, ref) { child.parent = this; const i = this.children.indexOf(ref); this.children.splice(i < 0 ? 0 : i, 0, child); return child; }
  append(...children) { for (const c of children) this.appendChild(c); }
  replaceChildren(...children) { for (const c of [...this.children]) c.remove(); this.append(...children); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  fire(type, extra = {}) {
    const event = { type, target: this, preventDefault() {}, stopPropagation() {}, ...extra };
    for (const fn of this.listeners[type] || []) fn(event);
    return event;
  }
  remove() { if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1); this.parent = null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  // Real focus order: blur and focusout on the old element, then focus on the new one.
  focus() {
    const from = document.activeElement;
    if (from === this) return;
    from.fire('blur', { relatedTarget: this });
    for (let e = from; e; e = e.parent) e.fire('focusout', { target: from, relatedTarget: this });
    document.activeElement = this;
    this.fire('focus');
  }
  set textContent(v) { this.children = []; this.ownText = String(v); }
  get textContent() { return this.ownText + this.children.map(c => c.textContent).join(''); }
  get parentNode() { return this.parent; }
  get firstChild() { return this.children[0] || null; }
  get lastElementChild() { return this.children.at(-1) || null; }
  get cells() { return this.children.filter(c => c.tagName === 'TD' || c.tagName === 'TH'); }
  get isConnected() { let e = this; while (e.parent) e = e.parent; return e === body; }
  get offsetParent() { return null; }
  contains(other) { for (let e = other; e; e = e.parent) if (e === this) return true; return false; }
  closest(sel) { for (let e = this; e; e = e.parent) if (e.matches(sel)) return e; return null; }
  *walk() { for (const c of this.children) { yield c; yield* c.walk(); } }
  matches(sel) {
    return sel.split(',').some(s => {
      s = s.trim();
      if (s === ':hover') return false;
      if (s.startsWith('.')) return this.className.split(/\s+/).includes(s.slice(1));
      const attr = s.match(/^\[data-([a-z-]+)\]$/);
      if (attr) { const key = attr[1].replace(/-([a-z])/g, (_, c) => c.toUpperCase()); return key in this.dataset; }
      if (s === 'article.spec') return this.tagName === 'ARTICLE' && this.className === 'spec';
      return this.tagName === s.toUpperCase();
    });
  }
  querySelectorAll(sel) { return [...this.walk()].filter(e => e.matches(sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
const body = new El('body');
body.classList = { contains: () => false };
const article = body.appendChild(new El('article'));
article.className = 'spec';
const section = anchor => {
  const s = article.appendChild(new El('section'));
  s.dataset.anchor = anchor;
  s.appendChild(new El('h3')).textContent = 'Heading ' + anchor;
  return s;
};
const anchors = ['rule', 'typo', 'crowded', 'overstep', 'quiet', 'type-unsure', 'story-gap', 'criterion-gap',
  'story-unsure', 'criterion-unsure', 'story-down', 'criterion-down', 'internals', 'audience-unsure'];
for (const a of anchors) section(a);
const row = article.appendChild(new El('table')).appendChild(new El('tr'));
row.dataset.anchor = 'row';
row.appendChild(new El('td')).textContent = 'first cell';
row.appendChild(new El('td')).textContent = 'last cell';
const textBefore = anchors.concat('row').map(a => article.querySelectorAll('[data-anchor]').find(e => e.dataset.anchor === a).textContent);
const docListeners = {};
const document = { body, activeElement: body, querySelectorAll: s => body.querySelectorAll(s), querySelector: s => body.querySelector(s),
  createElement: t => new El(t), addEventListener: (type, fn) => (docListeners[type] ||= []).push(fn) };
const fireDoc = (type, extra) => { for (const fn of docListeners[type] || []) fn({ type, ...extra }); };
const window = { addEventListener() {}, matchMedia: () => ({ matches: true }) };

const code = [
  slice('const JEV_TYPE_LABELS = {', '\n\nfunction jevParams('),
  slice('function findAnchor(', '\n\nfunction clearJev('),
  slice('function coveragePair(', '\n\n// Name the folder'),
  slice('function jevDisplayLabel(', '\n\nfunction goToJevTarget('),
  slice('/* ---------------- Jev markers', '\n\n/* ---------------- UI'),
].join('\n\n');
const item = (kind, id, stateName, label = null, target = null) => ({ kind, id, state: stateName, label, target, record: 'r' });
const typeItems = [
  item('type', 'rule', 'label', 'behavioral'),
  item('type', 'typo', 'label', 'cosmetic'),
  item('type', 'crowded', 'label', 'scope'),
  item('type', 'overstep', 'label', 'clarification'),
  item('type', 'type-unsure', 'unsure'),
  item('type', 'row', 'label', 'behavioral'),
  item('type', 'quiet', 'none'),
];
const suggestionItems = [
  ...typeItems,
  item('corpus', 'rule', 'label', 'contradicts', 'non-goal-text'),
  item('corpus', 'crowded', 'unsure'),
  item('corpus', 'overstep', 'label', 'oversteps', 'other#scope'),
  item('coverage', 'crowded::criterion-gap', 'label', 'unrelated'),
  item('coverage', 'story-gap::criterion-gap', 'label', 'unrelated'),
  item('coverage', 'story-unsure::criterion-unsure', 'unsure'),
  item('coverage', 'story-down::criterion-down', 'unavailable'),
];
// The real runtime state shape, as requestJev leaves it after a successful fetch.
const state = { readingView: false, jev: { request: 1, status: 'on', base: 'b', items: suggestionItems } };
const location = { search: '?focus=changes' };
const composed = [];
const openComposer = (...args) => composed.push(args);
const { renderJev, jevNoteSources } = Function('document', 'window', 'state', 'location', 'URLSearchParams', 'EMBED_REVIEW_DIR', 'NodeFilter',
  'requestAnimationFrame', 'cancelAnimationFrame', 'getComputedStyle', 'innerWidth', 'innerHeight', 'openComposer',
  code + '; return { renderJev, jevNoteSources };')(document, window, state, location, URLSearchParams, null, {}, () => 0, () => {},
  () => ({}), 1440, 900, openComposer);
renderJev();

const holder = anchor => article.querySelectorAll('[data-anchor]').find(e => e.dataset.anchor === anchor);
const markersOf = anchor => holder(anchor).querySelectorAll('.hx-jev-marker');
const marker = anchor => { const found = markersOf(anchor); assert.equal(found.length, 1, 'one marker on ' + anchor); return found[0]; };
const pop = () => body.querySelector('.hx-jev-pop');
const popNotes = () => pop().querySelectorAll('.hx-jev-pop-note').map(n => [n.dataset.group, n.querySelector('.hx-jev-pop-text').textContent]);

// #acceptance-markers: one marker per noted location however many notes, none elsewhere.
for (const a of ['rule', 'typo', 'crowded', 'overstep', 'type-unsure', 'story-gap', 'criterion-gap', 'story-unsure',
  'criterion-unsure', 'story-down', 'criterion-down', 'row']) marker(a);
assert.equal(markersOf('quiet').length, 0, 'an explicit none answer adds no marker');
assert.equal(markersOf('internals').length, 0, 'audience items stay out of Git focus');
// Only Contradicts and Oversteps color a marker.
assert.deepEqual(body.querySelectorAll('.hx-jev-marker').filter(m => m.dataset.attention === 'true')
  .map(m => m.closest('[data-anchor]').dataset.anchor), ['rule', 'overstep']);
// No tint, badge, or inline note; the only in-text display is the cosmetic dim; text is unchanged.
assert.deepEqual(article.querySelectorAll('[data-hx-jev-type]').map(e => [e.dataset.anchor, e.dataset.hxJevType]), [['typo', 'cosmetic']]);
assert.deepEqual(anchors.concat('row').map(a => holder(a).textContent), textBefore, 'markers add no text to the spec');
for (const a of anchors) assert.deepEqual(holder(a).children.map(c => c.tagName), markersOf(a).length ? ['H3', 'BUTTON'] : ['H3']);
assert.equal(row.children.length, 2, 'a row marker never adds a cell');
assert.equal(row.children[1].querySelectorAll('.hx-jev-marker').length, 1, 'a row marker sits in its last cell');
assert.equal(body.querySelectorAll('.hx-jev-note').length, 0);

// Hover opens; the popover lists every note of that location in spec order.
marker('crowded').fire('mouseenter');
assert.equal(pop().hidden, false);
assert.equal(marker('crowded').getAttribute('aria-expanded'), 'true');
assert.deepEqual(popNotes(), [['coverage', 'No criterion covers this'], ['type', 'Scope'], ['neutral', 'conflict?']]);
// Moving away closes it.
marker('crowded').fire('mouseleave');
await new Promise(resolve => setTimeout(resolve, 200));
assert.equal(pop().hidden, true);
assert.equal(marker('crowded').getAttribute('aria-expanded'), 'false');
// Keyboard focus opens and Escape closes.
marker('rule').fire('focus');
assert.deepEqual(popNotes(), [['conflict', 'Contradicts #non-goal-text'], ['type', 'Behavior']]);
assert.equal(pop().querySelector('a').href, '#non-goal-text');
fireDoc('keydown', { key: 'Escape' });
assert.equal(pop().hidden, true);
// #markers-open by keyboard: Tab from the marker enters the popover, Shift+Tab returns, Tab past
// its last control leaves from the marker, and Escape inside closes it and it stays closed.
const tab = (shiftKey = false) => {
  let prevented = false;
  fireDoc('keydown', { key: 'Tab', shiftKey, preventDefault() { prevented = true; } });
  return prevented;
};
marker('rule').focus();
assert.equal(pop().hidden, false);
const ruleLink = pop().querySelector('a');
assert.equal(tab(), true, 'Tab from the marker is taken into the popover');
assert.equal(document.activeElement, ruleLink);
assert.equal(pop().hidden, false, 'focus inside keeps the popover');
assert.equal(tab(true), true);
assert.equal(document.activeElement, marker('rule'), 'Shift+Tab from the first control returns to the marker');
assert.deepEqual(pop().querySelectorAll('a,button').map(e => e.tagName), ['A', 'BUTTON', 'BUTTON'], 'link and note buttons are reachable');
pop().querySelectorAll('button').at(-1).focus();
assert.equal(pop().hidden, false, 'the browser moves within the popover');
assert.equal(tab(), false, 'Tab past the last control is left to the browser');
assert.equal(document.activeElement, marker('rule'), 'and continues from the marker');
assert.equal(pop().hidden, false);
marker('typo').focus();
assert.deepEqual(popNotes(), [['type', 'Cosmetic']], 'the next marker opens its own popover');
marker('rule').focus();
tab();
fireDoc('keydown', { key: 'Escape' });
assert.equal(document.activeElement, marker('rule'), 'Escape returns focus to the marker');
assert.equal(pop().hidden, true, 'Escape inside the popover closes it and focus return does not reopen it');
assert.equal(marker('rule').getAttribute('aria-expanded'), 'false');
document.activeElement.fire('blur', { relatedTarget: body });
document.activeElement = body;
// Tap opens; a tap inside the popover keeps it; a tap elsewhere closes it.
marker('typo').fire('click');
assert.deepEqual(popNotes(), [['type', 'Cosmetic']]);
fireDoc('pointerdown', { target: pop().querySelector('.hx-jev-pop-text') });
assert.equal(pop().hidden, false);
fireDoc('pointerdown', { target: holder('rule') });
assert.equal(pop().hidden, true);
// Focus leaving to the popover keeps it; leaving elsewhere closes it.
marker('overstep').fire('focus');
marker('overstep').fire('blur', { relatedTarget: pop().querySelector('.hx-jev-pop-text') });
assert.equal(pop().hidden, false);
marker('overstep').fire('blur', { relatedTarget: holder('rule') });
assert.equal(pop().hidden, true);

// #acceptance-neutral and #neutral-questions: one muted word per question, its sentence the accessible name and shown on hover or focus.
const neutralNote = anchor => {
  marker(anchor).fire('focus');
  const notes = pop().querySelectorAll('.hx-jev-pop-note');
  assert.equal(notes.length, 1, anchor);
  const text = notes[0].querySelector('.hx-jev-pop-text');
  const sentence = notes[0].querySelector('.hx-jev-pop-sentence');
  assert.equal(text.getAttribute('tabindex'), '0', anchor + ' is focusable');
  assert.equal(sentence.getAttribute('aria-hidden'), 'true');
  assert.equal(text.getAttribute('aria-label'), sentence.textContent, anchor + ' sentence is its name');
  return [notes[0].dataset.group, text.textContent, sentence.textContent];
};
assert.deepEqual(neutralNote('type-unsure'), ['neutral', 'scope?', 'Jev is unsure whether this is scope or behavior']);
assert.deepEqual(neutralNote('story-unsure'), ['neutral', 'criterion?', 'Jev is unsure which criterion verifies this story']);
assert.deepEqual(neutralNote('criterion-unsure'), ['neutral', 'story?', 'Jev is unsure which story this criterion verifies']);
assert.deepEqual(neutralNote('story-down'), ['neutral', 'Jev unavailable', 'Jev could not check which criterion verifies this story']);
assert.deepEqual(neutralNote('criterion-down'), ['neutral', 'Jev unavailable', 'Jev could not check which story this criterion verifies']);
assert.match(runtime, /\.hx-jev-pop-sentence\{display:none;/, 'the sentence is hidden until hover or focus');
assert.match(runtime, /\.hx-jev-pop-note:hover \.hx-jev-pop-sentence,\.hx-jev-pop-note:focus-within \.hx-jev-pop-sentence\{display:block\}/);
// Evidence and other notes carry no sentence.
marker('rule').fire('focus');
assert.equal(pop().querySelectorAll('.hx-jev-pop-sentence').length, 0);
marker('story-gap').fire('focus');
assert.deepEqual(popNotes(), [['coverage', 'No criterion covers this']]);
marker('criterion-gap').fire('focus');
assert.deepEqual(popNotes(), [['coverage', 'No story backs this']]);

// #note-actions: one button per actionable note, none for Cosmetic, Clarification, unsure, or Jev unavailable.
const noteButtons = anchor => {
  marker(anchor).fire('focus');
  return pop().querySelectorAll('.hx-jev-pop-note').map(n => [n.querySelector('.hx-jev-pop-text').textContent,
    (n.querySelector('.hx-jev-pop-actions') || { children: [] }).children.map(b => b.textContent)]);
};
assert.deepEqual(noteButtons('rule'), [['Contradicts #non-goal-text', ['Ask agent to reconcile']], ['Behavior', ['Comment on this change']]]);
assert.deepEqual(noteButtons('overstep'), [['Oversteps other#scope', ['Ask agent to reconcile']], ['Clarification', []]]);
assert.deepEqual(noteButtons('crowded'), [['No criterion covers this', ['Ask for a criterion']], ['Scope', ['Comment on this change']], ['conflict?', []]]);
assert.deepEqual(noteButtons('criterion-gap'), [['No story backs this', ['Ask for a story']]]);
assert.deepEqual(noteButtons('row'), [['Behavior', ['Comment on this change']]]);
for (const a of ['typo', 'type-unsure', 'story-unsure', 'story-down', 'criterion-down']) {
  assert.ok(noteButtons(a).every(([, buttons]) => buttons.length === 0), a + ' shows no button');
}
// #acceptance-note-draft: a click opens the existing composer at the note's element with the table's fixed text,
// closes the popover, and writes nothing.
const clickNote = (anchor, label) => {
  marker(anchor).fire('focus');
  pop().querySelectorAll('.hx-btn').find(b => b.textContent === label).fire('click');
  assert.equal(pop().hidden, true, label + ' closes the popover');
  return composed.pop();
};
assert.deepEqual(clickNote('rule', 'Ask agent to reconcile'), ['rule', null, null, 'Reconcile this clause with #non-goal-text.']);
assert.deepEqual(clickNote('overstep', 'Ask agent to reconcile'), ['overstep', null, null, 'Reconcile this clause with other#scope.']);
assert.deepEqual(clickNote('story-gap', 'Ask for a criterion'), ['story-gap', null, null, 'Add an acceptance criterion that verifies this story.']);
assert.deepEqual(clickNote('criterion-gap', 'Ask for a story'), ['criterion-gap', null, null, 'Name or add the user story this criterion verifies.']);
assert.deepEqual(clickNote('rule', 'Comment on this change'), ['rule', null, null, 'About this change: ']);
assert.deepEqual(clickNote('crowded', 'Comment on this change'), ['crowded', null, null, 'About this change: ']);
assert.equal(composed.length, 0);

// A later note source (criterion evidence) joins first in the same marker and can color it.
jevNoteSources.push(() => [{ anchor: 'typo', group: 'evidence', state: 'label', text: 'QA stale', attention: true,
  actions: [{ label: 'Ask agent', run: () => {} }] }]);
renderJev();
assert.equal(marker('typo').dataset.attention, 'true');
marker('typo').fire('focus');
assert.deepEqual(popNotes(), [['evidence', 'QA stale'], ['type', 'Cosmetic']]);
assert.deepEqual(pop().querySelector('.hx-jev-pop-actions').children.map(b => [b.tagName, b.textContent]), [['BUTTON', 'Ask agent']]);
jevNoteSources.pop();

// Notes for different questions on one location each show once, even with the same words.
state.jev.items = [item('type', 'typo', 'unavailable'), item('corpus', 'typo', 'unavailable'), item('type', 'typo', 'unavailable')];
renderJev();
marker('typo').fire('focus');
assert.deepEqual(pop().querySelectorAll('.hx-jev-pop-sentence').map(n => n.textContent),
  ['Jev could not check whether this conflicts with another clause', 'Jev could not check whether this is scope or behavior']);
assert.deepEqual(popNotes(), [['neutral', 'Jev unavailable'], ['neutral', 'Jev unavailable']]);
state.jev.items = suggestionItems;
renderJev();

// Without Git focus, change types and draft checks do not show; coverage does.
location.search = '';
renderJev();
assert.equal(pop().hidden, true, 'rerender closes the popover');
assert.equal(markersOf('rule').length, 0);
assert.equal(article.querySelectorAll('[data-hx-jev-type]').length, 0);
marker('story-gap');

// Reading view: internals dim, audience unsure gets a neutral marker.
state.readingView = true;
state.jev.items = [item('audience', 'internals', 'label', 'internals'), item('audience', 'audience-unsure', 'unsure')];
renderJev();
assert.equal(holder('internals').dataset.hxAudience, 'internals');
assert.equal(markersOf('internals').length, 0);
assert.deepEqual(neutralNote('audience-unsure'), ['neutral', 'reader?', 'Jev is unsure whether this is for readers or internals']);
assert.equal(pop().querySelectorAll('.hx-jev-pop-actions').length, 0, 'reading view notes show no button');
state.readingView = false;

// Jev off: one page-level note, no markers.
state.jev.status = 'off';
state.jev.items = [];
renderJev();
assert.equal(body.querySelectorAll('.hx-jev-marker').length, 0, 'Jev off clears markers');
assert.deepEqual(body.querySelectorAll('.hx-jev-note').map(n => n.textContent), ['Jev off']);

// After Move comment here resolves the orphaned thread, its card offers no second move,
// even while the old orphan suggestion is still in state until the next Jev fetch.
const orphanHintElement = Function('document', 'state', 'goToJevTarget', 'moveOrphan',
  slice('function orphanHintElement(', '\n\nasync function moveOrphan(') + '; return orphanHintElement;',
)(document, { movingOrphans: new Set() }, () => {}, () => {});
const suggestion = { kind: 'orphan', id: 'thread-1', state: 'label', label: 'Moved section', target: 'moved-section', record: 'r9' };
const acts = el => el.querySelectorAll('button').map(b => b.dataset.act);
assert.deepEqual(acts(orphanHintElement({ id: 'thread-1', status: 'pending' }, suggestion)), ['orphan-go', 'orphan-move']);
assert.deepEqual(acts(orphanHintElement({ id: 'thread-1', status: 'resolved' }, suggestion)), ['orphan-go']);
assert.equal(orphanHintElement({ id: 'thread-1', status: 'pending' }, null), null);

console.log('runtime Jev render tests passed');
