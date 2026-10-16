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
let reply = { status: 200, body: { ok: true } };  // the record route's answer; an Error rejects
// A fake review server: GET /api/jev serves server.items as JSON on the wire, fresh objects each read, like the real
// route; POST /api/jev/offer records, and an accepted dismissal drops what jev.dismiss drops. held and heldPost park
// the next GET or POST until called.
const server = { items: [], held: null, heldPost: null };
const fetch = async (url, init) => {
  if (!init || init.method !== 'POST') {
    const wire = JSON.stringify({ jev: 'on', items: server.items, levels });
    if (server.held) await new Promise(resolve => { server.held = resolve; });
    return { ok: true, json: async () => JSON.parse(wire) };
  }
  const body = JSON.parse(init.body);
  posted.push([url, body]);
  if (server.heldPost) await new Promise(resolve => { server.heldPost = resolve; });
  if (reply instanceof Error) throw reply;
  if (reply.status < 400 && reply.body.ok === true && body.dismiss) {
    server.items = server.items.filter(i => body.dismiss === 'here' ? i.record !== body.record
      : !(i.kind === 'candidate' && i.id === body.anchor));
  }
  return { ok: reply.status < 400, status: reply.status, json: async () => reply.body };
};
const settle = async () => { for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve)); };
const pollTimers = [];
const code = [
  slice('function jevParams(', '\n\nfunction jevItem('),
  slice('function findAnchor(', '\n\nfunction clearJev('),
  slice('// Background answers', '\n\n// One evidence read'),
  slice('function coveragePair(', '\n\n// Name the folder'),
  slice('function jevDisplayLabel(', '\n\nfunction goToJevTarget('),
  slice('/* ---------------- Jev markers', '\n\n/* ---------------- UI'),
].join('\n\n');
const levels = JSON.parse(execFileSync('python3', ['-c', 'import json, sys; sys.path.insert(0, "skill/review-spec/assets"); from jev import MARK_LEVELS; print(json.dumps(MARK_LEVELS))'], { cwd: root, encoding: 'utf8' }));
const target = 'docs/specs/onboarding.spec.html#acceptance-onboarding';
const home = 'docs/specs/onboarding.spec.html#acceptance-onboarding'; // the rule's repo spec path and anchor
const ruleText = 'Every feature that changes a screen updates onboarding.';
const ruleName = 'Every screen change updates onboarding';
const rule = (stateName, extra = {}) => ({ kind: 'rule', id: 'acceptance', state: stateName, label: stateName === 'label' ? 'missed' : null,
  target, home, record: null, word: 'onboarding', text: ruleText, name: ruleName, escalated: false,
  level: stateName === 'label' ? levels.missed.human : null, ...extra });
const state = { readingView: false, jev: { request: 1, status: 'on', base: 'b', items: [], levels } };
const OWN_ADDRESS = new URL('http://review.example/ann601/docs/specs/b.spec.html'); // a mounted page
const composed = [];
const openComposer = (...args) => composed.push(args);
const { renderJev, requestJev: loadJev } = Function('document', 'window', 'state', 'OWN_ADDRESS', 'URLSearchParams', 'EMBED_REVIEW_DIR', 'NodeFilter',
  'requestAnimationFrame', 'cancelAnimationFrame', 'getComputedStyle', 'innerWidth', 'innerHeight', 'openComposer', 'fetch',
  'renderPanel', 'renderPins', 'setTimeout', 'clearTimeout', 'AbortController',
  code + '; return { renderJev, requestJev };')(document, window, state, OWN_ADDRESS, URLSearchParams, null, {}, () => 0, () => {},
  () => ({}), 1440, 900, openComposer, fetch, () => {}, () => {},
  (fn, ms) => { if (ms === 2000) pollTimers.push(fn); return 0; }, () => {}, AbortController);
const holder = anchor => article.querySelectorAll('[data-anchor]').find(e => e.dataset.anchor === anchor);
const markers = () => body.querySelectorAll('.hx-jev-marker');
const pop = () => body.querySelector('.hx-jev-pop');
const show = items => { state.jev.items = items; renderJev(); };
const buttons = el => el.querySelectorAll('button').map(b => b.textContent);
const route = '/api/jev/offer?path=ann601%2Fdocs%2Fspecs%2Fb.spec.html';

