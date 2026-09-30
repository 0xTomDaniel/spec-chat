import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
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

// Minimal DOM, as runtime-jev-render.mjs: enough for renderJev, its markers, the popover, and page notes.
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
for (const anchor of ['title', 'acceptance', 'story']) {
  const s = article.appendChild(new El('section'));
  s.dataset.anchor = anchor;
  s.appendChild(new El('h2')).textContent = 'Heading ' + anchor;
}
const document = { body, activeElement: body, querySelectorAll: s => body.querySelectorAll(s), querySelector: s => body.querySelector(s),
  createElement: t => new El(t), addEventListener() {} };
const window = { addEventListener() {}, matchMedia: () => ({ matches: true }) };
const posted = [];
const fetch = async (url, init) => { posted.push([url, JSON.parse(init.body)]); return { ok: true }; };
const code = [
  slice('function findAnchor(', '\n\nfunction clearJev('),
  slice('function coveragePair(', '\n\n// Name the folder'),
  slice('function jevDisplayLabel(', '\n\nfunction goToJevTarget('),
  slice('/* ---------------- Jev markers', '\n\n/* ---------------- UI'),
].join('\n\n');
const levels = JSON.parse(execFileSync('python3', ['-c', 'import json, sys; sys.path.insert(0, "skill/review-spec/assets"); from jev import MARK_LEVELS; print(json.dumps(MARK_LEVELS))'], { cwd: root, encoding: 'utf8' }));
const target = 'docs/specs/onboarding.spec.html#acceptance-onboarding';
const rule = (stateName, extra = {}) => ({ kind: 'rule', id: 'acceptance', state: stateName, label: stateName === 'label' ? 'missed' : null,
  target, record: null, word: 'onboarding', text: 'Every feature that changes a screen updates onboarding.', escalated: false, level: stateName === 'label' ? levels.missed.human : null, ...extra });
const state = { readingView: false, jev: { request: 1, status: 'on', base: 'b', items: [], levels, offer: null } };
const location = { search: '', pathname: '/docs/specs/b.spec.html' };
const composed = [];
const openComposer = (...args) => composed.push(args);
const { renderJev } = Function('document', 'window', 'state', 'location', 'URLSearchParams', 'EMBED_REVIEW_DIR', 'NodeFilter',
  'requestAnimationFrame', 'cancelAnimationFrame', 'getComputedStyle', 'innerWidth', 'innerHeight', 'openComposer', 'fetch',
  code + '; return { renderJev };')(document, window, state, location, URLSearchParams, null, {}, () => 0, () => {},
  () => ({}), 1440, 900, openComposer, fetch);
const holder = anchor => article.querySelectorAll('[data-anchor]').find(e => e.dataset.anchor === anchor);
const markers = () => body.querySelectorAll('.hx-jev-marker');
const pop = () => body.querySelector('.hx-jev-pop');
const show = items => { state.jev.items = items; renderJev(); };

// project-rules #acceptance-miss, #mark-miss, #mark-style: one important marker on the Acceptance criteria section,
// its note the fixed word linking the rule, not muted, with its sentence as accessible name, and Ask to cover.
show([rule('label')]);
assert.equal(markers().length, 1);
const marker = holder('acceptance').querySelector('.hx-jev-marker');
assert.equal(marker.dataset.attention, 'true', 'a rule miss is important, from the server levels');
assert.equal(marker.dataset.pending, 'false');
marker.fire('focus');
const note = pop().querySelector('.hx-jev-pop-note');
const text = note.querySelector('.hx-jev-pop-text');
assert.deepEqual([note.dataset.group, note.dataset.attention, text.tagName, text.textContent, text.href],
  ['rule', 'true', 'A', 'onboarding?', '/' + target]);