// project-rules #card, #acceptance-miss, #acceptance-card-readable, #acceptance-dismiss-no-unrule: one important
// marker on the Acceptance criteria section; its popover holds one rule card: label, the mark's name as title, the full
// verbatim quote, the source link naming home file and anchor, then Not for this spec left and the one primary action
// rightmost; no button unmarks or renames the rule.
show([rule('label')]);
assert.equal(markers().length, 1);
const marker = holder('acceptance').querySelector('.hx-jev-marker');
assert.equal(marker.dataset.attention, 'true', 'a rule miss is important, from the server levels');
assert.equal(marker.dataset.pending, 'false');
const sentence = 'This spec may need the rule ' + ruleName + ' from onboarding.spec.html';
assert.equal(marker.getAttribute('aria-label'), 'Jev notes: ' + sentence, 'the hover sentence is the marker name (#mark-sentence)');
marker.fire('focus');
const note = pop().querySelector('.hx-jev-pop-note');
assert.deepEqual([note.dataset.group, note.dataset.attention], ['rule', 'true']);
const card = note.querySelector('.hx-rule-card');
assert.ok(card, 'a rule note is one rule card');
assert.equal(card.getAttribute('role'), 'group');
assert.equal(card.getAttribute('aria-label'), 'Missing project rule: ' + ruleName, 'a labelled group named by label and title');
assert.equal(card.querySelector('.hx-rule-card-label').textContent, 'Missing project rule');
assert.equal(card.querySelector('.hx-rule-card-title').textContent, ruleName);
assert.equal(card.querySelector('.hx-rule-card-quote').textContent, ruleText, 'verbatim, never shortened');
const source = card.querySelector('.hx-rule-card-source');
assert.deepEqual([source.tagName, source.href, source.querySelector('.hx-rule-card-source-text').textContent],
  ['A', '/' + target, 'onboarding · #acceptance-onboarding']);
assert.deepEqual(buttons(card), ['Not for this spec', 'Ask to cover']);
const cardButtons = card.querySelectorAll('button');
assert.deepEqual(cardButtons.map(b => b.className), ['hx-rule-card-reject', 'hx-btn pri'],
  'the reject is a text-link button, the action is the primary button');
assert.equal(note.querySelector('.hx-jev-pop-sentence'), null, 'a card shows no hover sentence of its own');
// #acceptance-ask, #acceptance-ask-unsent: the composer opens at the Acceptance criteria section with the fixed draft; nothing is written.
cardButtons[1].fire('click');
assert.deepEqual(composed.pop(), ['acceptance', null, null,
  'Cover ' + target + ' with a criterion, or add one line saying why it does not apply.']);
assert.equal(pop().hidden, true);
assert.equal(posted.length, 0);
// The title falls back to home and anchor when no name reaches the page.
show([rule('label', { name: null })]);
markers()[0].fire('focus');
assert.equal(pop().querySelector('.hx-rule-card-title').textContent, 'onboarding · #acceptance-onboarding');
assert.equal(markers()[0].getAttribute('aria-label'), 'Jev notes: This spec may need the rule onboarding · #acceptance-onboarding from onboarding.spec.html');

// #dismiss-where, #acceptance-dismiss: through the served /api/jev read, the server the only truth.
// A dismiss click hides its card at once, records through the offer's record route, then re-reads /api/jev and shows
// what the server returns.
const wire = (id, record, extra = {}) => ({ kind: 'rule', id, target, home, word: 'onboarding', text: ruleText, name: ruleName, state: 'label',
  label: 'missed', record, level: levels.missed.human, agent_level: levels.missed.agent, ...extra });
const qa = wire('title', 'q1', { target: 'docs/specs/qa.spec.html#acceptance-qa', word: 'qa', text: 'Every feature has QA.', name: 'Every feature has QA' });
const load = async items => { server.items = items; pollTimers.length = 0; await loadJev('b'); };
const cardOf = (anchor, name) => {
  holder(anchor).querySelector('.hx-jev-marker').fire('focus');
  return pop().querySelectorAll('.hx-rule-card').find(c => c.querySelector('.hx-rule-card-title').textContent === name);
};
const click = (anchor, name, label) => cardOf(anchor, name).querySelectorAll('button').find(b => b.textContent === label).fire('click');
await load([wire('acceptance', 'r1'), qa]);
assert.equal(markers().length, 2);
assert.equal(cardOf('acceptance', ruleName).querySelector('.hx-rule-card-quote').textContent, ruleText, 'the served rule text reaches the card');
click('acceptance', ruleName, 'Not for this spec');
assert.equal(markers().length, 1, 'the card is gone without a reload');
assert.equal(holder('acceptance').querySelector('.hx-jev-marker'), null);
assert.equal(pop().hidden, true);
assert.deepEqual(posted.pop(), [route, { dismiss: 'here', home, record: 'r1' }]);
await settle();
assert.equal(markers().length, 1, 'the re-read shows the server, which no longer returns it');
assert.equal(state.jev.items.length, 1);
// #pending-poll: with a pending item polling, a same-content poll swaps in new item objects without a rerender;
// Not for this spec on the card then shown still drops it and records it.
const pendingItem = wire('story', null, { target: 'docs/specs/qa.spec.html#acceptance-qa', word: 'qa', text: 'Every feature has QA.',
  name: 'Every feature has QA', state: 'pending', label: null, level: undefined, agent_level: undefined, escalated: true });
await load([wire('acceptance', 'r1'), qa, pendingItem]);
assert.equal(markers().length, 3);
pollTimers.shift()();
await settle();
assert.equal(markers().length, 3);
click('acceptance', ruleName, 'Not for this spec');
assert.equal(markers().length, 2, 'dropped at once after a same-content poll');
assert.deepEqual(posted.pop()[1], { dismiss: 'here', home, record: 'r1' });
await settle();
assert.equal(markers().length, 2);
// A poll in flight when the reviewer dismisses answers from before the dismissal; while the record is still on its
// way, that answer does not bring the card back; the page shows the re-read after it.
await load([wire('acceptance', 'r1'), qa, pendingItem]);
server.held = true;
pollTimers.shift()();
await settle();
const release = server.held;
server.held = null;
server.heldPost = true;
click('acceptance', ruleName, 'Not for this spec');
await settle();
release();
await settle();
assert.equal(markers().length, 2, 'a stale poll does not bring the card back');
const recorded = server.heldPost;
server.heldPost = null;
recorded();
posted.pop();
await settle();
assert.equal(markers().length, 2);
assert.equal(state.jev.items.filter(i => i.word === 'onboarding').length, 0);
// #dismiss-where: a dismissal the server refuses or never gets comes back on the re-read, so it never looks done.
for (const refused of [{ status: 400, body: { ok: false } }, { status: 200, body: { ok: false } }, { status: 503, body: {} }, new Error('offline')]) {
  reply = refused;
  await load([wire('acceptance', 'r1'), qa]);
  click('acceptance', ruleName, 'Not for this spec');
  assert.equal(markers().length, 1, 'dropped at once');
  await settle();
  assert.equal(markers().length, 2, 'back from the server: ' + JSON.stringify(refused.body || refused.message));
  assert.equal(state.jev.items.filter(i => i.word === 'onboarding').length, 1);
  posted.pop();
}
reply = { status: 200, body: { ok: true } };
// The level comes only from the item: the runtime keeps no mapping for rules.
show([rule('label', { level: 'warning' })]);
assert.equal(markers()[0].dataset.attention, 'false');
// Rule cards list after coverage in the popover.
show([rule('label'), { kind: 'type', id: 'acceptance', state: 'unavailable', label: null, target: null, record: 'r', level: null }]);
OWN_ADDRESS.search = '?focus=changes';
renderJev();
markers()[0].fire('focus');
assert.deepEqual(pop().querySelectorAll('.hx-jev-pop-note').map(n => n.dataset.group), ['rule', 'neutral']);
OWN_ADDRESS.search = '';

// #card-variants: a Contradicts mark whose other clause is a marked rule is the same card, in the conflict place,
// labelled Contradicts project rule, with Not for this spec and Ask agent to reconcile.
OWN_ADDRESS.search = '?focus=changes';
const cited = { kind: 'corpus', id: 'story', state: 'label', label: 'contradicts', target, record: 'c1', level: levels.contradicts.human,
  rule: { target, text: ruleText, name: ruleName, home } };
await load([cited]);
holder('story').querySelector('.hx-jev-marker').fire('focus');
const citeNote = pop().querySelector('.hx-jev-pop-note');
assert.equal(citeNote.dataset.group, 'conflict', 'in the mark place in the popover order');
const citeCard = citeNote.querySelector('.hx-rule-card');
assert.deepEqual([citeCard.querySelector('.hx-rule-card-label').textContent, citeCard.querySelector('.hx-rule-card-title').textContent,
  citeCard.querySelector('.hx-rule-card-quote').textContent], ['Contradicts project rule', ruleName, ruleText]);