const sentence = 'This spec may need #acceptance-onboarding from onboarding.spec.html';
assert.equal(text.getAttribute('aria-label'), sentence);
assert.equal(text.getAttribute('role'), null, 'the link keeps link semantics');
assert.equal(note.querySelector('.hx-jev-pop-sentence').textContent, sentence);
// #mark-rule-text: the rule's own criterion text follows the word in full, as plain quoted text.
assert.equal(note.querySelector('.hx-jev-pop-quote').textContent, '\u201cEvery feature that changes a screen updates onboarding.\u201d');
assert.deepEqual(note.querySelectorAll('button').map(b => b.textContent), ['Ask to cover', 'Not here', 'Dismiss rule']);
// #acceptance-ask: the composer opens at the Acceptance criteria section with the fixed draft; nothing is written.
note.querySelector('button').fire('click');
assert.deepEqual(composed.pop(), ['acceptance', null, null,
  'Cover ' + target + ' with a criterion, or add one line saying why it does not apply.']);
assert.equal(pop().hidden, true);
assert.equal(posted.length, 0);
// #dismiss-where, #acceptance-dismiss: Not here removes the note at once and records dismissed on its rule-check record,
// through the offer's record route; other rules stay.
const other = { ...rule('label'), id: 'story', target: 'docs/specs/qa.spec.html#acceptance-qa', word: 'qa', text: 'Every feature has QA.', record: 'q1' };
show([rule('label', { record: 'r1' }), other]);
assert.equal(markers().length, 2);
holder('acceptance').querySelector('.hx-jev-marker').fire('focus');
pop().querySelectorAll('button').find(b => b.textContent === 'Not here').fire('click');
assert.equal(markers().length, 1, 'the note is gone without a reload');
assert.equal(holder('acceptance').querySelector('.hx-jev-marker'), null);
assert.equal(pop().hidden, true);
assert.deepEqual(posted.pop(), ['/api/jev/offer?path=docs%2Fspecs%2Fb.spec.html',
  { dismiss: 'here', rule: 'Every feature that changes a screen updates onboarding.', record: 'r1' }]);
// #acceptance-dismiss-rule: Dismiss rule removes every note of that rule text on the page and records not-a-rule by text.
show([rule('label', { record: 'r1' }), rule('label', { id: 'story', record: 'r2' }), other]);
assert.equal(markers().length, 2);
holder('story').querySelector('.hx-jev-marker').fire('focus');
pop().querySelectorAll('.hx-jev-pop-note').find(n => n.querySelector('.hx-jev-pop-text').textContent === 'onboarding?')
  .querySelectorAll('button').find(b => b.textContent === 'Dismiss rule').fire('click');
assert.deepEqual(state.jev.items.map(i => i.word), ['qa']);
assert.equal(markers().length, 1);
assert.deepEqual(posted.pop(), ['/api/jev/offer?path=docs%2Fspecs%2Fb.spec.html',
  { dismiss: 'rule', rule: 'Every feature that changes a screen updates onboarding.' }]);
assert.equal(posted.length, 0);
// The level comes only from the item: the runtime keeps no mapping for rules.
show([rule('label', { level: 'warning' })]);
assert.equal(markers()[0].dataset.attention, 'false');
// Rule notes list after coverage in the popover.
show([rule('label'), { kind: 'type', id: 'acceptance', state: 'unavailable', label: null, target: null, record: 'r', level: null }]);
location.search = '?focus=changes';
renderJev();
markers()[0].fire('focus');
assert.deepEqual(pop().querySelectorAll('.hx-jev-pop-note').map(n => n.dataset.group), ['rule', 'neutral']);
location.search = '';

// #acceptance-pending, #mark-pending: only an escalated check shows the wheel in the marker's place, named by its sentence.
show([rule('pending', { escalated: true })]);
assert.equal(markers().length, 1);
assert.deepEqual([markers()[0].dataset.pending, markers()[0].dataset.attention, markers()[0].getAttribute('aria-label')],
  ['true', 'false', 'checking onboarding…']);