assert.deepEqual(buttons(citeCard), ['Not for this spec', 'Ask agent to reconcile']);
citeCard.querySelectorAll('button')[1].fire('click');
assert.deepEqual(composed.pop(), ['story', null, null, 'Reconcile this clause with ' + target + '.']);
// #card-cites-reject: Not for this spec on a cited card records dismissed for that clause and the rule; the mark is gone.
click('story', ruleName, 'Not for this spec');
assert.equal(markers().length, 0);
assert.deepEqual(posted.pop(), [route, { dismiss: 'here', home, record: 'c1', anchor: 'story' }]);
await settle();
assert.equal(markers().length, 0);
OWN_ADDRESS.search = '';

// #mark-candidate, #acceptance-candidates: a candidate on its own criterion is a card labelled Possible project rule
// above the criterion's anchor, warning level, quoting the criterion in full, with Not a project rule and Mark as rule.
const candidate = (id, extra = {}) => ({ kind: 'candidate', id, target: 'docs/specs/b.spec.html#story', text: 'Every feature ships docs.',
  state: 'label', label: 'candidate', level: levels.candidate.human, record: 's1', ...extra });
await load([candidate('story'), candidate(null, { target: 'docs/specs/x.spec.html#acceptance-x', text: 'Every change has x.' })]);
assert.equal(markers().length, 1, 'a candidate elsewhere is data only');
const candidateMarker = holder('story').querySelector('.hx-jev-marker');
assert.deepEqual([candidateMarker.dataset.attention, candidateMarker.dataset.warning], ['false', 'true']);
assert.equal(candidateMarker.getAttribute('aria-label'), 'Jev notes: Jev reads this as a rule for every feature');
candidateMarker.fire('focus');
const candidateCard = pop().querySelector('.hx-rule-card');
assert.deepEqual([candidateCard.querySelector('.hx-rule-card-label').textContent, candidateCard.querySelector('.hx-rule-card-title').textContent,
  candidateCard.querySelector('.hx-rule-card-quote').textContent], ['Possible project rule', '#story', 'Every feature ships docs.']);
assert.equal(candidateCard.getAttribute('aria-label'), 'Possible project rule: #story');
assert.deepEqual(buttons(candidateCard), ['Not a project rule', 'Mark as rule']);
assert.deepEqual(candidateCard.querySelectorAll('button').map(b => b.className), ['hx-rule-card-reject', 'hx-btn pri']);
// #acceptance-mark-as-rule, #acceptance-mark-as-rule-unsent: the composer opens at the criterion with the draft; nothing is written.
candidateCard.querySelectorAll('button')[1].fire('click');
assert.deepEqual(composed.pop(), ['story', null, null, 'Mark this criterion as a project rule with a short name.']);
assert.equal(posted.length, 0);
assert.equal(markers().length, 1, 'Mark as rule decides nothing');
// #acceptance-not-a-rule: Not a project rule records not-a-rule for the criterion; the card is gone at once.
holder('story').querySelector('.hx-jev-marker').fire('focus');
pop().querySelector('.hx-rule-card').querySelectorAll('button')[0].fire('click');
assert.deepEqual(posted.pop(), [route, { dismiss: 'rule', anchor: 'story' }]);
assert.equal(markers().length, 0);
await settle();
assert.equal(markers().length, 0);

// #acceptance-pending, #mark-pending: only an escalated check shows the wheel in the marker's place, named by its sentence.
show([rule('pending', { escalated: true })]);
assert.equal(markers().length, 1);
assert.deepEqual([markers()[0].dataset.pending, markers()[0].dataset.attention, markers()[0].getAttribute('aria-label')],
  ['true', 'false', 'checking ' + ruleName + '…']);
markers()[0].fire('focus');
assert.deepEqual(pop().querySelectorAll('.hx-jev-pop-text').map(t => t.textContent), ['checking ' + ruleName + '…']);
assert.equal(pop().querySelectorAll('button').length, 0);
// An escalated candidate scope shows the same wheel on its criterion.
show([candidate('story', { state: 'pending', label: null, level: undefined, escalated: true })]);
assert.deepEqual([markers()[0].dataset.pending, markers()[0].getAttribute('aria-label')], ['true', 'checking #story…']);
// #q-fallback: a failed LLM fallback shows the neutral Jev unavailable note at the rule's spot, its sentence naming the rule.
show([rule('unavailable')]);
assert.equal(markers().length, 1);
assert.equal(holder('acceptance').querySelector('.hx-jev-marker').dataset.attention, 'false');
markers()[0].fire('focus');
const unavailable = pop().querySelector('.hx-jev-pop-note');
const unavailableSentence = 'Jev could not check whether this spec needs the rule ' + ruleName;
assert.deepEqual([unavailable.dataset.group, unavailable.querySelector('.hx-jev-pop-text').textContent,
  unavailable.querySelector('.hx-jev-pop-text').getAttribute('aria-label'), unavailable.querySelector('.hx-jev-pop-sentence').textContent,
  unavailable.querySelectorAll('button').length], ['neutral', 'Jev unavailable', unavailableSentence, unavailableSentence, 0]);