markers()[0].fire('focus');
assert.deepEqual(pop().querySelectorAll('.hx-jev-pop-text').map(t => t.textContent), ['checking onboarding…']);
assert.equal(pop().querySelectorAll('button').length, 0);
// #q-fallback: a failed LLM fallback shows the neutral Jev unavailable note at the rule's spot, its sentence naming the rule.
show([rule('unavailable')]);
assert.equal(markers().length, 1);
assert.equal(holder('acceptance').querySelector('.hx-jev-marker').dataset.attention, 'false');
markers()[0].fire('focus');
const unavailable = pop().querySelector('.hx-jev-pop-note');
const unavailableSentence = 'Jev could not check whether this spec needs #acceptance-onboarding from onboarding.spec.html';
assert.deepEqual([unavailable.dataset.group, unavailable.querySelector('.hx-jev-pop-text').textContent,
  unavailable.querySelector('.hx-jev-pop-text').getAttribute('aria-label'), unavailable.querySelector('.hx-jev-pop-sentence').textContent,
  unavailable.querySelectorAll('button').length], ['neutral', 'Jev unavailable', unavailableSentence, unavailableSentence, 0]);
// Plain Jev pending, covered, and not triggered show nothing (#mark-none).
for (const items of [[rule('pending')], [rule('none')], [rule('label', { label: null })]]) {
  show(items);
  assert.equal(markers().length, 0, JSON.stringify(items));
}
// Reduced motion: the wheel is still.
assert.match(runtime, /\.hx-jev-marker\[data-pending=true\]\{[^}]*animation:hx-jev-spin/);
assert.match(runtime, /@media\(prefers-reduced-motion:reduce\)\{[^@]*\.hx-jev-marker\[data-pending=true\]\{animation:none\}/);

// #pending-poll: while any item is pending the page asks again; a failed poll keeps the last answer.
const pollCode = slice('async function fetchJev(', '\n\nfunction jevItem(') + '\n' + slice('// Background answers', '\n\n// One evidence read');
const timers = [];
const flush = () => new Promise(resolve => setImmediate(resolve));
const replies = [];
const pollState = { readingView: false, jev: { request: 0, status: 'idle', items: [], levels: {}, offer: null, base: null } };
const renders = [];
const { requestJev } = Function('document', 'state', 'location', 'fetch', 'setTimeout', 'clearTimeout', 'AbortController', 'renderJev', 'renderPanel',
  'renderPins', 'jevParams', pollCode + '; return { requestJev };')({ hidden: false, addEventListener() {} }, pollState, { protocol: 'http:' },
  async () => { const next = replies.shift(); if (next instanceof Error) throw next; return { ok: true, json: async () => next }; },
  (fn, ms) => { if (ms !== 120000) timers.push(fn); return timers.length; }, () => {}, AbortController, () => renders.push(pollState.jev.status), () => {}, () => {}, () => '');
const pending = { kind: 'rule', id: 'acceptance', state: 'pending', target, word: 'onboarding', escalated: true };
replies.push({ jev: 'on', items: [pending], levels, offer: null });
await requestJev('b');
assert.equal(timers.length, 1, 'a pending item schedules one more ask');
const rendered = renders.length;
replies.push({ jev: 'on', items: [pending], levels, offer: null });
timers.shift()();
await flush();
assert.equal(renders.length, rendered, 'an unchanged poll does not rerender');
assert.equal(timers.length, 1);
replies.push(new Error('down'));
timers.shift()();
await flush();
assert.equal(pollState.jev.items[0].state, 'pending', 'a failed poll keeps the last answer');
assert.equal(timers.length, 1, 'and asks again');
replies.push({ jev: 'on', items: [{ ...pending, state: 'label', label: 'missed', escalated: false, level: 'important' }], levels, offer: null });
timers.shift()();
await flush();
assert.deepEqual([pollState.jev.items[0].state, pollState.jev.items[0].level, renders.length > rendered], ['label', 'important', true]);
assert.equal(timers.length, 0, 'no pending item, no more asks');

// #acceptance-offer, #bootstrap-offer: one page note; Reconcile drafts one comment, recorded only once sent; dismiss records.
const offer = { count: 2, specs: [
  { spec: 'docs/specs/a.spec.html', rules: [{ target, word: 'onboarding' }, { target: 'docs/specs/qa.spec.html#acceptance-qa', word: 'qa' }] },
  { spec: 'docs/specs/c.spec.html', rules: [{ target, word: 'onboarding' }] }] };
state.jev.offer = offer;
show([]);
const offerNote = () => body.querySelectorAll('.hx-jev-note');
assert.match(offerNote()[0].querySelector('span').textContent, /^2 existing specs miss project rules: reconcile\?$/);
assert.equal(body.children[0], offerNote()[0], 'a page note before the spec');

// Disclosure toggle: collapsed by default with detail hidden.
const disclosureBtn = offerNote()[0].querySelectorAll('.hx-disclosure')[0];
assert.equal(disclosureBtn.getAttribute('aria-expanded'), 'false', 'disclosure collapsed by default');
assert.equal(disclosureBtn.textContent, '▸', 'collapsed triangle');
const detailDiv = offerNote()[0].querySelectorAll('.hx-jev-offer-detail')[0];
assert.equal(detailDiv.hidden, true, 'detail hidden by default');

// Expanding shows spec links and rule links.
disclosureBtn.fire('click');
assert.equal(disclosureBtn.getAttribute('aria-expanded'), 'true', 'expanded after click');
assert.equal(disclosureBtn.textContent, '▾', 'expanded triangle');
assert.equal(detailDiv.hidden, false, 'detail visible after click');
const specRows = detailDiv.querySelectorAll('.hx-jev-offer-spec');
assert.equal(specRows.length, 2, 'one row per spec');
const specLinks = specRows[0].querySelectorAll('a');
assert.equal(specLinks[0].textContent, 'docs/specs/a.spec.html');
assert.equal(specLinks[0].href, '/docs/specs/a.spec.html');
assert.equal(specLinks[1].textContent, 'onboarding?');
assert.equal(specLinks[1].href, '/' + target);
assert.equal(specLinks[2].textContent, 'qa?');
assert.equal(specLinks[2].href, '/docs/specs/qa.spec.html#acceptance-qa');
const specLinks2 = specRows[1].querySelectorAll('a');
assert.equal(specLinks2[0].textContent, 'docs/specs/c.spec.html');
assert.equal(specLinks2[1].textContent, 'onboarding?');

// Collapsing again hides detail.
disclosureBtn.fire('click');
assert.equal(disclosureBtn.getAttribute('aria-expanded'), 'false', 'collapsed again');
assert.equal(detailDiv.hidden, true, 'detail hidden again');

// Reconcile still works (button[1] after disclosure button[0]).
offerNote()[0].querySelectorAll('button')[1].fire('click');
const [anchor, , , draft, onSent] = composed.pop();
assert.equal(anchor, 'title');
assert.equal(draft, 'Reconcile each spec with its missed rules:\n' +
  'docs/specs/a.spec.html onboarding? ' + target + ' qa? docs/specs/qa.spec.html#acceptance-qa\n' +
  'docs/specs/c.spec.html onboarding? ' + target);
assert.equal(posted.length, 0, 'nothing is written until the reviewer sends');
assert.equal(offerNote().length, 1, 'the offer stays until sent or dismissed');
onSent();
assert.equal(offerNote().length, 0);
assert.deepEqual(posted.pop(), ['/api/jev/offer?path=docs%2Fspecs%2Fb.spec.html', { offer: 'sent' }]);
state.jev.offer = { ...offer, count: 1 };
renderJev();
assert.match(offerNote()[0].textContent, /^1 existing spec misses project rules: reconcile\?/);
// Dismiss still works (button[2] after disclosure[0] and reconcile[1]).
const dismiss = offerNote()[0].querySelectorAll('button')[2];
assert.equal(dismiss.getAttribute('aria-label'), 'Dismiss');
dismiss.fire('click');
assert.equal(offerNote().length, 0);
assert.deepEqual(posted.pop(), ['/api/jev/offer?path=docs%2Fspecs%2Fb.spec.html', { offer: 'dismissed' }]);
state.jev.status = 'off';
state.jev.offer = offer;
renderJev();
assert.deepEqual(offerNote().map(n => n.textContent), ['Jev off'], 'no offer with Jev off');

// The composer runs onSent only after the comment is posted.
assert.match(slice('function addComposer(', '\n}\n'), /await state\.transport\.postEvent\(body\);\n\s*state\.composer = null;\n\s*if \(c\.onSent\) c\.onSent\(\);/);

console.log('runtime Jev rule tests passed');