// Plain Jev pending, covered, and not triggered show nothing (#mark-none).
for (const items of [[rule('pending')], [rule('none')], [rule('label', { label: null })], [candidate('story', { state: 'pending', label: null })]]) {
  show(items);
  assert.equal(markers().length, 0, JSON.stringify(items));
}
// Reduced motion: the wheel is still.
assert.match(runtime, /\.hx-jev-marker\[data-pending=true\]\{[^}]*animation:hx-jev-spin/);
assert.match(runtime, /@media\(prefers-reduced-motion:reduce\)\{[^@]*\.hx-jev-marker\[data-pending=true\]\{animation:none\}/);
// #card-readable: a card widens the popover, its buttons never truncate, and they are keyboard stops in order.
assert.match(runtime, /\.hx-jev-pop:has\(\.hx-rule-card\)\{[^}]*max-width:min\(440px,calc\(100vw - 16px\)\)/);
assert.match(runtime, /\.hx-rule-card-actions\{[^}]*flex-wrap:wrap/);
assert.match(runtime, /\.hx-rule-card-actions button\{[^}]*white-space:nowrap/);
assert.match(slice('function wireJevPopover(', '\n}\n'), /querySelectorAll\('a,button'\)/);

// #pending-poll: while any item is pending the page asks again; a failed poll keeps the last answer.
const pollCode = slice('async function fetchJev(', '\n\nfunction jevItem(') + '\n' + slice('// Background answers', '\n\n// One evidence read');
const timers = [];
const flush = () => new Promise(resolve => setImmediate(resolve));
const replies = [];
const pollState = { readingView: false, jev: { request: 0, status: 'idle', items: [], levels: {}, base: null } };
const renders = [];
const { requestJev } = Function('document', 'state', 'OWN_ADDRESS', 'fetch', 'setTimeout', 'clearTimeout', 'AbortController', 'renderJev', 'renderPanel',
  'renderPins', 'jevParams', pollCode + '; return { requestJev };')({ hidden: false, addEventListener() {} }, pollState, new URL('http://review.example/docs/specs/b.spec.html'),
  async () => { const next = replies.shift(); if (next instanceof Error) throw next; return { ok: true, json: async () => next }; },
  (fn, ms) => { if (ms !== 120000) timers.push(fn); return timers.length; }, () => {}, AbortController, () => renders.push(pollState.jev.status), () => {}, () => {}, () => '');
const pending = { kind: 'rule', id: 'acceptance', state: 'pending', target, word: 'onboarding', escalated: true };
replies.push({ jev: 'on', items: [pending], levels });
await requestJev('b');
assert.equal(timers.length, 1, 'a pending item schedules one more ask');
const rendered = renders.length;
replies.push({ jev: 'on', items: [pending], levels });
timers.shift()();
await flush();
assert.equal(renders.length, rendered, 'an unchanged poll does not rerender');
assert.equal(timers.length, 1);
replies.push(new Error('down'));
timers.shift()();
await flush();
assert.equal(pollState.jev.items[0].state, 'pending', 'a failed poll keeps the last answer');
assert.equal(timers.length, 1, 'and asks again');
replies.push({ jev: 'on', items: [{ ...pending, state: 'label', label: 'missed', escalated: false, level: 'important' }], levels });
timers.shift()();
await flush();
assert.deepEqual([pollState.jev.items[0].state, pollState.jev.items[0].level, renders.length > rendered], ['label', 'important', true]);
assert.equal(timers.length, 0, 'no pending item, no more asks');

// No offer, candidate list, or Confirm rule reaches the page: rules and candidates are only items (#declare).
assert.doesNotMatch(runtime, /candidate_offer|candidateOffer|Confirm rule|hx-jev-offer/);


console.log('runtime Jev rule tests passed');
