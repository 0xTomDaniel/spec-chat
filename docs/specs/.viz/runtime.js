// spec-chat runtime v0.1 — hydrates semantic islands and mounts the annotation layer.
// spec-chat-capabilities: address-browser answer-buttons box-picture changed-root-focus conversation custom-style-focus design-set diff-visibility-control error-overlay finish-review fit-settles git-focus keys-icons manual-resume-status mobile-handoff mobile-pre-wrap mobile-review mockup-chips mockup-directions mockup-questions mockup-review mockup-walkthrough next-screen next-tbd one-surface prototype-frame-id prototype-mockup prototype-origin reopen-thread reply-lane round-video screen-index semantic-islands served-storage shared-style-ownership shell-held spec-acceptance stage-tag tbd-later typed-kept video-moment wake-reason
// Transports: FSA (file://, primary) | HTTP review-serve (http(s)://, secondary).
// Same spools, same event schema either way. See DESIGN.md.
// Classic script, NOT a module: browsers CORS-block module scripts on file:// pages,
// and file:// is the primary transport. Specs load it with <script defer src=...>.
// Embed mode: a host app (SPA or any live page) loads the same script with
// data-review-dir="<repo-relative>.review" on the tag. That pins one spool for the whole
// app (routes change its address, which normally names the spool), skips the
// document-presentation CSS (the host owns its look; only the hx-* overlay ships), and
// disables spec-mtime watching (there is no spec file to go stale). HTTP transport only.
(function () {
'use strict';

// served-pages #served-own-address: the page's own address is its document's base address, fragment aside;
// served directly, that is its location. Everything the page addresses comes from it, never from its location.
// Its query follows what the page shows (#served-rewrite).
const OWN_ADDRESS = new URL(document.baseURI);
OWN_ADDRESS.hash = '';
const SPEC_FILE = decodeURIComponent(OWN_ADDRESS.pathname.split('/').pop());
// document.currentScript is only valid during the initial synchronous run — capture now.
const EMBED_REVIEW_DIR = (document.currentScript && document.currentScript.dataset && document.currentScript.dataset.reviewDir) || null;
// comment-layer #load: served at <url>/static/comment-layer.js, this runtime is the comment layer on a host page, and
// <url>/ is the review service every request goes to, never the host page's own address.
const SCRIPT_SERVICE = (() => {
  const src = document.currentScript && document.currentScript.src;
  const at = src ? new URL(src) : null;
  return at && /\/static\/comment-layer\.js$/.test(at.pathname) ? new URL('../', at) : null;
})();
// shell #shell-ui-page: the comment UI page a shell frames loads the runtime from the same address, marked
// data-comment-ui; it draws only the conversation of the page the shell relays, and reads and writes through the
// review service it is served by.
const COMMENT_UI = Boolean(SCRIPT_SERVICE && document.currentScript.hasAttribute('data-comment-ui'));
const LAYER_SERVICE = COMMENT_UI ? null : SCRIPT_SERVICE;
// mockups #prototype-route: on a prototype page the service's proxy serves, the layer's tag names the lane's prototype
// origin it runs at, so no <base> the page sets moves it; null on any other page.
const PROTOTYPE_BASE = (() => {
  const base = LAYER_SERVICE && document.currentScript.dataset.specChatPrototype;
  return base ? new URL(base, LAYER_SERVICE) : null;
})();
const REVIEW_DIRNAME = SPEC_FILE + '.review';
// The spool this page reviews; also keys its outbox and last-loaded events in browser storage. The layer's outbox
// holds every scope's events, each naming its own, so it is keyed by the service; so is the comment UI page's, whose
// entries each name the spool or scope they go to.
const REVIEW_DIR = EMBED_REVIEW_DIR || (SCRIPT_SERVICE ? (COMMENT_UI ? 'comments:' : 'layer:') + SCRIPT_SERVICE.href : OWN_ADDRESS.pathname.replace(/^\//, '') + '.review');
const RUNTIME_URL = (document.currentScript && document.currentScript.src) || new URL('./.viz/runtime.js', document.baseURI).href;
const VENDOR = { echarts: new URL('./vendor/echarts-5.5.1.min.js', RUNTIME_URL).href };

/* ---------------- error overlay (headless-debuggable) ---------------- */
// spec-page-design #error-overlay: one line per distinct error, counting its times, so an error on a timer never grows
// it. Fixed under the top line, and at the top of the page while no top line shows; the page starts under both.
// A host page's own errors are the host's: the layer shows no error (comment-layer #ui-absent).
if (!LAYER_SERVICE) window.addEventListener('error', e => {
  // Browsers intentionally redact some foreign/extension failures to this
  // detail-free signature. It cannot identify a spec-chat fault and must not
  // cover a still-working review surface with a fatal-looking overlay.
  if (e.message === 'Script error.' && !e.filename && !e.lineno && !e.colno && !e.error) return;
  overlay('error', e.message + ' @ ' + (e.filename || '').split('/').pop() + ':' + e.lineno);
});
if (!LAYER_SERVICE) window.addEventListener('unhandledrejection', e => overlay('rejection', String(e.reason)));
const errorLines = new Map(); // 'kind: message' -> { line, times }
function overlay(kind, msg) {
  let el = document.getElementById('hx-errors');
  if (!el) {
    el = document.createElement('div');
    el.id = 'hx-errors';
    el.className = 'hx-errors';
    const line = document.getElementById('hx-top-line');
    document.body.insertBefore(el, line ? line.nextSibling : document.body.firstChild);
    errorLines.clear();
    new ResizeObserver(measureTopNext).observe(el); // its height alone changes too, as the screen's does while it is capped
  }
  const text = kind + ': ' + msg;
  let error = errorLines.get(text);
  if (!error) errorLines.set(text, error = { line: el.appendChild(document.createElement('p')), times: 0 });
  error.times += 1;
  error.line.textContent = error.times > 1 ? text + ' (\u00d7' + error.times + ')' : text;
  measureTop();
}

/* ---------------- state ---------------- */
const state = {
  transport: null,       // {mode, ready, listEvents, postEvent, specModified, label}
  events: [],            // [{actor, name, body}] sorted by name
  seenNames: new Set(),
  threads: new Map(),    // root comment id -> {id, ev, messages:[], status, latestHumanId}
  lanes: { lane: null, stages: {} }, // its review's lane and the lanes' stages, as its last events read told (stagesTold)
  focusThread: null,     // the thread shown focused, its messages alone (threads #focus)
  focusQuestion: null,   // on a mockup page, the anchor of the question shown focused (mockups #mockup-questions-show)
  openReplies: new Set(), // thread ids whose replies are open in place in the conversation (threads #replies-collapse)
  commentMode: false,
  panelOpen: false,
  activeThread: null,
  composer: null,        // {anchorId, target, quote, holder}
  charts: new Map(),     // sectionAnchor -> {chart, config, el}
  specMtime: null,
  loopsStarted: false,
  eventsRendered: false,
  lastTbd: null,         // open TBD marker focused by the last TBD open or Next TBD activation
  range: { baseline: null, loaded: null, loading: false, pickerOpen: false }, // loaded: anchor signatures of the page as served
  jev: { status: 'idle', items: [], levels: {}, base: null, request: 0 }, // levels: the server's mark kind -> level table
  evidence: { criteria: null, levels: {} }, // criteria: anchor -> entry once /api/evidence answers, null shows nothing
  readingView: false,
  movingOrphans: new Set(),
  handoffPosting: false,
  reviewer: null,        // {browser, author}: this browser's id and fruit name (#model-author-assign)
  authors: new Map(),    // author key -> shown name (#model-author-shown)
  version: null,         // SHA-256 of the spec text this page shows: its ETag (#model-fields)
  versionAsked: Promise.resolve(null), // the version, once its own address answers
  lastStamp: null,       // last event-name stamp this page chose
  outboxBlocked: false,  // a send failed; the offline notice shows until the outbox drains
  storedCount: -1,       // events last kept in browser storage
  mockups: [],           // the spec's mockup set as the service last told it (#chip-source); null until it first answers
  mockupAsk: false,      // an HTTP page asks for its set, a mockup page its spec's, with each read; a page that is no spec is told so and stops
  mockupAsking: false,
  mockupChips: '',       // what the drawn chips show, so a read that changes only open counts redraws none
  screenIndex: '[]',     // what the drawn Screens index shows (mockups #screen-index-live)
};

// A narrow screen, a phone's: every narrow-width choice the chrome makes in script reads this one query, the CSS's
// @media(max-width:640px). The comment UI page draws no narrow-width choice in script; its CSS reads a phone by its
// touch screen (mountStyle).
const NARROW = window.matchMedia('(max-width: 640px)');

// mockups #set-where: a page at mockups/<spec name>/<file>.mockup.html beside a spec is a screen of that spec's mockup
// set, reviewed as the spec page is (#review-same); MOCKUP_SPEC is that spec's name, null on any other page.
const MOCKUP_SPEC = (() => {
  const parts = OWN_ADDRESS.pathname.split('/').map(part => { try { return decodeURIComponent(part); } catch (_) { return part; } });
  return !LAYER_SERVICE && !EMBED_REVIEW_DIR && parts.length >= 4 && parts[parts.length - 3] === 'mockups'
    && /\.mockup\.html$/.test(parts[parts.length - 1]) && parts[parts.length - 2] ? parts[parts.length - 2] : null;
})();
const MOCKUP_VIEWPORTS = [['desktop', 'Desktop'], ['phone', 'Phone']]; // each frame's viewport and label (#frame-viewports)
const DIRECTIONS_SHOWN = 2; // a mockup's directions list shows from two directions (#directions-what)
// mockups #prototype-file: a prototype mockup names its route on the lane's running prototype and draws no frame; its
// page frames the route (#prototype-route). null on any other page.
const PROTOTYPE_ROUTE = MOCKUP_SPEC ? ((document.querySelector('meta[name="mockup-prototype"]') || {}).content || '').trim() || null : null;
const proto = {
  base: null,            // the lane's prototype origin, as the page's answer names it while its prototype answers
  commit: null,          // the lane branch commit the page loaded at (#prototype-updated)
  down: false,           // the prototype does not answer, or no address names it (#prototype-down)
  rounds: [],            // the round videos, newest first (#round-video-show)
  roundsRead: null,      // the rounds' read, once asked
  reach: 0,              // the latest step asked of the frames; an older replay stops
  earlier: false,        // Earlier rounds shows the older rounds' videos
};
// A prototype route its mockup page holds (shell #shell-setting-own, #shell-msg-prototype): the id its page knows it by,
// the frame it is, its regions, and the marks its page last sent; its viewport's height and the size it last read; the
// actions it does, the act doing them, and the next step's, as a reviewer pressing through does them.
const protoHeld = { on: false, id: '', frame: '', regions: [], pointing: false, marks: null, view: 0, past: null, sizes: null,
  reading: 0, telling: false, acts: Promise.resolve(), acting: null, pressing: { next: [], done: 0 }, leaving: false,
  staying: 0, stayed: () => {} };
const mockup = {
  stage: null,           // the frames' row, or column on a phone (#review-fit)
  slots: [],             // [{viewport, slot, fit, frame}], desktop first
  styles: [],            // the mockup's own style sheets, off while Before shows
  base: null,            // the mockup at the compared base, as a document; null when absent there or unknown
  differs: false,        // the mockup differs from the base: Before and After show (#before-table)
  before: false,         // Before shows: the frames at the base, with no pins and no comment control
  request: 0,            // the latest base compared; an older answer is dropped
  shown: null,           // the page's served text, once read (shownMockupText)
  steps: [],             // the walkthrough of the version showing: [{name, caption, by}] in order (#markup-steps)
  step: -1,              // the step showing, an index into steps; -1 with no walkthrough
  bar: '',               // the steps the step bar was built for, so a redraw keeps its buttons and their focus
  play: null,            // the walkthrough playing, until it stops (#walkthrough-step)
  question: null,        // the question Next TBD last opened (#mockup-questions-next)
  settle: 0,             // a redraw of marks once the frames' motion settles
  set: [],               // its spec's set as the service last told it, for its place in it (#next-screen)
  pager: '',             // what the drawn Previous and Next show, so a read that changes nothing redraws none
  dirs: null,            // the first round's directions, in its file's order, while its page shows them (#directions-page)
  view: null,            // the viewport the reviewer toggled the directions to; null follows the screen's width
  arrival: null,         // the arrival asking a prototype's frames (showInFrames), and the viewport it found, until it ends
};

// shell #shell: a page this runtime runs is alone until a shell holds it (#shell-roles); the comment UI page
// (COMMENT_UI) draws the conversation of whatever page its shell relays (#shell-ui-page). Set before boot runs.
const shell = {
  held: false,           // a shell at a named origin said hello with comments (#shell-roles-held)
  shown: null,           // the spot the shell's last show named, ringed while no thread is active (#shell-msg-show)
  published: '',         // the publish last sent, so one is sent only when what the page shows changes
  answered: '',          // the marks answer last sent
  answers: new Map(),    // question anchor -> its chosen answer's words, as marks last told
  pointing: false,       // a held host page points for the shell (#shell-msg-point)
};
const ui = {
  shell: null,           // the parent's origin, a named shell, once a message came from it (#shell-ui-page-check)
  frames: new Map(),     // frame -> its page's last publish
  frame: null,           // the frame whose publish the shell relayed last: its page's conversation shows
  page: null,            // that publish, as checked (publishedPage)
  dir: null,             // that page's spool, a spec or mockup page's; null for a host page
  mockup: false,         // that page is a mockup page
  doc: null,             // that page as its service serves it, read to name spots and key answers (readPage)
  docVersion: null,      // the version it was read for
  gone: new Set(),       // thread ids whose quote the page's text no longer holds, as it answered
  sent: '',              // the marks last sent to that frame
  lastTbd: null,         // the question Next TBD last showed
  picked: null,          // the thread a pin picked before its page's threads were read, focused once they are
};
const SHELL_PAGE = { hello: (origin, data) => shellHello(origin, data), message: data => shellMessage(data) };

/* ---------------- transports ---------------- */
// review-state #offline-outbox: an events answer, the service's or the copy this page kept of it, is the list it holds
// or refused, so the page never keeps, reads, or adds to anything else.
function eventList(answer) {
  if (!Array.isArray(answer)) throw new Error('review service answered no event list');
  return answer;
}

// threads #stage-tag-source: what an events answer tells of its lanes' stages (X-Spec-Chat-Stages): `lane`, the read's
// own lane, which a message naming none has, and `stages`, each lane's stage in the box's words; none when it tells none.
function stagesTold(answer) {
  try {
    const told = JSON.parse(decodeURIComponent(answer.headers.get('X-Spec-Chat-Stages') || ''));
    const stages = told && typeof told.stages === 'object' && !Array.isArray(told.stages) ? told.stages : {};
    return { lane: typeof told.lane === 'string' ? told.lane : null,
      stages: Object.fromEntries(Object.entries(stages).filter(([, words]) => typeof words === 'string' && words.trim())) };
  } catch (_) { return { lane: null, stages: {} }; }
}

// threads #stage-tag-source, review-state #model-fields-stage: the lane a comment recorded, a lane's review slug, which
// its thread's replies take their stage from; and the lanes a page's threads recorded, whose stages its reads ask for, so
// a reply on a thread about another lane's work carries that lane's stage.
const laneOf = body => typeof body.lane === 'string' && /^[a-z0-9][a-z0-9-]{0,62}$/.test(body.lane) ? body.lane : null;
const threadLanes = threads => [...new Set([...threads].map(th => laneOf(th.ev.body)).filter(Boolean))];

// The comment UI page reads and writes the spool of the page its shell relayed last (ui.dir), at its own service's
// address; every other page its own spool at its own.
const apiPath = path => COMMENT_UI ? new URL('api/' + path, SCRIPT_SERVICE).href : '/api/' + path;

function httpTransport() {
  const POST_TIMEOUT_MS = 10000; // a save that has not answered by then is waiting, not stored
  return {
    mode: 'http', label: EMBED_REVIEW_DIR ? 'review-serve (embedded)' : 'review-serve',
    ready: Promise.resolve(true),
    // `lanes`: the lanes whose stages it asks beside its own, its threads' (threads #stage-tag-source).
    async listEvents(lanes = []) {
      const asked = lanes.map(lane => '&lane=' + encodeURIComponent(lane)).join('');
      const r = await fetch(apiPath('events?dir=' + encodeURIComponent(COMMENT_UI ? ui.dir : REVIEW_DIR) + asked));
      if (!r.ok) throw new Error('review service answered ' + r.status); // a page it reviews no spool for: no event list
      return { events: eventList(await r.json()), wake: r.headers.get('X-Spec-Chat-Wake') || null, stages: stagesTold(r) };
    },
    // 'stored' (also a resend of the same bytes), 'refused' (400, 403, 409: validation no resend fixes);
    // throws on anything else, a hung save included, so the outbox keeps the event and sends it again (#offline-outbox).
    async postEvent(entry) {
      const abort = new AbortController();
      const timer = setTimeout(() => abort.abort(), POST_TIMEOUT_MS);
      let r;
      try {
        r = await fetch(apiPath('events?dir=' + encodeURIComponent(entry.dir || REVIEW_DIR) + '&actor=human&name=' + encodeURIComponent(entry.name)), { method: 'POST', body: JSON.stringify(entry.body), signal: abort.signal });
      } finally { clearTimeout(timer); }
      if (r.ok) return 'stored';
      if (r.status === 400 || r.status === 403 || r.status === 409) return 'refused';
      throw new Error('review service answered ' + r.status);
    },
    async specModified() {
      if (EMBED_REVIEW_DIR) return null; // no spec file behind a live app; watchSpec stays quiet
      const r = await fetch(OWN_ADDRESS.pathname, { method: 'HEAD' });
      return new Date(r.headers.get('Last-Modified') || 0).getTime();
    },
  };
}

function classifyAnchorSignatures(current, baseline) {
  const result = new Map();
  for (const [anchor, signature] of current) {
    result.set(anchor, baseline === null || baseline.get(anchor) !== signature ? 'changed' : 'unchanged');
  }
  return result;
}

function changedRootAnchors(parents, classification) {
  const result = new Set();
  for (const [anchor, parent] of parents) {
    if (classification.get(anchor) !== 'changed') continue;
    if (!parent || classification.get(parent) !== 'changed') result.add(anchor);
  }
  return result;
}

function ownAnchorSignature(element) {
  const clone = element.cloneNode(true);
  for (const child of clone.querySelectorAll('[data-anchor]')) child.remove();
  return clone.outerHTML;
}

function anchorSignatures(doc) {
  const result = new Map();
  for (const element of doc.querySelectorAll('[data-anchor]')) {
    result.set(element.dataset.anchor, ownAnchorSignature(element));
  }
  return result;
}

function shortCommit(id) {
  return id ? String(id).slice(0, 7) : 'unknown';
}

function commitDate(value) {
  const date = String(value || '').slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : '';
}

function rangeBarText(baseline) {
  const base = baseline && baseline.base;
  const head = baseline && baseline.head;
  const short = id => id ? String(id).slice(0, 7) : 'unknown';
  const date = value => {
    const text = String(value || '').slice(0, 10);
    return /^\d{4}-\d{2}-\d{2}$/.test(text) ? text : '';
  };
  const baseDate = date(baseline && baseline.baseDate);
  const headDate = date(baseline && baseline.headDate);
  const headLabel = (baseline && baseline.dirty ? 'working copy of ' : '') + short(head);
  return 'Changes from ' + short(base) + (baseDate ? ' ' + baseDate : '') + ' to ' + headLabel + (headDate ? ' ' + headDate : '');
}

function baselineParams(base) {
  const params = new URLSearchParams({ path: OWN_ADDRESS.pathname.replace(/^\//, '') });
  if (base) params.set('base', base);
  return params;
}

// The change type's two answers (jev-suggestions #change-type).
const JEV_TYPE_LABELS = {
  behavior: 'Behavior',
  'no-behavior-change': 'No behavior change',
};

function jevParams(base) {
  const params = new URLSearchParams({ path: OWN_ADDRESS.pathname.replace(/^\//, ''), base: String(base || '') });
  if (state.readingView) params.set('view', 'reading');
  return params;
}

async function fetchJev(base, signal) {
  const response = await fetch('/api/jev?' + jevParams(base), { signal });
  if (!response.ok) throw new Error('Jev unavailable');
  const result = await response.json();
  return {
    jev: result && result.jev === 'off' ? 'off' : 'on',
    items: Array.isArray(result && result.items) ? result.items.filter(item => item && typeof item === 'object').map(item => ({
      kind: String(item.kind || ''),
      id: String(item.id || ''),
      state: String(item.state || 'none'),
      label: item.label == null ? null : String(item.label),
      target: item.target == null ? null : String(item.target),
      record: item.record == null ? null : String(item.record),
      level: item.level == null ? null : String(item.level),
      side: item.side == null ? null : String(item.side),
      other: item.other == null ? null : String(item.other), // lane items name the other slug (#acceptance-cross-lane)
      word: item.word == null ? null : String(item.word),
      text: item.text == null ? null : String(item.text), // a rule's or candidate's criterion text, verbatim (project-rules #card)
      name: item.name == null ? null : String(item.name), // the name its mark gives (#card-parts)
      home: item.home == null ? null : String(item.home), // the rule's repo spec path and anchor, as Not for this spec names it
      served: item.served !== false, // whether the page's own lane serves the rule's home spec (project-rules #card-source)
      rule: jevRuleOf(item.rule), // the marked rule a draft-check mark cites (#card-variants)
      escalated: item.escalated === true,
    })) : [],
    levels: result ? result.levels : null, // the server's levels table (#markers-levels-source); markLevel reads it
  };
}

// A rule as every card shows it: served target, verbatim criterion text, its mark's name, home, and whether the page's
// own lane serves that home (project-rules #card).
function jevRuleOf(value) {
  if (!value || typeof value !== 'object' || !value.target || !value.text) return null;
  return { target: String(value.target), text: String(value.text), name: value.name == null ? null : String(value.name),
    home: value.home == null ? null : String(value.home), served: value.served !== false };
}

function jevItem(kind, id) {
  return state.jev.items.find(item => item.kind === kind && item.id === String(id)) || null;
}

// Jev's resolved-in-spirit hint, shown only while the thread is open; once resolved its own indicator replaces it.
function looksResolved(th) {
  const hint = jevItem('resolved', th.id);
  return Boolean(th.status !== 'resolved' && hint && hint.state === 'label' && hint.label === 'resolved in spirit');
}

function findAnchor(anchorId) {
  return [...document.querySelectorAll('[data-anchor]')].find(el => el.dataset.anchor === String(anchorId)) || null;
}

function clearJev() {
  state.jev.request += 1;
  state.jev.status = 'idle';
  state.jev.items = [];
  state.jev.levels = {};
  state.jev.base = null;
  scheduleJevPoll();
  renderJev();
  renderPanel();
  renderPins();
}

// Background answers (#fast-marks-background): while any item is pending and the page is shown, read the
// same base again in 2 s; a newer request, a base change, or hiding the page cancels it, and showing resumes it.
let jevPollTimer = 0;

function scheduleJevPoll() {
  clearTimeout(jevPollTimer);
  jevPollTimer = 0;
  if (state.jev.status !== 'on' || document.hidden || !state.jev.items.some(item => item.state === 'pending')) return;
  const request = state.jev.request;
  const base = state.jev.base;
  jevPollTimer = setTimeout(() => {
    jevPollTimer = 0;
    if (request === state.jev.request && base === state.jev.base && !document.hidden) requestJev(base, true);
  }, 2000);
}

document.addEventListener('visibilitychange', scheduleJevPoll);

// refresh: a re-read for pending items; it shows only what changed and keeps the page as is when it fails.
async function requestJev(base, refresh = false) {
  if (!['http:', 'https:'].includes(OWN_ADDRESS.protocol) || !base) return;
  const request = ++state.jev.request;
  if (!refresh) {
    state.jev.status = 'loading';
    state.jev.base = String(base);
    renderJev();
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 120000);
  try {
    const result = await fetchJev(base, controller.signal);
    if (request !== state.jev.request) return;
    const status = result.jev === 'off' ? 'off' : 'on';
    // An unchanged answer keeps the shown items, so each note shown is of an item the page holds.
    if (refresh && status === state.jev.status
      && JSON.stringify([result.items, result.levels]) === JSON.stringify([state.jev.items, state.jev.levels])) {
      scheduleJevPoll();
      return;
    }
    state.jev.status = status;
    state.jev.items = result.items;
    state.jev.levels = result.levels;
    scheduleJevPoll();
    renderJev();
    renderPanel();
    renderPins();
  } catch (_) {
    if (request !== state.jev.request) return;
    if (refresh) { scheduleJevPoll(); return; }
    state.jev.status = 'unavailable';
    state.jev.items = [];
    state.jev.levels = {};
    renderJev();
    renderPanel();
    renderPins();
  } finally {
    clearTimeout(timeout);
  }
}

// One evidence read per page, after it renders; any failure leaves the page without evidence notes.
async function requestEvidence() {
  try {
    const response = await fetch('/api/evidence?' + new URLSearchParams({ path: OWN_ADDRESS.pathname.replace(/^\//, '') }));
    const result = response.ok ? await response.json() : null;
    const criteria = result && result.criteria;
    if (!criteria || typeof criteria !== 'object' || Array.isArray(criteria)) return;
    state.evidence.levels = result.levels;
    state.evidence.criteria = criteria;
    renderJev();
  } catch (_) {}
}

/* ---------------- the spec's mockup set (mockups #chip, #accept) ----------------
 * An HTTP spec page asks the service for its set with each read of its events (#chip-source): each mockup's page,
 * screen, version, route, the spec anchors its parts and steps illustrate, and its open threads and open questions. The
 * set draws the See mockup chips and the Screens index (#screen-index), lists each mockup with open threads or questions
 * in the panel, holds Accept spec until they are settled (#accept-gate), and is what Accept spec names (#accept-set). A
 * mockup page asks for its spec's set, the same set in the same order, for Previous and Next alone (#next-screen). A
 * page that is no spec is answered that it has no set and asks no more; an unanswered read keeps the set last told.
 */
async function requestMockups() {
  if (!state.mockupAsk || state.mockupAsking) return;
  state.mockupAsking = true;
  try {
    const path = (COMMENT_UI ? ui.page.path : MOCKUP_SPEC ? new URL(mockupSpecAddress()).pathname : OWN_ADDRESS.pathname).replace(/^\//, '');
    const response = await fetch(apiPath('mockups?' + new URLSearchParams({ path })));
    if (response.status === 400 || response.status === 404) {
      state.mockupAsk = false;
      return showMockups([]);
    }
    const result = response.ok ? await response.json() : null;
    if (result && Array.isArray(result.mockups)) showMockups(result.mockups.filter(mockupEntry));
  } catch (_) { /* offline: the set last told stands */
  } finally {
    state.mockupAsking = false;
  }
}

const mockupEntry = m => Boolean(m && typeof m.page === 'string' && m.page && typeof m.screen === 'string'
  && m.illustrates && typeof m.illustrates === 'object' && !Array.isArray(m.illustrates));

function showMockups(mockups) {
  if (MOCKUP_SPEC) return renderPager(mockups); // a mockup page draws no chip, index, or gate of its set
  const changed = JSON.stringify(mockups) !== JSON.stringify(state.mockups);
  state.mockups = mockups;
  renderMockupChips();
  renderScreenIndex();
  if (changed) renderPanel();
}

// A mockup page's address from the spec page's: its path in the set from the spec's folder, the base this page's
// address names (mockups #before-after: a mockup page compares against its spec page's base), the step to open at
// (#walkthrough-address), and the part to arrive at.
function mockupHref(page, part, step) {
  const at = MOCKUP_SPEC ? new URL(mockupSpecAddress()) : contextAddress();
  const url = new URL(page, at);
  const base = at.searchParams.get('base');
  if (base) url.searchParams.set('base', base);
  if (step) url.searchParams.set('step', step);
  if (part) url.hash = part;
  return url.href;
}

// #accept-set: Accept spec names each mockup of the set, by its path from the spec's folder, and the version the service
// last told this page, the SHA-256 of the mockup text it serves.
function acceptedMockups(mockups) {
  return mockups && mockups.length ? { mockups: mockups.map(m => ({ path: decodeURIComponent(m.page), version: m.version })) } : {};
}

// #chip-rule: each anchored block a part or a step of a mockup illustrates shows one See mockup chip per mockup after its
// text, naming the screen and opening its page at the first part illustrating it, at the first step illustrating it
// (#walkthrough-address). Drawn again only when what they show changes.
function renderMockupChips() {
  const first = list => Array.isArray(list) && list.length ? String(list[0]) : null;
  const chips = (state.mockups || []).flatMap(m => {
    const steps = m.steps && typeof m.steps === 'object' && !Array.isArray(m.steps) ? m.steps : {};
    return [...new Set([...Object.keys(m.illustrates), ...Object.keys(steps)])]
      .map(anchor => ({ anchor, part: first(m.illustrates[anchor]), step: first(steps[anchor]) }))
      .filter(chip => chip.part || chip.step)
      .map(({ anchor, part, step }) => ({ anchor, screen: m.screen, href: mockupHref(m.page, part, step) }));
  });
  const drawn = JSON.stringify(chips);
  if (drawn === state.mockupChips) return;
  state.mockupChips = drawn;
  document.querySelectorAll('.hx-mockup-chip').forEach(chip => chip.remove());
  for (const { anchor, screen, href } of chips) {
    const holder = findAnchor(anchor);
    if (!holder) continue;
    const chip = document.createElement('a');
    chip.className = 'hx-mockup-chip';
    chip.href = href;
    chip.dataset.hxFor = anchor;
    chip.textContent = 'See mockup · ' + screen;
    chip.setAttribute('aria-label', 'See mockup: ' + screen);
    placeMockupChip(holder, chip);
  }
}

// After the block's text, in the flow, so it never covers it: a row's last cell, and beside a block that holds no
// inline element (a list, a table, a drawing).
function placeMockupChip(holder, chip) {
  if (holder.tagName === 'TR') return (holder.cells[holder.cells.length - 1] || holder).appendChild(chip);
  let outer = holder;
  while (outer.ownerSVGElement) outer = outer.ownerSVGElement;
  if (outer !== holder || /^(TABLE|THEAD|TBODY|TFOOT|UL|OL|DL|SVG|IMG|HR|INPUT|SELECT|TEXTAREA|CANVAS|IFRAME|VIDEO|AUDIO)$/i.test(holder.tagName)) return outer.after(chip);
  holder.appendChild(chip);
}

// #accept-gate: a mockup's open threads and open questions, each counted, as the review panel and the Screens index
// read them; '' with neither.
function mockupOpenCounts(m) {
  const counted = (n, word) => n + ' open ' + word + (n === 1 ? '' : 's');
  const threads = Number.isInteger(m.open) ? m.open ? counted(m.open, 'thread') : null : 'threads unreadable';
  const questions = Number.isInteger(m.questions) ? m.questions ? counted(m.questions, 'question') : null : 'questions unreadable';
  return [threads, questions].filter(Boolean).join(' · ');
}

// #accept-gate: each mockup holding an open thread or question, as a link to its page with its screen and each count.
function mockupOpenList(mockups) {
  const waiting = (mockups || []).filter(mockupOpenCounts);
  if (!waiting.length) return null;
  const list = document.createElement('ul');
  list.className = 'hx-mockup-open';
  list.setAttribute('aria-label', 'Mockups with open threads or questions');
  for (const m of waiting) {
    const link = list.appendChild(document.createElement('li')).appendChild(document.createElement('a'));
    link.href = mockupHref(m.page, null);
    if (COMMENT_UI) link.target = '_blank'; // the comment UI page never leaves its frame
    link.textContent = m.screen + ' · ' + mockupOpenCounts(m);
  }
  return list;
}

// #screen-index: under the spec's header, before its first section, Screens with their count, then one row per mockup
// of the set this page was last told, in its order, each row one link to its mockup page at the base this page compares
// (#screen-index-press): the set's status mark, the screen's name, its kind, its directions while its page shows them,
// its viewports, its open counts, and a chevron. Its kind is Prototype and its route, or Drawn mockup; an entry that
// tells no route names none, nor directions none, so a service of another version still indexes the set
// (#screen-index-rule). Drawn again only when what it shows changes (#screen-index-live). A page with no set, or none
// told, shows none; the comment UI page holds no spec to index.
function renderScreenIndex() {
  const rows = COMMENT_UI ? [] : (state.mockups || []).map(m => ({
    screen: m.screen, href: mockupHref(m.page, null), open: mockupOpenCounts(m),
    kind: typeof m.route === 'string' ? 'Prototype' : m.route === null ? 'Drawn mockup' : '',
    route: typeof m.route === 'string' ? m.route : '',
    directions: Number.isInteger(m.directions) && m.directions >= DIRECTIONS_SHOWN ? m.directions + ' directions' : '',
  }));
  const drawn = JSON.stringify(rows);
  if (drawn === state.screenIndex) return;
  state.screenIndex = drawn;
  const shown = document.getElementById('hx-screens');
  const header = document.querySelector('article.spec > header') || document.querySelector('header');
  const first = document.querySelector('[data-spec-section]');
  if (!rows.length || !(header || first)) return shown && keepReading(() => shown.remove());
  const index = document.createElement('nav');
  index.id = 'hx-screens';
  index.className = 'hx-screens';
  index.setAttribute('aria-label', 'Screens');
  const head = index.appendChild(document.createElement('p'));
  head.className = 'hx-screens-head';
  head.append('Screens ', Object.assign(document.createElement('span'), { className: 'hx-screens-count', textContent: String(rows.length) }));
  const list = index.appendChild(document.createElement('ul'));
  for (const row of rows) {
    const link = list.appendChild(document.createElement('li')).appendChild(document.createElement('a'));
    link.className = 'hx-screen';
    link.href = row.href;
    const part = (into, name, text) => Object.assign(into.appendChild(document.createElement('span')), { className: name }, text ? { textContent: text } : {});
    part(link, 'hx-screen-mark').innerHTML = row.open ? MARKS.attention : MARKS.neutral;
    part(link, 'hx-screen-name', row.screen);
    const meta = part(link, 'hx-screen-meta');
    const kind = row.kind ? part(meta, 'hx-screen-kind', row.kind) : null;
    if (row.route) kind.appendChild(document.createElement('code')).textContent = row.route;
    part(meta, 'hx-screen-views', 'Desktop and phone');
    if (row.directions) part(meta, 'hx-screen-dirs', row.directions);
    part(link, 'hx-screen-open' + (row.open ? ' hx-waiting' : ''), row.open || 'No open threads');
    part(link, 'hx-screen-go').innerHTML = ICONS['chevron-right'];
    link.setAttribute('aria-label', [row.screen, [row.kind, row.route].filter(Boolean).join(' '), row.directions, 'Desktop and phone', row.open || 'No open threads'].filter(Boolean).join(', '));
  }
  keepReading(() => shown ? shown.replaceWith(index) : header ? header.after(index) : first.before(index));
}

// The reader's place (currentPlace) through `change`: the block at the screen's top stays there as the page above it
// grows or shrinks, in a browser that anchors no scroll of its own too.
function keepReading(change) {
  const place = currentPlace();
  change();
  goToPlace(place);
}

function markIssueFocus(current, baseline) {
  const prior = baseline.html === null ? null : anchorSignatures(new DOMParser().parseFromString(baseline.html, 'text/html'));
  const classification = classifyAnchorSignatures(current, prior);
  const focused = [...document.querySelectorAll('[data-anchor]')];
  for (const element of focused) {
    delete element.dataset.hxFocus;
    delete element.dataset.hxFocusRoot;
    element.dataset.hxFocus = classification.get(element.dataset.anchor) || 'changed';
  }
  const parents = new Map(focused.map(element => [
    element.dataset.anchor,
    element.parentElement?.closest('[data-anchor]')?.dataset.anchor || null,
  ]));
  const changedRoots = changedRootAnchors(parents, classification);
  for (const element of focused) {
    if (changedRoots.has(element.dataset.anchor)) element.dataset.hxFocusRoot = 'changed';
  }
  document.body.classList.toggle('hx-focus-active', focused.some(element => element.dataset.hxFocus === 'changed'));
}

async function fetchBaseline(base, signal) {
  const response = await fetch('/api/baseline?' + baselineParams(base), { signal });
  if (!response.ok) throw new Error('Git baseline unavailable');
  return response.json();
}

async function applyIssueFocus() {
  if (EMBED_REVIEW_DIR || OWN_ADDRESS.protocol === 'file:') return;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const requestedBase = OWN_ADDRESS.searchParams.get('base');
    const baseline = await fetchBaseline(requestedBase, controller.signal);
    state.range.baseline = baseline;
    renderRangeBar(baseline);
    if (MOCKUP_SPEC) { await showMockupBase(baseline); return; } // a mockup offers Before, never Jev or focus
    requestJev(baseline.base);
    markIssueFocus(state.range.loaded, baseline);
  } catch (error) {
    const copy = document.getElementById('hx-range-copy');
    if (copy) copy.textContent = 'Compared range unavailable.';
    if (OWN_ADDRESS.searchParams.get('focus') === 'changes') showFocusError();
  } finally {
    clearTimeout(timeout);
  }
}

function showFocusError() {
  document.querySelectorAll('.hx-focus-error').forEach(el => el.remove());
  const notice = document.createElement('div');
  notice.className = 'hx-focus-error';
  notice.textContent = 'Issue focus unavailable. Showing the complete current spec.';
  document.body.appendChild(notice);
}

function setRangeError(message) {
  const error = document.getElementById('hx-range-error');
  if (!error) return;
  error.textContent = message || '';
  error.hidden = !message;
}

function renderRangePicker(baseline) {
  const list = document.getElementById('hx-range-commits');
  if (!list) return;
  list.replaceChildren();
  const commits = Array.isArray(baseline && baseline.commits) ? baseline.commits.slice(0, 20) : [];
  if (!commits.length) {
    const empty = document.createElement('p');
    empty.className = 'hx-range-empty';
    empty.textContent = 'No committed versions of this spec were found.';
    list.appendChild(empty);
    return;
  }
  const selected = baseline.base;
  for (const commit of commits) {
    if (!commit || !commit.id) continue;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'hx-range-commit';
    button.dataset.base = commit.id;
    button.title = commit.id;
    if (commit.id === selected) button.dataset.selected = 'true';
    const id = document.createElement('strong');
    id.textContent = shortCommit(commit.id);
    const date = document.createElement('time');
    date.textContent = commitDate(commit.date);
    const subject = document.createElement('span');
    subject.textContent = commit.subject || '';
    button.append(id, date, subject);
    button.addEventListener('click', () => selectRangeBase(commit.id));
    list.appendChild(button);
  }
}

function renderRangeBar(baseline) {
  const bar = document.getElementById('hx-range-bar');
  const copy = document.getElementById('hx-range-copy');
  if (!bar || !copy || !baseline) return;
  const base = baseline.base;
  const head = baseline.head;
  const baseId = document.createElement('span');
  baseId.className = 'hx-range-id';
  baseId.title = base || '';
  baseId.textContent = shortCommit(base);
  const headId = document.createElement('span');
  headId.className = 'hx-range-id';
  headId.title = head || '';
  headId.textContent = shortCommit(head);
  copy.setAttribute('aria-label', rangeBarText(baseline));
  copy.replaceChildren(document.createTextNode('Changes from '));
  copy.append(baseId);
  const baseDate = commitDate(baseline.baseDate);
  if (baseDate) copy.append(document.createTextNode(' ' + baseDate));
  copy.append(document.createTextNode(' to '));
  if (baseline.dirty) copy.append(document.createTextNode('working copy of '));
  copy.append(headId);
  const headDate = commitDate(baseline.headDate);
  if (headDate) copy.append(document.createTextNode(' ' + headDate));
  renderRangePicker(baseline);
}

function openRangePicker(open) {
  state.range.pickerOpen = Boolean(open);
  const picker = document.getElementById('hx-range-picker');
  const button = document.getElementById('hx-range-change');
  if (!picker || !button) return;
  picker.hidden = !state.range.pickerOpen;
  button.setAttribute('aria-expanded', String(state.range.pickerOpen));
  if (state.range.pickerOpen) setTimeout(() => document.getElementById('hx-range-input')?.focus(), 0);
}

// served-pages #served-rewrite: the page rewrites its address only while its own address is its location,
// fragment aside; served at another address it changes only what it shows, so a reload opens what the host served.
function rewriteAddress(edit) {
  const here = new URL(location.href);
  here.hash = '';
  const direct = here.href === OWN_ADDRESS.href;
  edit(OWN_ADDRESS.searchParams);
  if (direct) history.replaceState(null, '', OWN_ADDRESS.pathname + OWN_ADDRESS.search + location.hash);
}

async function selectRangeBase(value) {
  const requested = String(value || '').trim();
  if (!requested) return setRangeError('Enter a commit id.');
  if (requested.startsWith('-')) return setRangeError('Commit ids cannot begin with “-”.');
  if (state.range.loading) return;
  state.range.loading = true;
  setRangeError('');
  const apply = document.getElementById('hx-range-apply');
  if (apply) apply.disabled = true;
  clearJev();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const baseline = await fetchBaseline(requested, controller.signal);
    state.range.baseline = baseline;
    if (!MOCKUP_SPEC) markIssueFocus(state.range.loaded, baseline);
    renderRangeBar(baseline);
    // The address names the new base first: a mockup page's spec link takes the base from it (mockups #before-base).
    rewriteAddress(query => { query.set('focus', 'changes'); query.set('base', baseline.base || requested); });
    if (MOCKUP_SPEC) showMockupBase(baseline);
    else requestJev(baseline.base || requested);
    openRangePicker(false);
  } catch (error) {
    setRangeError(error.name === 'AbortError' ? 'Commit lookup timed out.' : 'That commit could not be resolved locally.');
  } finally {
    clearTimeout(timeout);
    state.range.loading = false;
    if (apply) apply.disabled = false;
  }
}

function mountRangeBar(line) {
  if (EMBED_REVIEW_DIR || !['http:', 'https:'].includes(OWN_ADDRESS.protocol) || document.getElementById('hx-range-bar')) return;
  const bar = document.createElement('section');
  bar.className = 'hx-range-bar';
  bar.id = 'hx-range-bar';
  bar.setAttribute('aria-label', 'Compared commit range');
  bar.innerHTML = '<div class="hx-range-row"><p id="hx-range-copy" class="hx-range-copy">Loading compared range…</p><button id="hx-range-change" class="hx-range-change" type="button" aria-expanded="false" aria-controls="hx-range-picker">Change base</button></div><div id="hx-range-picker" class="hx-range-picker" hidden><div id="hx-range-commits" class="hx-range-commits"></div><form id="hx-range-form" class="hx-range-form"><label for="hx-range-input">Paste a commit id</label><div><input id="hx-range-input" name="base" autocomplete="off" spellcheck="false"><button id="hx-range-apply" type="submit">Apply</button></div><p id="hx-range-error" class="hx-range-error" role="alert" hidden></p></form></div>';
  line.appendChild(bar);
  bar.querySelector('#hx-range-change').addEventListener('click', () => openRangePicker(!state.range.pickerOpen));
  bar.querySelector('#hx-range-form').addEventListener('submit', event => {
    event.preventDefault();
    selectRangeBase(bar.querySelector('#hx-range-input').value);
  });
  bar.addEventListener('keydown', event => {
    if (event.key === 'Escape') openRangePicker(false);
  });
}

/* ---------------- Jev coverage ----------------
 * Coverage stays in a separate block so other suggestion builders can add their
 * markers without changing the review transport or thread model.
 */
function coveragePair(item) {
  if (item && item.story !== undefined && item.criterion !== undefined) {
    return { story: item.story == null ? '' : String(item.story), criterion: item.criterion == null ? '' : String(item.criterion) };
  }
  const id = String(item && item.id || '');
  const split = id.indexOf('::');
  if (split >= 0 && (split > 0 || split < id.length - 2)) return { story: id.slice(0, split), criterion: id.slice(split + 2) };
  return null;
}

function coverageGapFlags(items) {
  const values = new Map();
  const ensure = (anchor, side) => {
    const key = side + ':' + anchor;
    if (!values.has(key)) values.set(key, { anchor, side, verifies: false, unavailable: false, pending: false });
    return values.get(key);
  };
  for (const item of Array.isArray(items) ? items : []) {
    if (!item || item.kind !== 'coverage') continue;
    const pair = coveragePair(item);
    if (!pair) continue;
    const story = ensure(pair.story, 'story');
    const criterion = ensure(pair.criterion, 'criterion');
    const verifies = item.state === 'label' && item.label === 'verifies';
    const unavailable = item.state === 'unavailable';
    const pending = item.state === 'pending';
    for (const value of [story, criterion]) {
      value.verifies ||= verifies;
      value.unavailable ||= unavailable;
      value.pending ||= pending;
    }
  }
  return [...values.values()].flatMap(value => {
    if (!value.anchor) return [];
    if (value.verifies || value.pending) return [];
    if (value.unavailable) return [{ anchor: value.anchor, side: value.side, state: 'unavailable', label: 'Jev unavailable' }];
    return [{ anchor: value.anchor, side: value.side, state: 'gap',
      label: value.side === 'story' ? 'No criterion covers this' : 'No story backs this' }];
  });
}

function coverageFlags(items) {
  return coverageGapFlags(items);
}

// Name the folder the user should grant: the first ancestor Chromium will accept
// (it blocklists the home/Documents/Desktop/Downloads roots themselves).
function suggestedGrant() {
  const segs = decodeURIComponent(OWN_ADDRESS.pathname).split('/').filter(Boolean);
  segs.pop();
  let i = 0;
  if ((segs[0] === 'Users' || segs[0] === 'home') && segs.length > 2) i = 2; // past /Users/<name>
  if (['Documents', 'Desktop', 'Downloads'].includes(segs[i])) i++;
  return segs[Math.min(i, Math.max(segs.length - 1, 0))] || 'the spec’s folder';
}

function fsaTransport() {
  let root = null; // directory handle of the folder containing the spec
  let pending = null; // persisted handle awaiting a gesture-borne write regrant
  let resumeInFlight = null;
  const idb = () => new Promise((res, rej) => {
    const q = indexedDB.open('spec-chat', 1);
    q.onupgradeneeded = () => q.result.createObjectStore('handles');
    q.onsuccess = () => res(q.result);
    q.onerror = () => rej(q.error);
  });
  const store = async (mode, fn) => {
    const db = await idb();
    return new Promise((res, rej) => {
      const tx = db.transaction('handles', mode);
      const rq = fn(tx.objectStore('handles'));
      rq.onsuccess = () => res(rq.result);
      rq.onerror = () => rej(rq.error);
    });
  };
  const t = {
    mode: 'fsa', label: 'local folder', connected: false,
    // Any ancestor of the spec works as a grant: the page knows its own absolute path, so
    // walk from the granted handle down the remaining segments. Deepest name-match first,
    // validated by the spec file actually being there. Returns the spec's dir or null.
    async _toSpecDir(h) {
      try { await h.getFileHandle(SPEC_FILE); return h; } catch {}
      if (!h.getDirectoryHandle) return null;
      const segs = decodeURIComponent(OWN_ADDRESS.pathname).split('/').filter(Boolean);
      segs.pop(); // the spec filename
      const walk = async at => {
        let d = h;
        try {
          for (const seg of segs.slice(at)) d = await d.getDirectoryHandle(seg);
          await d.getFileHandle(SPEC_FILE);
          return d;
        } catch { return null; }
      };
      const candidates = async eq => {
        const found = [];
        for (let i = 0; i < segs.length; i++) {
          if (eq(segs[i], h.name)) { const d = await walk(i + 1); if (d) found.push(d); }
        }
        return found;
      };
      // exact segment match first; APFS-casing fallback second. Accept only an unambiguous
      // result — two valid walks could bind the spool to the wrong same-named spec.
      let found = await candidates((a, b) => a === b);
      if (!found.length) found = await candidates((a, b) => a.toLowerCase() === b.toLowerCase());
      return found.length === 1 ? found[0] : null;
    },
    async _settle(h, specDir, alsoScope) { // persist a working grant
      root = specDir;
      pending = null;
      t.connected = true;
      try { await store('readwrite', s => s.put(specDir, OWN_ADDRESS.href)); } catch {}
      try { await store('readwrite', s => s.put(h, 'last-dir')); } catch {}
      if (alsoScope) try { await store('readwrite', s => s.put(h, 'scope-root')); } catch {}
    },
    async tryRestore() {
      try {
        let h = await store('readonly', s => s.get(OWN_ADDRESS.href));
        if (!h) h = await store('readonly', s => s.get('scope-root')); // broad grant covers new specs
        if (!h) return 'none';
        const p = await h.queryPermission({ mode: 'readwrite' });
        if (p === 'granted') {
          const d = await t._toSpecDir(h);
          if (!d) return 'none'; // moved/renamed since the grant
          await t._settle(h, d, false);
          return 'granted';
        }
        pending = h;
        return 'prompt'; // expired grant; UI offers direct regrant plus picker fallback
      } catch { return 'none'; }
    },
    async resume() { // user gesture required; keeps the already-selected folder
      if (t.connected) return true;
      if (!pending) return false;
      if (!resumeInFlight) {
        const candidate = pending;
        resumeInFlight = (async () => {
          try {
            if (await candidate.requestPermission({ mode: 'readwrite' }) !== 'granted') return;
            if (t.connected) return; // an explicit picker fallback won the race
            const d = await t._toSpecDir(candidate);
            if (d) await t._settle(candidate, d, false);
          } finally {
            resumeInFlight = null;
          }
        })();
      }
      await resumeInFlight;
      return t.connected;
    },
    async connect({ useLastDir = true } = {}) { // user gesture required
      // Some Chromium shells leave requestPermission() pending forever without surfacing
      // browser UI for a restored file:// handle. Explicit reconnect skips that path and
      // opens the directory picker while the button's activation is still live.
      if (t.connected) return;
      // The picker can't be pointed at a path, but ANY ancestor folder works (Documents,
      // home, the repos dir) — so wherever it opens, "Open" usually suffices. Steer it
      // anyway: id-scoped memory, startIn from the last grant, and the exact path on the
      // clipboard for the native panel's Go-to-Folder (⌘⇧G on macOS).
      const opts = { mode: 'readwrite', id: 'spec-chat' };
      if (useLastDir) try { const last = await store('readonly', s => s.get('last-dir')); if (last) opts.startIn = last; } catch {}
      const dir = decodeURIComponent(OWN_ADDRESS.pathname).replace(/\/[^/]*$/, '');
      // fire-and-forget: awaiting could burn the gesture's activation before the picker call
      try { navigator.clipboard.writeText(dir).then(() => toast(/Mac/.test(navigator.platform) ? 'Pick \u201c' + suggestedGrant() + '\u201d — or any folder above the spec. Exact path copied: \u2318\u21e7G + paste jumps there' : 'Pick \u201c' + suggestedGrant() + '\u201d or any folder above the spec (path copied)'), () => {}); } catch {}
      let picked;
      try { picked = await window.showDirectoryPicker(opts); }
      catch (e) {
        if (e && e.name === 'AbortError') throw e; // user cancelled
        delete opts.startIn; // stale/moved last-dir handle
        picked = await window.showDirectoryPicker(opts);
      }
      const d = await t._toSpecDir(picked);
      if (!d) throw new Error('that folder isn’t above this spec — pick a parent of ' + dir + ' (Chrome refuses top-level folders like Documents itself; a projects folder works)');
      await t._settle(picked, d, true);
      return 'connected';
    },
    async adopt(h) { // directory handle from drag-and-drop; write access needs an explicit ask
      if (t.connected) return 'ok';
      const d = await t._toSpecDir(h); // drop carries read access — locate first, then ask for write
      if (!d) return 'wrong';
      if (await h.requestPermission({ mode: 'readwrite' }) !== 'granted') return 'denied';
      await t._settle(h, d, true);
      return 'ok';
    },
    async _dir(actor, create) {
      const rev = await root.getDirectoryHandle(REVIEW_DIRNAME, { create: true });
      return rev.getDirectoryHandle(actor, { create: !!create });
    },
    async listEvents() {
      if (!t.connected) return [];
      const out = [];
      for (const actor of ['human', 'agent']) {
        let d;
        try { d = await t._dir(actor); } catch { continue; }
        for await (const [name, h] of d.entries()) {
          if (h.kind !== 'file') continue;
          try { out.push({ actor, name, body: JSON.parse(await (await h.getFile()).text()) }); } catch {}
        }
      }
      out.sort((a, b) => a.name < b.name ? -1 : 1);
      return out;
    },
    async postEvent(entry) {
      const d = await t._dir('human', true);
      const fh = await d.getFileHandle(entry.name, { create: true });
      const w = await fh.createWritable();
      await w.write(JSON.stringify(entry.body));
      await w.close();
      return 'stored';
    },
    async specModified() {
      if (!t.connected) return null;
      try { return (await (await root.getFileHandle(SPEC_FILE)).getFile()).lastModified; } catch { return null; }
    },
  };
  return t;
}

/* ---------------- islands ---------------- */
async function hydrateIslands() {
  const islands = [...document.querySelectorAll('script[type="application/spec+json"]')];
  if (!islands.length) return;
  // never load a second ECharts if the spec brought its own: two loads mean two instance
  // registries, and getInstanceByDom in ours would be blind to the spec's charts
  if (!window.echarts && islands.some(s => s.dataset.lib === 'echarts')) await loadScript(VENDOR.echarts);
  else if (window.echarts && window.echarts.version !== '5.5.1') console.warn('[spec-chat] page ECharts ' + window.echarts.version + ' differs from vendored 5.5.1; islands will use the page copy');
  for (const s of islands) {
    const target = s.parentElement.querySelector('[data-render-target]');
    if (!target) continue;
    let config;
    try { config = JSON.parse(s.textContent); } catch (e) { overlay('island', 'bad JSON in ' + holderOf(s)?.dataset.anchor); continue; }
    if (s.dataset.lib === 'echarts') {
      target.style.minHeight = target.style.minHeight || '300px';
      if (config.animation === undefined) config.animation = false; // deterministic renders: screenshots, diffs, headless review
      const chart = window.echarts.init(target);
      // clickable axes/labels for universal anchoring
      // multi-axis charts pass xAxis/yAxis as arrays — wrap each element, never Object.assign an array
      for (const ax of ['xAxis', 'yAxis']) if (config[ax]) {
        config[ax] = Array.isArray(config[ax])
          ? config[ax].map(a => Object.assign({ triggerEvent: true }, a))
          : Object.assign({ triggerEvent: true }, config[ax]);
      }
      // line series only emit clicks from their (tiny) symbols; the line body needs this flag
      if (config.series) {
        const wrap = s => s && s.type === 'line' ? Object.assign({ triggerLineEvent: true }, s) : s;
        config.series = Array.isArray(config.series) ? config.series.map(wrap) : wrap(config.series);
      }
      chart.setOption(config);
      const anchor = holderOf(s)?.dataset.anchor;
      state.charts.set(anchor, { anchor, chart, config, el: target });
      chart.on('click', params => onChartClick(anchor, params));
      wireChartCommentEvents(anchor, chart);
      const holderEl = holderOf(s);
      zrFallback(chart, () => {
        const peers = [...holderEl.querySelectorAll('[data-render-target]')];
        openComposer(anchor, { type: 'element', key: 'figure[' + (peers.indexOf(target) + 1) + ']' }, 'figure: chart');
      });
      // hover ring for canvas marks CSS can't reach (axis labels, ticks)
      chart.on('mouseover', p => {
        if (!state.commentMode || p.componentType === 'series') return; // series get emphasis borders
        let r = null;
        try {
          r = p.event.target.getBoundingRect().clone();
          if (p.event.target.transform) r.applyTransform(p.event.target.transform);
        } catch { return; }
        let ring = target.querySelector('.hx-ring') || target.appendChild(Object.assign(document.createElement('div'), { className: 'hx-ring' }));
        ring.style.cssText += ';left:' + (r.x - 4) + 'px;top:' + (r.y - 4) + 'px;width:' + (r.width + 8) + 'px;height:' + (r.height + 8) + 'px;display:block';
      });
      chart.on('mouseout', () => { const ring = target.querySelector('.hx-ring'); if (ring) ring.style.display = 'none'; });
      new ResizeObserver(() => { chart.resize(); renderPins(); renderThreadHighlight(); }).observe(target);
    }
  }
}
function loadScript(src) {
  return new Promise((res, rej) => {
    const el = document.createElement('script');
    el.src = src; el.onload = res; el.onerror = () => rej(new Error('failed to load ' + src));
    document.head.appendChild(el);
  });
}
const holderOf = el => el && el.closest('[data-anchor]');

// In comment mode a legend click means "comment on this series", not "toggle it":
// undo the toggle echarts already applied, then compose against the legend name.
function wireChartCommentEvents(chartKey, chart) {
  let undoing = false;
  chart.on('legendselectchanged', p => {
    if (!state.commentMode || undoing) return;
    undoing = true;
    try { chart.dispatchAction({ type: p.selected[p.name] ? 'legendUnSelect' : 'legendSelect', name: p.name }); } finally { undoing = false; }
    const info = state.charts.get(chartKey);
    openComposer((info && info.anchor) || chartKey, { type: 'legend', key: String(p.name) }, 'legend: ' + p.name);
  });
}

// A comment-mode canvas click nobody claims (blank space, gridlines, markAreas, silent
// marks) anchors to the figure itself — no click may feel dead. Claimed clicks open the
// composer synchronously, so "composer unchanged after a tick" means unclaimed.
function zrFallback(chart, openFig) {
  chart.getZr().on('click', ev => {
    if (!state.commentMode) return;
    if (!ev.target) { openFig(); return; }
    const before = state.composer;
    setTimeout(() => { if (state.commentMode && state.composer === before) openFig(); }, 60);
  });
}

/* ---------------- foreign charts ---------------- */
// Spec scripts may echarts.init() their own charts (no spec+json island). Adopt them so
// their marks get the same comment-mode targeting as island charts. Re-entrant: rescans
// refresh configs and drop disposed instances (spec scripts can dispose+recreate).
function adoptForeignCharts() {
  if (!window.echarts) return;
  for (const [k, info] of state.charts) {
    if (info.chart.isDisposed && info.chart.isDisposed()) state.charts.delete(k);
    else try { info.config = info.chart.getOption(); } catch {}
  }
  const known = new Set([...state.charts.values()].map(i => i.chart));
  for (const canvas of document.querySelectorAll('[data-anchor] canvas')) {
    let el = canvas.parentElement, chart = null;
    while (el && el !== document.body && !chart) { chart = window.echarts.getInstanceByDom(el); if (!chart) el = el.parentElement; }
    if (!chart || (chart.isDisposed && chart.isDisposed()) || known.has(chart)) continue;
    known.add(chart);
    const dom = chart.getDom();
    const holder = holderOf(dom);
    if (!holder) continue;
    const anchor = holder.dataset.anchor;
    const key = state.charts.has(anchor) ? anchor + '::' + (dom.id || chart.id) : anchor;
    // adopted charts get the same click flags islands get at hydrate; getOption() returns
    // normalized arrays, so a same-length positional merge is exact
    try {
      const opt0 = chart.getOption();
      const patch = {};
      if (Array.isArray(opt0.series) && opt0.series.some(s => s.type === 'line')) patch.series = opt0.series.map(s => s.type === 'line' ? { triggerLineEvent: true } : {});
      for (const ax of ['xAxis', 'yAxis']) if (Array.isArray(opt0[ax]) && opt0[ax].length) patch[ax] = opt0[ax].map(() => ({ triggerEvent: true }));
      if (Object.keys(patch).length) chart.setOption(patch);
    } catch {}
    state.charts.set(key, { anchor, chart, config: chart.getOption(), el: dom });
    chart.on('click', params => onChartClick(key, params));
    wireChartCommentEvents(key, chart);
    zrFallback(chart, () => {
      openComposer(anchor, dom.id ? { type: 'element', key: dom.tagName.toLowerCase() + '#' + dom.id } : null, 'figure: chart');
    });
  }
}

/* ---------------- anchoring cascade ---------------- */
function datumKey(params) { // grep-friendly even when name is empty (time axes, sankey edges)
  if (params.name != null && String(params.name).trim() !== '') return String(params.name);
  const d = params.data;
  if (d && d.source != null && d.target != null) return d.source + '>' + d.target;
  if (Array.isArray(params.value)) return String(params.value[0] ?? params.dataIndex);
  return String(params.value ?? params.dataIndex ?? params.seriesIndex ?? 'unknown');
}

function nearestDatum(info, seriesIndex, ev) { // line-body clicks carry no dataIndex — snap to the closest point
  try {
    const opt = info.config;
    const sList = Array.isArray(opt.series) ? opt.series : [opt.series];
    const s = sList[seriesIndex] || {};
    const data = s.data || [];
    if (!data.length) return null;
    const xv = info.chart.convertFromPixel({ seriesIndex }, [ev.offsetX, ev.offsetY])[0];
    const axes = Array.isArray(opt.xAxis) ? opt.xAxis : [opt.xAxis];
    const xa = axes[s.xAxisIndex || 0] || axes[0] || {};
    const rawX = d => Array.isArray(d) ? d[0] : (d && typeof d === 'object' && d.value !== undefined ? (Array.isArray(d.value) ? d.value[0] : d.value) : null);
    let idx;
    if (xa.data) idx = Math.max(0, Math.min(data.length - 1, Math.round(xv)));
    else {
      let best = Infinity; idx = 0;
      data.forEach((d, i) => { const v = rawX(d); if (v == null) return; const dist = Math.abs(v - xv); if (dist < best) { best = dist; idx = i; } });
    }
    const name = xa.data ? xa.data[idx] : rawX(data[idx]);
    return { dataIndex: idx, name: name != null ? String(name) : String(idx) };
  } catch { return null; }
}

function onChartClick(chartKey, params) {
  if (!state.commentMode) return;
  const info = state.charts.get(chartKey);
  const anchor = (info && info.anchor) || chartKey;
  let target, quote;
  if (params.componentType === 'series') {
    if (params.dataIndex == null && params.event && info) {
      const nd = nearestDatum(info, params.seriesIndex, params.event);
      if (nd) params = Object.assign({}, params, nd, { value: undefined });
    }
    const key = datumKey(params);
    target = { type: 'datum', key, seriesIndex: params.seriesIndex, dataIndex: params.dataIndex };
    if (chartKey !== anchor) target.chartKey = chartKey;
    quote = (params.seriesName || 'mark') + ': ' + key + (params.value != null ? ' · ' + (Array.isArray(params.value) ? params.value.join(', ') : params.value) : '');
  } else if (params.componentType === 'xAxis') {
    target = { type: 'axis-x', key: String(params.value) };
    quote = 'x-axis label: ' + params.value;
  } else if (params.componentType === 'yAxis') {
    target = { type: 'axis-y', key: String(params.value) };
    quote = 'y-axis tick: ' + params.value;
  } else if (/^mark(Line|Point|Area)$/.test(params.componentType)) {
    target = { type: 'target', key: String(params.value ?? params.name ?? '') };
    quote = params.componentType.replace('mark', 'mark ').toLowerCase() + ': ' + (params.value ?? params.name ?? '');
  } else return;
  openComposer(anchor, target, quote);
}

const TARGETABLE = 'h1, h2, h3, h4, h5, h6, p, li, ul, ol, table, tr, td, th, blockquote, pre, code, nav, figcaption, button, input, select, textarea, label, a, output, summary, video, [data-render-target]';
const cssId = id => window.CSS && window.CSS.escape ? window.CSS.escape(id) : id.replace(/([^a-zA-Z0-9_-])/g, '\\$1');

// The element a click on `el` marks within its block `holder`: the nearest targetable one, else the block itself. In a
// mockup frame every element is a target (mockups #review-targets): an image is its own, and an element inside no
// targetable one is itself; only the block's own empty spot marks the block, its region.
function markedElement(el, holder) {
  const every = MOCKUP_SPEC || protoHeld.on; // a prototype route under its mockup page too (mockups #prototype-route)
  const targetable = every ? TARGETABLE + ', img' : TARGETABLE;
  let node = el;
  while (node && node !== holder && !node.matches(targetable)) node = node.parentElement;
  return every && node === holder ? el : node;
}

// The runtime's own nodes inside the spec's content: pins, question marks, Jev markers, badges, See mockup chips, the
// Screens index, and video rails and boxes. No source holds them, so every reader of a block's text or elements skips them, and keys and quotes
// read as place.py reads the source.
const OVERLAY = '.hx-pin,.hx-qmark,.hx-jev-marker,.hx-badge,.hx-mockup-chip,.hx-screens,.hx-video-rail,.hx-video-box';
const UNREAD = OVERLAY + ',script,style';

// A block's elements of one selector as its source holds them: never the runtime's own.
const ownElements = (holder, sel) => [...holder.querySelectorAll(sel)].filter(el => !el.closest(OVERLAY));

// A node's text as its source reads, whitespace collapsed: without the runtime's own nodes, nor what `skip` names.
function sourceText(node, skip = UNREAD) {
  const parts = [];
  const walk = parent => {
    for (const child of parent.childNodes) {
      if (child.nodeType === 3) parts.push(child.nodeValue);
      else if (child.nodeType === 1 && !child.matches(skip)) walk(child);
    }
  };
  walk(node);
  return parts.join('').split(/\s+/).filter(Boolean).join(' ');
}

function elementDescriptor(el, holder) {
  if (el.closest && el.closest('svg')) return svgDescriptor(el, holder);
  const node = markedElement(el, holder);
  if (!node || node === holder) return null;
  const isFig = node.hasAttribute('data-render-target');
  const tag = node.tagName.toLowerCase();
  const quote = (sourceText(node) || (node.value || '').trim().replace(/\s+/g, ' ')).slice(0, 60);
  // ids are hand-authored and survive spec edits better than positional indexes
  if (!isFig && node.id && ownElements(holder, tag + '#' + cssId(node.id)).length === 1) {
    return { key: tag + '#' + node.id, quote };
  }
  const sel = isFig ? '[data-render-target]' : tag;
  const peers = ownElements(holder, sel);
  const name = isFig ? 'figure' : sel === 'h1' ? 'title' : sel === 'nav' ? 'breadcrumbs' : sel;
  return { key: name + '[' + (peers.indexOf(node) + 1) + ']', quote };
}

function svgDescriptor(el, holder) { // structural path key, e.g. 'svg[1]/g[2]/path[5]' or 'svg[1]/text#label'
  const svg = el.closest('svg');
  if (!svg || !holder.contains(svg)) return null;
  const seg = n => {
    const tag = n.tagName.toLowerCase();
    if (n.id) return tag + '#' + n.id;
    const peers = [...n.parentElement.children].filter(c => c.tagName === n.tagName);
    return tag + '[' + (peers.indexOf(n) + 1) + ']';
  };
  const svgs = [...holder.querySelectorAll('svg')];
  const parts = [svg.id ? 'svg#' + svg.id : 'svg[' + (svgs.indexOf(svg) + 1) + ']'];
  const chain = [];
  for (let n = el; n && n !== svg; n = n.parentElement) chain.unshift(n);
  for (const n of chain) parts.push(seg(n));
  const txt = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 60);
  return { key: parts.join('/'), quote: txt || 'svg ' + el.tagName.toLowerCase() };
}

function resolveElement(holder, key) { // 'p[2]' | 'button#cycle-play' | 'svg[1]/g[2]/path[5]' -> element, for pin positioning
  if (/^svg[#\[]/.test(key)) return resolveSvgPath(holder, key);
  let m = /^([a-z][a-z0-9-]*|title|breadcrumbs|figure)#(.+)$/.exec(key);
  if (m) {
    const sel = m[1] === 'title' ? 'h1' : m[1] === 'breadcrumbs' ? 'nav' : m[1] === 'figure' ? '[data-render-target]' : m[1];
    return ownElements(holder, sel + '#' + cssId(m[2]))[0] || null;
  }
  m = /^([a-z][a-z0-9-]*|title|breadcrumbs|figure)\[(\d+)\]$/.exec(key);
  if (!m) return null;
  const sel = m[1] === 'title' ? 'h1' : m[1] === 'breadcrumbs' ? 'nav' : m[1] === 'figure' ? '[data-render-target]' : m[1];
  return ownElements(holder, sel)[+m[2] - 1] || null;
}

function resolveSvgPath(holder, key) {
  let ctx = null;
  for (const [i, s] of key.split('/').entries()) {
    const m = /^([a-z][a-z0-9-]*)(?:#([^\s/#\[\]]+)|\[(\d+)\])$/.exec(s);
    if (!m) return null;
    const [, tag, id, idx] = m;
    if (i === 0) ctx = id ? holder.querySelector('svg#' + cssId(id)) : [...holder.querySelectorAll('svg')][+idx - 1];
    else if (id) ctx = [...ctx.children].find(c => c.id === id && c.tagName.toLowerCase() === tag);
    else ctx = [...ctx.children].filter(c => c.tagName.toLowerCase() === tag)[+idx - 1];
    if (!ctx) return null;
  }
  return ctx;
}

function onDocClick(e) {
  if (performance.now() < videos.swallow) { // the click that ends a box's drag, which commented already (threads #video-target)
    videos.swallow = 0;
    e.preventDefault();
    e.stopImmediatePropagation();
    return;
  }
  if (!state.commentMode) { if (!pressStep(e)) pressAnswer(e); return; }
  if (e.target.closest('.hx-pin,.hx-qmark,.hx-jev-marker,.hx-mockup-chip,.hx-screens,.hx-video-rail,.hx-jev-pop,.hx-panel,.hx-focused,.hx-toolbar,.hx-top-line,#hx-errors')) return;
  const holder = holderOf(e.target);
  if (!holder) return;
  if (e.target.tagName === 'CANVAS') return; // canvas clicks are the chart's business: marks via chart events, blanks via zrender
  // comment mode suspends the page: no link navigation, label toggling, or spec-script handlers
  e.preventDefault();
  e.stopImmediatePropagation();
  const video = markedVideo(e.target);
  if (video) return commentOnVideo(video, null); // threads #video-target: the moment it shows
  const sel = window.getSelection();
  const selTxt = sel ? String(sel).trim() : '';
  if (selTxt) {
    openComposer(holder.dataset.anchor, { type: 'text', key: selTxt.slice(0, 40) }, selTxt);
    sel.removeAllRanges();
  } else {
    const desc = elementDescriptor(e.target, holder);
    if (desc) openComposer(holder.dataset.anchor, { type: 'element', key: desc.key }, desc.quote);
    else openComposer(holder.dataset.anchor, null, null);
  }
  if (state.composer) Object.assign(state.composer, pageStep()); // mockups #step-threads-carry: the step its frames show
}

/* ---------------- answer buttons (prompt-first-shaping #answer-buttons) ----------------
 * A button with a non-empty data-spec-choice inside an anchored block. Its answer is an ordinary
 * comment through the one comment save; the chosen mark derives from the review state.
 */
const ANSWER = 'button[data-spec-choice]';
const answerButton = el => { // a button that is its own anchored block has no enclosing one: plain
  const button = el && el.closest && el.closest(ANSWER);
  const holder = button && button.dataset.specChoice.trim() && holderOf(button);
  return holder && holder !== button ? button : null;
};

// Each block's answer buttons by target key, and its chosen one: the one the newest comment targets
// where the service places it (placedMark), so the chosen mark sits where that comment's pin does.
function answerBlocks() {
  const blocks = new Map(); // anchorId -> { buttons: Map(key -> button), chosen }
  for (const button of [...document.querySelectorAll(ANSWER)].filter(answerButton)) {
    const holder = holderOf(button);
    if (!blocks.has(holder.dataset.anchor)) blocks.set(holder.dataset.anchor, { buttons: new Map(), chosen: null });
    blocks.get(holder.dataset.anchor).buttons.set(elementDescriptor(button, holder).key, button);
  }
  for (const th of state.threads.values()) { // fold order: the last is the newest comment
    const b = placedMark(th.ev), block = blocks.get(b.anchorId);
    if (block && b.target && b.target.type === 'element' && block.buttons.has(b.target.key)) block.chosen = block.buttons.get(b.target.key);
  }
  // A held page's chosen answers are the ones the shell's marks name, by their words (shell #shell-fields-marks).
  if (shell.held) for (const [anchor, block] of blocks) {
    block.chosen = [...block.buttons.values()].find(button => button.dataset.specChoice.trim() === shell.answers.get(anchor)) || null;
  }
  return blocks;
}

function renderChoices() {
  for (const { buttons, chosen } of answerBlocks().values()) for (const b of buttons.values()) b.setAttribute('aria-pressed', String(b === chosen));
  renderPicks(); // a mockup's directions derive the reviewer's pick the same way (mockups #directions-pick)
}

// Outside comment mode a press saves the answer on that button and opens nothing; the chosen
// answer pressed again saves nothing. Until the review state loads the chosen answer is unknown,
// so the press stays plain (a page that cannot comment never loads it).
function pressAnswer(e) {
  const button = answerButton(e.target);
  if (!button || !state.eventsRendered) return;
  e.preventDefault();
  answerWith(button);
}

// One answer, pressed on the page or in its question's bubble (threads #questions): the same comment on the same button.
// On the comment UI page an answer is the one its held page published (answerPublished); a held page's press is a pick
// that page saves (shell #shell-protocol).
function answerWith(button) {
  if (COMMENT_UI) return answerPublished(button);
  if (protoHeld.on) return; // a page held as a prototype is its product's: its answer buttons answer nothing
  const holder = holderOf(button);
  if (answerBlocks().get(holder.dataset.anchor).chosen === button) return;
  saveAnswer(holder, button, button.dataset.specChoice.trim(), pageStep());
}

// An answer saved on its block: a comment through the one comment save, its target the element answered with, its text
// the answer; a held page's a pick its shell saves. A mockup's Pick saves its direction's this way (mockups
// #directions-pick).
function saveAnswer(holder, el, choice, spot = {}) {
  const desc = elementDescriptor(el, holder);
  const target = { type: 'element', key: desc.key };
  if (shell.held) return pickSpot({ anchor: holder.dataset.anchor, target, quote: desc.quote, answer: choice, ...spot });
  saveMessage({ kind: 'comment', anchorId: holder.dataset.anchor, target, quote: desc.quote, ...spot }, choice);
}

/* ---------------- events -> threads ---------------- */
// Events sorted by file name; meaning follows references (#model-order): an event whose
// referent sorts later waits for it, so a skewed clock never drops or detaches a message.
function foldThreads(events) {
  // The statuses an event may name (event schema): an agent reply's, and the human's resolve. Any other value is no
  // status: an agent reply naming none acknowledges, a status event naming none changes nothing.
  const NAMED = ['acknowledged', 'orphaned', 'resolved'];
  const threads = new Map();
  const drafts = draftIds(events);
  const ids = new Set(events.map(e => e.body.id));
  const known = new Set();
  const parked = new Map();
  const messageThread = new Map();
  const messageSlot = new Map();
  const humanStatus = e => drafts.has(e.body.id) ? 'draft' : 'pending';
  const markDraft = (th, e) => { if (drafts.has(e.body.id)) th.draftBy.add(e.body.browser || ''); };
  const apply = e => {
    const b = e.body;
    if (b.event === 'comment' && e.actor === 'human') {
      const th = { id: b.id, ev: e, messages: [e], status: humanStatus(e), latestHumanId: b.id, history: new Map(), draftBy: new Set(), spot: commentSpot(b) };
      markDraft(th, e);
      threads.set(b.id, th);
      messageThread.set(b.id, b.id);
      messageSlot.set(b.id, { th, index: 0 });
      return;
    }
    if (b.event === 'reply') {
      const threadId = b.threadId || messageThread.get(b.respondsTo) || (threads.has(b.respondsTo) ? b.respondsTo : null);
      const th = threadId && threads.get(threadId);
      if (!th) return;
      const index = th.messages.push(e) - 1;
      messageThread.set(b.id, th.id);
      messageSlot.set(b.id, { th, index });
      if (e.actor === 'human') {
        th.latestHumanId = b.id;
        th.status = humanStatus(e);
        markDraft(th, e);
      } else if (b.respondsTo === th.latestHumanId) {
        th.status = NAMED.includes(b.status) ? b.status : 'acknowledged';
      }
      return;
    }
    if (b.event === 'edit' && e.actor === 'human') {
      const prior = messageSlot.get(b.supersedes);
      const threadId = b.threadId || messageThread.get(b.supersedes);
      const th = (prior && prior.th) || (threadId && threads.get(threadId));
      if (!th) return;
      const index = prior ? prior.index : th.messages.length;
      if (!th.history.has(index)) th.history.set(index, { original: th.messages[index] || e, edits: [] });
      th.history.get(index).edits.push(e);
      th.messages[index] = e; // th.ev stays the root comment: its version, target, and quote place the thread
      messageThread.set(b.id, th.id);
      messageSlot.set(b.id, { th, index });
      th.latestHumanId = b.id;
      th.status = humanStatus(e);
      markDraft(th, e);
      return;
    }
    if (b.event === 'status') {
      const threadId = b.threadId || messageThread.get(b.respondsTo) || (threads.has(b.respondsTo) ? b.respondsTo : null);
      const th = threadId && threads.get(threadId);
      if (th && NAMED.includes(b.status)) th.status = b.status;
    }
  };
  const visit = e => {
    apply(e);
    known.add(e.body.id);
    const waiting = parked.get(e.body.id);
    if (waiting) { parked.delete(e.body.id); waiting.forEach(visit); }
  };
  for (const e of events) {
    const b = e.body;
    const ref = b.event === 'edit' ? b.supersedes : ['reply', 'status'].includes(b.event) ? b.respondsTo : null;
    if (ref && ref !== b.id && !known.has(ref) && ids.has(ref)) {
      if (!parked.has(ref)) parked.set(ref, []);
      parked.get(ref).push(e);
    } else visit(e);
  }
  return threads;
}

// Human message ids no hand-off covers (review-state #live-handoff): a hand-off covers the ids
// it lists in `events`; one without `events` (older, or Accept spec) covers every draft before it.
function draftIds(events) {
  const listed = new Set();
  let legacy = '';
  for (const e of events) {
    if (e.actor !== 'human' || e.body.event !== 'handoff') continue;
    if (Array.isArray(e.body.events)) e.body.events.forEach(id => listed.add(id));
    else if (e.name > legacy) legacy = e.name;
  }
  const drafts = new Set();
  for (const e of events) {
    if (e.actor === 'human' && ['comment', 'reply', 'edit'].includes(e.body.event) && e.name > legacy && !listed.has(e.body.id)) drafts.add(e.body.id);
  }
  return drafts;
}

// review-state #model-fields-spot: a comment's step, moment (seconds), and box (left, top, width, height as fractions
// of the frame), each null unless well formed; a thread's spot is its root comment's. spool.py's comment_spot agrees.
function commentSpot(b) {
  const fraction = v => typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 1;
  const box = b.box !== null && typeof b.box === 'object' && !Array.isArray(b.box) && ['left', 'top', 'width', 'height'].every(k => fraction(b.box[k]))
    ? { left: b.box.left, top: b.box.top, width: b.box.width, height: b.box.height } : null;
  return {
    step: typeof b.step === 'string' && b.step ? b.step : null,
    moment: typeof b.moment === 'number' && Number.isFinite(b.moment) && b.moment >= 0 ? b.moment : null,
    box,
  };
}

// The spot fields a save carries: those its caller gives, well formed.
const spotFields = c => Object.fromEntries(Object.entries(commentSpot(c)).filter(([, v]) => v !== null));

// threads #box-picture: a picture a page made of a boxed spot, as a pick carries it, {type, data}: a PNG, JPEG, or WebP
// image's type and its bytes in base64, at most 1 MB (shell #shell-fields-pick); null for any other value. The
// comment carries it to the service, which keeps it beside the comment and stores the comment naming its file.
const PICTURE_TYPES = ['image/png', 'image/jpeg', 'image/webp'];
const PICTURE_BYTES = 1024 * 1024;
const PICTURE_FILE = /^[0-9a-f]{64}\.(png|jpg|webp)$/;
function pictureOf(value) {
  if (value === null || typeof value !== 'object' || !PICTURE_TYPES.includes(value.type) || typeof value.data !== 'string') return null;
  const data = value.data, pad = data.endsWith('==') ? 2 : data.endsWith('=') ? 1 : 0;
  if (!data || data.length % 4 || !/^[A-Za-z0-9+/]*={0,2}$/.test(data) || data.length / 4 * 3 - pad > PICTURE_BYTES) return null;
  return { type: value.type, data };
}

// This browser's drafts: what its Hand off lists. A draft from before browser ids goes with whichever page hands off next.
function handoffEvents(events, browser) {
  const drafts = draftIds(events);
  return events.filter(e => e.actor === 'human' && drafts.has(e.body.id) && (!e.body.browser || e.body.browser === browser)).map(e => e.body.id);
}

// Browsers sharing a fruit are numbered by their first event's file name (#model-author-shown).
const authorKey = b => b.browser || (b.author ? 'author:' + b.author : null);
function authorNames(events) {
  const shown = new Map();
  const count = new Map();
  for (const e of events) {
    const key = e.actor === 'human' && e.body.author && authorKey(e.body);
    if (!key || shown.has(key)) continue;
    const n = (count.get(e.body.author) || 0) + 1;
    count.set(e.body.author, n);
    shown.set(key, n === 1 ? e.body.author : e.body.author + ' ' + n);
  }
  return shown;
}

function messageAuthor(names, message) {
  if (message.actor !== 'human') return 'Agent';
  const key = authorKey(message.body);
  return (key && names.get(key)) || 'Reviewer';
}

// #model-author-assign: a random id and a fruit name, created once and kept in this browser. #model-fields: a page with
// no storage whose address passes a browser id (`address`) takes it as its browser; its name, until that browser's
// earlier events give one (earlierAuthor), is the one kept for that id, else one assigned.
const FRUITS = ['Mango', 'Papaya', 'Lychee', 'Guava', 'Rambutan', 'Pineapple', 'Passion Fruit', 'Dragon Fruit', 'Durian', 'Jackfruit', 'Mangosteen', 'Starfruit', 'Soursop', 'Coconut', 'Banana', 'Tamarind', 'Longan', 'Feijoa', 'Cherimoya', 'Sapodilla', 'Kiwano', 'Pitanga', 'Jabuticaba', 'Salak'];
const REVIEWER_KEY = 'spec-chat:reviewer';
function reviewerIdentity(storage, random, address) {
  const named = reviewer => address ? Object.assign(reviewer, { address: true }) : reviewer;
  try {
    const kept = JSON.parse(storage.getItem(REVIEWER_KEY) || 'null');
    if (kept && typeof kept.browser === 'string' && kept.browser && FRUITS.includes(kept.author) && (!address || kept.browser === address)) {
      return named({ browser: kept.browser, author: kept.author });
    }
  } catch (_) { /* no storage: a name for this page only */ }
  const browser = address || Array.from({ length: 20 }, () => Math.floor(random() * 36).toString(36)).join('');
  const fresh = { browser, author: FRUITS[Math.min(FRUITS.length - 1, Math.floor(random() * FRUITS.length))] };
  try { storage.setItem(REVIEWER_KEY, JSON.stringify(fresh)); } catch (_) { /* nothing to keep it in */ }
  return named(fresh);
}

// shell #shell-phone-id: the browser id a shell passes in the address fragment, as `#browser=<id>`.
function addressBrowser(fragment) {
  const id = new URLSearchParams(String(fragment || '').replace(/^#/, '')).get('browser');
  return id && /^[A-Za-z0-9_-]{1,64}$/.test(id) ? id : null;
}

// #model-author-assign: the name a browser's earlier events carry, from its first event by file name; null when none.
function earlierAuthor(events, browser) {
  const first = events.find(e => e.actor === 'human' && e.body.browser === browser && FRUITS.includes(e.body.author));
  return first ? first.body.author : null;
}

// #live-save: `<ns>-<event>-<id>.json`, chosen once before the first send; strictly increasing within a page.
function nextStamp(last, nowMs, random) {
  let stamp = BigInt(nowMs) * 1000000n + BigInt(Math.floor(random() * 1e6));
  if (last && stamp <= BigInt(last)) stamp = BigInt(last) + 1n;
  return String(stamp);
}

function eventName(body, stamp) {
  return stamp + '-' + body.event + '-' + body.id + '.json';
}

function offlineNotice(waiting) {
  return waiting ? 'Offline: ' + waiting + ' change' + (waiting === 1 ? '' : 's') + ' waiting' : '';
}

function threadReplyAction(thread) {
  const message = thread.messages[thread.messages.length - 1];
  if (!message || message.actor !== 'agent') return null;
  return { label: thread.status === 'resolved' ? 'Reply and reopen' : 'Reply', message };
}

function commentModeShortcut(e) {
  const target = e.target || {};
  return String(e.key || '').toLowerCase() === 'c'
    && !e.defaultPrevented && !e.repeat
    && !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey
    && !target.isContentEditable
    && !/^(textarea|input|select)$/i.test(target.tagName || '');
}

// threads #stream-context, #questions: the conversation's entries in reading order. Threads in the order their first
// messages were written, the fold's order; a thread on an open question's block is an answer, read under its question;
// a question with an answer sits where its first answer was written; unanswered questions close it, in page order.
function conversationEntries(threads, questions) {
  const asked = new Map(questions.map(question => [question.anchor, { kind: 'question', question, answers: [] }]));
  const entries = [];
  for (const thread of threads.values()) {
    const entry = asked.get(thread.ev.body.anchorId);
    if (!entry) entries.push({ kind: 'thread', thread });
    else if (entry.answers.push(thread) === 1) entries.push(entry);
  }
  return entries.concat([...asked.values()].filter(entry => !entry.answers.length));
}

// threads #receipts: a thread's status read as a receipt.
const RECEIPTS = { draft: 'Draft', pending: 'Sent', acknowledged: 'Seen', orphaned: 'Seen', resolved: 'Resolved' };
function receiptWord(status) {
  return RECEIPTS[status] || 'Sent';
}

// threads #unread: a thread whose newest message is the agent's, and that is not resolved, waits on the reviewer.
function waitsOnReviewer(thread) {
  if (typeof thread.waiting === 'boolean') return thread.waiting; // a held page's thread, as marks tell it
  const last = thread.messages[thread.messages.length - 1];
  return thread.status !== 'resolved' && Boolean(last) && last.actor !== 'human';
}

// threads #receipts: the receipt sits under the thread's newest message from a reviewer; -1 when it has none.
function newestReviewerIndex(thread) {
  for (let index = thread.messages.length - 1; index >= 0; index--) if (thread.messages[index].actor === 'human') return index;
  return -1;
}

// A text's first words, for a chip, a replies line, or a resolved line: up to its first clause's end, at most `count`
// words; a cut text ends in an ellipsis.
function firstWords(text, count = 7) {
  const words = String(text || '').split(/\s+/).filter(Boolean);
  const kept = [];
  for (const word of words) {
    kept.push(word);
    if (kept.length >= count || (kept.length >= 3 && /[,;:.!?]$/.test(word))) break;
  }
  const shown = kept.join(' ').replace(/[,;:.]$/, '');
  return kept.length < words.length ? shown.replace(/[!?]$/, '') + '…' : shown;
}

// threads #stream-head: the panel's head counts its threads and its open questions.
function conversationCount(threads, questions) {
  return threads + ' thread' + (threads === 1 ? '' : 's') + (questions ? ' · ' + questions + ' question' + (questions === 1 ? '' : 's') : '');
}

function threadDockEntries(threads) {
  return [...threads.values()].map((thread, index) => ({ thread, number: index + 1 })).reverse();
}

function acknowledgedReplyCount(threads) {
  return [...threads.values()].filter(thread => thread.status === 'acknowledged').length;
}

// With open TBDs the action is TBD open once settled; until then Next TBD sits beside Hand off.
// `browser` counts only that browser's draft threads: its Hand off hands off only its own drafts.
// `mockupOpen`, the open threads and questions of the spec's mockups (mockupOpen): the review settles only at 0 (mockups #accept-gate).
function reviewHandoffState(threads, openTbds = 0, browser = null, mockupOpen = 0) {
  const values = [...threads.values()];
  const drafts = values.filter(thread => browser ? thread.draftBy?.has(browser) || thread.draftBy?.has('') : thread.status === 'draft').length;
  const unsent = values.some(thread => thread.status === 'draft');
  const settled = !unsent && mockupOpen === 0 && values.every(thread => thread.status === 'resolved');
  const finish = !openTbds && settled;
  const tbd = openTbds > 0 && settled;
  return { drafts, openTbds, finish, tbd, nextTbd: openTbds > 0 && !tbd, enabled: drafts > 0 || finish || tbd };
}

// The open threads and open questions of a mockup set as the service tells it (#chip-source); null while the set is not
// yet known or a mockup's spool or file is unreadable, so Accept spec waits rather than accept what it cannot see.
function mockupOpen(mockups) {
  if (!mockups) return null;
  let open = 0;
  for (const mockup of mockups) {
    if (!Number.isInteger(mockup.open) || !Number.isInteger(mockup.questions)) return null;
    open += mockup.open + mockup.questions;
  }
  return open;
}

function tbdCountLabel(label, openTbds) {
  return openTbds >= 2 ? label + ' (' + openTbds + ')' : label;
}

function isOpenTbd(value) {
  return value !== 'later';
}

function openTbdMarkers(markers) {
  return [...markers].filter(el => isOpenTbd(el.getAttribute('data-spec-tbd')));
}

function nextOpenTbd(open, last) {
  if (!open.length) return null;
  return open[(open.indexOf(last) + 1) % open.length];
}

function tbdBlock(el) {
  return el.closest('[data-anchor]') || el;
}

function tbdHighlightBlocks(open) {
  return [...new Set(open.map(tbdBlock))];
}

function advanceTbd(st, open) {
  return st.lastTbd = nextOpenTbd(open, st.lastTbd);
}

// Open TBDs and the addressed anchor share one highlight; the addressed one keeps it while it is the address.
function renderHighlight() {
  const handoffState = reviewHandoffState(state.threads, openTbdCount(), state.reviewer?.browser, mockupOpen(state.mockups));
  if (COMMENT_UI) return handoffState; // the comment UI page draws on no page (shell #shell-review-actions)
  const openTbds = openTbdMarkers(document.querySelectorAll('[data-spec-tbd]'));
  const addressed = addressPlace();
  renderTbdHighlight(document, tbdHighlightBlocks(openTbds).concat(addressed ? [findAnchor(addressed.anchor)] : []));
  return handoffState;
}

// Arrival at an anchor (a conflict link on this or another spec, or Go to) scrolls to it and highlights it as an open TBD is.
function arriveAtAddress() {
  renderHighlight();
  const addressed = addressPlace();
  if (addressed) inView(findAnchor(addressed.anchor)).scrollIntoView({ block: 'center', behavior: 'smooth' });
}

function renderTbdHighlight(root, blocks) {
  const keep = new Set(blocks);
  root.querySelectorAll('.hx-tbd-open').forEach(el => { if (!keep.has(el)) el.classList.remove('hx-tbd-open'); });
  keep.forEach(el => el.classList.add('hx-tbd-open'));
}

function handoffObservation(events, nowMs) {
  let handoff = null;
  for (const event of events) if (event.actor === 'human' && event.body.event === 'handoff') handoff = event;
  if (!handoff) return null;
  if (events.some(event => event.actor === 'agent' && event.name > handoff.name)) return null;
  const createdAt = Date.parse(handoff.body.createdAt || '');
  return Number.isFinite(createdAt) && nowMs - createdAt >= 30000 ? 'queued' : 'waiting';
}

// wake is the X-Spec-Chat-Wake value: the host's wake state, and for failed "; " and its URI-encoded reason (host-wake #wake-states).
function handoffAgentText(observation, wake, last) {
  const cut = wake ? wake.indexOf('; ') : -1;
  const state = cut < 0 ? wake : wake.slice(0, cut);
  const reason = cut < 0 ? '' : decodeURIComponent(wake.slice(cut + 2));
  if (observation && state === 'failed') return '· wake failed' + (reason ? ': ' + reason : '') + '; send a new chat message to resume';
  if (observation === 'waiting' || (observation === 'queued' && (state === 'deferred' || state === 'retrying'))) return '· handed off, waiting for agent';
  if (observation === 'queued') return '· automatic wake did not occur; send a new chat message to resume';
  return last ? '· agent last event ' + new Date(last.body.createdAt).toLocaleTimeString() : '· no agent events yet';
}

function ingest(events, force = false) {
  let changed = force;
  for (const e of events) {
    if (state.seenNames.has(e.actor + '/' + e.name)) continue;
    state.seenNames.add(e.actor + '/' + e.name);
    state.events.push(e);
    changed = true;
  }
  if (!changed && state.eventsRendered) return;
  state.eventsRendered = true;
  if (changed) {
    state.events.sort((a, b) => a.name < b.name ? -1 : 1);
    state.threads = LAYER_SERVICE || uiHost() ? foldLayerThreads(state.events) : foldThreads(state.events);
    state.authors = authorNames(state.events);
    if (state.reviewer && state.reviewer.address) state.reviewer.author = earlierAuthor(state.events, state.reviewer.browser) || state.reviewer.author;
    if (state.focusThread && !state.threads.has(state.focusThread)) state.focusThread = null;
    for (const id of state.openReplies) if (!state.threads.has(id)) state.openReplies.delete(id);
  }
  if (LAYER_SERVICE) return renderLayer(changed);
  renderPanel();
  if (COMMENT_UI) return sendMarks(); // its held page draws them (#shell-msg-marks)
  renderPins();
  renderBadges();
  renderChoices();
}

function jevDisplayLabel(item) {
  if (!item) return '';
  if (item.state === 'unsure') return 'unsure';
  if (item.state === 'unavailable') return 'Jev unavailable';
  return item.label || '';
}

function corpusFlags(items) {
  const labels = { contradicts: 'Contradicts', overlaps: 'Overlaps', 'partial-overlap': 'Partial overlap', oversteps: 'Oversteps' };
  const seenUnavailable = new Set();
  const result = [];
  for (const item of Array.isArray(items) ? items : []) {
    if (!item || item.kind !== 'corpus' || !item.id) continue;
    const anchor = String(item.id);
    if (item.state === 'unavailable') {
      if (seenUnavailable.has(anchor)) continue;
      seenUnavailable.add(anchor);
      result.push({ anchor, state: 'unavailable', label: 'Jev unavailable', target: null });
      continue;
    }
    if (item.state !== 'label') continue;
    const label = labels[String(item.label || '').toLowerCase()];
    if (label) result.push({ anchor, state: 'label', label, level: item.level || null,
      target: item.target == null ? null : String(item.target), rule: item.rule || null, item });
  }
  return result;
}

function corpusTargetLink(target) {
  const value = String(target || '').trim();
  if (!value) return null;
  const hash = value.indexOf('#');
  const anchor = hash < 0 ? value : value.slice(hash + 1);
  if (!anchor) return null;
  const href = hash < 0 ? '#' + anchor : hash === 0 ? value :
    (value.slice(0, hash).startsWith('/') ? value.slice(0, hash) : '/' + value.slice(0, hash)) + '#' + anchor;
  return { text: hash < 0 ? '#' + anchor : value, href };
}

function goToJevTarget(target) {
  const value = String(target || '');
  const hash = value.indexOf('#');
  const path = hash < 0 ? '' : value.slice(0, hash);
  const anchor = hash < 0 ? value : value.slice(hash + 1);
  if (path && path !== OWN_ADDRESS.pathname.replace(/^\//, '')) {
    return followLink(new URL((path.startsWith('/') ? path : '/' + path) + (anchor ? '#' + anchor : ''), OWN_ADDRESS).href);
  }
  scrollToJevAnchor(anchor || value);
}

// served-pages #served-move: another page opens by following a link to its address, never by setting the location,
// so a host's link handling receives it as it does the reviewer's own taps.
function followLink(href) {
  const link = document.createElement('a');
  link.href = href;
  link.hidden = true;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

/* ---------------- Jev markers ----------------
 * Jev never changes spec layout: each noted anchor gets one margin marker, and
 * one shared popover lists that anchor's notes. A note source returns
 * { anchor, group, state, text, href, level, actions }; criterion evidence
 * adds a source and note buttons add actions, without touching placement.
 */
const JEV_NOTE_GROUPS = ['evidence', 'conflict', 'coverage', 'rule', 'type', 'neutral'];
const JEV_RECONCILE_LABELS = new Set(['Contradicts', 'Oversteps', 'Overstepped by']);
// Cross-lane labels (#cross-lane-finding); a pending lane item is an unanswered question and never a note.
const JEV_LANE_LABELS = { contradicts: 'Contradicts', oversteps: 'Oversteps', 'overstepped by': 'Overstepped by' };
const jevNoteSources = [jevSuggestionNotes, evidenceNotes];
const jevPopoverState = { element: null, marker: null, closeTimer: 0, wired: false };

// A derived mark's level from the human column of the server's levels table in one response state (#markers-levels-source);
// the runtime holds no mapping of its own.
function markLevel(source, kind) {
  const level = source && source.levels && source.levels[kind] && source.levels[kind].human;
  return typeof level === 'string' ? level : null;
}

function jevGitFocus() {
  return OWN_ADDRESS.searchParams.get('focus') === 'changes' || document.body.classList.contains('hx-focus-active');
}

// Note buttons only open the existing composer with fixed text; nothing is written until the reviewer sends.
function jevDraftAction(label, text) {
  return { label, run: note => { closeJevPopover(); openComposer(note.anchor, null, null, text); } };
}

// #batch: the one popover batch. entries are { note, line, warning } for one per-item button kind. With n listed
// (non-warning) entries, n >= 1 and all entries >= 2, the first note of each clause gets <label> (n), drafting
// <heading> then the listed lines in page order, and, when m warnings exist, a +m link drafting all of them.
function jevBatch(entries, label, heading) {
  const order = new Map([...document.querySelectorAll('[data-anchor]')].map((el, index) => [el.dataset.anchor, index]));
  entries.sort((a, b) => (order.get(a.note.anchor) ?? Infinity) - (order.get(b.note.anchor) ?? Infinity));
  const listed = entries.filter(entry => !entry.warning);
  const m = entries.length - listed.length;
  if (!listed.length || entries.length < 2) return;
  const draft = chosen => [heading, ...chosen.map(entry => entry.line)].join('\n');
  const shown = new Set();
  for (const { note } of entries) {
    if (shown.has(note.anchor)) continue;
    shown.add(note.anchor);
    note.actions.push(jevDraftAction(label + ' (' + listed.length + ')', draft(listed)));
    if (m) note.actions.push({ ...jevDraftAction('+' + m + (m === 1 ? ' warning' : ' warnings'), draft(entries)), link: true });
  }
}

// Jev unavailable names what Jev could not check in its hover sentence (jev-suggestions #neutral-questions); an unsure
// answer is settled by the LLM fallback in the seam, so the browser never shows one.
const JEV_QUESTIONS = {
  type: 'whether this changes behavior',
  criterion: 'which story this criterion verifies',
  story: 'which criterion verifies this story',
  corpus: 'whether this conflicts with another clause',
  audience: 'whether this is for readers or internals',
};
function jevNeutralNote(anchor, question) {
  return { anchor, group: 'neutral', state: 'unavailable', text: 'Jev unavailable', sentence: 'Jev could not check ' + JEV_QUESTIONS[question], level: null };
}

// project-rules #dismiss: one click that Jev is wrong about the item a card shows. Drops that item at once, ends any
// read in flight, records body through the Jev record route for this page, then re-reads /api/jev and shows what the
// server returns, the one source of what an answer removes: a recorded answer leaves out each note its ledger line
// keys, a refused or failed one brings the item back.
function jevRecordAction(label, item, body) {
  return { label, run: () => {
    const base = state.jev.base;
    state.jev.request += 1;
    state.jev.items = state.jev.items.filter(other => other !== item);
    renderJev();
    const reread = () => { if (base === state.jev.base) requestJev(base, true); };
    fetch('/api/jev/offer?' + new URLSearchParams({ path: OWN_ADDRESS.pathname.replace(/^\//, '') }),
      { method: 'POST', body: JSON.stringify(body) }).then(reread, reread);
  } };
}

// A rule's source: its home file name without .spec.html, a middle dot, and the clause anchor (#card).
function jevRuleSource(target) {
  const link = corpusTargetLink(target);
  if (!link) return null;
  const hash = link.text.indexOf('#');
  const file = hash > 0 ? link.text.slice(0, hash).split('/').pop() : '';
  const stem = file.replace(/\.spec\.html$/, '');
  return { href: link.href, link: link.text, file, text: (stem ? stem + ' · ' : '') + link.text.slice(Math.max(hash, 0)) };
}

// The rule as a draft names it, as the card's source does: the link's target when the page's lane serves the home
// spec, else its home spec and anchor (#card-source, #acceptance-ask).
function jevRuleCited(rule) {
  const source = jevRuleSource(rule.target);
  return rule.served === false && rule.home ? rule.home : source ? source.link : rule.target;
}

// The rule's plain-words name, as its mark gives it; home and anchor when a mark gives none (#card-parts).
function jevRuleTitle(rule) {
  const source = jevRuleSource(rule.target);
  return rule.name || (source ? source.text : '');
}

// Not for this spec (#dismiss): left of the primary on every rule card, one click, naming from the card's item the
// rule's home and the record the server gave the note; on a mark citing the rule, that flagged clause too
// (#card-cites-reject).
function jevNotForThisSpec(item) {
  const cited = item.kind === 'corpus';
  return jevRecordAction('Not for this spec', item,
    { dismiss: 'here', home: cited ? item.rule.home : item.home, record: item.record, ...(cited ? { anchor: item.id } : {}) });
}

// project-rules #card: every note about a project rule is one card, rendered the same wherever it shows. Label, the
// title, the full verbatim quote, the source link, then the reject left and the one primary rightmost after a
// hairline. A labelled group; buttons are real buttons in visual order.
function jevRuleCard(card, note = null) {
  const box = document.createElement('div');
  box.className = 'hx-rule-card';
  box.setAttribute('role', 'group');
  const title = card.title || jevRuleTitle(card.rule);
  box.setAttribute('aria-label', card.label + ': ' + title);
  box.appendChild(document.createElement('div')).className = 'hx-rule-card-label';
  box.lastElementChild.textContent = card.label;
  box.appendChild(document.createElement('div')).className = 'hx-rule-card-title';
  box.lastElementChild.textContent = title;
  box.appendChild(document.createElement('blockquote')).className = 'hx-rule-card-quote';
  box.lastElementChild.textContent = card.rule.text;
  const source = card.source === false ? null : jevRuleSource(card.rule.target);
  if (source && card.rule.served === false) {
    // #card-source: a home the page's lane does not serve, as for a rule only main holds, is the same words, no link.
    box.appendChild(document.createElement('span')).className = 'hx-rule-card-source-plain';
    box.lastElementChild.textContent = source.text;
  } else if (source) {
    const link = box.appendChild(document.createElement('a'));
    link.className = 'hx-rule-card-source';
    link.href = source.href;
    link.appendChild(document.createElement('span')).className = 'hx-rule-card-source-text';
    link.lastElementChild.textContent = source.text;
    const arrow = link.appendChild(document.createElement('span'));
    arrow.className = 'hx-rule-card-source-arrow';
    arrow.innerHTML = ICONS.external;
  }
  const actions = box.appendChild(document.createElement('div'));
  actions.className = 'hx-rule-card-actions';
  for (const action of card.actions) {
    const button = actions.appendChild(document.createElement('button'));
    button.type = 'button';
    button.className = action.primary ? 'hx-btn pri' : 'hx-rule-card-reject';
    button.textContent = action.label;
    button.addEventListener('click', event => { event.stopPropagation(); action.run(note, box); });
  }
  return box;
}

// A note holding one card: its text and sentence name the marker; the card is what the popover shows.
function jevCardNote(anchor, group, level, sentence, card) {
  return { anchor, group, state: 'label', level, sentence, text: card.label + ' ' + (card.title || jevRuleTitle(card.rule)), card };
}

// project-rules #marks, #card-variants: a marked rule the spec misses is the missed-rule card on the Acceptance
// criteria heading; a candidate on its own criterion is the candidate card, its anchor in place of a name; a check
// escalated to the LLM and still unanswered is a wheel in the marker's place; a failed LLM fallback is the neutral
// Jev unavailable note naming the rule (#q-fallback); anything else, a candidate elsewhere included, shows nothing.
function jevRuleNote(item) {
  if (!item.id || !item.text || !item.target) return null;
  const rule = { target: item.target, text: item.text, name: item.name, home: item.home, served: item.served };
  const candidate = item.kind === 'candidate';
  const title = candidate ? '#' + item.id : jevRuleTitle(rule);
  if (item.state === 'pending') {
    return item.escalated ? { anchor: item.id, group: 'rule', state: 'pending', text: 'checking ' + title + '…' } : null;
  }
  if (candidate) {
    if (item.state !== 'label') return null;
    // Mark as rule is a hand-off: a draft asking the criterion's owner to mark it (#card-mark-as-rule).
    const mark = { ...jevDraftAction('Mark as rule', 'Mark this criterion as a project rule with a short name.'), primary: true };
    return jevCardNote(item.id, 'rule', item.level || null, 'Jev reads this as a rule for every feature',
      { label: 'Possible project rule', title, rule, source: false,
        actions: [jevRecordAction('Not a project rule', item, { dismiss: 'rule', anchor: item.id }), mark] });
  }
  if (item.state !== 'unavailable' && (item.state !== 'label' || item.label !== 'missed')) return null;
  const source = jevRuleSource(item.target);
  if (!source) return null;
  if (item.state === 'unavailable') {
    return { anchor: item.id, group: 'neutral', state: 'unavailable', text: 'Jev unavailable', level: null,
      sentence: 'Jev could not check whether this spec needs the rule ' + title };
  }
  const ask = { ...jevDraftAction('Ask to cover', 'Cover ' + jevRuleCited(rule) + ' with a criterion, or add one line saying why it does not apply.'), primary: true };
  return jevCardNote(item.id, 'rule', item.level || null, 'This spec may need the rule ' + title + ' from ' + source.file,
    { label: 'Missing project rule', rule, actions: [jevNotForThisSpec(item), ask] });
}

function jevSuggestionNotes() {
  if (state.jev.status !== 'on') return [];
  const notes = [];
  const gaps = [];
  for (const item of state.jev.items) {
    const note = item.kind === 'rule' || item.kind === 'candidate' ? jevRuleNote(item) : null;
    if (note) notes.push(note);
  }
  for (const flag of coverageGapFlags(state.jev.items)) {
    if (flag.state !== 'gap') { notes.push(jevNeutralNote(flag.anchor, flag.side)); continue; }
    const note = { anchor: flag.anchor, group: 'coverage', state: 'label', text: flag.label,
      level: markLevel(state.jev, flag.side === 'story' ? 'no-criterion' : 'no-story'),
      actions: [flag.side === 'story'
        ? jevDraftAction('Ask for a criterion', 'Add an acceptance criterion that verifies this story.')
        : jevDraftAction('Ask for a story', 'Name or add the user story this criterion verifies.')] };
    gaps.push({ note, line: '#' + flag.anchor + ' ' + flag.label });
    notes.push(note);
  }
  jevBatch(gaps, 'Ask about all gaps', 'Add a criterion for each story, or name the story for each criterion:');
  if (state.readingView) {
    for (const item of state.jev.items) {
      if (item.kind === 'audience' && item.id && item.state === 'unavailable') notes.push(jevNeutralNote(item.id, 'audience'));
    }
    return notes;
  }
  const reconcile = [];
  // One conflict note; a reconcile link (draft) is the full target so the agent can open it (#note-reconcile).
  const conflict = (anchor, label, level, text, href, draftLink) => {
    const note = { anchor, group: 'conflict', state: 'label', text, href, level, actions: [] };
    if (draftLink && JEV_RECONCILE_LABELS.has(label)) {
      note.actions.push(jevDraftAction('Ask agent to reconcile', 'Reconcile this clause with ' + draftLink + '.'));
      reconcile.push({ note, line: '#' + anchor + ' ' + label + ' ' + draftLink, warning: level !== 'important' });
    }
    notes.push(note);
  };
  // Cross-lane marks compare against target main, not the Git focus base, so they show in either view (#cross-lane-finding).
  for (const item of state.jev.items) {
    const label = item && item.kind === 'lane' && item.state === 'label' && item.id ? JEV_LANE_LABELS[item.label] : null;
    const link = label ? corpusTargetLink(item.target) : null;
    if (!link) continue;
    conflict(String(item.id), label, item.level || null, label + ' ' + item.other + ' #' + link.href.slice(link.href.indexOf('#') + 1),
      link.href, link.text);
  }
  const gitFocus = jevGitFocus();
  for (const flag of gitFocus ? corpusFlags(state.jev.items) : []) {
    if (flag.state !== 'label') { notes.push(jevNeutralNote(flag.anchor, 'corpus')); continue; }
    if (flag.rule) {
      // project-rules #card-variants: a mark whose other clause is a marked rule is that rule's card, in its place.
      const label = flag.label + ' project rule';
      notes.push(jevCardNote(flag.anchor, 'conflict', flag.level, label + ' ' + jevRuleTitle(flag.rule), { label, rule: flag.rule,
        actions: [jevNotForThisSpec(flag.item),
          { ...jevDraftAction('Ask agent to reconcile', 'Reconcile this clause with ' + jevRuleCited(flag.rule) + '.'), primary: true }] }));
      continue;
    }
    const link = corpusTargetLink(flag.target);
    conflict(flag.anchor, flag.label, flag.level, flag.label + (link ? ' ' + link.text : ''), link ? link.href : null, link && link.text);
  }
  jevBatch(reconcile, 'Reconcile all', 'Reconcile each clause with its link:');
  if (!gitFocus) return notes;
  for (const item of state.jev.items) {
    if (item.kind !== 'type' || !item.id) continue;
    if (item.state === 'unavailable') notes.push(jevNeutralNote(item.id, 'type'));
    else if (item.state === 'label' && JEV_TYPE_LABELS[item.label]) {
      const text = JEV_TYPE_LABELS[item.label];
      notes.push({ anchor: item.id, group: 'type', state: 'label', text, level: null,
        actions: item.label === 'behavior' ? [jevDraftAction('Comment on this change', 'About this change: ')] : [] });
    }
  }
  return notes;
}

/* Criterion evidence (criterion-evidence spec): one note per acceptance criterion once evidence loads. */
// Every label names QA, so evidence never reads as a Jev note beside it (#chip-labels).
function evidenceLabel(entry) {
  if (!entry) return 'No QA yet';
  if (entry.uncommitted) return 'QA stale';
  const verdict = entry.verdict === 'fail' ? 'QA failed' : 'QA passed';
  if (entry.match) return verdict;
  return entry.judgment === 'cosmetic' ? verdict + ' \u00b7 reworded' : 'QA stale';
}

function evidenceAge(capturedAt, now = Date.now()) {
  const captured = Date.parse(capturedAt);
  if (Number.isNaN(captured)) return '';
  const minutes = Math.max(0, Math.floor((now - captured) / 60000));
  if (minutes < 60) return minutes + ' m';
  if (minutes < 1440) return Math.floor(minutes / 60) + ' h';
  return Math.floor(minutes / 1440) + ' d';
}

// The criterion as read: its own text, without nested anchors or runtime overlays, whitespace collapsed as the server reads it.
function evidenceReadText(element) {
  return sourceText(element, '[data-anchor],' + UNREAD);
}

// Word diff of the proven text against the text as read: [{ op: 'same' | 'del' | 'ins', text }].
function evidenceDiff(proven, read) {
  const a = String(proven).split(/\s+/).filter(Boolean);
  const b = String(read).split(/\s+/).filter(Boolean);
  const lcs = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
  }
  const parts = [];
  const push = (op, word) => {
    const last = parts[parts.length - 1];
    if (last && last.op === op) last.text += ' ' + word;
    else parts.push({ op, text: word });
  };
  let i = 0, j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) { push('same', a[i]); i++; j++; }
    else if (i < a.length && (j === b.length || lcs[i + 1][j] >= lcs[i][j + 1])) push('del', a[i++]);
    else push('ins', b[j++]);
  }
  return parts;
}

// Plugin bridge (host-bridge #plugin-bridge): IDs, never URLs, read from the service's bundle page address.
function evidenceBundleId(url) {
  try {
    const parts = new URL(url).pathname.split('/');
    const at = parts.lastIndexOf('bundles');
    return at >= 0 && parts[at + 1] ? decodeURIComponent(parts[at + 1]) : null;
  } catch (_) { return null; }
}

function evidenceCriterionKey(url) {
  try {
    const match = /^#criterion=(.+)$/.exec(new URL(url).hash);
    return match ? decodeURIComponent(match[1]) : null;
  } catch (_) { return null; }
}

/* ---------------- host bridge (host-bridge #plugin-bridge) ----------------
 * Self-contained (window, document, navigator only): review-serve inlines this exact block
 * into the review index, so spec pages and the index run one bridge (#bridge-tab-pages). */
// The parent frame's origin per step its hello offers; null keeps the browser's own behavior. `comments`, the shell's,
// is set by the page once it takes the shell's hello (shell #shell-roles-held).
const hostBridge = { evidence: null, tab: null, comments: null };

// Step 2 (#bridge-open, #bridge-intercept): true when sent, so the caller cancels the link; false keeps the link.
function evidenceOpen(bundle, criterion) {
  if (!bundle) return null;
  return () => {
    if (!hostBridge.evidence) return false;
    window.parent.postMessage(criterion ? { type: 'spec-chat-open-evidence', bundle, criterion } : { type: 'spec-chat-open-evidence', bundle }, hostBridge.evidence);
    return true;
  };
}

// The index's lane Proof link (#bridge-tab-pages): a link carrying data-evidence-bundle speaks step 2 with that bundle alone.
function hostEvidenceClick(event) {
  if (event.defaultPrevented) return;
  const link = event.target && event.target.closest && event.target.closest('a[data-evidence-bundle]');
  const open = link && evidenceOpen(link.getAttribute('data-evidence-bundle'));
  if (open && open()) event.preventDefault();
}

// #bridge-new-tab-click: Cmd+click on macOS, Ctrl+click elsewhere, or middle-click (auxclick button 1), same-origin link.
function hostTabClick(event) {
  if (!hostBridge.tab || event.defaultPrevented) return;
  const modifier = /Mac|iPhone|iPad/.test(navigator.platform) ? event.metaKey : event.ctrlKey;
  if (!(event.type === 'auxclick' ? event.button === 1 : event.button === 0 && modifier)) return;
  const link = event.target && event.target.closest && event.target.closest('a[href]');
  if (!link) return;
  let url;
  try { url = new URL(link.getAttribute('href'), document.baseURI); } catch (_) { return; }
  if (url.origin !== new URL(document.baseURI).origin) return; // the page's own address (served-pages #served-own-address)
  event.preventDefault();
  window.parent.postMessage({ type: 'spec-chat-open-tab', href: url.href }, hostBridge.tab);
}

// #bridge-hello-check: only the parent window at a real origin; "evidence" and "tab" each turn on only their own steps,
// and "comments" only the shell messages: `shell`, a page that can be held, takes the hello (`shell.hello(origin, data)`),
// which checks the origin is a named shell before it sets hostBridge.comments, and every later message from that
// parent at that origin (`shell.message(data)`) (shell #shell-protocol).
function listenHost(shell) {
  if (window.parent === window) return;
  window.addEventListener('message', event => {
    const data = event.data;
    if (event.source !== window.parent || !event.origin || event.origin === 'null') return;
    if (!data || typeof data !== 'object') return;
    if (shell && event.origin === hostBridge.comments && data.type !== 'spec-chat-host') return shell.message(data);
    if (data.type !== 'spec-chat-host' || !Array.isArray(data.opens)) return;
    if (data.opens.includes('evidence')) hostBridge.evidence = event.origin;
    if (shell && data.opens.includes('comments')) shell.hello(event.origin, data);
    if (!data.opens.includes('tab')) return;
    hostBridge.tab = event.origin;
    event.source.postMessage({ type: 'spec-chat-title', title: String(document.title) }, event.origin);
  });
  for (const type of ['click', 'auxclick']) document.addEventListener(type, hostTabClick, true);
  document.addEventListener('click', hostEvidenceClick);
}
/* ---------------- end host bridge ---------------- */

function evidenceNotes() {
  const criteria = state.evidence && state.evidence.criteria;
  if (!criteria) return [];
  const notes = [];
  const stale = [];
  for (const element of document.querySelectorAll('[data-acceptance-criterion]')) {
    const anchor = element.dataset.anchor;
    if (!anchor) continue;
    const entry = Object.prototype.hasOwnProperty.call(criteria, anchor) && criteria[anchor] && typeof criteria[anchor] === 'object' ? criteria[anchor] : null;
    const text = evidenceLabel(entry);
    const note = { anchor, group: 'evidence', state: text === 'No QA yet' ? 'none' : 'label', text,
      level: text === 'QA stale' ? markLevel(state.evidence, 'qa-stale') : text.startsWith('QA failed') ? markLevel(state.evidence, 'qa-failed') : null,
      passed: text.startsWith('QA passed') };
    if (entry) {
      const pr = Number.isInteger(entry.pr) ? '#' + entry.pr : '';
      const context = [pr, evidenceAge(entry.capturedAt), entry.onMain === false ? 'not on main' : ''].filter(Boolean).join(' · ');
      const view = typeof entry.view === 'string' ? entry.view : null;
      const bundle = typeof entry.bundle === 'string' ? entry.bundle : null;
      const bundleId = evidenceBundleId(view) || evidenceBundleId(bundle);
      Object.assign(note, { href: view, external: true, context, open: evidenceOpen(bundleId, evidenceCriterionKey(view)),
        link: bundle ? { text: 'bundle', href: bundle, open: evidenceOpen(bundleId, null) } : null });
      if (text !== 'QA passed' && text !== 'QA failed' && typeof entry.proven === 'string') note.diff = evidenceDiff(entry.proven, evidenceReadText(element));
      if (text === 'QA stale') {
        const date = commitDate(entry.capturedAt);
        const since = [pr, date].filter(Boolean).join(', ');
        const cited = since ? ' (' + since + ')' : '';
        note.actions = [jevDraftAction('Ask for re-proof', anchor + ' changed since its evidence' + cited + ': please recapture it.')];
        stale.push({ note, line: anchor + cited });
      }
    }
    notes.push(note);
  }
  jevBatch(stale, 'Re-proof all stale', 'Re-proof each criterion, changed since its evidence:');
  return notes;
}

function jevNotesByAnchor() {
  const byAnchor = new Map();
  for (const source of jevNoteSources) {
    for (const note of source() || []) {
      if (!note || !note.anchor || !note.text || !JEV_NOTE_GROUPS.includes(note.group)) continue;
      const anchor = String(note.anchor);
      const notes = byAnchor.get(anchor) || [];
      if (note.group === 'neutral' && notes.some(other => other.group === 'neutral' && other.sentence === note.sentence)) continue;
      notes.push(note);
      byAnchor.set(anchor, notes);
    }
  }
  for (const notes of byAnchor.values()) notes.sort((a, b) => JEV_NOTE_GROUPS.indexOf(a.group) - JEV_NOTE_GROUPS.indexOf(b.group));
  return byAnchor;
}

function mountJevMarker(holder, notes) {
  const marker = document.createElement('button');
  marker.type = 'button';
  marker.className = 'hx-jev-marker';
  const attention = notes.some(note => note.level === 'important');
  marker.dataset.attention = String(attention);
  const passed = !attention && notes.some(note => note.group === 'evidence' && note.passed);
  marker.dataset.passed = String(passed);
  // #markers-color: a light red dot when any note is Warning at the human level, below ! and the green check.
  marker.dataset.warning = String(!attention && !passed && notes.some(note => note.level === 'warning'));
  // Only escalated checks here: the marker is the pending wheel, its hover sentence its name (project-rules #pending).
  const pending = notes.every(note => note.state === 'pending');
  marker.dataset.pending = String(pending);
  marker.setAttribute('aria-label', (pending ? '' : 'Jev notes: ') + notes.map(note => note.sentence || note.text).join('; '));
  marker.setAttribute('aria-haspopup', 'dialog');
  marker.setAttribute('aria-expanded', 'false');
  marker.jevNotes = notes;
  marker.addEventListener('mouseenter', () => openJevPopover(marker));
  marker.addEventListener('mouseleave', scheduleJevPopoverClose);
  marker.addEventListener('focus', () => openJevPopover(marker));
  marker.addEventListener('blur', event => { if (!jevPopoverKeeps(event.relatedTarget)) closeJevPopover(); });
  marker.addEventListener('click', event => { event.preventDefault(); event.stopPropagation(); openJevPopover(marker); });
  // Rows cannot hold a positioned child; the marker sits inside the row's last cell (a cell, never a pin).
  (holder.tagName === 'TR' ? holder.cells[holder.cells.length - 1] || holder : holder).appendChild(marker);
  placeJevMarker(marker);
  wireJevPopover();
  return marker;
}

// Center the marker on the first rendered line of its anchor, without moving any text. It sits in
// the right margin, or just inside the column edge for rows and wherever the margin cannot hold it.
function placeJevMarker(marker) {
  const holder = marker.closest('[data-anchor]');
  const parent = marker.offsetParent;
  if (!holder || !parent || !document.createTreeWalker) return;
  marker.dataset.inset = String(holder.tagName === 'TR');
  if (marker.getBoundingClientRect().right > document.documentElement.clientWidth) marker.dataset.inset = 'true';
  const walker = document.createTreeWalker(holder, NodeFilter.SHOW_TEXT, {
    acceptNode: node => node.nodeValue.trim() && !node.parentElement.closest(UNREAD) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP,
  });
  const text = walker.nextNode();
  if (!text) return;
  const range = document.createRange();
  const start = text.nodeValue.search(/\S/);
  range.setStart(text, start);
  range.setEnd(text, start + 1);
  const line = range.getClientRects()[0];
  if (!line) return;
  const top = line.top + line.height / 2 - parent.getBoundingClientRect().top - parent.clientTop - marker.offsetHeight / 2;
  marker.style.top = Math.round(top) + 'px';
}

function jevPopoverKeeps(target) {
  const pop = jevPopoverState.element;
  return Boolean(target && (target === jevPopoverState.marker || (pop && pop.contains(target))));
}

function scheduleJevPopoverClose() {
  clearTimeout(jevPopoverState.closeTimer);
  jevPopoverState.closeTimer = setTimeout(() => {
    const pop = jevPopoverState.element;
    const marker = jevPopoverState.marker;
    if (!marker || marker.matches(':hover') || (pop && pop.matches(':hover'))) return;
    if (jevPopoverKeeps(document.activeElement)) return;
    closeJevPopover();
  }, 160);
}

function jevPopoverElement() {
  if (jevPopoverState.element) return jevPopoverState.element;
  const pop = document.createElement('div');
  pop.className = 'hx-jev-pop';
  pop.id = 'hx-jev-pop';
  pop.setAttribute('role', 'dialog');
  pop.setAttribute('aria-label', 'Jev notes');
  pop.hidden = true;
  pop.addEventListener('mouseenter', () => clearTimeout(jevPopoverState.closeTimer));
  pop.addEventListener('mouseleave', scheduleJevPopoverClose);
  pop.addEventListener('focusout', event => { if (!jevPopoverKeeps(event.relatedTarget) && !pop.matches(':hover')) closeJevPopover(); });
  document.body.appendChild(pop);
  jevPopoverState.element = pop;
  return pop;
}

function renderJevNote(note) {
  const row = document.createElement('li');
  row.className = 'hx-jev-pop-note';
  row.dataset.group = note.group;
  row.dataset.state = note.state || 'label';
  row.dataset.attention = String(note.level === 'important');
  if (note.card) {
    row.appendChild(jevRuleCard(note.card, note));
    return row;
  }
  const text = document.createElement(note.href ? 'a' : 'span');
  text.className = 'hx-jev-pop-text';
  if (note.href) text.href = note.href;
  if (note.href && note.external) { text.target = '_blank'; text.rel = 'noopener'; }
  if (note.href && note.open) text.addEventListener('click', event => { if (note.open()) event.preventDefault(); });
  text.textContent = note.text;
  row.appendChild(text);
  if (note.quote) {
    const quote = row.appendChild(document.createElement('span'));
    quote.className = 'hx-jev-pop-quote';
    quote.textContent = '\u201c' + note.quote + '\u201d';
  }
  // A neutral note's sentence is its accessible name and shows in the popover on hover or focus.
  if (note.sentence) {
    if (!note.href) {
      text.setAttribute('role', 'note');
      text.setAttribute('tabindex', '0');
    }
    text.setAttribute('aria-label', note.sentence);
    const sentence = row.appendChild(document.createElement('span'));
    sentence.className = 'hx-jev-pop-sentence';
    sentence.setAttribute('aria-hidden', 'true');
    sentence.textContent = note.sentence;
  }
  if (note.context || (note.link && note.link.href)) {
    const meta = document.createElement('span');
    meta.className = 'hx-jev-pop-meta';
    if (note.context) meta.appendChild(document.createElement('span')).textContent = note.context;
    if (note.link && note.link.href) {
      const link = meta.appendChild(document.createElement('a'));
      link.className = 'hx-jev-pop-link';
      link.href = note.link.href;
      if (note.external) { link.target = '_blank'; link.rel = 'noopener'; }
      link.textContent = note.link.text;
      if (note.link.open) link.addEventListener('click', event => { if (note.link.open()) event.preventDefault(); });
    }
    row.appendChild(meta);
  }
  if (Array.isArray(note.diff) && note.diff.length) {
    const legend = row.appendChild(document.createElement('div'));
    legend.className = 'hx-jev-pop-diff-legend';
    legend.innerHTML = '<del>removed</del> / <ins>added</ins>';
    const diff = row.appendChild(document.createElement('span'));
    diff.className = 'hx-jev-pop-diff';
    note.diff.forEach((part, index) => {
      if (index) diff.appendChild(document.createElement('span')).textContent = ' ';
      const word = diff.appendChild(document.createElement(part.op === 'del' ? 'del' : part.op === 'ins' ? 'ins' : 'span'));
      word.textContent = part.text;
    });
  }
  const actions = Array.isArray(note.actions) ? note.actions.filter(action => action && action.label) : [];
  if (actions.length) {
    const slot = document.createElement('div');
    slot.className = 'hx-jev-pop-actions';
    for (const action of actions) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = action.link ? 'hx-jev-pop-more' : 'hx-btn';
      button.textContent = action.label;
      button.addEventListener('click', event => { event.stopPropagation(); if (action.run) action.run(note); });
      // A link action sits on its button's line: both share one unwrapped group.
      const before = action.link && slot.lastElementChild;
      if (before && before.tagName === 'BUTTON') {
        const group = slot.appendChild(document.createElement('span'));
        group.className = 'hx-jev-pop-batch';
        group.append(before, button);
      } else slot.appendChild(button);
    }
    row.appendChild(slot);
  }
  return row;
}

function openJevPopover(marker) {
  clearTimeout(jevPopoverState.closeTimer);
  const pop = jevPopoverElement();
  if (jevPopoverState.marker !== marker) {
    if (jevPopoverState.marker) jevPopoverState.marker.setAttribute('aria-expanded', 'false');
    const list = document.createElement('ul');
    list.className = 'hx-jev-pop-notes';
    for (const note of marker.jevNotes || []) list.appendChild(renderJevNote(note));
    pop.replaceChildren(list);
    jevPopoverState.marker = marker;
  }
  marker.setAttribute('aria-expanded', 'true');
  marker.setAttribute('aria-controls', pop.id);
  pop.hidden = false;
  placeJevPopover();
}

function closeJevPopover() {
  clearTimeout(jevPopoverState.closeTimer);
  const pop = jevPopoverState.element;
  if (jevPopoverState.marker) jevPopoverState.marker.setAttribute('aria-expanded', 'false');
  jevPopoverState.marker = null;
  if (pop) {
    pop.hidden = true;
    pop.replaceChildren();
  }
}

// Desktop: beside the marker, below or above, never over the toolbar, dock, or open panel.
// Its box is measured with every note sentence shown and that room is kept. Above the marker it is anchored by
// its bottom edge, so the unhovered box hugs the marker and a hover sentence grows it upward into the kept room.
// Narrow screens: CSS makes it a sheet above the toolbar.
function placeJevPopover() {
  const pop = jevPopoverState.element;
  const marker = jevPopoverState.marker;
  if (!pop || !marker || pop.hidden) return;
  pop.style.left = '';
  pop.style.top = '';
  pop.style.bottom = '';
  pop.style.width = '';
  if (NARROW.matches) return;
  const anchor = marker.getBoundingClientRect();
  if (!marker.isConnected || anchor.bottom < 0 || anchor.top > innerHeight) { closeJevPopover(); return; }
  const controls = [...document.querySelectorAll('.hx-toolbar,.hx-thread-dock,.hx-panel.open,.hx-top-line,.hx-banner')]
    .filter(control => getComputedStyle(control).visibility !== 'hidden' && getComputedStyle(control).opacity !== '0')
    .map(control => control.getBoundingClientRect()).filter(rect => rect.width && rect.height);
  pop.dataset.measure = '';
  const { width, height } = pop.getBoundingClientRect();
  delete pop.dataset.measure;
  const panel = document.querySelector('.hx-panel.open');
  const rightLimit = innerWidth - 8 - (panel ? panel.getBoundingClientRect().width : 0);
  const left = Math.max(8, Math.min(anchor.right - width, rightLimit - width));
  const covers = top => controls.some(rect => left < rect.right && left + width > rect.left && top < rect.bottom && top + height > rect.top);
  const fits = top => top >= 8 && top + height <= innerHeight - 8;
  const candidates = [anchor.bottom + 8, anchor.top - 8 - height];
  const top = candidates.find(value => fits(value) && !covers(value)) ?? candidates.find(fits) ?? Math.max(8, candidates[0]);
  pop.style.left = Math.round(left) + 'px';
  if (top === candidates[1]) pop.style.bottom = Math.round(innerHeight - anchor.top + 8) + 'px';
  else pop.style.top = Math.round(top) + 'px';
  pop.style.width = width + 'px';
}

function wireJevPopover() {
  if (jevPopoverState.wired) return;
  jevPopoverState.wired = true;
  document.addEventListener('pointerdown', event => {
    if (jevPopoverState.marker && !jevPopoverKeeps(event.target)) closeJevPopover();
  }, true);
  // The popover lives at the end of body, so the keyboard path through it is bridged here:
  // Tab from the marker enters it, Tab past its last control leaves from the marker, and Escape
  // returns focus to the marker before closing so the marker's focus listener cannot reopen it.
  document.addEventListener('keydown', event => {
    const marker = jevPopoverState.marker;
    if (!marker) return;
    const pop = jevPopoverState.element;
    const active = document.activeElement;
    if (event.key === 'Escape') {
      if (pop.contains(active)) marker.focus({ preventScroll: true });
      closeJevPopover();
      return;
    }
    if (event.key !== 'Tab') return;
    const stops = pop.querySelectorAll('a,button');
    if (!stops.length) return;
    if (active === marker && !event.shiftKey) { event.preventDefault(); stops[0].focus(); }
    else if (active === stops[0] && event.shiftKey) { event.preventDefault(); marker.focus(); }
    else if (active === stops[stops.length - 1] && !event.shiftKey) marker.focus();
  });
  let frame = 0;
  document.addEventListener('scroll', () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(placeJevPopover);
  }, true);
  window.addEventListener('resize', placeJevPopover);
}

function renderJev() {
  closeJevPopover();
  document.querySelectorAll('.hx-jev-marker,.hx-jev-note').forEach(el => el.remove());
  document.querySelectorAll('[data-hx-audience]').forEach(el => delete el.dataset.hxAudience);
  document.querySelectorAll('[data-hx-jev-type]').forEach(el => delete el.dataset.hxJevType);
  if (EMBED_REVIEW_DIR) return;

  const pageNote = note => {
    const article = document.querySelector('article.spec');
    if (article) article.parentNode.insertBefore(note, article);
    else document.body.insertBefore(note, document.body.firstChild);
    return note;
  };
  if (state.jev.status === 'off' || state.jev.status === 'unavailable') {
    const note = document.createElement('p');
    note.className = 'hx-jev-note';
    note.textContent = state.jev.status === 'off' ? 'Jev off' : 'Jev unavailable';
    pageNote(note);
  }

  // Dims are the only in-text Jev display: reading view internals and Git focus No behavior change sections.
  const gitFocus = jevGitFocus();
  for (const item of state.jev.items) {
    if (!item.id || item.state !== 'label') continue;
    const holder = findAnchor(item.id);
    if (!holder) continue;
    if (state.readingView) {
      if (item.kind !== 'audience') continue;
      if (item.state === 'label' && item.label === 'internals') holder.dataset.hxAudience = 'internals';
    } else if (gitFocus && item.kind === 'type' && item.label === 'no-behavior-change') {
      holder.dataset.hxJevType = 'no-behavior-change';
    }
  }
  for (const [anchor, notes] of jevNotesByAnchor()) {
    const holder = findAnchor(anchor);
    if (holder) mountJevMarker(holder, notes);
  }
}

/* ---------------- UI ---------------- */
// Document presentation applies only to standalone spec pages: an embedding host app
// owns its own look, so embed mode ships the hx-* overlay CSS alone, and so does a mockup
// page, whose frames keep the mockup's own look (mockups #review-chrome).
/* design-set icons 241c10fe2754d1928d1cbbd9a9902c7c2673ba21 begin: the pinned set's icons/, built in by scripts/design-set-sync; never edit by hand */
const ICONS = {
  "chevron-down": "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"16\" height=\"16\" viewBox=\"0 0 16 16\" fill=\"none\" stroke-width=\"1.75\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\" stroke=\"currentColor\"><path d=\"M3.5 6L8 10.5 12.5 6\"/></svg>",
  "chevron-left": "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"16\" height=\"16\" viewBox=\"0 0 16 16\" fill=\"none\" stroke-width=\"1.75\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\" stroke=\"currentColor\"><path d=\"M10 3.5L5.5 8l4.5 4.5\"/></svg>",
  "chevron-right": "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"16\" height=\"16\" viewBox=\"0 0 16 16\" fill=\"none\" stroke-width=\"1.75\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\" stroke=\"currentColor\"><path d=\"M6 3.5l4.5 4.5L6 12.5\"/></svg>",
  "chevron-up": "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"16\" height=\"16\" viewBox=\"0 0 16 16\" fill=\"none\" stroke-width=\"1.75\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\" stroke=\"currentColor\"><path d=\"M3.5 10L8 5.5l4.5 4.5\"/></svg>",
  "close": "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"16\" height=\"16\" viewBox=\"0 0 16 16\" fill=\"none\" stroke-width=\"1.75\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\" stroke=\"currentColor\"><path d=\"M4 4l8 8M12 4l-8 8\"/></svg>",
  "comment": "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"16\" height=\"16\" viewBox=\"0 0 16 16\" fill=\"none\" stroke-width=\"1.75\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\" stroke=\"currentColor\"><path d=\"M4.5 2.5h7a2 2 0 0 1 2 2V9a2 2 0 0 1-2 2H8l-3 2.5V11h-.5a2 2 0 0 1-2-2V4.5a2 2 0 0 1 2-2z\"/></svg>",
  "external": "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"16\" height=\"16\" viewBox=\"0 0 16 16\" fill=\"none\" stroke-width=\"1.75\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\" stroke=\"currentColor\"><path d=\"M7 3.5H4A1.5 1.5 0 0 0 2.5 5v7A1.5 1.5 0 0 0 4 13.5h7a1.5 1.5 0 0 0 1.5-1.5V9\"/><path d=\"M9.5 2.5h4v4M13.5 2.5l-6 6\"/></svg>",
  "gear": "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"16\" height=\"16\" viewBox=\"0 0 16 16\" fill=\"none\" stroke-width=\"1.75\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\" stroke=\"currentColor\"><path d=\"M6.34 3.18L6.6 1.4L9.4 1.4L9.66 3.18L11.35 4.15L13.02 3.48L14.42 5.91L13.01 7.03L13.01 8.97L14.42 10.09L13.02 12.52L11.35 11.85L9.66 12.82L9.4 14.6L6.6 14.6L6.34 12.82L4.65 11.85L2.98 12.52L1.58 10.09L2.99 8.97L2.99 7.03L1.58 5.91L2.98 3.48L4.65 4.15z\"/><circle cx=\"8\" cy=\"8\" r=\"2\"/></svg>",
  "keys": "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"16\" height=\"16\" viewBox=\"0 0 16 16\" fill=\"none\" stroke-width=\"1.75\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\" stroke=\"currentColor\"><rect x=\"1.5\" y=\"4\" width=\"13\" height=\"8\" rx=\"1.5\"/><path d=\"M5 7h.01M8 7h.01M11 7h.01M5.5 9.5h5\"/></svg>",
  "not-answering": "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"16\" height=\"16\" viewBox=\"0 0 16 16\" fill=\"none\" stroke-width=\"1.75\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\" stroke=\"currentColor\"><path d=\"M2.5 7.4a8 8 0 0 1 11 0M4.8 9.9a4.6 4.6 0 0 1 6.4 0\"/><path d=\"M8 12.6h.01\"/><path d=\"M2.5 2.5l11 11\"/></svg>",
  "panel": "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"16\" height=\"16\" viewBox=\"0 0 16 16\" fill=\"none\" stroke-width=\"1.75\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\" stroke=\"currentColor\"><rect x=\"2\" y=\"2.5\" width=\"12\" height=\"11\" rx=\"2\"/><path d=\"M10 2.5v11\"/></svg>",
  "retry": "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"16\" height=\"16\" viewBox=\"0 0 16 16\" fill=\"none\" stroke-width=\"1.75\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\" stroke=\"currentColor\"><path d=\"M13.5 8a5.5 5.5 0 1 1-5.5-5.5c1.54 0 3.01.61 4.12 1.67L13.5 5.5\"/><path d=\"M13.5 2.5v3h-3\"/></svg>",
  "threads": "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"16\" height=\"16\" viewBox=\"0 0 16 16\" fill=\"none\" stroke-width=\"1.75\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\" stroke=\"currentColor\"><path d=\"M3.5 6h5A1.5 1.5 0 0 1 10 7.5v3A1.5 1.5 0 0 1 8.5 12H6.5L4 14v-2h-.5A1.5 1.5 0 0 1 2 10.5v-3A1.5 1.5 0 0 1 3.5 6z\"/><path d=\"M6 6V3.5A1.5 1.5 0 0 1 7.5 2h5A1.5 1.5 0 0 1 14 3.5v3A1.5 1.5 0 0 1 12.5 8H12v2l-2-1.5\"/></svg>",
};
/* design-set icons end */
/* design-set marks 241c10fe2754d1928d1cbbd9a9902c7c2673ba21 begin: the pinned set's marks/, built in by scripts/design-set-sync; never edit by hand */
const MARKS = {
  "attention": "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"16\" height=\"16\" viewBox=\"0 0 16 16\" fill=\"none\" stroke-width=\"1.75\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><circle cx=\"8\" cy=\"8\" r=\"6.5\" stroke=\"var(--ui-attention, currentColor)\"/><path d=\"M8 4.8v3.6\" stroke=\"var(--ui-attention, currentColor)\"/><circle cx=\"8\" cy=\"11\" r=\"1\" fill=\"var(--ui-attention, currentColor)\"/></svg>",
  "fail": "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"16\" height=\"16\" viewBox=\"0 0 16 16\" fill=\"none\" stroke-width=\"1.75\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><circle cx=\"8\" cy=\"8\" r=\"6.5\" stroke=\"var(--ui-fail, currentColor)\"/><path d=\"M5.8 5.8l4.4 4.4M10.2 5.8l-4.4 4.4\" stroke=\"var(--ui-fail, currentColor)\"/></svg>",
  "neutral": "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"16\" height=\"16\" viewBox=\"0 0 16 16\" fill=\"none\" stroke-width=\"1.75\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><circle cx=\"8\" cy=\"8\" r=\"6.5\" stroke=\"var(--ui-muted, currentColor)\"/></svg>",
  "pass": "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"16\" height=\"16\" viewBox=\"0 0 16 16\" fill=\"none\" stroke-width=\"1.75\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><circle cx=\"8\" cy=\"8\" r=\"7\" fill=\"var(--ui-pass, currentColor)\"/><path d=\"M5 8.3l2 2 4-4.6\" stroke=\"var(--ui-page, Canvas)\"/></svg>",
  "running": "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"16\" height=\"16\" viewBox=\"0 0 16 16\" fill=\"none\" stroke-width=\"1.75\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><circle cx=\"8\" cy=\"8\" r=\"6.5\" stroke=\"var(--ui-action, currentColor)\"/><path d=\"M8 3.5a4.5 4.5 0 0 1 0 9z\" fill=\"var(--ui-action, currentColor)\"/></svg>",
};
/* design-set marks end */
// The set's Find field: the review index builds it in from here, never a copy (review-index #find-seam); no spec page
// draws it.
/* design-set find 241c10fe2754d1928d1cbbd9a9902c7c2673ba21 begin: the pinned set's find/, built in by scripts/design-set-sync; never edit by hand */
const FIND = {
  "find.css": "/* design-set Find: the field, its no-match line, and what it hides. Needs tokens.css first.\n   The field is 44px tall and its label tappable across that height: a full-size touch target at every width. */\n.ui-find { display: flex; align-items: center; gap: var(--ui-space-3); min-width: 0; margin: 0; }\n.ui-find label { flex: none; align-self: stretch; display: flex; align-items: center; font-weight: var(--ui-weight-semibold); }\n.ui-find input { flex: 1; box-sizing: border-box; min-width: 0; min-height: calc(var(--ui-space-6) + var(--ui-space-3)); padding: var(--ui-space-1) var(--ui-space-3); border: 1px solid var(--ui-border); border-radius: var(--ui-radius-sm); background: var(--ui-surface); color: var(--ui-ink); font: inherit; font-size: var(--ui-text-sm); }\n.ui-find input::placeholder { color: var(--ui-muted); opacity: 1; }\n.ui-find input:focus-visible { outline: var(--ui-focus-width) solid var(--ui-focus); outline-offset: var(--ui-focus-offset); }\n.ui-find-none { margin: 0; padding: var(--ui-space-3) var(--ui-space-1); color: var(--ui-muted); }\n[data-find][hidden], [data-find-group][hidden], [data-find-empty][hidden], .ui-find-none[hidden] { display: none !important; }\n",
  "find.html": "<p class=\"ui-find\"><label for=\"ui-find\">Find</label><input id=\"ui-find\" type=\"search\" autocomplete=\"off\" spellcheck=\"false\" placeholder=\"{placeholder}\"></p>\n<p class=\"ui-find-none\" hidden>{none}</p>\n",
  "find.js": "/* design-set Find: filters what the page already holds as the field is typed in, case aside. It fetches nothing and\n   stores nothing. The page marks what it filters (README, Find); load this after it, at the end of the body. */\n(() => {\n  const field = document.querySelector(\".ui-find input\");\n  if (!field) return;\n  const marked = (selector) => [...document.querySelectorAll(selector)];\n  // The nearest group holding an element; null is the page itself.\n  const owner = (el) => el.parentElement?.closest(\"[data-find-group]\") ?? null;\n  const words = new Map(marked(\"[data-find]\").map((item) => [item, item.dataset.find.toLowerCase().split(\"\\n\")]));\n  const groups = marked(\"[data-find-group]\");\n  const empties = marked(\"[data-find-empty]\");\n  const withEmpty = new Set(empties.map(owner));\n  const counts = marked(\"[data-find-count]\");\n  const disclosures = marked(\"details\").filter((details) => details.querySelector(\"[data-find]\"));\n  const none = document.querySelector(\".ui-find-none\");\n  const touched = [...words.keys(), ...groups, ...empties, ...(none ? [none] : [])];\n  let before = null; // the page as it stood before typing, put back once the field is empty\n\n  const apply = () => {\n    const typed = field.value.trim().toLowerCase();\n    if (!typed) {\n      if (!before) return;\n      for (const [el, hidden] of before.hidden) el.hidden = hidden;\n      for (const [count, text] of before.text) count.textContent = text;\n      for (const [details, open] of before.open) details.open = open;\n      before = null;\n      return;\n    }\n    if (!before) before = {\n      hidden: new Map(touched.map((el) => [el, el.hidden])),\n      text: new Map(counts.map((count) => [count, count.textContent])),\n      open: new Map(disclosures.map((details) => [details, details.open])),\n    };\n    // Per group, null the page: how many shown items it holds, and how many of its own groups hold one.\n    const items = new Map();\n    const held = new Map();\n    const add = (map, key) => map.set(key, (map.get(key) || 0) + 1);\n    for (const [item, list] of words) {\n      item.hidden = !list.some((word) => word.includes(typed));\n      if (item.hidden) continue;\n      for (let group = owner(item); ; group = owner(group)) {\n        add(items, group);\n        if (!group) break;\n      }\n    }\n    for (const group of groups) {\n      group.hidden = !items.has(group) && !withEmpty.has(group);\n      if (items.has(group)) add(held, owner(group));\n    }\n    for (const line of empties) line.hidden = items.has(owner(line));\n    for (const count of counts) {\n      const group = count.closest(\"[data-find-group]\");\n      count.textContent = String((count.dataset.findCount === \"groups\" ? held : items).get(group) || 0);\n    }\n    for (const details of disclosures) {\n      if ([...details.querySelectorAll(\"[data-find]\")].some((item) => !item.hidden)) details.open = true;\n    }\n    if (none) none.hidden = items.has(null);\n  };\n\n  const clear = () => {\n    if (!field.value) return;\n    field.value = \"\";\n    apply();\n  };\n  field.addEventListener(\"input\", apply);\n  field.addEventListener(\"keydown\", (event) => { if (event.key === \"Escape\") clear(); });\n  addEventListener(\"pageshow\", (event) => { if (event.persisted) clear(); });\n})();\n",
};
/* design-set find end */
const DOC_CSS = `
/* document presentation: the spec file stays lean; the dialect's look lives here, every value from the design set the
   chrome CSS opens with (spec-page-design #document) */
:where(:root){color-scheme:light dark;--spec-paper:var(--ui-page);--spec-ink:var(--ui-ink);--spec-muted:var(--ui-muted);--spec-hairline:var(--ui-border);--spec-link:var(--ui-link);--spec-soft:var(--ui-surface-soft)}
:where(body){margin:0;background:var(--spec-paper);color:var(--spec-ink);padding-bottom:100px}
article.spec{max-width:720px;margin:0 auto;padding:40px 24px;font:var(--ui-weight-regular) 15px/1.6 var(--ui-font)}
article.spec header{margin-bottom:28px}
article.spec h1{font-size:var(--ui-text-2xl);font-weight:var(--ui-weight-semibold);line-height:1.2;margin:6px 0 8px;letter-spacing:-.01em}
article.spec h2{font-size:var(--ui-text-xl);font-weight:var(--ui-weight-semibold);line-height:1.25;margin:32px 0 10px;padding-top:16px;border-top:1px solid var(--spec-hairline)}
article.spec nav{font:var(--ui-text-xs)/1.5 var(--ui-font);color:var(--spec-muted)}
article.spec p{margin:0 0 10px;max-width:72ch}
article.spec pre{white-space:pre-wrap;overflow-wrap:anywhere}
article.spec a{color:var(--spec-link)}
[data-render-target]{border:1px solid var(--spec-hairline);border-radius:var(--ui-radius);background:var(--spec-soft);margin:6px 0 10px}
@media(max-width:640px){
:where(body){padding-bottom:calc(112px + env(safe-area-inset-bottom))}
article.spec{padding:24px 16px}
article.spec h1{font-size:var(--ui-text-xl)}
}`;
const FOCUS_CSS = `
/* Unchanged context sits beneath a readable black veil; changed content stays clear. */
body.hx-focus-active [data-hx-focus=unchanged]:not(:has([data-hx-focus=changed])):not([data-hx-focus=unchanged]:not(:has([data-hx-focus=changed])) *):not(tr):not(td):not(th):not(script):not(style){position:relative}
body.hx-focus-active [data-hx-focus=unchanged]:not(:has([data-hx-focus=changed])):not([data-hx-focus=unchanged]:not(:has([data-hx-focus=changed])) *):not(tr):not(td):not(th):not(script):not(style)::after{content:"";position:absolute;inset:-3px;background:rgba(0,0,0,calc(.5*var(--hx-veil,1)));border-radius:inherit;pointer-events:none;z-index:2;-webkit-backdrop-filter:blur(calc(2.5px*var(--hx-veil,1)));backdrop-filter:blur(calc(2.5px*var(--hx-veil,1)))}
body.hx-focus-active tr[data-hx-focus=unchanged]:not([data-hx-focus=unchanged]:not(:has([data-hx-focus=changed])) *) > :is(td,th){position:relative}
body.hx-focus-active tr[data-hx-focus=unchanged]:not([data-hx-focus=unchanged]:not(:has([data-hx-focus=changed])) *) > :is(td,th)::after{content:"";position:absolute;inset:0;background:rgba(0,0,0,calc(.5*var(--hx-veil,1)));pointer-events:none;z-index:2;-webkit-backdrop-filter:blur(calc(2.5px*var(--hx-veil,1)));backdrop-filter:blur(calc(2.5px*var(--hx-veil,1)))}
body.hx-focus-active .hx-tbd-open{position:relative;z-index:3}
body.hx-focus-active .hx-tbd-open[data-hx-focus=unchanged]::after,body.hx-focus-active tr.hx-tbd-open[data-hx-focus=unchanged] > :is(td,th)::after{display:none!important}
body.hx-focus-active [data-hx-focus=unchanged] .hx-pin,body.hx-focus-active [data-hx-focus=unchanged] .hx-badge{opacity:1;filter:none;z-index:700}
body.hx-focus-active [data-hx-focus=unchanged] .hx-jev-marker{opacity:1;filter:none;z-index:700}
@media(prefers-color-scheme:dark){
body.hx-focus-active [data-hx-focus=unchanged]:not(:has([data-hx-focus=changed])):not([data-hx-focus=unchanged]:not(:has([data-hx-focus=changed])) *):not(tr):not(td):not(th):not(script):not(style)::after{background:rgba(0,0,0,calc(.6*var(--hx-veil,1)))}
body.hx-focus-active tr[data-hx-focus=unchanged]:not([data-hx-focus=unchanged]:not(:has([data-hx-focus=changed])) *) > :is(td,th)::after{background:rgba(0,0,0,calc(.6*var(--hx-veil,1)))}
}
`;
const CSS = `
/* design-set 241c10fe2754d1928d1cbbd9a9902c7c2673ba21 begin: the pinned set's tokens.css, built in by scripts/design-set-sync; never edit by hand */
/* design-set tokens. Light in :root, dark under prefers-color-scheme.
   tokens.json holds the same values; tests/test_design_set.py checks both. */
:root {
  /* color */
  --ui-page: #ffffff;
  --ui-surface: #ffffff;
  --ui-surface-soft: #f5f5f5;
  --ui-ink: #333333;
  --ui-muted: #525252;
  --ui-muted-soft: #eeeeee;
  --ui-border: #dfdfdf;
  --ui-action: #1d4ed8;
  --ui-action-soft: #eff3ff;
  --ui-link: var(--ui-action);
  --ui-focus: var(--ui-action);
  --ui-pass: #005c32;
  --ui-pass-soft: #e6efea;
  --ui-fail: #a5000f;
  --ui-fail-soft: #f6e6e7;
  --ui-attention: #b0540e;
  --ui-attention-soft: #faf4ef;

  /* type */
  --ui-font: "Inter Variable", Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --ui-mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --ui-text-xs: 12px;
  --ui-text-ui: 13px;
  --ui-text-sm: 14px;
  --ui-text-md: 16px;
  --ui-text-lg: 20px;
  --ui-text-xl: 24px;
  --ui-text-2xl: 30px;
  --ui-weight-regular: 400;
  --ui-weight-medium: 500;
  --ui-weight-semibold: 600;

  /* space and shape */
  --ui-space-1: 4px;
  --ui-space-2: 8px;
  --ui-space-3: 12px;
  --ui-space-4: 16px;
  --ui-space-5: 24px;
  --ui-space-6: 32px;
  --ui-radius-sm: 6px;
  --ui-radius: 8px;
  --ui-radius-pill: 999px;
  --ui-focus-width: 2px;
  --ui-focus-offset: 2px;

  /* motion */
  --ui-motion-duration: 150ms;
  --ui-motion-ease: ease-out;

  /* layout: media queries cannot read a custom property, so build it in from tokens.json */
  --ui-phone-width: 640px;
}

@media (prefers-color-scheme: dark) {
  :root {
    --ui-page: #1a1a1a;
    --ui-surface: #242424;
    --ui-surface-soft: #2a2a2a;
    --ui-ink: #e0e0e0;
    --ui-muted: #999999;
    --ui-muted-soft: #2e2e2e;
    --ui-border: #3a3a3a;
    --ui-action: #7896ef;
    --ui-action-soft: #1a2040;
    --ui-pass: #3daa6e;
    --ui-pass-soft: #1a2e22;
    --ui-fail: #ffb4ab;
    --ui-fail-soft: #2e1a1c;
    --ui-attention: #e5873a;
    --ui-attention-soft: #2e2218;
  }
}

@media (prefers-reduced-motion: reduce) {
  :root {
    --ui-motion-duration: 0ms;
  }
}
/* design-set end */
/* ui tokens */
:root{--ui-draft:var(--ui-attention);--ui-draft-soft:var(--ui-attention-soft);--ui-ack:var(--ui-action);--ui-ack-soft:var(--ui-action-soft);--ui-resolved:var(--ui-pass);--ui-resolved-soft:var(--ui-pass-soft);--ui-error:var(--ui-fail);--ui-marker:#767b85;--ui-marker-important:#d1242f;--ui-marker-pass:#1a7f37;--ui-on-marker:#ffffff;--ui-marker-warning:#e5534b;--ui-pin-ring:rgba(176,84,14,0.25);--ui-shadow:rgba(51,51,51,0.14);--ui-veil:rgba(0,0,0,0.42)}
@media(prefers-color-scheme:dark){
:root{--ui-pin-ring:rgba(229,135,58,0.25);--ui-shadow:rgba(0,0,0,0.4);--ui-veil:rgba(0,0,0,0.6)}
}
/* focus ring: the set's, on every chrome control (spec-page-design #button-system-rule, #acceptance-focus-set) */
:is(.hx-top-line,.hx-toolbar,.hx-thread-dock,.hx-panel,.hx-jev-pop,.hx-jev-note,.hx-banner,.hx-layer) :focus-visible,:is(.hx-jev-marker,.hx-mockup-chip,.hx-screen,.hx-video-mark):focus-visible{outline:var(--ui-focus-width) solid var(--ui-focus);outline-offset:var(--ui-focus-offset)}
/* top line (spec-page-design #top-line, compared-range #bar-flow): one fixed line on the page color over a hairline,
   holding the index link and the range bar; the body starts under it */
.hx-top-line{position:fixed;top:0;left:0;right:0;z-index:860;box-sizing:border-box;max-height:100vh;overflow-y:auto;display:flex;align-items:flex-start;gap:var(--ui-space-3);padding:var(--ui-space-1) var(--ui-space-4);background:var(--ui-page);border-bottom:1px solid var(--ui-border);color:var(--ui-ink);font:var(--ui-text-sm)/1.4 var(--ui-font)}
body.hx-panel-open :is(.hx-top-line,.hx-errors){right:330px}
body:has(> :is(.hx-top-line,.hx-errors)){padding-top:var(--hx-top-space,0px)}
html:has(> body > :is(.hx-top-line,.hx-errors)){scroll-padding-top:var(--hx-top-space,0px)}
/* a mockup page's height never changes a width (mockups #review-settle): the scrollbar's room is kept, so sizing the
   page to the chrome above it, or the frames to their space, never shows or hides the scrollbar beside what it follows */
html:has(.hx-mockup){scrollbar-gutter:stable}
:is(.hx-top-line,.hx-panel-head,.hx-thread-dock,.hx-focused-head,.hx-rule-card-source) svg{display:block;flex:0 0 auto}
/* index link (review-index #index-link): a chevron and Review index at the start of the top line */
.hx-service-index-link{flex:0 0 auto;display:inline-flex;align-items:center;gap:var(--ui-space-1);min-height:var(--ui-space-6);box-sizing:border-box;border-radius:var(--ui-radius-sm);color:var(--ui-ink);font:var(--ui-weight-medium) var(--ui-text-xs)/1 var(--ui-font);text-decoration:none}
.hx-service-index-link:hover{text-decoration:underline;text-underline-offset:3px}
.hx-service-index-link svg{color:var(--ui-muted)}
/* spec link (mockups #review-chrome): a mockup page's spec, after the index link */
.hx-spec-link{flex:0 1 auto;min-width:0;display:block;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;border-radius:var(--ui-radius-sm);color:var(--ui-link);font:var(--ui-weight-semibold) var(--ui-text-xs)/1 var(--ui-font);line-height:var(--ui-space-6);text-decoration:none}
.hx-spec-link:hover{text-decoration:underline;text-underline-offset:3px}
/* range bar (compared-range #range-bar): the rest of the top line; the base picker opens as a row under it */
.hx-range-bar{flex:1 1 auto;min-width:0;display:flex;flex-wrap:wrap;align-items:center;margin:0;padding:0;border:0}
.hx-range-row{display:flex;flex:1 1 100%;align-items:center;gap:var(--ui-space-2);min-width:0}
.hx-range-copy{flex:1 1 auto;min-width:0;max-width:none;margin:0;color:var(--ui-muted);overflow-wrap:anywhere}
.hx-range-id{font-weight:var(--ui-weight-medium);font-family:var(--ui-mono);color:var(--ui-ink)}
.hx-range-change{flex:0 0 auto;min-height:var(--ui-space-6);padding:var(--ui-space-2) var(--ui-space-3);border:none;border-radius:var(--ui-radius-sm);background:transparent;color:var(--ui-ink);font:var(--ui-weight-semibold) var(--ui-text-xs) var(--ui-font);cursor:pointer}
.hx-range-change:hover,.hx-range-change[aria-expanded=true]{background:var(--ui-surface-soft)}
/* Before and After (mockups #before-table): a mockup page's two views, the pressed one marked, before the base picker */
.hx-mockup-view{flex:0 0 auto;display:inline-flex;gap:var(--ui-space-1)}
.hx-mockup-view[hidden]{display:none}
.hx-mockup-view button{min-height:var(--ui-space-6);padding:var(--ui-space-2) var(--ui-space-3);border:none;border-radius:var(--ui-radius-sm);background:transparent;color:var(--ui-ink);font:var(--ui-weight-semibold) var(--ui-text-xs) var(--ui-font);cursor:pointer}
.hx-mockup-view button:hover,.hx-mockup-view button[aria-pressed=true]{background:var(--ui-surface-soft)}
.hx-mockup-view button[aria-pressed=true]{text-decoration:underline;text-underline-offset:3px}
/* Previous and Next (mockups #next-screen): around the screen's place in its set, at the start of the range bar's row */
.hx-pager{flex:0 0 auto;display:inline-flex;align-items:center;gap:var(--ui-space-1);margin-right:var(--ui-space-1)}
.hx-pager button{display:inline-grid;place-items:center;box-sizing:border-box;width:var(--ui-space-6);min-height:var(--ui-space-6);padding:0;border:none;border-radius:var(--ui-radius-sm);background:transparent;color:var(--ui-ink);cursor:pointer}
.hx-pager button:hover:not(:disabled){background:var(--ui-surface-soft)}
.hx-pager button:disabled{color:var(--ui-muted);opacity:.45;cursor:default}
.hx-pager-place{color:var(--ui-muted);font:var(--ui-weight-medium) var(--ui-text-xs) var(--ui-font);white-space:nowrap}
.hx-range-picker{flex:1 1 100%;box-sizing:border-box;max-width:720px;margin:var(--ui-space-1) 0 var(--ui-space-2);padding-top:var(--ui-space-2);border-top:1px solid var(--ui-border)}
.hx-range-commits{display:grid;gap:var(--ui-space-1);max-height:270px;overflow:auto}
.hx-range-commit{display:grid;grid-template-columns:5.5em 6.5em minmax(0,1fr);gap:var(--ui-space-2);align-items:baseline;width:100%;padding:var(--ui-space-1) var(--ui-space-2);border:1px solid transparent;border-radius:var(--ui-radius-sm);background:transparent;color:var(--ui-ink);text-align:left;font:var(--ui-text-xs)/1.35 var(--ui-font);cursor:pointer}
.hx-range-commit:hover,.hx-range-commit[data-selected=true]{border-color:var(--ui-resolved);background:var(--ui-resolved-soft)}
.hx-range-commit strong{font:var(--ui-weight-semibold) var(--ui-text-xs) var(--ui-mono)}
.hx-range-commit time{color:var(--ui-muted);font-variant-numeric:tabular-nums}
.hx-range-commit span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hx-range-empty{margin:0;padding:var(--ui-space-1) var(--ui-space-2);color:var(--ui-muted)}
.hx-range-form{margin-top:var(--ui-space-2);padding-top:var(--ui-space-2);border-top:1px solid var(--ui-border)}
.hx-range-form label{display:block;margin-bottom:var(--ui-space-1);font-size:var(--ui-text-xs);font-weight:var(--ui-weight-semibold);color:var(--ui-ink)}
.hx-range-form>div{display:flex;gap:var(--ui-space-1)}
.hx-range-form input{box-sizing:border-box;min-width:0;flex:1 1 auto;padding:var(--ui-space-2);border:1px solid var(--ui-border);border-radius:var(--ui-radius-sm);background:var(--ui-surface);color:var(--ui-ink);font:var(--ui-text-sm) var(--ui-mono)}
.hx-range-form button{flex:0 0 auto;min-height:var(--ui-space-6);padding:var(--ui-space-2) var(--ui-space-3);border:1px solid var(--ui-ink);border-radius:var(--ui-radius-sm);background:var(--ui-ink);color:var(--ui-page);font:var(--ui-weight-semibold) var(--ui-text-xs) var(--ui-font);cursor:pointer}
.hx-range-form button:disabled{cursor:wait;opacity:.5}
.hx-range-error{margin:var(--ui-space-1) 0 0;color:var(--ui-error);font-size:var(--ui-text-xs)}
/* phone: the range copy wraps to its own row under the index link; its picker button stays on the first row */
@media(max-width:640px){
.hx-top-line{flex-wrap:wrap;gap:0 var(--ui-space-2);padding:env(safe-area-inset-top) var(--ui-space-2) var(--ui-space-1)}
.hx-service-index-link{min-height:44px;padding:0 var(--ui-space-1)}
.hx-range-bar{flex:1 1 100%;padding:0 var(--ui-space-1)}
.hx-range-change{position:absolute;top:env(safe-area-inset-top);right:var(--ui-space-2);min-height:44px}
.hx-spec-link{line-height:44px;margin-right:calc(3 * var(--ui-space-6) + var(--ui-space-2))} /* clear of the base picker */
.hx-mockup-view button{min-height:44px}
/* the range a row of its own, Previous and Next at the end of the row under it, after Before and After (mockups #next-screen-phone) */
.hx-range-row:has(>.hx-pager){flex-wrap:wrap;row-gap:0}
.hx-range-row:has(>.hx-pager) .hx-range-copy{flex:1 1 100%}
.hx-range-row:has(>.hx-pager) .hx-mockup-view{order:1}
.hx-pager{order:2;margin:0 0 0 auto}
.hx-pager button{width:44px;min-height:44px;touch-action:manipulation}
.hx-range-copy{font-size:var(--ui-text-xs)}
.hx-range-commits{max-height:none}
.hx-range-commit{grid-template-columns:5.5em 6.5em minmax(0,1fr);padding:var(--ui-space-2) var(--ui-space-1);min-height:44px}
.hx-range-form input,.hx-range-form button{min-height:44px}
.hx-range-form input{font-size:var(--ui-text-md)}
}
/* step bar (mockups #walkthrough-bar): a row of its own under the top line, Play then every step by number and name, the
   showing one pressed with its caption and a count on a step holding open questions or threads; on a phone it scrolls
   sideways within itself, so the page never does (#walkthrough-phone) */
.hx-top-line:has(>.hx-steps){flex-wrap:wrap}
@media(min-width:641px){.hx-top-line:has(>.hx-steps) .hx-range-bar{flex-basis:0}}
.hx-steps{flex:1 1 100%;order:9;display:flex;align-items:center;gap:var(--ui-space-1);box-sizing:border-box;min-width:0;margin:var(--ui-space-1) calc(-1 * var(--ui-space-4)) calc(-1 * var(--ui-space-1));padding:var(--ui-space-2) var(--ui-space-4);border-top:1px solid var(--ui-border);overflow-x:auto;overscroll-behavior-x:contain;white-space:nowrap;scrollbar-width:thin}
.hx-step{flex:0 0 auto;display:inline-flex;align-items:center;gap:var(--ui-space-1);box-sizing:border-box;min-height:var(--ui-space-6);margin:0;padding:var(--ui-space-2) var(--ui-space-3);border:1px solid var(--ui-border);border-radius:var(--ui-radius-pill);background:var(--ui-surface);color:var(--ui-ink);font:var(--ui-weight-semibold) var(--ui-text-xs)/1 var(--ui-font);cursor:pointer;transition:background var(--ui-motion-duration) var(--ui-motion-ease),color var(--ui-motion-duration) var(--ui-motion-ease)}
.hx-step:hover{background:var(--ui-surface-soft)}
.hx-step[aria-pressed=true]{background:var(--ui-ink);border-color:var(--ui-ink);color:var(--ui-page)}
.hx-step-n{color:var(--ui-muted);font-weight:var(--ui-weight-medium)}
.hx-step[aria-pressed=true] .hx-step-n{color:var(--ui-page)}
.hx-step-count{box-sizing:border-box;min-width:18px;height:18px;padding:0 var(--ui-space-1);border-radius:var(--ui-radius-pill);background:var(--ui-attention);color:var(--ui-page);display:inline-grid;place-items:center;font-size:var(--ui-text-xs)}
.hx-step-count[hidden]{display:none}
/* with no top line, as a plain file, the bar heads the page itself */
body > .hx-steps{position:sticky;top:0;z-index:860;margin:0;background:var(--ui-page);border-top:0;border-bottom:1px solid var(--ui-border)}
.hx-step-caption{flex:0 0 auto;margin-left:var(--ui-space-3);color:var(--ui-muted);font:var(--ui-text-sm)/1.4 var(--ui-font)}
@media(max-width:640px){
.hx-steps{margin:var(--ui-space-1) calc(-1 * var(--ui-space-2)) calc(-1 * var(--ui-space-1));padding:var(--ui-space-1) var(--ui-space-2)}
.hx-step{min-height:44px;padding:0 var(--ui-space-3);touch-action:manipulation}
}
/* toolbar */
.hx-toolbar{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);display:flex;gap:var(--ui-space-1);align-items:center;background:var(--ui-page);border:1px solid var(--ui-border);border-radius:var(--ui-radius);box-shadow:0 8px 28px var(--ui-shadow);padding:var(--ui-space-1);z-index:900;font:var(--ui-text-sm) var(--ui-font)}
.hx-toolbar button{font:var(--ui-weight-semibold) var(--ui-text-xs) var(--ui-font);border:none;background:transparent;border-radius:var(--ui-radius-sm);padding:var(--ui-space-2) var(--ui-space-3);min-height:var(--ui-space-6);cursor:pointer;color:var(--ui-ink)}
.hx-toolbar button:disabled{cursor:default;opacity:.45}
.hx-toolbar button[aria-pressed=true]{background:var(--ui-draft-soft);color:var(--ui-draft)}
.hx-toolbar .hx-status{color:var(--ui-muted);font-size:var(--ui-text-xs);padding:0 var(--ui-space-2)}
.hx-toolbar .hx-offline{font-size:var(--ui-text-xs);font-weight:var(--ui-weight-semibold);padding:0 var(--ui-space-2)}
/* panel */
.hx-panel{position:fixed;top:0;right:0;width:330px;height:100vh;background:var(--ui-page);color:var(--ui-ink);border-left:1px solid var(--ui-border);z-index:800;display:none;flex-direction:column;font:var(--ui-text-sm) var(--ui-font);box-shadow:none}
.hx-panel.open{display:flex}
body.hx-panel-open{padding-right:330px}
/* The header text runs between the toggle and the gear, each held clear by its inset, its size, and one gap: the head's
   own properties place both and its side padding derives from them, so moving or resizing either moves the text (on a
   phone, 44px controls, the toggle a space-1 inset); the agent note moves to its own line as one unit when it does not
   fit beside Conversation and its count, and wraps only when longer than that line. */
.hx-panel-head{--hx-head-control:var(--ui-space-6);--hx-head-gap:var(--ui-space-2);--hx-gear-inset:var(--ui-space-2);--hx-toggle-inset:var(--ui-space-2);position:relative;min-height:44px;padding:var(--ui-space-3) calc(var(--hx-gear-inset) + var(--hx-head-control) + var(--hx-head-gap)) var(--ui-space-3) calc(var(--hx-toggle-inset) + var(--hx-head-control) + var(--hx-head-gap));box-sizing:border-box;border-bottom:1px solid var(--ui-border);font-weight:var(--ui-weight-semibold);overflow-wrap:anywhere}
.hx-panel-head :is(.hx-sub,.hx-count){display:inline-block;font-weight:var(--ui-weight-regular);font-size:var(--ui-text-xs);color:var(--ui-muted)}
.hx-panel-toggle{position:absolute;top:var(--ui-space-2);left:var(--hx-toggle-inset);width:var(--hx-head-control);height:var(--hx-head-control);border:1px solid var(--ui-border);background:var(--ui-surface);border-radius:var(--ui-radius-sm);color:var(--ui-muted);cursor:pointer;display:grid;place-items:center;padding:0}
.hx-panel-toggle:hover{background:var(--ui-page);color:var(--ui-ink)}
.hx-panel-gear{position:absolute;top:var(--ui-space-2);right:var(--hx-gear-inset);width:var(--hx-head-control);height:var(--hx-head-control);border:1px solid var(--ui-border);background:var(--ui-surface);border-radius:var(--ui-radius-sm);color:var(--ui-muted);cursor:pointer;display:grid;place-items:center;padding:0}
.hx-panel-gear:hover{background:var(--ui-page);color:var(--ui-ink)}
.hx-panel-gear[aria-expanded=true]{background:var(--ui-draft-soft);color:var(--ui-draft);border-color:var(--ui-draft)}
.hx-settings{padding:var(--ui-space-3) var(--ui-space-4);border-bottom:1px solid var(--ui-border);background:var(--ui-surface)}
.hx-set-row{display:grid;grid-template-columns:1fr auto;gap:var(--ui-space-1) var(--ui-space-2);align-items:center;font:var(--ui-weight-semibold) var(--ui-text-xs) var(--ui-font);color:var(--ui-ink)}
.hx-set-row input[type=range]{grid-column:1/-1;width:100%;margin:var(--ui-space-1) 0 0;accent-color:var(--ui-resolved);min-height:24px;touch-action:manipulation}
.hx-set-val{font:var(--ui-weight-semibold) var(--ui-text-xs) var(--ui-mono);color:var(--ui-muted)}
.hx-set-note{margin:var(--ui-space-2) 0 0;font:var(--ui-weight-regular) var(--ui-text-xs)/1.45 var(--ui-font);color:var(--ui-muted)}
.hx-panel-content{display:flex;flex:1;min-height:0;flex-direction:column}
/* dock */
.hx-thread-dock{position:fixed;top:calc(var(--hx-top-space,0px) + var(--ui-space-3));right:var(--ui-space-3);z-index:850;display:flex;flex-direction:column;gap:var(--ui-space-1);padding:var(--ui-space-1);background:var(--ui-surface);border:1px solid var(--ui-border);border-radius:var(--ui-radius);font:var(--ui-text-xs) var(--ui-font);transition:opacity .15s,transform .15s}
body.hx-panel-open .hx-thread-dock{opacity:0;transform:translateX(10px);pointer-events:none}
.hx-dock-open,.hx-dock-thread{position:relative;width:var(--ui-space-6);height:var(--ui-space-6);box-sizing:border-box;border:1px solid var(--ui-border);background:var(--ui-surface);border-radius:var(--ui-radius-sm);color:var(--ui-muted);cursor:pointer;display:grid;place-items:center;padding:0}
.hx-dock-open:hover,.hx-dock-thread:hover{background:var(--ui-page);border-color:var(--ui-muted);color:var(--ui-ink)}
.hx-unread-badge{position:absolute;top:-6px;right:-6px;min-width:20px;height:20px;box-sizing:border-box;border:2px solid var(--ui-page);border-radius:var(--ui-radius-pill);padding:0 var(--ui-space-1);background:var(--ui-ack);color:var(--ui-page);display:grid;place-items:center;font:var(--ui-weight-semibold) var(--ui-text-xs)/1 var(--ui-font);box-shadow:0 1px 4px var(--ui-shadow)}
.hx-unread-badge[hidden]{display:none}
.hx-dock-threads{display:flex;flex-direction:column;gap:var(--ui-space-1);max-height:calc(100vh - var(--hx-top-space,0px) - 76px);overflow-y:auto;scrollbar-width:none}
.hx-dock-threads:not(:empty){border-top:1px solid var(--ui-border);padding-top:var(--ui-space-1)}
.hx-dock-threads::-webkit-scrollbar{display:none}
.hx-dock-thread{font:var(--ui-weight-semibold) var(--ui-text-xs)/1 var(--ui-mono)}
.hx-dock-thread[data-s=draft],.hx-dock-thread[data-s=pending]{border-color:var(--ui-draft);color:var(--ui-draft);background:var(--ui-draft-soft)}
.hx-dock-thread[data-s=acknowledged]{border-color:var(--ui-ack);color:var(--ui-ack);background:var(--ui-ack-soft)}
.hx-dock-thread[data-s=resolved]{border-color:var(--ui-resolved);color:var(--ui-resolved);background:var(--ui-resolved-soft)}
.hx-dock-thread.active{box-shadow:0 0 0 2px var(--ui-pin-ring)}
/* conversation (threads #stream, spec-page-design #surface-panel): one column of bubbles, oldest thread at the top and
   the newest at the bottom; this browser's at the right on the action color, everyone else's at the left on the soft
   surface; each thread its first message with its chip, its replies folded into one line, and its receipt */
.hx-stream{position:relative;display:flex;flex:1;min-height:0;flex-direction:column}
.hx-threads{flex:1;min-height:0;overflow-y:auto;padding:var(--ui-space-3) var(--ui-space-3) var(--ui-space-2);display:flex;flex-direction:column;gap:var(--ui-space-3)}
.hx-threads>:first-child{margin-top:auto}
.hx-entry{align-self:stretch;display:flex;flex-direction:column;align-items:flex-start;gap:var(--ui-space-1);min-width:0}
.hx-entry.hx-me{align-items:flex-end}
.hx-msg{position:relative;display:flex;flex-direction:column;align-items:flex-start;gap:var(--ui-space-1);max-width:86%;min-width:0}
.hx-msg.hx-me{align-self:flex-end;align-items:flex-end}
.hx-msg.hx-them{align-self:flex-start}
.hx-who{padding:0 var(--ui-space-3);font:var(--ui-text-xs)/1.4 var(--ui-font);color:var(--ui-muted)}
.hx-stage{display:inline-block;padding:0 var(--ui-space-2);border:1px solid var(--ui-border);border-radius:var(--ui-radius-pill);background:var(--ui-page);color:var(--ui-muted);font:var(--ui-weight-medium) var(--ui-text-xs)/1.5 var(--ui-font)}
.hx-bubble{position:relative;box-sizing:border-box;max-width:100%;padding:var(--ui-space-2) var(--ui-space-3);border-radius:calc(2 * var(--ui-radius));font:var(--ui-text-sm)/1.4 var(--ui-font);overflow-wrap:anywhere;white-space:pre-wrap}
.hx-me>.hx-bubble{background:var(--ui-action);color:var(--ui-page);border-bottom-right-radius:var(--ui-radius-sm)}
.hx-them>.hx-bubble{background:var(--ui-surface-soft);color:var(--ui-ink);border-bottom-left-radius:var(--ui-radius-sm)}
.hx-me>.hx-bubble[data-draft]{background:var(--ui-action-soft);color:var(--ui-ink);box-shadow:inset 0 0 0 1px var(--ui-action)}
.hx-bubble[role=button]{cursor:pointer}
.hx-edited{margin-left:var(--ui-space-1);font-size:var(--ui-text-xs);font-weight:var(--ui-weight-semibold)}
.hx-dot{position:absolute;top:-3px;right:-3px;box-sizing:border-box;width:10px;height:10px;border-radius:50%;background:var(--ui-action);border:2px solid var(--ui-page);pointer-events:none}
.hx-chip{box-sizing:border-box;max-width:100%;margin:0;padding:0 var(--ui-space-2);border:1px solid var(--ui-border);border-radius:var(--ui-radius-pill);background:var(--ui-page);color:var(--ui-muted);font:var(--ui-text-xs)/1.6 var(--ui-font);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:left;cursor:pointer}
.hx-chip:hover{color:var(--ui-ink);border-color:var(--ui-muted)}
.hx-replies{max-width:100%;margin:0;padding:0 var(--ui-space-3);border:0;background:none;color:var(--ui-muted);font:var(--ui-text-xs)/1.5 var(--ui-font);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:left;cursor:pointer}
.hx-replies b{color:var(--ui-ink);font-weight:var(--ui-weight-semibold)}
.hx-replies:hover{text-decoration:underline;text-underline-offset:2px}
.hx-receipt{padding:0 var(--ui-space-3);font:var(--ui-weight-semibold) var(--ui-text-xs)/1.5 var(--ui-font)}
.hx-receipt[data-s=draft],.hx-receipt[data-s=pending]{color:var(--ui-draft)}
.hx-receipt[data-s=acknowledged],.hx-receipt[data-s=orphaned]{color:var(--ui-ack)}
.hx-receipt[data-s=resolved]{color:var(--ui-resolved)}
.hx-resolved-line{align-self:stretch;box-sizing:border-box;margin:0;padding:var(--ui-space-1) var(--ui-space-3);border:0;border-top:1px solid var(--ui-border);border-bottom:1px solid var(--ui-border);background:none;color:var(--ui-muted);font:var(--ui-text-xs)/1.5 var(--ui-font);text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer}
.hx-resolved-line .hx-receipt{padding:0}
.hx-choices{display:flex;flex-wrap:wrap;gap:var(--ui-space-1);margin-top:var(--ui-space-2);white-space:normal}
.hx-choice{min-height:var(--ui-space-6);margin:0;padding:var(--ui-space-2) var(--ui-space-3);border:1px solid var(--ui-border);border-radius:var(--ui-radius-pill);background:var(--ui-surface);color:var(--ui-ink);font:var(--ui-weight-semibold) var(--ui-text-xs) var(--ui-font);cursor:pointer}
.hx-choice:hover{background:var(--ui-surface-soft)}
.hx-choice.hx-text-choice{border-color:transparent;background:transparent}
.hx-msg-actions{display:flex;gap:var(--ui-space-1)}
.hx-msg-actions .hx-btn{margin:0}
.hx-picture{display:flex;flex-direction:column;align-items:inherit;gap:var(--ui-space-1);margin:0}
.hx-picture img{display:block;box-sizing:border-box;width:220px;max-width:100%;max-height:160px;object-fit:contain;padding:var(--ui-space-2);border:1px solid var(--ui-border);border-radius:var(--ui-radius);background:var(--ui-page)}
.hx-picture figcaption{color:var(--ui-muted);font:var(--ui-text-xs)/1.5 var(--ui-font)}
.hx-history{margin:0;padding-left:var(--ui-space-4);font-size:var(--ui-text-xs);color:var(--ui-muted)}
.hx-quote{display:block;border-left:2px solid var(--ui-border);padding-left:var(--ui-space-2);color:var(--ui-muted);font-style:italic;font-size:var(--ui-text-xs);margin:var(--ui-space-1) 0}
.hx-anchor{font-family:var(--ui-mono);font-size:var(--ui-text-xs);color:var(--ui-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hx-entry>:is(.hx-orphan-hint,.hx-place-notice,.hx-host-from){align-self:stretch;margin:0}
.hx-thread-labels{display:flex;flex-wrap:wrap;gap:var(--ui-space-1)}
/* the focused thread (threads #focus): its messages alone over the conversation dimmed; on a desktop it fills the panel,
   on a phone it is a sheet over the screen's lower half, above the bottom toolbar's one hand-off control */
.hx-veil{position:absolute;inset:0;z-index:1;background:var(--ui-veil)}
.hx-focused{position:absolute;top:var(--ui-space-2);left:var(--ui-space-2);right:var(--ui-space-2);bottom:var(--ui-space-2);z-index:2;box-sizing:border-box;display:flex;flex-direction:column;min-height:0;background:var(--ui-page);color:var(--ui-ink);border:1px solid var(--ui-border);border-radius:calc(2 * var(--ui-radius));box-shadow:0 12px 32px var(--ui-shadow);font:var(--ui-text-sm) var(--ui-font)}
.hx-grip{display:none}
.hx-focused-head{display:flex;align-items:center;gap:var(--ui-space-1);padding:var(--ui-space-1) var(--ui-space-2);border-bottom:1px solid var(--ui-border)}
.hx-focused-head .hx-btn{margin:0}
.hx-btn.hx-back{margin-right:auto;border-color:transparent;background:transparent}
.hx-focused-stream{flex:1;min-height:0;overflow-y:auto;padding:var(--ui-space-3);display:flex;flex-direction:column;gap:var(--ui-space-2)}
.hx-focused>.hx-composer{display:flex;align-items:flex-end;gap:var(--ui-space-2);padding:var(--ui-space-2) var(--ui-space-3);border-top:1px solid var(--ui-border)}
.hx-focused>.hx-composer textarea{flex:1;min-height:36px;max-height:160px;margin:0;resize:none;overflow-y:auto;line-height:1.4;border-radius:calc(2 * var(--ui-radius));padding:var(--ui-space-2) var(--ui-space-3)}
.hx-focused>.hx-composer .hx-btn{margin:0}
/* a new comment's composer, at the bottom of the conversation where its thread will begin */
.hx-compose{display:flex;flex-direction:column;gap:var(--ui-space-1);padding:var(--ui-space-2) var(--ui-space-3);border:1px solid var(--ui-border);border-radius:var(--ui-radius);background:var(--ui-surface)}
/* composer */
.hx-composer textarea{width:100%;min-height:56px;font:var(--ui-text-sm) var(--ui-font);border:1px solid var(--ui-border);border-radius:var(--ui-radius-sm);padding:var(--ui-space-1) var(--ui-space-2);box-sizing:border-box;margin-top:var(--ui-space-1);background:var(--ui-surface);color:var(--ui-ink)}
/* buttons */
.hx-btn{font:var(--ui-weight-semibold) var(--ui-text-xs) var(--ui-font);border:1px solid var(--ui-border);background:var(--ui-surface);border-radius:var(--ui-radius-sm);padding:var(--ui-space-2) var(--ui-space-3);cursor:pointer;margin:var(--ui-space-1) var(--ui-space-1) 0 0;color:var(--ui-ink);min-height:var(--ui-space-6)}
.hx-btn.pri{background:var(--ui-ink);color:var(--ui-page);border-color:var(--ui-ink)}
.hx-btn:has(>svg){display:inline-flex;align-items:center;gap:var(--ui-space-1)}
.hx-btn>svg{flex:none}
/* handoff bar */
.hx-handoff{border-top:1px solid var(--ui-border);padding:var(--ui-space-2) var(--ui-space-3);display:flex;justify-content:flex-end;align-items:center;gap:var(--ui-space-1)}
/* a focused question keeps the hand-off bar above the dimmed conversation, so Next TBD steps on (mockups #mockup-questions-next) */
.hx-panel:has(.hx-focused[data-question]) .hx-handoff{position:relative;z-index:3;background:var(--ui-page)}
/* threads #stream: its note, Next TBD, and Hand off on one line, as the conversation's mockup draws it */
.hx-handoff .hx-note{font-size:var(--ui-text-xs);color:var(--ui-muted);margin-right:auto;min-width:0}
.hx-handoff .hx-btn{margin:0;flex:none;white-space:nowrap}
/* pins */
/* pins and question marks sit among the page's own styles, a mockup frame's rules for its buttons among them: every
   look they take is the chrome's, important, so none of those rules reaches them; where they sit is set on each */
:is(.hx-pin,.hx-qmark){box-sizing:border-box!important;min-width:0!important;min-height:0!important;max-width:none!important;max-height:none!important;margin:0!important;padding:0!important;letter-spacing:normal!important;text-transform:none!important;text-decoration:none!important;text-shadow:none!important;transform:none!important;transition:none!important;animation:none!important;opacity:1!important;filter:none!important;cursor:pointer!important;pointer-events:auto!important}
:is(.hx-pin,.hx-qmark):focus-visible{outline:var(--ui-focus-width) solid var(--ui-focus)!important;outline-offset:var(--ui-focus-offset)!important}
.hx-pin{position:absolute!important;z-index:700!important;width:24px!important;height:24px!important;border-radius:50% 50% 50% 4px!important;border:none!important;font:var(--ui-weight-semibold) var(--ui-text-xs) var(--ui-font)!important;color:var(--ui-page)!important;display:flex!important;align-items:center!important;justify-content:center!important;box-shadow:0 2px 8px var(--ui-shadow)!important}
.hx-pin[data-s=draft],.hx-pin[data-s=pending]{background:var(--ui-draft)!important}
.hx-pin[data-s=acknowledged]{background:var(--ui-ack)!important}
.hx-pin[data-s=resolved]{background:var(--ui-surface)!important;color:var(--ui-resolved)!important;border:2px solid var(--ui-resolved)!important}
.hx-pin.active{box-shadow:0 0 0 3px var(--ui-pin-ring),0 2px 8px var(--ui-shadow)!important}
[data-anchor]:where(:not(.hx-mockup-fit *)){position:relative}
/* A block with a marker and a corner pin reserves a right column: marker on the first line, pin below it. */
[data-anchor]:has(> .hx-pin[data-column]){padding-right:var(--ui-space-6);min-height:calc(.5lh + 42px)}
[data-anchor]:has(> .hx-pin[data-column]) > .hx-jev-marker,.hx-pin[data-column]{left:auto;right:0}
tr[data-anchor]:has(> .hx-pin[data-column]) > :has(> .hx-jev-marker){box-sizing:content-box;padding-right:var(--ui-space-6);height:calc(.5lh + 42px)}
body.hx-comment [data-anchor]{cursor:copy}
body.hx-comment [data-anchor]:hover:not(:has(:is(h1,h2,h3,h4,h5,h6,p,li,ul,ol,table,tr,td,th,blockquote,pre,code,nav,figcaption,button,input,select,textarea,label,a,output,summary,video,svg,[data-render-target]):hover)){outline:2px dashed var(--ui-draft);outline-offset:6px}
body.hx-comment [data-anchor] :is(h1,h2,h3,h4,h5,h6,p,li,td,th,blockquote,pre,code,nav,figcaption,button,label,a,output,summary,video):hover{outline:1.5px dashed var(--ui-draft);outline-offset:4px;border-radius:var(--ui-radius-sm)}
body.hx-comment [data-anchor] svg, body.hx-comment [data-anchor] svg *{cursor:copy}
body.hx-comment [data-anchor] :is(button,input,select,textarea,label,a,summary){cursor:copy}
[data-render-target]{position:relative}
.hx-ring{position:absolute;border:2px dashed var(--ui-draft);border-radius:var(--ui-radius-sm);pointer-events:none;z-index:650}
.hx-thread-ring{position:fixed;border:2px solid var(--ui-draft);border-radius:var(--ui-radius-sm);pointer-events:none;z-index:750;box-shadow:0 0 0 3px var(--ui-pin-ring);transition:left .12s,top .12s,width .12s,height .12s}
body.hx-comment [data-render-target]:hover{border:1.5px dashed var(--ui-draft)}
body.hx-comment [data-render-target] canvas{cursor:copy!important}
.hx-tbd-open{outline:2px solid var(--ui-draft);outline-offset:var(--ui-space-1)}
/* mockup page (mockups #review-fit): the frames side by side, desktop first, each labeled and zoomed whole into its slot;
   stacked on a phone. The frames keep the mockup's own look: an anchored region holds a pin with no weight of its own,
   so the mockup's own position wins, and only pins and the arrival ring keep the chrome's size at any zoom. */
.hx-mockup{display:flex;align-items:flex-start;gap:var(--ui-space-5);box-sizing:border-box;width:100%;margin:0;padding:var(--ui-space-5) calc(var(--ui-space-6) + var(--ui-space-5)) calc(var(--hx-dock-space,var(--ui-space-6)) + 2 * var(--ui-space-6)) var(--ui-space-5)}
.hx-mockup-slot{flex:0 1 var(--hx-natural,auto);min-width:0;margin:0}
.hx-mockup-label{margin:0 0 var(--ui-space-2);color:var(--ui-muted);font:var(--ui-weight-semibold) var(--ui-text-xs)/1.4 var(--ui-font)}
.hx-mockup-fit{position:relative;width:max-content}
:where(.hx-mockup-fit [data-anchor]){position:relative}
.hx-mockup-fit .hx-pin{zoom:calc(1 / var(--hx-zoom,1))}
.hx-mockup-fit .hx-tbd-open{outline-width:calc(2px / var(--hx-zoom,1));outline-offset:calc(var(--ui-space-1) / var(--hx-zoom,1))}
.hx-mockup-before :is(.hx-pin,.hx-qmark,.hx-thread-ring,.hx-video-rail,.hx-video-box,#hx-mode){display:none!important}
/* a first round's directions (mockups #directions-page): the Desktop and Phone toggle above them, then each direction in
   one frame under its name, the agent's pick's reason, and the line saying what it gives up, on shared rows so the
   frames start level; Pick on each but the standing pick. Each column's frame at the viewport the toggle does not show
   is laid out unseen. No other direction takes a comment. While Before shows, the pick's two frames at the base alone. */
.hx-mockup.hx-directions{flex-direction:column;align-items:stretch;gap:var(--ui-space-4)}
.hx-dirs-toggle{display:inline-flex;align-self:flex-start;padding:calc(var(--ui-space-1) / 2);border:1px solid var(--ui-border);border-radius:var(--ui-radius);background:var(--ui-surface-soft)}
.hx-dirs-toggle .hx-btn{margin:0;border-color:transparent;background:transparent;color:var(--ui-muted);white-space:nowrap;transition:background var(--ui-motion-duration) var(--ui-motion-ease),color var(--ui-motion-duration) var(--ui-motion-ease)}
.hx-dirs-toggle .hx-btn:hover{color:var(--ui-ink)}
.hx-dirs-toggle .hx-btn[aria-pressed=true]{background:var(--ui-page);border-color:var(--ui-border);color:var(--ui-ink)}
.hx-dir-row{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(0,1fr);grid-template-rows:auto auto auto auto;column-gap:var(--ui-space-5);row-gap:var(--ui-space-2);align-items:start}
.hx-dir{display:contents}
.hx-dir-head{grid-row:1;display:flex;flex-wrap:wrap;align-items:center;gap:var(--ui-space-2);min-width:0;min-height:var(--ui-space-6)}
.hx-dir-name{min-width:0;overflow-wrap:anywhere;color:var(--ui-ink);font:var(--ui-weight-semibold) var(--ui-text-md)/1.3 var(--ui-font)}
.hx-dir-tag{padding:calc(var(--ui-space-1) / 2) var(--ui-space-2);border-radius:var(--ui-radius-pill);font:var(--ui-weight-semibold) var(--ui-text-xs)/1.4 var(--ui-font);white-space:nowrap}
.hx-dir-agent{background:var(--ui-surface-soft);color:var(--ui-muted);border:1px solid var(--ui-border)}
.hx-dir-yours{background:var(--ui-draft-soft);color:var(--ui-draft);border:1px solid var(--ui-draft)}
.hx-btn.hx-dir-pick{margin:0 0 0 var(--ui-space-1);white-space:nowrap;transition:background var(--ui-motion-duration) var(--ui-motion-ease)}
.hx-dir-pick:hover{background:var(--ui-surface-soft)}
.hx-dir-pick:active{background:var(--ui-muted-soft)}
.hx-dir-head [hidden]{display:none}
:is(.hx-dirs-toggle,.hx-dir-head) button:focus-visible{outline:var(--ui-focus-width) solid var(--ui-focus);outline-offset:var(--ui-focus-offset)}
.hx-dir-why,.hx-dir-gives{grid-row:2;max-width:72ch;margin:0;color:var(--ui-ink);font:var(--ui-text-sm)/1.4 var(--ui-font)}
.hx-dir-gives{grid-row:3;color:var(--ui-muted)}
.hx-dir-gives b{color:var(--ui-ink);font-weight:var(--ui-weight-semibold)}
.hx-dir-frames{grid-row:4;position:relative;display:flex;align-items:flex-start;gap:var(--ui-space-5);min-width:0;margin-top:var(--ui-space-2)}
.hx-dir-frames>.hx-mockup-slot[data-off]{position:absolute;top:0;left:0;right:0;height:0;overflow:hidden;visibility:hidden}
.hx-directions .hx-mockup-label{display:none}
body.hx-mockup-before .hx-directions :is(.hx-dirs-toggle,.hx-dir-head,.hx-dir-why,.hx-dir-gives,.hx-dir-other){display:none}
body.hx-mockup-before .hx-directions .hx-mockup-label{display:block}
/* a prototype mockup (mockups #prototype-route): each frame the running route at its viewport's width, as tall as its
   content; Prototype not running in their place (#prototype-down) */
.hx-mockup[hidden]{display:none}
.hx-proto-frame{display:block;border:0;background:var(--ui-page);outline:1px solid var(--ui-border)}
.hx-proto-down{display:flex;flex-direction:column;gap:var(--ui-space-2);padding:var(--ui-space-5);border:1px solid var(--ui-border);border-radius:var(--ui-radius);background:var(--ui-surface);color:var(--ui-ink);font:var(--ui-text-sm)/1.4 var(--ui-font)}
.hx-proto-down strong{font-weight:var(--ui-weight-semibold)}
.hx-proto-down code{font:var(--ui-weight-medium) var(--ui-text-xs) var(--ui-mono);color:var(--ui-muted)}
/* a held prototype route's question marks, each at its region's corner (mockups #mockup-questions-show) */
.hx-proto-box{position:fixed;pointer-events:none;z-index:701}
/* each round's video under the frames (mockups #round-video-show): the player, then its title, commit, and captions */
.hx-mockup:has(+ .hx-rounds){padding-bottom:var(--ui-space-5)}
.hx-rounds{display:flex;flex-direction:column;gap:var(--ui-space-4);box-sizing:border-box;width:100%;padding:0 calc(var(--ui-space-6) + var(--ui-space-5)) calc(var(--hx-dock-space,var(--ui-space-6)) + 2 * var(--ui-space-6)) var(--ui-space-5)}
.hx-round{display:flex;gap:var(--ui-space-4);align-items:flex-start}
.hx-round-before{box-sizing:border-box;padding:var(--ui-space-5)}
.hx-round-video{display:block;flex:0 0 auto;width:400px;max-width:100%;aspect-ratio:16/9;background:var(--ui-ink);border-radius:var(--ui-radius)}
.hx-round[data-viewport=phone] .hx-round-video{width:auto;height:min(70vh,560px);aspect-ratio:390/844}
.hx-round-meta{display:flex;flex-direction:column;align-items:flex-start;gap:var(--ui-space-2);padding-top:var(--ui-space-1);color:var(--ui-ink);font:var(--ui-text-sm)/1.4 var(--ui-font)}
.hx-round-title{font-weight:var(--ui-weight-semibold)}
.hx-round-meta code{font:var(--ui-weight-medium) var(--ui-text-xs) var(--ui-mono)}
.hx-round-note{color:var(--ui-muted);font-size:var(--ui-text-xs)}
.hx-rounds-earlier{display:flex;flex-direction:column;gap:var(--ui-space-4)}
.hx-rounds-earlier[hidden]{display:none}
.hx-round-player{flex:0 0 auto;max-width:100%}
/* a moment of a video (threads #video-moment): in comment mode a video takes a click or a dragged box; under a video with
   threads, a rail as wide as it marks each at its moment, numbered and colored by status as its pin is; a box on its
   frame while it is paused at a thread's moment */
body.hx-comment [data-anchor] video{cursor:crosshair;touch-action:none}
.hx-video-box{position:absolute;z-index:690;box-sizing:border-box;border:2px solid var(--ui-attention);border-radius:var(--ui-radius-sm);box-shadow:0 0 0 4px var(--ui-pin-ring);pointer-events:none}
.hx-mockup-fit .hx-video-box{zoom:calc(1 / var(--hx-zoom,1))}
.hx-video-rail{position:relative;display:block;box-sizing:border-box;height:28px;margin-top:var(--ui-space-2);border-bottom:1px solid var(--ui-border)}
.hx-video-mark{position:absolute;top:2px;width:22px;height:22px;transform:translateX(-50%);margin:0;padding:0;border:none;border-radius:50% 50% 50% 4px;display:grid;place-items:center;cursor:pointer;color:var(--ui-page);font:var(--ui-weight-semibold) var(--ui-text-xs) var(--ui-font)}
.hx-video-mark[data-s=draft],.hx-video-mark[data-s=pending]{background:var(--ui-draft)}
.hx-video-mark[data-s=acknowledged]{background:var(--ui-ack)}
.hx-video-mark[data-s=resolved]{background:var(--ui-surface);color:var(--ui-resolved);border:2px solid var(--ui-resolved)}
.hx-video-mark.active{box-shadow:0 0 0 4px var(--ui-pin-ring)}
.hx-video-time{position:absolute;right:0;top:4px;color:var(--ui-muted);font:var(--ui-text-xs) var(--ui-mono)}
/* a mockup's walkthrough and questions show in the chrome, never as the page's own (mockups #markup-steps, #markup-questions) */
.hx-mockup-markup{display:none!important}
/* a step drawn at once, as the page opens or a version changes: no motion */
.hx-mockup-still *{transition-duration:0s!important;transition-delay:0s!important;animation-duration:0s!important;animation-delay:0s!important}
/* question mark (mockups #mockup-questions-show) on a part an open question is about, at the chrome's size at any zoom;
   the focused question's parts ringed as an arrival is. It sits among the mockup's own styles, whose rules for its
   buttons never reach it (the pins' rule above). */
.hx-qmark{position:absolute!important;top:-12px!important;right:-12px!important;left:auto!important;bottom:auto!important;z-index:701!important;width:24px!important;height:24px!important;border:none!important;border-radius:50%!important;background:var(--ui-attention)!important;color:var(--ui-page)!important;display:grid!important;place-items:center!important;font:var(--ui-weight-semibold) var(--ui-text-sm)/1 var(--ui-font)!important;box-shadow:0 2px 8px var(--ui-shadow)!important}
.hx-mockup-fit .hx-qmark{zoom:calc(1 / var(--hx-zoom,1))}
.hx-mockup-fit .hx-asked{outline:calc(2px / var(--hx-zoom,1)) solid var(--ui-draft);outline-offset:calc(var(--ui-space-1) / var(--hx-zoom,1))}
/* chosen answer button: pressed, checked, and outlined, not color alone; the check stays out of its name */
:is(button[data-spec-choice],.hx-choice)[aria-pressed=true]{border:2px solid var(--ui-draft);background:var(--ui-draft-soft);color:var(--ui-ink);font-weight:var(--ui-weight-semibold)}
:is(button[data-spec-choice],.hx-choice)[aria-pressed=true]::before{content:"\\2713";content:"\\2713" / "";margin-right:var(--ui-space-1)}
/* badge */
.hx-badge{font:var(--ui-weight-semibold) var(--ui-text-xs) var(--ui-font);color:var(--ui-resolved);background:var(--ui-resolved-soft);border-radius:var(--ui-radius-pill);padding:0 var(--ui-space-2);line-height:1.6;margin-left:var(--ui-space-2);vertical-align:middle}
/* See mockup chip (mockups #chip-look): the chrome's pill radius and link color, in the flow after the block's text */
.hx-mockup-chip{display:inline-block;box-sizing:border-box;user-select:none;-webkit-user-select:none;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:middle;margin-left:var(--ui-space-2);padding:0 var(--ui-space-2);border:1px solid currentColor;border-radius:var(--ui-radius-pill);background:var(--ui-surface);color:var(--ui-link);font:var(--ui-weight-semibold) var(--ui-text-xs)/1.6 var(--ui-font);text-decoration:none;text-indent:0}
.hx-mockup-chip:hover{background:var(--ui-action-soft)}
body.hx-comment [data-anchor] a.hx-mockup-chip{cursor:pointer}
/* Screens index (mockups #screen-index-look, spec-page-design #surface-screen-index): chrome on the page color under the
   spec's header, a list in hairlines, each row one link: the status mark, the name at the panel's text step, muted meta,
   the open count in attention, the chevron, and the set's ring (above), unclipped, the end rows rounded instead. Its
   columns are the mockup's 220px, 1fr, 220px at the document's full 1080px, in proportion below it; on a phone each
   row's meta sits under its name. Prefixed to outrank the document's own nav, p, and a rules. */
nav.hx-screens{display:block;margin:var(--ui-space-5) 0 0;padding:0;background:var(--ui-page);color:var(--ui-ink);font:var(--ui-weight-regular) var(--ui-text-xs)/1.4 var(--ui-font)}
.hx-screens .hx-screens-head{display:flex;align-items:baseline;gap:var(--ui-space-2);max-width:none;margin:0;color:var(--ui-ink);font:var(--ui-weight-semibold) var(--ui-text-sm)/1.4 var(--ui-font)}
.hx-screens .hx-screens-count{color:var(--ui-muted);font-weight:var(--ui-weight-regular);font-size:var(--ui-text-xs)}
.hx-screens ul{list-style:none;margin:0;padding:0;border:1px solid var(--ui-border);border-radius:var(--ui-radius)}
.hx-screens li{max-width:none;margin:0;padding:0}
.hx-screens li+li{border-top:1px solid var(--ui-border)}
.hx-screens .hx-screen{display:grid;grid-template-columns:16px minmax(0,220fr) minmax(0,526fr) minmax(0,220fr) 16px;grid-template-areas:"mark name meta open go";align-items:center;column-gap:var(--ui-space-3);box-sizing:border-box;min-height:44px;padding:var(--ui-space-2) var(--ui-space-4);color:var(--ui-ink);text-decoration:none;font:var(--ui-text-xs)/1.4 var(--ui-font);transition:background var(--ui-motion-duration) var(--ui-motion-ease)}
.hx-screens .hx-screen:hover{background:var(--ui-surface-soft)}
.hx-screens .hx-screen:active{background:var(--ui-muted-soft)}
.hx-screens li:first-child .hx-screen{border-top-left-radius:var(--ui-radius);border-top-right-radius:var(--ui-radius)}
.hx-screens li:last-child .hx-screen{border-bottom-left-radius:var(--ui-radius);border-bottom-right-radius:var(--ui-radius)}
.hx-screens .hx-screen svg{display:block}
.hx-screens .hx-screen-mark{grid-area:mark}
.hx-screens .hx-screen-name{grid-area:name;min-width:0;overflow-wrap:anywhere;color:var(--ui-ink);font:var(--ui-weight-medium) var(--ui-text-sm)/1.4 var(--ui-font)}
.hx-screens .hx-screen-meta{grid-area:meta;display:grid;grid-template-columns:minmax(0,1fr) 140px;column-gap:var(--ui-space-3);min-width:0;color:var(--ui-muted)}
.hx-screens .hx-screen-views{grid-column:2}
.hx-screens .hx-screen-dirs{grid-column:1}
.hx-screens .hx-screen-kind code{margin-left:var(--ui-space-1);padding:0;background:none;color:var(--ui-muted);font:var(--ui-text-xs) var(--ui-mono);overflow-wrap:anywhere}
.hx-screens .hx-screen-open{grid-area:open;color:var(--ui-muted)}
.hx-screens .hx-screen-open.hx-waiting{color:var(--ui-attention);font-weight:var(--ui-weight-medium)}
.hx-screens .hx-screen-go{grid-area:go;color:var(--ui-muted)}
/* mockups with open threads, at the head of the panel (mockups #accept-gate) */
.hx-mockup-open{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:var(--ui-space-1)}
.hx-mockup-open a{color:var(--ui-link);font:var(--ui-weight-medium) var(--ui-text-sm)/1.4 var(--ui-font)}
/* jev note */
.hx-jev-note{box-sizing:border-box;max-width:720px;margin:var(--ui-space-3) auto 0;padding:var(--ui-space-1) var(--ui-space-3);border:1px solid var(--ui-border);border-radius:var(--ui-radius);background:var(--ui-draft-soft);color:var(--ui-draft);font:var(--ui-weight-semibold) var(--ui-text-xs)/1.35 var(--ui-font)}
[data-hx-jev-type=no-behavior-change]{color:var(--spec-muted)!important}
[data-hx-jev-type=no-behavior-change] :is(h1,h2,h3,h4,h5,h6,p,li,td,th,blockquote,code,strong,em,a){color:inherit!important}
/* jev markers */
.hx-jev-marker{position:absolute;top:.35em;left:calc(100% + 10px);z-index:640;box-sizing:border-box;width:10px;height:10px;margin:0;padding:0;border:0;border-radius:50%;background:var(--ui-marker);color:var(--ui-on-marker);cursor:pointer;display:grid;place-items:center;font:var(--ui-weight-semibold) var(--ui-text-xs)/1 var(--ui-font)}
.hx-jev-marker::before{content:"";position:absolute;inset:-10px 0 -10px -16px}
.hx-jev-marker[data-attention=true]{width:14px;height:14px;background:var(--ui-marker-important)}
.hx-jev-marker[data-attention=true]::after{content:"!"}
.hx-jev-marker[data-passed=true]{width:14px;height:14px;background:var(--ui-marker-pass)}
.hx-jev-marker[data-warning=true]{background:var(--ui-marker-warning)}
.hx-jev-marker[data-passed=true]::after{content:"\\2713"}
.hx-jev-marker[data-inset=true]{left:auto;right:0}
.hx-jev-marker:hover,.hx-jev-marker[aria-expanded=true]{box-shadow:0 0 0 3px var(--ui-pin-ring)}
.hx-jev-marker[data-pending=true]{background:transparent;border:2px solid var(--ui-marker);border-right-color:transparent;animation:hx-jev-spin 1s linear infinite}
@keyframes hx-jev-spin{to{transform:rotate(360deg)}}
/* rule card (project-rules #card): label, title, verbatim quote, source, then rejects left and one primary rightmost */
.hx-rule-card{display:grid;gap:var(--ui-space-2);min-width:0;color:var(--ui-ink);font:var(--ui-text-sm)/1.55 var(--ui-font)}
.hx-rule-card-label{font:var(--ui-weight-semibold) var(--ui-text-xs)/1.3 var(--ui-font);letter-spacing:.02em;color:var(--ui-muted)}
.hx-rule-card-title{font:var(--ui-weight-semibold) var(--ui-text-md)/1.35 var(--ui-font);color:var(--ui-ink);overflow-wrap:anywhere}
.hx-rule-card-quote{margin:var(--ui-space-1) 0 0;padding:0 0 0 var(--ui-space-3);border-left:3px solid var(--ui-border);border-radius:0;font:inherit;font-style:normal;color:var(--ui-ink);overflow-wrap:anywhere;white-space:normal}
.hx-rule-card-source{justify-self:start;display:inline-flex;gap:var(--ui-space-1);align-items:center;font-size:var(--ui-text-xs);color:var(--ui-ack);text-decoration:none;overflow-wrap:anywhere}
.hx-rule-card-source-text{text-decoration:underline;text-underline-offset:2px}
.hx-rule-card-source-plain{justify-self:start;font-size:var(--ui-text-xs);color:var(--ui-muted);overflow-wrap:anywhere}
.hx-rule-card-actions{display:flex;flex-wrap:wrap;align-items:center;gap:var(--ui-space-2) var(--ui-space-4);margin-top:var(--ui-space-1);padding-top:var(--ui-space-3);border-top:1px solid var(--ui-border)}
.hx-rule-card-actions button{white-space:nowrap}
.hx-rule-card-actions .hx-btn.pri{margin:0 0 0 auto}
.hx-rule-card-reject{margin:0;padding:var(--ui-space-1) 0;min-height:var(--ui-space-6);border:0;background:none;font:var(--ui-weight-semibold) var(--ui-text-xs) var(--ui-font);color:var(--ui-ink);text-decoration:underline;text-decoration-color:var(--ui-border);text-decoration-thickness:1px;text-underline-offset:3px;cursor:pointer;border-radius:var(--ui-radius-sm)}
.hx-rule-card-reject:hover{text-decoration-color:currentColor}
.hx-jev-pop:has(.hx-rule-card){max-width:min(440px,calc(100vw - 16px));padding:var(--ui-space-4)}
.hx-jev-pop-note:has(>.hx-rule-card)+.hx-jev-pop-note,.hx-jev-pop-note+.hx-jev-pop-note:has(>.hx-rule-card){margin-top:var(--ui-space-2);padding-top:var(--ui-space-3);border-top:1px solid var(--ui-border)}
/* jev popover */
.hx-jev-pop{position:fixed;z-index:880;box-sizing:border-box;width:max-content;min-width:180px;max-width:min(340px,calc(100vw - 16px));max-height:calc(100vh - 16px);overflow:auto;padding:var(--ui-space-2) var(--ui-space-3);border:1px solid var(--ui-ink);border-radius:var(--ui-radius);background:var(--ui-surface);color:var(--ui-ink);box-shadow:0 8px 28px var(--ui-shadow);font:var(--ui-text-sm)/1.4 var(--ui-font)}
.hx-jev-pop[hidden]{display:none}
.hx-jev-pop-notes{margin:0;padding:0;list-style:none;display:grid;gap:var(--ui-space-1)}
.hx-jev-pop-note{display:grid;gap:var(--ui-space-1)}
.hx-jev-pop-text{font-weight:var(--ui-weight-semibold);color:var(--ui-ink);overflow-wrap:anywhere}
.hx-jev-pop-note[data-attention=true] .hx-jev-pop-text{color:var(--ui-fail)}
.hx-jev-pop-note[data-group=neutral] .hx-jev-pop-text{font-weight:var(--ui-weight-semibold);color:var(--ui-muted)}
a.hx-jev-pop-text{text-decoration:underline;text-underline-offset:2px}
.hx-jev-pop-quote{color:var(--ui-ink);overflow-wrap:anywhere}
.hx-jev-pop-sentence{display:none;font-size:var(--ui-text-xs);color:var(--ui-muted);overflow-wrap:anywhere}
.hx-jev-pop[data-measure] .hx-jev-pop-sentence,.hx-jev-pop-note:hover .hx-jev-pop-sentence,.hx-jev-pop-note:focus-within .hx-jev-pop-sentence{display:block}
.hx-jev-pop-meta{display:flex;flex-wrap:wrap;align-items:baseline;gap:var(--ui-space-1) var(--ui-space-2);font-size:var(--ui-text-xs);color:var(--ui-muted);overflow-wrap:anywhere}
.hx-jev-pop-link{color:var(--ui-ack);text-decoration:underline;text-underline-offset:2px}
.hx-jev-pop-diff{font-size:var(--ui-text-xs);color:var(--ui-ink);overflow-wrap:anywhere}
.hx-jev-pop-diff del{text-decoration:none;background:var(--ui-fail-soft)}
.hx-jev-pop-diff ins{text-decoration:none;background:var(--ui-pass-soft)}
.hx-jev-pop-diff-legend{font-size:var(--ui-text-xs);color:var(--ui-muted);margin-bottom:var(--ui-space-1)}
.hx-jev-pop-diff-legend del{text-decoration:none;background:var(--ui-fail-soft);padding:0 var(--ui-space-1)}
.hx-jev-pop-diff-legend ins{text-decoration:none;background:var(--ui-pass-soft);padding:0 var(--ui-space-1)}
.hx-jev-pop-actions{display:flex;flex-wrap:wrap;gap:var(--ui-space-1)}
.hx-jev-pop-actions .hx-btn{margin:0;border-color:var(--ui-ack);background:var(--ui-surface);color:var(--ui-ack)}
.hx-jev-pop-batch{display:inline-flex;align-items:center;gap:var(--ui-space-1);white-space:nowrap}
.hx-jev-pop-more{margin:0;min-height:var(--ui-space-6);padding:0;border:0;background:none;font:inherit;font-size:var(--ui-text-xs);color:var(--ui-ack);text-decoration:underline;text-underline-offset:2px;cursor:pointer;align-self:center}
/* jev thread label */
.hx-jev-thread-label{flex:0 0 auto;color:var(--ui-resolved);background:var(--ui-resolved-soft);border-radius:var(--ui-radius-pill);padding:0 var(--ui-space-2);line-height:1.6;font-size:var(--ui-text-xs);font-weight:var(--ui-weight-semibold);white-space:nowrap}
.hx-jev-thread-label[data-state=unavailable]{color:var(--ui-error);background:var(--ui-fail-soft)}
/* orphan hint */
.hx-orphan-hint{display:flex;align-items:center;flex-wrap:wrap;gap:var(--ui-space-1);margin:var(--ui-space-2) 0;padding:var(--ui-space-2);border-left:3px solid var(--ui-draft);background:var(--ui-draft-soft);color:var(--ui-draft);font-size:var(--ui-text-xs);line-height:1.35}
.hx-orphan-hint>span{flex:1 1 100%}
.hx-place-notice{margin:var(--ui-space-2) 0;padding:var(--ui-space-2);border-left:3px solid var(--ui-draft);background:var(--ui-draft-soft);color:var(--ui-draft);font-size:var(--ui-text-xs);line-height:1.35}
.hx-orphan-hint .hx-btn{margin:0}
.hx-pin-jev{position:absolute;left:calc(100% + 4px);top:50%;transform:translateY(-50%);width:max-content;max-width:120px;color:var(--ui-resolved);background:var(--ui-resolved-soft);border:1px solid var(--ui-resolved);border-radius:var(--ui-radius-pill);padding:0 var(--ui-space-1);font:var(--ui-weight-semibold) var(--ui-text-xs)/1.4 var(--ui-font);white-space:nowrap;pointer-events:none}
/* banner */
.hx-banner{position:fixed;top:0;left:0;right:0;background:var(--ui-pass);color:var(--ui-page);font:var(--ui-weight-semibold) var(--ui-text-sm) var(--ui-font);padding:var(--ui-space-2) var(--ui-space-4);z-index:950;display:flex;gap:var(--ui-space-3);align-items:center;justify-content:center}
/* error overlay (#error-overlay): under the top line, at most a fifth of the screen, its lines scrolling within it */
.hx-errors{position:fixed;top:var(--hx-line-space,0px);left:0;right:0;z-index:870;box-sizing:border-box;max-height:20vh;overflow:auto;padding:var(--ui-space-2) var(--ui-space-3);background:var(--ui-error);color:var(--ui-page);font:var(--ui-text-xs)/1.4 var(--ui-mono);white-space:pre-wrap}
.hx-errors p{margin:0;max-width:none;font:inherit;color:inherit}
.hx-errors p+p{margin-top:var(--ui-space-1)}
/* focus error */
.hx-focus-error{position:fixed;top:calc(var(--hx-top-space,0px) + var(--ui-space-3) + env(safe-area-inset-top));left:50%;transform:translateX(-50%);max-width:calc(100vw - 24px);box-sizing:border-box;padding:var(--ui-space-2) var(--ui-space-3);border-radius:var(--ui-radius);background:var(--ui-error);color:var(--ui-page);font:var(--ui-weight-semibold) var(--ui-text-xs) var(--ui-font);z-index:970;box-shadow:0 6px 20px var(--ui-shadow)}
/* toast */
.hx-toast{position:fixed;bottom:76px;left:50%;transform:translateX(-50%);background:var(--ui-ink);color:var(--ui-page);font:var(--ui-weight-semibold) var(--ui-text-xs) var(--ui-font);border-radius:var(--ui-radius);padding:var(--ui-space-2) var(--ui-space-4);box-shadow:0 8px 28px var(--ui-shadow);z-index:960;opacity:0;transition:opacity .25s;pointer-events:none}
.hx-toast.show{opacity:1}
.hx-banner button{font:var(--ui-weight-semibold) var(--ui-text-xs) var(--ui-font);border:1px solid var(--ui-page);background:transparent;color:var(--ui-page);border-radius:var(--ui-radius-sm);min-height:var(--ui-space-6);padding:var(--ui-space-2) var(--ui-space-3);cursor:pointer}
/* host source (comment-layer #ui-from) */
.hx-host-from{display:flex;flex-wrap:wrap;align-items:baseline;gap:0 var(--ui-space-1);margin-top:var(--ui-space-1);font-size:var(--ui-text-xs);color:var(--ui-muted);min-width:0}
.hx-host-kind{font-weight:var(--ui-weight-semibold);color:var(--ui-ink)}
.hx-host-from a{color:var(--ui-ack);text-decoration:underline;text-underline-offset:2px;overflow-wrap:anywhere;min-width:0}
/* starters (comment-layer #ui-starters) */
.hx-starters{display:flex;flex-wrap:wrap}
/* comment layer (comment-layer #ui-style): its own overlay on a host page, the host's look untouched */
:host{all:initial;font:var(--ui-text-sm)/1.4 var(--ui-font);color:var(--ui-ink)}
.hx-layer-control,.hx-quote-mark,.hx-layer-box{position:fixed}
.hx-layer-control{z-index:700;box-sizing:border-box;width:24px;height:24px;margin:0;padding:0;display:grid;place-items:center;border:1px solid var(--ui-border);border-radius:var(--ui-radius-sm);background:var(--ui-surface);color:var(--ui-muted);cursor:pointer;box-shadow:0 2px 8px var(--ui-shadow)}
.hx-layer-control:hover,.hx-layer-control[aria-expanded=true]{color:var(--ui-ink);border-color:var(--ui-muted)}
.hx-layer-control svg{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.hx-layer .hx-pin{position:fixed!important}
.hx-quote-mark{z-index:640;pointer-events:none;background:var(--ui-pin-ring);border-bottom:2px solid var(--ui-draft)}
.hx-layer-box{z-index:800;box-sizing:border-box;display:flex;flex-direction:column;width:380px;max-width:calc(100vw - 16px);max-height:calc(100vh - 16px);overflow:hidden;background:var(--ui-page);color:var(--ui-ink);border:1px solid var(--ui-border);border-radius:var(--ui-radius);box-shadow:0 8px 28px var(--ui-shadow);font:var(--ui-text-sm) var(--ui-font)}
.hx-layer-head{display:flex;align-items:center;gap:var(--ui-space-2);padding:var(--ui-space-2) var(--ui-space-2) var(--ui-space-2) var(--ui-space-3);border-bottom:1px solid var(--ui-border)}
.hx-layer-title{flex:1 1 auto;min-width:0;font-weight:var(--ui-weight-semibold);overflow-wrap:anywhere}
.hx-layer-kind{flex:0 1 auto;min-width:0;font-size:var(--ui-text-xs);color:var(--ui-muted);overflow-wrap:anywhere}
.hx-layer-close{flex:0 0 auto;width:var(--ui-space-6);height:var(--ui-space-6);margin:0;padding:0;display:grid;place-items:center;border:1px solid var(--ui-border);border-radius:var(--ui-radius-sm);background:var(--ui-surface);color:var(--ui-muted);cursor:pointer;font:var(--ui-text-lg)/1 var(--ui-font)}
.hx-layer-close:hover{background:var(--ui-page);color:var(--ui-ink)}
.hx-layer-box .hx-stream{flex:1 1 auto}
.hx-layer-box .hx-stream:has(>.hx-focused){min-height:min(60vh,420px)}
.hx-layer-box .hx-threads>[data-act=comment]{align-self:flex-start;margin:0}
.hx-layer-box .hx-handoff{flex-wrap:wrap;gap:var(--ui-space-1) var(--ui-space-2)}
.hx-layer-box .hx-offline{font-size:var(--ui-text-xs);font-weight:var(--ui-weight-semibold);color:var(--ui-draft)}
.hx-layer .hx-toast{bottom:18px}
/* the comment UI page a shell frames (shell #shell-ui-page): the panel is the whole page, its head closing it */
.hx-comment-ui{margin:0;background:var(--ui-page)}
.hx-comment-ui .hx-panel{inset:0;width:auto;height:auto;max-height:none;border-left:0;padding:0 0 env(safe-area-inset-bottom)}
.hx-comment-ui .hx-panel[hidden]{display:none}
.hx-comment-ui .hx-panel-head{padding-left:var(--ui-space-4)}
.hx-panel-close{position:absolute;top:var(--ui-space-2);right:var(--hx-gear-inset);width:var(--hx-head-control);height:var(--hx-head-control);border:1px solid var(--ui-border);background:var(--ui-surface);border-radius:var(--ui-radius-sm);color:var(--ui-muted);cursor:pointer;display:grid;place-items:center;padding:0}
.hx-panel-close:hover{background:var(--ui-page);color:var(--ui-ink)}
.hx-comment-ui .hx-handoff .hx-btn{flex:0 1 auto;white-space:normal}
.hx-comment-ui .hx-point{border-color:transparent;background:transparent}
.hx-comment-ui .hx-offline{font-size:var(--ui-text-xs);font-weight:var(--ui-weight-semibold);color:var(--ui-draft)}
.hx-comment-ui .hx-toast{bottom:64px}
/* mobile */
@media(max-width:640px){
.hx-screens .hx-screen{grid-template-columns:16px minmax(0,1fr) 16px;grid-template-areas:"mark name go" ". meta go" ". open go";row-gap:calc(var(--ui-space-1) / 2);padding:var(--ui-space-3)}
.hx-screens .hx-screen-meta{display:flex;flex-wrap:wrap;column-gap:var(--ui-space-1)}
.hx-screens .hx-screen-kind::after{content:" ·"}
.hx-toolbar{left:var(--ui-space-3);right:var(--ui-space-3);bottom:calc(10px + env(safe-area-inset-bottom));transform:none;max-width:none;display:flex;flex-wrap:wrap;justify-content:stretch;gap:var(--ui-space-1);padding:var(--ui-space-1)}
.hx-toolbar button{min-height:44px;flex:1 1 auto;padding:var(--ui-space-2) var(--ui-space-3);touch-action:manipulation}
.hx-toolbar .hx-handoff{display:contents}
.hx-toolbar .hx-status{flex:1 0 100%;min-width:0;padding:0 var(--ui-space-2) var(--ui-space-1);overflow:hidden;text-align:center;text-overflow:ellipsis;white-space:nowrap}
.hx-panel{width:100vw;height:100dvh;max-height:100dvh;border-left:0;box-sizing:border-box;padding-bottom:calc(var(--hx-dock-space,0px) + var(--ui-space-3) + env(safe-area-inset-bottom))}
body.hx-panel-open{padding-right:0;overflow:hidden}
.hx-mockup{flex-direction:column;gap:var(--ui-space-4);padding:var(--ui-space-3) var(--ui-space-3) calc(var(--hx-dock-space,112px) + var(--ui-space-5) + env(safe-area-inset-bottom))}
.hx-mockup-slot{flex:none;width:100%}
.hx-dir-row{display:flex;flex-direction:column;gap:var(--ui-space-6)}
.hx-dir{display:flex;flex-direction:column;gap:var(--ui-space-2);width:100%}
.hx-dir-frames{flex-direction:column;gap:var(--ui-space-4)}
.hx-dirs-toggle .hx-btn{min-width:88px}
.hx-rounds{padding:0 var(--ui-space-3) calc(var(--hx-dock-space,112px) + var(--ui-space-5) + env(safe-area-inset-bottom))}
.hx-round{flex-direction:column;gap:var(--ui-space-2)}
.hx-round-video{width:100%;height:auto}
.hx-round[data-viewport=phone] .hx-round-video{width:auto;max-width:100%;height:min(60vh,560px)}
.hx-round-earlier{min-height:44px}
.hx-round-player{align-self:stretch}
.hx-video-rail{height:44px}
.hx-video-mark{width:36px;height:36px;touch-action:manipulation}
.hx-video-time{top:12px}
.hx-panel-head{--hx-head-control:44px;--hx-toggle-inset:var(--ui-space-1);min-height:56px;padding-top:var(--ui-space-4)}
.hx-panel-toggle,.hx-panel-gear{top:var(--ui-space-1);touch-action:manipulation}
.hx-thread-dock{top:calc(var(--hx-top-space,0px) + var(--ui-space-2));right:var(--ui-space-2);padding:var(--ui-space-1)}
body.hx-panel-open :is(.hx-top-line,.hx-errors){display:none}
.hx-dock-open,.hx-dock-thread{width:44px;height:44px;touch-action:manipulation}
.hx-dock-threads{max-height:calc(100dvh - var(--hx-top-space,0px) - 68px)}
.hx-threads{padding:var(--ui-space-3);overscroll-behavior:contain}
.hx-composer textarea{min-height:120px;font-size:var(--ui-text-md);padding:var(--ui-space-2) var(--ui-space-3)}
.hx-choice{min-height:44px;touch-action:manipulation}
.hx-focused>.hx-composer textarea{min-height:44px}
.hx-focused.hx-sheet{position:fixed;top:50dvh;left:0;right:0;bottom:0;z-index:880;border:0;border-top-left-radius:calc(2 * var(--ui-radius));border-top-right-radius:calc(2 * var(--ui-radius));border-bottom-left-radius:0;border-bottom-right-radius:0;box-shadow:0 -12px 32px var(--ui-shadow);padding-bottom:calc(var(--hx-dock-space,112px) + var(--ui-space-5) + env(safe-area-inset-bottom))}
.hx-sheet .hx-grip{display:block;align-self:center;width:36px;height:4px;margin:var(--ui-space-2) 0 0;border-radius:var(--ui-radius-pill);background:var(--ui-border)}
.hx-focused-stream{overscroll-behavior:contain}
body.hx-focused-sheet{padding-bottom:50dvh}
body.hx-focused-sheet .hx-thread-dock{display:none}
body.hx-focused-sheet .hx-thread-ring{z-index:870;box-shadow:0 0 0 3px var(--ui-pin-ring),0 0 0 100vmax var(--ui-veil)}
.hx-btn{min-height:44px;padding:var(--ui-space-2) var(--ui-space-3);touch-action:manipulation}
.hx-handoff{gap:var(--ui-space-2);flex-wrap:wrap;padding:var(--ui-space-3) var(--ui-space-3) calc(var(--ui-space-3) + env(safe-area-inset-bottom))}
.hx-handoff .hx-note{flex:1 1 auto}
.hx-handoff .hx-btn{flex:1 1 auto;margin:0}
.hx-pin{width:44px!important;height:44px!important;touch-action:manipulation}
.hx-qmark{width:44px!important;height:44px!important;top:-16px!important;right:-16px!important;touch-action:manipulation}
[data-anchor]:has(> .hx-pin[data-column]){padding-right:calc(var(--ui-space-6) + var(--ui-space-4));min-height:calc(.5lh + 66px)}
tr[data-anchor]:has(> .hx-pin[data-column]) > :has(> .hx-jev-marker){padding-right:calc(var(--ui-space-6) + var(--ui-space-4));height:calc(.5lh + 66px)}
.hx-pin-jev{left:auto;right:calc(100% + 4px);max-width:110px;text-align:right}
.hx-jev-note{margin:var(--ui-space-2) var(--ui-space-4) 0}
.hx-jev-marker::before{inset:-14px 0 -14px -28px}
.hx-jev-pop{left:var(--ui-space-3);right:var(--ui-space-3);top:auto;bottom:calc(10px + var(--hx-dock-space,64px) + var(--ui-space-2) + env(safe-area-inset-bottom));width:auto;max-width:none;max-height:40dvh;padding:var(--ui-space-3)}
.hx-jev-pop-actions .hx-btn,.hx-jev-pop-more{min-height:44px}
.hx-rule-card-reject{min-height:44px;padding:var(--ui-space-2) 0}
.hx-jev-pop:has(.hx-rule-card){max-width:none;padding:var(--ui-space-3)}
.hx-banner{align-items:flex-start;flex-wrap:wrap;padding:calc(var(--ui-space-2) + env(safe-area-inset-top)) var(--ui-space-3) var(--ui-space-2);text-align:center}
.hx-banner button{min-height:44px;padding:var(--ui-space-2) var(--ui-space-3);touch-action:manipulation}
.hx-toast{bottom:calc(112px + env(safe-area-inset-bottom));max-width:calc(100vw - 24px);box-sizing:border-box;text-align:center}
.hx-layer-control{width:44px;height:44px;touch-action:manipulation}
.hx-layer-close{width:44px;height:44px;touch-action:manipulation}
.hx-layer-box{width:auto;max-width:none;max-height:75dvh}
.hx-layer .hx-toast{bottom:calc(18px + env(safe-area-inset-bottom))}
.hx-comment-ui .hx-panel{width:auto;height:auto;max-height:none}
.hx-comment-ui .hx-panel-close{top:var(--ui-space-1);touch-action:manipulation}
.hx-comment-ui .hx-handoff .hx-btn{flex:1 1 auto}
.hx-comment-ui .hx-toast{bottom:calc(72px + env(safe-area-inset-bottom))}
}
@media(prefers-reduced-motion:reduce){
.hx-thread-dock,.hx-thread-ring,.hx-toast{transition:none}
.hx-jev-marker[data-pending=true]{animation:none}
}`;

// spec-page-design #top-line, review-index #index-link: one fixed line at the top of an HTTP page holding the back link
// and the range bar; the body and the dock start under it, at the height it measures (--hx-top-space). An embed host
// keeps its own look, so it gets no line.
function mountTopLine() {
  if (EMBED_REVIEW_DIR || !['http:', 'https:'].includes(OWN_ADDRESS.protocol) || document.getElementById('hx-top-line')) return;
  const line = document.createElement('div');
  line.className = 'hx-top-line';
  line.id = 'hx-top-line';
  const indexLink = line.appendChild(document.createElement('a'));
  indexLink.className = 'hx-service-index-link';
  indexLink.href = new URL('/', OWN_ADDRESS).href;
  indexLink.innerHTML = ICONS['chevron-left'] + '<span>Review index</span>';
  indexLink.dataset.specChatNavigation = 'index';
  if (MOCKUP_SPEC) { // mockups #review-chrome: the mockup's spec, after the back link, named by its file name in words
    const specLink = line.appendChild(document.createElement('a'));
    specLink.className = 'hx-spec-link';
    specLink.id = 'hx-spec-link';
    specLink.href = mockupSpecAddress();
    specLink.textContent = sentenceCase(MOCKUP_SPEC.replace(/-/g, ' ')) + ' spec';
  }
  document.body.insertBefore(line, document.body.firstChild);
  mountRangeBar(line);
  // Measured as the line goes in, so the page is already under it when a place is restored. Any change the page makes in
  // the line (the picker opening or closing, the range, an error) is measured at once, before any other task reads or
  // paints the page; the resize observer keeps it current as the width changes, in the frame after it reports.
  measureTop();
  new MutationObserver(measureTop).observe(line, { subtree: true, childList: true, attributes: true, characterData: true });
  new ResizeObserver(measureTopNext).observe(line);
}

// A size its resize observer reports, measured in the next frame, never in the observation itself: the space it sets moves
// the page's root and body, which a held prototype route observes too (followPrototype), so moving them inside an
// observation leaves the browser a notification undelivered, an error the page would show.
function measureTopNext() {
  requestAnimationFrame(measureTop);
}

// The space the page starts under: the top line (--hx-line-space, 0 while none shows), then the error overlay under it
// (#error-overlay), the two together --hx-top-space. Both hide while the phone panel covers the page: keep the last
// heights, so the page does not move under it.
function measureTop() {
  const line = document.getElementById('hx-top-line'), errors = document.getElementById('hx-errors');
  if ([line, errors].some(el => el && !el.offsetHeight)) return;
  const lineSpace = line ? line.offsetHeight : 0;
  document.documentElement.style.setProperty('--hx-line-space', lineSpace + 'px');
  document.documentElement.style.setProperty('--hx-top-space', lineSpace + (errors ? errors.offsetHeight : 0) + 'px');
}

// spec-page-design #keys-rule: no label carries its key; the tooltip names it beside the label, and on a phone,
// where a tooltip does not show, the control has none.
function keyTip(control, key) {
  const label = control.textContent;
  const apply = () => {
    if (NARROW.matches) control.removeAttribute('title');
    else control.title = label + ' (' + key + ')';
  };
  apply();
  NARROW.addEventListener('change', apply);
}

// spec-page-design #type-system-rule: status words read in sentence case.
const sentenceCase = text => String(text).charAt(0).toUpperCase() + String(text).slice(1);

// The page's chrome CSS, first at boot: whatever shows before the chrome mounts, the error overlay included, has its look.
function mountStyle() {
  const style = document.createElement('style');
  style.setAttribute('data-hx-chrome', '');
  const sharedDocumentStyle = document.querySelector('link[rel~="stylesheet"][href*=".style/spec.css"]');
  style.textContent = (EMBED_REVIEW_DIR || MOCKUP_SPEC || COMMENT_UI || sharedDocumentStyle ? '' : DOC_CSS) + FOCUS_CSS + CSS;
  // The comment UI page's width is the frame its shell gives it, as narrow beside a desktop page as on a phone: its phone
  // is its touch screen.
  if (COMMENT_UI) style.textContent = style.textContent.replace(/@media\(max-width:640px\)/g, '@media(pointer:coarse)');
  document.head.appendChild(style);
  // mockups #review-fit: the review lays the page out at the screen's width, as a spec page declares; the frames keep theirs.
  if (MOCKUP_SPEC && !document.querySelector('meta[name="viewport"]')) {
    document.head.appendChild(Object.assign(document.createElement('meta'), { name: 'viewport', content: 'width=device-width, initial-scale=1, viewport-fit=cover' }));
  }
}

function mountUI() {
  function setupDiffVisibility() {
    const gear = document.getElementById('hx-panel-gear');
    const box = document.getElementById('hx-settings');
    const slider = document.getElementById('hx-veil');
    const out = document.getElementById('hx-veil-val');
    if (!gear || !box || !slider || !out) return;
    const KEY = 'hx-diff-visibility';
    const apply = pct => {
      document.documentElement.style.setProperty('--hx-veil', String(pct / 100));
      out.textContent = pct + '%';
    };
    let start = 100;
    try {
      const saved = window.localStorage.getItem(KEY);
      if (saved !== null && saved !== '' && !Number.isNaN(Number(saved))) {
        start = Math.min(100, Math.max(0, Number(saved)));
      }
    } catch (_) { /* private mode or blocked storage: keep the default */ }
    slider.value = String(start);
    apply(start);
    slider.addEventListener('input', () => {
      const pct = Number(slider.value);
      apply(pct);
      try { window.localStorage.setItem(KEY, String(pct)); } catch (_) { /* nothing to persist to */ }
    });
    gear.addEventListener('click', () => {
      const show = box.hasAttribute('hidden');
      if (show) {
        box.removeAttribute('hidden');
        if (!state.panelOpen) openPanel(true);
      } else {
        box.setAttribute('hidden', '');
      }
      gear.setAttribute('aria-expanded', show ? 'true' : 'false');
    });
  }

  mountTopLine();

  const bar = document.createElement('div');
  bar.className = 'hx-toolbar';
  bar.innerHTML = '<button id="hx-mode" aria-pressed="false">Comment</button><button id="hx-connect" hidden>Connect review folder</button><button id="hx-repick" hidden>Choose different folder</button><span class="hx-offline" id="hx-offline" role="status" hidden></span><span class="hx-status" id="hx-status">starting…</span>';
  document.body.appendChild(bar);
  keyTip(document.getElementById('hx-mode'), 'C');
  // The narrow sidebar ends above the fixed dock, so its last content stays reachable.
  new ResizeObserver(() => document.documentElement.style.setProperty('--hx-dock-space', bar.offsetHeight + 'px')).observe(bar);

  const dock = document.createElement('nav');
  dock.className = 'hx-thread-dock';
  dock.setAttribute('aria-label', 'Review conversations');
  dock.innerHTML = '<button class="hx-dock-open" id="hx-dock-open" type="button" aria-label="Open review sidebar">' + ICONS.threads + '<span class="hx-unread-badge" id="hx-unread-badge" aria-hidden="true" hidden></span></button><div class="hx-dock-threads" id="hx-dock-threads"></div>';
  document.body.appendChild(dock);

  const panel = document.createElement('aside');
  panel.className = 'hx-panel';
  panel.setAttribute('aria-label', 'Review sidebar');
  panel.innerHTML = '<div class="hx-panel-head">Conversation <span class="hx-count" id="hx-count"></span> <span class="hx-sub" id="hx-agent"></span><button class="hx-panel-toggle" id="hx-panel-toggle" type="button" aria-expanded="false" aria-label="Open review sidebar">' + ICONS.panel + '</button><button class="hx-panel-gear" id="hx-panel-gear" type="button" aria-expanded="false" aria-controls="hx-settings" aria-label="Review display settings" title="Display settings">' + ICONS.gear + '</button></div><div class="hx-panel-content" id="hx-panel-content" aria-hidden="true" inert><div class="hx-settings" id="hx-settings" hidden><label class="hx-set-row" for="hx-veil">Diff visibility<span class="hx-set-val" id="hx-veil-val">100%</span><input type="range" id="hx-veil" min="0" max="100" step="5" value="100"></label><p class="hx-set-note">How strongly unchanged blocks are dimmed and blurred. At 0 the whole spec reads at normal clarity.</p></div><div class="hx-stream" id="hx-stream"><div class="hx-threads" id="hx-threads"></div></div><div class="hx-handoff"><span class="hx-note" id="hx-drafts">0 drafts</span><button class="hx-btn hx-next-tbd" id="hx-next-tbd" type="button" hidden>Next TBD</button><button class="hx-btn pri" id="hx-handoff">Hand off to agent →</button></div></div>';
  document.body.appendChild(panel);
  // #mobile-handoff: on a narrow screen the panel's one hand-off bar sits in the bottom toolbar, panel open or closed,
  // so it stays operable while the panel's content is inert; on a desktop it ends the panel's content.
  const handoffBar = panel.querySelector('.hx-handoff');
  const dockHandoff = () => NARROW.matches ? bar.insertBefore(handoffBar, document.getElementById('hx-mode').nextSibling) : document.getElementById('hx-panel-content').appendChild(handoffBar);
  dockHandoff();
  NARROW.addEventListener('change', () => { if (!shell.held) { dockHandoff(); renderPanel(); } });

  document.getElementById('hx-mode').addEventListener('click', () => setCommentMode(!state.commentMode));
  document.getElementById('hx-dock-open').addEventListener('click', () => openPanel(true));
  document.getElementById('hx-panel-toggle').addEventListener('click', () => openPanel(!state.panelOpen));
  document.getElementById('hx-handoff').addEventListener('click', handoff);
  document.getElementById('hx-next-tbd').addEventListener('click', MOCKUP_SPEC ? nextQuestion : nextTbd);
  if (MOCKUP_SPEC) ['hx-panel-gear', 'hx-settings'].forEach(id => document.getElementById(id).remove()); // a spec's diff dimming
  setupDiffVisibility();
  document.addEventListener('keydown', e => {
    // Enter or Space on a comment-target block is a plain click on the block itself (onDocClick):
    // a plain click collapses any selection, so a leftover one never becomes the quote
    if (state.commentMode && (e.key === 'Enter' || e.key === ' ') && !e.repeat
      && !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey && e.target.hasAttribute && e.target.hasAttribute('data-hx-target')) {
      e.preventDefault();
      getSelection().removeAllRanges();
      e.target.click();
      return;
    }
    if (commentModeShortcut(e)) setCommentMode(!state.commentMode);
    if (e.key === 'Escape') { state.composer = null; setCommentMode(false); if (state.focusThread || state.focusQuestion) closeFocus(false); else renderPanel(); }
  });
  document.addEventListener('click', onDocClick, true); // capture: runs before spec-script handlers
  // suspend page interactivity while commenting; hover and text selection stay live
  // a comment-target block's own button role is not a control: selection inside it stays live
  const INTERACTIVE = 'button, input, select, textarea, label, a, summary, [role="button"]:not([data-hx-target]), [role="link"]';
  const suspend = e => {
    if (!state.commentMode) return;
    if (e.target.closest && e.target.closest('.hx-pin,.hx-qmark,.hx-jev-marker,.hx-mockup-chip,.hx-screens,.hx-video-rail,.hx-jev-pop,.hx-panel,.hx-thread-dock,.hx-toolbar,.hx-top-line,#hx-errors')) return;
    if (e.target.tagName === 'CANVAS') return;
    if (!holderOf(e.target)) return;
    // native drag/toggle on controls dies here; elsewhere only spec-script handlers die (selection survives). A touch's
    // default is its click, which marks the control (onDocClick blocks its action), so only a touch's handlers die.
    if (/^(pointerdown|mousedown|touchstart)$/.test(e.type)) {
      if (e.target.closest(INTERACTIVE)) { if (e.cancelable && e.type !== 'touchstart') e.preventDefault(); e.stopImmediatePropagation(); }
      return;
    }
    if (e.cancelable) e.preventDefault();
    e.stopImmediatePropagation();
  };
  for (const t of ['pointerdown', 'mousedown', 'touchstart', 'dblclick', 'auxclick', 'contextmenu', 'dragstart', 'submit', 'beforeinput', 'input', 'change']) {
    document.addEventListener(t, suspend, { capture: true, passive: false });
  }
  listenVideoDrag();
  let highlightFrame = null;
  document.addEventListener('scroll', () => {
    cancelAnimationFrame(highlightFrame);
    highlightFrame = requestAnimationFrame(renderThreadHighlight);
  }, true);
  // hover ring so SVG children and controls show what a click would target
  let ring = null;
  document.addEventListener('pointermove', e => {
    const t = e.target;
    let box = null;
    if (state.commentMode && t instanceof Element && !t.closest('.hx-pin,.hx-qmark,.hx-jev-marker,.hx-mockup-chip,.hx-video-rail,.hx-jev-pop,.hx-panel,.hx-thread-dock,.hx-toolbar,.hx-top-line')) {
      const svg = t.closest && t.closest('[data-anchor] svg');
      // in a mockup frame the ring shows what a click marks: an element, or an anchored region (mockups #review-targets)
      if (MOCKUP_SPEC && !svg && holderOf(t) && t.closest('[data-mockup-frame]')) box = markedElement(t, holderOf(t)).getBoundingClientRect();
      else if (svg && t !== svg) box = t.getBoundingClientRect();
      else if (!svg && t.closest && t.closest(INTERACTIVE) && holderOf(t)) box = t.closest(INTERACTIVE).getBoundingClientRect();
    }
    if (!box) { if (ring) ring.style.display = 'none'; return; }
    ring = ring || document.body.appendChild(Object.assign(document.createElement('div'), { className: 'hx-ring' }));
    ring.style.cssText = 'position:fixed;display:block'
      + ';left:' + (box.left - 4) + 'px;top:' + (box.top - 4) + 'px;width:' + (Math.max(box.width, 8) + 8) + 'px;height:' + (Math.max(box.height, 8) + 8) + 'px';
  }, true);
  renderThreadDock();
}

// Comment mode makes every leaf anchored block a button Tab stop named by its text; off restores
// each block's own role and Tab order (data-hx-target holds them while on).
function setCommentTargets(on) {
  if (on) {
    for (const block of document.querySelectorAll('[data-anchor]:not([data-hx-target])')) {
      if (block.querySelector('[data-anchor]') || block.matches('iframe')) continue; // a prototype's frame points itself
      block.setAttribute('data-hx-target', JSON.stringify([block.getAttribute('role'), block.getAttribute('tabindex')]));
      block.setAttribute('role', 'button');
      block.setAttribute('tabindex', '0');
    }
    return;
  }
  for (const block of document.querySelectorAll('[data-hx-target]')) {
    const [role, tabindex] = JSON.parse(block.getAttribute('data-hx-target'));
    if (role === null) block.removeAttribute('role'); else block.setAttribute('role', role);
    if (tabindex === null) block.removeAttribute('tabindex'); else block.setAttribute('tabindex', tabindex);
    block.removeAttribute('data-hx-target');
  }
}

function setCommentMode(on) {
  if (on && mockup.before) return; // a mockup's Before takes no comment (mockups #before-changed)
  if (on && protoHeld.on) return; // a page held as a prototype points for its mockup page alone (#shell-msg-prototype)
  if (on) stopPlay(); // mockups #walkthrough-step: comment mode stops the walkthrough playing
  state.commentMode = on;
  document.body.classList.toggle('hx-comment', on);
  setCommentTargets(on);
  if (PROTOTYPE_ROUTE) pointFrames(on); // the held frames point, a pick or Escape ending it (shell #shell-msg-point)
  document.getElementById('hx-mode')?.setAttribute('aria-pressed', String(on));
  if (on) adoptForeignCharts(); // catch charts the spec script created since the last scan
  for (const info of state.charts.values()) {
    try {
      const opt = info.chart.getOption() || {};
      const patch = {};
      // draft-color hover highlight on chart marks (canvas can't take CSS outlines); a single-element
      // series array only merges onto series[0], so build one entry per series
      const n = (opt.series || []).length || 1;
      const draft = getComputedStyle(document.documentElement).getPropertyValue('--ui-draft').trim();
      patch.series = Array.from({ length: n }, () => ({ emphasis: { itemStyle: on ? { borderColor: draft, borderWidth: 3 } : { borderWidth: 0 } } }));
      // comment mode silences chart interactivity: no tooltips / axis pointers.
      // Only touch charts that have a tooltip — patching one in would add hover UI on exit.
      const tt = Array.isArray(opt.tooltip) ? opt.tooltip[0] : opt.tooltip;
      if (tt) {
        if (info.tooltipShow === undefined) info.tooltipShow = tt.show !== false;
        patch.tooltip = { show: on ? false : info.tooltipShow };
      }
      info.chart.setOption(patch);
    } catch {}
  }
  if (on) openPanel(!NARROW.matches);
}
function openPanel(open) {
  // A held page has no panel; the comment UI page is its panel, always open (shell #shell-roles-held).
  if (shell.held || COMMENT_UI) return void (state.panelOpen = COMMENT_UI);
  const opening = Boolean(open) && !state.panelOpen;
  state.panelOpen = Boolean(open);
  document.querySelector('.hx-panel').classList.toggle('open', state.panelOpen);
  const stream = document.getElementById('hx-threads');
  if (opening && stream) stream.scrollTop = stream.scrollHeight; // threads #stream-context: it opens at its bottom
  document.body.classList.toggle('hx-panel-open', state.panelOpen);
  const toggle = document.getElementById('hx-panel-toggle');
  toggle.setAttribute('aria-expanded', String(state.panelOpen));
  toggle.setAttribute('aria-label', state.panelOpen ? 'Collapse review sidebar' : 'Open review sidebar');
  const content = document.getElementById('hx-panel-content');
  content.toggleAttribute('inert', !state.panelOpen);
  content.setAttribute('aria-hidden', String(!state.panelOpen));
  const dock = document.querySelector('.hx-thread-dock');
  dock.toggleAttribute('inert', state.panelOpen);
  dock.setAttribute('aria-hidden', String(state.panelOpen));
}
function status(msg) { const el = document.getElementById('hx-status'); if (el) el.textContent = msg; }

// `spot`: the step, moment, and box the comment carries (review-state #model-fields-spot).
function openComposer(anchorId, target, quote, text = '', spot = {}) {
  // shell #shell-roles-held: a held page's comment is written in the shell's comment UI: the spot is a pick.
  if (shell.held) {
    setCommentMode(false);
    return pickSpot(Object.assign({ anchor: anchorId, target, quote }, pageStep(), spot));
  }
  state.composer = Object.assign({ kind: 'comment', anchorId, target, quote, text }, spot);
  state.focusThread = state.focusQuestion = null; // a new thread's composer sits in the conversation, never under a focused one
  setCommentMode(false);
  openPanel(true);
  renderPanel();
  setTimeout(() => document.querySelector('.hx-composer textarea')?.focus(), 0);
}

const label = (b) => '#' + b.anchorId + (b.target ? ' › ' + b.target.key : '');
const humanId = prefix => prefix + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);

function addComposer(parent, c) {
  const box = document.createElement('div');
  box.className = 'hx-composer';
  const verb = c.kind === 'edit' ? 'Save edit' : c.kind === 'reply' ? 'Reply' : 'Comment';
  box.innerHTML = '<textarea placeholder="' + verb + '… (⌘⏎ to send)"></textarea>' +
    '<button class="hx-btn pri" data-act="save">' + verb + '</button><button class="hx-btn" data-act="cancel">Cancel</button>';
  const textarea = box.querySelector('textarea');
  textarea.value = c.text || '';
  // comment-layer #ui-starters: the item's starters as buttons above the comment box; one begins the draft with its
  // word, and the comment stays ordinary text.
  if (c.kind === 'comment' && c.starters && c.starters.length) {
    const row = document.createElement('div');
    row.className = 'hx-starters';
    for (const word of c.starters) {
      const starter = row.appendChild(document.createElement('button'));
      starter.type = 'button';
      starter.className = 'hx-btn';
      starter.dataset.starter = word;
      starter.textContent = word;
      starter.addEventListener('click', e => {
        e.stopPropagation();
        textarea.value = c.text = startedWith(word, textarea.value, c.starters);
        textarea.focus();
        textarea.setSelectionRange(textarea.value.length, textarea.value.length);
      });
    }
    box.insertBefore(row, textarea);
  }
  // Live events re-render the panel; the composer keeps what was typed.
  textarea.addEventListener('input', () => { c.text = textarea.value; });
  textarea.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); box.querySelector('[data-act=save]').click(); }
  });
  box.querySelector('[data-act=save]').addEventListener('click', async e => {
    e.stopPropagation();
    const text = textarea.value.trim();
    if (!text) return;
    state.composer = null;
    saveMessage(c, text);
  });
  box.querySelector('[data-act=cancel]').addEventListener('click', e => { e.stopPropagation(); state.composer = null; renderPanel(); });
  parent.appendChild(box);
  setTimeout(() => textarea.focus(), 0);
}

// A draft begun with a starter word: any starter it already begins with gives way to this one.
function startedWith(word, text, starters) {
  const rest = String(text || '').replace(/^\s+/, '');
  const prior = starters.find(other => rest === other || rest.startsWith(other + ' '));
  return word + ' ' + (prior ? rest.slice(prior.length).replace(/^ /, '') : rest);
}

// The one human message save: the composer and answer buttons write the same events. A comment written through the
// comment layer also carries its item's subject, kind, label, and from address (c.host), and goes to its scope (c.route).
// A comment, and an edit repeating it, carries the step, moment, and box its caller gives (review-state #model-fields-spot).
function saveMessage(c, text) {
  const common = { anchorId: c.anchorId, target: c.target, quote: c.quote || null, text, actor: 'human', createdAt: new Date().toISOString(), schemaVersion: 1 };
  let body;
  if (c.kind === 'reply') body = Object.assign(common, { id: humanId('u'), event: 'reply', respondsTo: c.respondsTo, threadId: c.threadId });
  else if (c.kind === 'edit') body = Object.assign(common, { id: humanId('e'), event: 'edit', supersedes: c.supersedes, threadId: c.threadId }, spotFields(c));
  else body = Object.assign(common, { id: humanId('u'), event: 'comment' }, c.host || {}, spotFields(c), pictureOf(c.picture) ? { picture: pictureOf(c.picture) } : {});
  // threads #stage-tag-fixed, #stage-tag-source: a comment or reply carries its lane's stage as it is written, never
  // again, and a comment the lane it took it from, which its replies take theirs from; an edit carries none.
  if (c.kind !== 'edit') {
    const route = LAYER_SERVICE || uiHost() ? c.route || layerRoute(body) : null;
    const lane = messageLane(c, route);
    const stage = lane ? stageNow(route, lane) : null;
    if (lane && c.kind === 'comment') body.lane = lane;
    if (stage) body.stage = stage;
  }
  save(body, c.route);
  toast((c.kind === 'edit' ? 'Edit' : c.kind === 'reply' ? 'Reply' : 'Comment') + ' saved as draft — hand off when ready');
  refresh();
}

// threads #stage-tag-source: what its service told this page of its lanes at its last read: on a host page, its scope's
// read; on a spec or mockup page, its review's.
const lanesTold = route => LAYER_SERVICE || uiHost() ? (route && layer.lanes.get(route.scope)) || null : state.lanes;

// threads #stage-tag-source: the lane a message written now has: a reply, the lane its thread's comment recorded; a
// comment, or a reply on a thread whose comment recorded none, on a host page the lane its item's anchor names, else its
// scope's or its review's.
function messageLane(c, route) {
  const th = c.kind === 'reply' && state.threads.get(c.threadId);
  if (th && laneOf(th.ev.body)) return laneOf(th.ev.body);
  const item = route && layerItems().find(item => item.scope === route.scope && item.subject === route.subject);
  const told = lanesTold(route);
  return (item && item.lane) || (told && told.lane) || null;
}

// The stage a message written now carries: its lane's, as its service told this page at its last read; none when it
// told none for that lane.
function stageNow(route, lane) {
  const told = lanesTold(route);
  return told && Object.hasOwn(told.stages, lane) ? told.stages[lane] : null;
}

function startEdit(th, message) {
  const b = message.body;
  state.activeThread = th.id;
  state.composer = Object.assign({ kind: 'edit', threadId: th.id, supersedes: b.id, anchorId: b.anchorId || th.ev.body.anchorId, target: b.target || th.ev.body.target, quote: b.quote || null, text: b.text || '', opened: b.text || '' }, spotFields(b));
  renderPanel();
}

function renderThreadDock() {
  const wrap = document.getElementById('hx-dock-threads');
  if (!wrap) return;
  wrap.innerHTML = '';
  const entries = threadDockEntries(state.threads);
  const open = document.getElementById('hx-dock-open');
  const acknowledged = acknowledgedReplyCount(state.threads);
  const badge = document.getElementById('hx-unread-badge');
  badge.textContent = acknowledged > 99 ? '99+' : String(acknowledged);
  badge.hidden = acknowledged === 0;
  const awaiting = acknowledged + ' agent ' + (acknowledged === 1 ? 'reply' : 'replies') + ' awaiting your response';
  open.title = 'Open review sidebar · ' + entries.length + ' thread' + (entries.length === 1 ? '' : 's') + (acknowledged ? ' · ' + awaiting : '');
  open.setAttribute('aria-label', 'Open review sidebar' + (acknowledged ? ', ' + awaiting : ''));
  for (const { thread: th, number } of entries) {
    const b = th.ev.body;
    const button = document.createElement('button');
    button.className = 'hx-dock-thread' + (state.activeThread === th.id ? ' active' : '');
    button.type = 'button';
    button.dataset.s = th.status;
    button.textContent = number;
    button.title = label(b) + ' · ' + th.status;
    button.setAttribute('aria-label', 'Open thread ' + number + ': ' + label(b) + ', ' + th.status);
    button.addEventListener('click', e => { e.stopPropagation(); selectThread(th, true); });
    wrap.appendChild(button);
  }
}

function renderPanel() {
  if (LAYER_SERVICE) return renderLayer(true);
  const wrap = document.getElementById('hx-threads');
  if (!wrap) return;
  // threads #stream-context: the open conversation stays where the reviewer left it, and at its bottom when it was there.
  // A closed one is left alone: openPanel opens it at its bottom, and a hidden stream's scroll read or written while the
  // page loads moves the page from the place it keeps (reading-place #place-restore).
  const open = state.panelOpen;
  const stick = open && wrap.scrollHeight - wrap.scrollTop - wrap.clientHeight < 8;
  const top = open ? wrap.scrollTop : 0;
  const nodes = [];
  const openMockups = mockupOpenList(state.mockups);
  if (openMockups) nodes.push(openMockups);
  // On the comment UI page, the conversation of the page the shell relayed last: a host page's, its open item's alone
  // (threads #stream-context).
  const host = uiHost();
  const questions = host ? [] : pageQuestions();
  const shown = contextThreads();
  const ctx = conversationContext(threadPlace());
  const entries = conversationEntries(shown, questions);
  for (const entry of entries) nodes.push(entry.kind === 'thread' ? threadEntry(entry.thread, ctx) : questionEntry(entry, ctx));
  const threads = [...shown.values()];
  const movable = threads.map(th => ({ th, hint: openOrphanHint(th) }))
    .filter(m => m.hint && m.hint.state === 'label' && m.hint.target)
    .map(m => ({ th: m.th, target: m.hint.target }));
  if (movable.length >= 2) {
    const moveAll = document.createElement('button');
    moveAll.className = 'hx-btn';
    moveAll.dataset.act = 'orphan-move-all';
    moveAll.textContent = 'Move all (' + movable.length + ')';
    moveAll.disabled = movable.some(m => state.movingOrphans.has(m.th.id));
    moveAll.addEventListener('click', e => { e.stopPropagation(); moveOrphans(movable); });
    nodes.push(moveAll);
  }
  const hinted = threads.filter(looksResolved);
  if (hinted.length) {
    const all = document.createElement('button');
    all.className = 'hx-btn';
    all.dataset.act = 'resolve-all';
    all.textContent = 'Resolve all (' + hinted.length + ')';
    all.addEventListener('click', e => { e.stopPropagation(); resolveThreads(hinted); });
    nodes.push(all);
  }
  // threads #stream-new: a comment saved from comment mode starts a new thread at the bottom, where its composer sits.
  if (state.composer && state.composer.kind === 'comment') {
    const c = state.composer;
    nodes.push(composerCard(c, host ? 'New comment' : label({ anchorId: c.anchorId, target: c.target })));
  }
  drawChildren(wrap, nodes);
  if (open) wrap.scrollTop = stick || (state.composer && state.composer.kind === 'comment') ? wrap.scrollHeight : top;
  // On the comment UI page the head also names the page, or the host page's open item (shell #shell-ui-page).
  const named = COMMENT_UI ? contextName() : '';
  document.getElementById('hx-count').textContent = (named ? named + ' · ' : '') + conversationCount(shown.size, questions.length);
  const handoffState = host ? layerHandoffState() : pageHandoffState(renderHighlight());
  const drafts = handoffState.drafts;
  document.getElementById('hx-drafts').textContent = handoffState.finish ? 'Ready to accept' : drafts + ' draft' + (drafts === 1 ? '' : 's');
  const handoffButton = document.getElementById('hx-handoff');
  handoffButton.disabled = !handoffState.enabled;
  handoffButton.textContent = handoffState.finish ? 'Accept spec' : handoffState.tbd ? tbdCountLabel('TBD open', handoffState.openTbds) : 'Hand off to agent →';
  const nextButton = document.getElementById('hx-next-tbd');
  nextButton.hidden = !handoffState.nextTbd;
  nextButton.textContent = tbdCountLabel('Next TBD', handoffState.openTbds);
  // threads #focus: on a desktop the focused thread fills the panel over the conversation dimmed; on a phone it is a sheet.
  renderFocus(document.getElementById('hx-stream'), document.querySelector('.hx-panel'), NARROW.matches && !COMMENT_UI);
  renderThreadDock();
  renderThreadHighlight();
  scheduleVideos(); // the box of a comment being written, or of one no longer written (threads #video-rail)
  if (MOCKUP_SPEC) updateStepBar();
}

// threads #questions: each open TBD question of the page, in page order: its block, its words, and its answer buttons;
// on a mockup, the parts it is about and the step it is asked at, read from the block, else from its marker
// (mockups #markup-questions).
function pageQuestions() {
  if (COMMENT_UI) return publishedQuestions();
  const blocks = answerBlocks();
  const questions = new Map();
  for (const marker of openTbdMarkers(document.querySelectorAll('[data-spec-tbd]'))) {
    const block = marker.closest('[data-anchor]');
    if (!block || questions.has(block.dataset.anchor)) continue;
    const answers = blocks.get(block.dataset.anchor) || { buttons: new Map(), chosen: null };
    const named = name => block.getAttribute(name) || marker.getAttribute(name) || '';
    questions.set(block.dataset.anchor, { anchor: block.dataset.anchor, block, text: sourceText(block, UNREAD + ',' + ANSWER),
      buttons: [...answers.buttons.values()], chosen: answers.chosen,
      about: named('data-about').split(/\s+/).filter(Boolean), step: named('data-step').trim() || null });
  }
  return [...questions.values()];
}

// What every entry of one conversation reads by: each message's draft state, where each thread's mark sits now
// (`place`), and the author of the bubble drawn last, so a run of one author's bubbles is headed once.
function conversationContext(place) {
  return { drafts: draftIds(state.events), place, run: null };
}

const mineOf = message => message.actor === 'human' && Boolean(state.reviewer) && message.body.browser === state.reviewer.browser;

// threads #stage-tag: the stage a message carries, stamped as it was written; an edited one shows its original's.
const messageStage = message => typeof message.body.stage === 'string' && message.body.stage.trim() ? message.body.stage.trim() : null;

// A run's head: its author's name, then the stage its messages were written in as a small tag beside it (threads
// #stage-tag-what), so a run is one author's messages of one stage. A space after it, which no layout shows: read as
// text, the head never runs into the message.
function runHead(row, ctx, name, stage) {
  const run = name + '\n' + (stage || '');
  if (ctx.run !== run) {
    const who = row.appendChild(Object.assign(document.createElement('span'), { className: 'hx-who', textContent: name }));
    if (stage) who.append(' · ', Object.assign(document.createElement('span'), { className: 'hx-stage', textContent: stage }));
    row.append(' ');
  }
  ctx.run = run;
}

// One message as a bubble: this browser's at the right, everyone else's at the left, a run of one author's headed by its
// name and stage; a draft outlined on the soft action color; an edited one showing its effective text, marked Edited.
// `press` opens its thread focused.
function messageRow(th, index, ctx, press) {
  const message = th.messages[index], m = message.body;
  const history = th.history && th.history.get(index);
  const author = history ? history.original : message;
  const row = document.createElement('div');
  row.className = 'hx-msg ' + (mineOf(author) ? 'hx-me' : 'hx-them');
  row.dataset.messageId = m.id;
  const name = messageAuthor(state.authors, author);
  runHead(row, ctx, name, messageStage(author));
  const bubble = row.appendChild(document.createElement('div'));
  bubble.className = 'hx-bubble';
  if (ctx.drafts.has(m.id)) bubble.dataset.draft = '';
  bubble.innerHTML = '<span class="hx-text">' + esc(m.text || '') + '</span>' + (history ? '<span class="hx-edited">Edited</span>' : '');
  if (press) {
    bubble.setAttribute('role', 'button');
    bubble.tabIndex = 0;
    bubble.setAttribute('aria-label', name + ': ' + (m.text || '') + '. Open this thread');
    bubble.addEventListener('click', e => { e.stopPropagation(); press(); });
    bubble.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); press(); } });
  }
  return row;
}

const waitingDot = () => Object.assign(document.createElement('span'), { className: 'hx-dot' });

function receiptOf(th) {
  const receipt = document.createElement('span');
  receipt.className = 'hx-receipt';
  receipt.dataset.s = th.status;
  receipt.textContent = receiptWord(th.status);
  return receipt;
}

// threads #root-chip: the words a thread's chip reads, naming its spot; on a video, its moment, then Box when one was
// drawn, after the video's name (#root-chip-moment); on a box with no video, Box after the thing's name (#root-chip-box).
function spotWords(th, place) {
  const b = th.ev.body;
  const quoted = b.quote && b.target && b.target.type === 'text' ? '“' + b.quote + '”' : null;
  const removed = PLACE_NOTICES.gone + (b.quote ? ' · “' + b.quote + '”' : '');
  if (place && place.state === 'gone') return removed;
  const moment = momentParts(th.spot);
  // In an item's box every thread is the item's: one written on the spec page carries no label, so the item names it.
  if (LAYER_SERVICE || uiHost()) return [b.label || (layer.open && (layer.open.label || layer.open.subject)), quoted, ...moment].filter(Boolean).join(' · ');
  // mockups #round-video-comment: a round's video by its round, shown or not at this width
  const round = roundName(placedMark(th.ev).anchorId);
  if (round) return [round, ...moment].join(' · ');
  if (COMMENT_UI) { // read from its held page as its service serves it
    const at = uiAnchor(placedMark(th.ev).anchorId);
    return at ? uiSpotWords(at, th, quoted) : ui.doc ? removed : label(placedMark(th.ev));
  }
  const holder = findAnchor(placedMark(th.ev).anchorId);
  if (!holder) return removed;
  if (MOCKUP_SPEC && holder.matches('[data-mockup-directions]')) return 'Directions · Pick'; // mockups #directions-pick
  // mockups #step-threads-pin: a part of a mockup at a step reads the part's name, then the step's number and name; a
  // part of a prototype, a region or a frame, reads its name with no step too.
  if (PROTOTYPE_ROUTE && (isRegion(holder) || holder.closest('[data-mockup-frame]'))) {
    return [partName(holder), th.spot && th.spot.step ? stepChip(th.spot.step) : null, ...moment].filter(Boolean).join(' · ');
  }
  if (MOCKUP_SPEC && th.spot && th.spot.step && holder.closest('[data-mockup-frame]')) return [partName(holder), stepChip(th.spot.step), ...moment].join(' · ');
  return blockWords(holder, quoted, moment);
}

// A block's spot in words: its section, then its first words, or the quote; on a video or a box, its section, then the
// moment and Box.
function blockWords(holder, quoted, moment) {
  const spot = blockSpot(holder);
  if (moment.length) return [spot.section || spot.chip, ...moment].join(' · ');
  return quoted ? [spot.section, quoted].filter(Boolean).join(' · ') : spot.chip;
}

// A block's chip: its section's name, then its first words; a section, or its heading, reads its name alone; on a mockup
// page the frame's name is the section.
function blockSpot(holder) {
  const slot = holder.closest('.hx-mockup-slot');
  let section = slot ? slot.getAttribute('aria-label') || '' : '';
  for (let s = slot ? null : holder.closest('section,[data-spec-section]'); s; s = s.parentElement && s.parentElement.closest('section,[data-spec-section]')) {
    const heading = s.querySelector(':scope > :is(h1,h2,h3,h4,h5,h6)');
    if (!heading) continue;
    if (holder.contains(heading)) return { section: sourceText(heading), chip: sourceText(heading) };
    section = sourceText(heading);
    break;
  }
  const words = firstWords(sourceText(holder, UNREAD + ',' + ANSWER)) || '#' + holder.dataset.anchor;
  return { section, chip: [section, words].filter(Boolean).join(' · ') };
}

function spotChip(text, go) {
  const chip = document.createElement('button');
  chip.type = 'button';
  chip.className = 'hx-chip';
  chip.textContent = text;
  chip.title = text;
  chip.addEventListener('click', e => { e.stopPropagation(); go(); });
  return chip;
}

// What a thread carries beside its words: where its mark sits now (#anchoring-states), a Jev guess while it is open, and,
// written on a host page, its kind and from address (comment-layer #ui-from).
function threadNotes(th, place) {
  const b = th.ev.body;
  const notes = [];
  // A resolved thread shows its own resolved indicator, never a resolved-in-spirit Jev label.
  const resolvedHint = th.status === 'resolved' ? null : jevItem('resolved', th.id);
  const orphanHint = openOrphanHint(th);
  const threadJevState = threadJevNotice(orphanHint, resolvedHint);
  const labels = (looksResolved(th) ? '<span class="hx-jev-thread-label">Looks resolved</span>' : '') +
    (threadJevState ? '<span class="hx-jev-thread-label" data-state="' + threadJevState.state + '">' + esc(jevDisplayLabel(threadJevState)) + '</span>' : '');
  if (labels) notes.push(Object.assign(document.createElement('div'), { className: 'hx-thread-labels', innerHTML: labels }));
  if (b.kind || b.from) notes.push(hostSource(b));
  if (place && place.state === 'changed') {
    const n = document.createElement('div');
    n.className = 'hx-place-notice';
    n.dataset.state = place.state;
    n.innerHTML = esc(PLACE_NOTICES.changed) + (b.quote ? '<span class="hx-quote">“' + esc(b.quote) + '”</span>' : '');
    notes.push(n);
  }
  const hint = orphanHintElement(th, orphanHint);
  if (hint) notes.push(hint);
  return notes;
}

// threads #stream, #replies-collapse, #receipts, #unread: one thread in the conversation. Its first message with its chip
// (`ctx.chip` false under a question, whose chip names the spot), its replies folded into one line that opens them in
// place, its receipt under its newest reviewer message, and a dot while it waits on the reviewer; resolved, one line.
function threadEntry(th, ctx) {
  const d = document.createElement('div');
  d.className = 'hx-entry';
  d.dataset.thread = th.id;
  d.dataset.s = th.status;
  if (th.status === 'resolved') {
    const line = d.appendChild(document.createElement('button'));
    line.type = 'button';
    line.className = 'hx-resolved-line';
    line.innerHTML = '<span class="hx-receipt" data-s="resolved">Resolved</span> · ' + esc(firstWords(th.messages[0].body.text) || 'Resolved comment');
    line.addEventListener('click', e => { e.stopPropagation(); selectThread(th, true); });
    ctx.run = null;
    return d;
  }
  const open = () => selectThread(th, true);
  const first = d.appendChild(messageRow(th, 0, ctx, open));
  d.classList.add(first.classList.contains('hx-me') ? 'hx-me' : 'hx-them');
  if (waitsOnReviewer(th)) {
    d.dataset.waiting = '';
    first.querySelector('.hx-bubble').appendChild(waitingDot());
  }
  const place = ctx.place(th);
  if (ctx.chip !== false) d.appendChild(spotChip(spotWords(th, place), () => goToSpot(th)));
  for (const note of threadNotes(th, place)) d.appendChild(note);
  const newest = newestReviewerIndex(th);
  if (newest === 0) d.appendChild(receiptOf(th));
  const replies = th.messages.length - 1;
  if (replies > 0) {
    const shown = state.openReplies.has(th.id);
    const last = th.messages[replies];
    const lastAuthor = (th.history && th.history.get(replies) || { original: last }).original;
    const line = d.appendChild(document.createElement('button'));
    line.type = 'button';
    line.className = 'hx-replies';
    line.setAttribute('aria-expanded', String(shown));
    line.innerHTML = esc(replies + (replies === 1 ? ' reply' : ' replies')) + ' · <b>' + esc(mineOf(lastAuthor) ? 'You' : messageAuthor(state.authors, lastAuthor)) + ':</b> ' + esc(firstWords(last.body.text));
    line.addEventListener('click', e => {
      e.stopPropagation();
      if (shown) state.openReplies.delete(th.id); else state.openReplies.add(th.id);
      renderPanel();
    });
    ctx.run = null;
    if (shown) {
      for (let index = 1; index <= replies; index++) {
        const row = d.appendChild(messageRow(th, index, ctx, open));
        if (index === newest) row.appendChild(receiptOf(th));
      }
    } else if (newest > 0) d.appendChild(receiptOf(th));
  }
  ctx.run = null;
  return d;
}

// threads #questions: an open TBD question as the agent's bubble, its answer buttons and a Reply for an answer in words in
// it, a chip naming its block, and its answers under it as the reviewer's replies. A button in the bubble saves the same
// answer comment as that button on the page, and opens nothing.
function questionEntry(entry, ctx) {
  const q = entry.question;
  const d = document.createElement('div');
  d.className = 'hx-entry hx-them hx-question';
  d.dataset.question = q.anchor;
  const row = d.appendChild(document.createElement('div'));
  row.className = 'hx-msg hx-them';
  runHead(row, ctx, 'Agent', null); // a question in the page is no message written to the spool: it carries no stage
  const bubble = row.appendChild(document.createElement('div'));
  bubble.className = 'hx-bubble';
  bubble.appendChild(document.createElement('span')).className = 'hx-text';
  bubble.firstChild.textContent = q.text;
  const choices = bubble.appendChild(document.createElement('div'));
  choices.className = 'hx-choices';
  for (const button of q.buttons) {
    const choice = choices.appendChild(document.createElement('button'));
    choice.type = 'button';
    choice.className = 'hx-choice';
    choice.textContent = COMMENT_UI ? button.text : sourceText(button);
    choice.setAttribute('aria-pressed', String(button === q.chosen));
    choice.addEventListener('click', e => { e.stopPropagation(); answerWith(button); });
  }
  const words = choices.appendChild(document.createElement('button'));
  words.type = 'button';
  words.className = 'hx-choice hx-text-choice';
  words.dataset.act = 'answer';
  words.textContent = 'Reply';
  words.addEventListener('click', e => { e.stopPropagation(); openComposer(q.anchor, null, null); Object.assign(state.composer, pageStep()); });
  d.appendChild(spotChip(questionSpot(q), () => COMMENT_UI ? showQuestion(q) : MOCKUP_SPEC ? goToQuestion(q) : jumpToTbd(q.block)));
  ctx.run = null;
  const answers = { ...ctx, chip: false }; // the question's chip names their spot
  for (const th of entry.answers) d.appendChild(threadEntry(th, answers));
  return d;
}

// threads #focus: the focused thread, its messages alone in order with the reply field and Resolve, over the
// conversation dimmed. Its card and its reply field stay while it shows, so live reads never take a reply's typing away.
const focusView = { card: null, veil: null, composer: '', text: '', picture: null };

function closeFocusView() {
  for (const node of [focusView.card, focusView.veil]) if (node) node.remove();
  focusView.card = focusView.veil = focusView.picture = null;
  focusView.composer = '';
}

// `stream` holds the card over the conversation and `holder` the veil over all of it; `sheet`, on a phone, the card is a
// sheet over the screen's lower half instead, the spot above it dimmed around (the thread's ring).
function renderFocus(stream, holder, sheet) {
  const th = state.focusThread && state.threads.get(state.focusThread);
  // mockups #mockup-questions-show: a question shows focused as a thread does; one the agent settled closes.
  const q = !th && state.focusQuestion ? pageQuestions().find(question => question.anchor === state.focusQuestion) : null;
  if (!q) state.focusQuestion = null;
  if (!LAYER_SERVICE) document.body.classList.toggle('hx-focused-sheet', Boolean((th || q) && sheet));
  if ((!th && !q) || !stream) return closeFocusView();
  const host = sheet ? document.body : stream;
  if (!focusView.card || focusView.card.parentNode !== host) {
    closeFocusView();
    focusView.card = host.appendChild(document.createElement('div')); // a page's own section styles never reach it
    focusView.card.className = 'hx-focused' + (sheet ? ' hx-sheet' : '');
    focusView.card.setAttribute('role', 'dialog');
    focusView.card.setAttribute('aria-label', 'Thread');
    focusView.card.innerHTML = '<span class="hx-grip"></span><div class="hx-focused-head"></div><div class="hx-focused-stream"></div>';
  }
  if (!sheet && (!focusView.veil || focusView.veil.parentNode !== holder)) {
    focusView.veil = holder.appendChild(document.createElement('div'));
    focusView.veil.className = 'hx-veil';
    focusView.veil.addEventListener('click', () => closeFocus(true));
  }
  const card = focusView.card;
  const head = card.querySelector('.hx-focused-head');
  head.innerHTML = '<button class="hx-btn hx-back" type="button" data-act="back">' + ICONS['chevron-left'] + '<span>Conversation</span></button>';
  head.firstChild.addEventListener('click', () => closeFocus(true));
  if (q) return renderFocusedQuestion(card, q);
  delete card.dataset.question;
  card.dataset.thread = th.id;
  if (th.status === 'acknowledged') {
    const resolve = head.appendChild(document.createElement('button'));
    resolve.className = 'hx-btn';
    resolve.type = 'button';
    resolve.dataset.act = 'resolve';
    resolve.textContent = 'Resolve';
    resolve.addEventListener('click', e => { e.stopPropagation(); resolveThread(th); });
  }
  const list = card.querySelector('.hx-focused-stream');
  const top = list.scrollTop;
  list.replaceChildren(...focusMessages(th));
  list.scrollTop = top;
  const editing = state.composer && state.composer.kind === 'edit' && state.composer.threadId === th.id ? state.composer : null;
  const verb = replyVerb(th);
  const mode = editing ? 'edit:' + editing.supersedes : verb ? verb + ':' + th.id : '';
  if (mode !== focusView.composer) {
    card.querySelector(':scope > .hx-composer')?.remove();
    focusView.composer = mode;
    if (mode) card.appendChild(focusComposer(th, editing, verb));
  }
}

// The focused thread's reply box: Reply on a thread not resolved, Reply and reopen on a resolved one whose latest message
// is the agent's (prompt-first-shaping #thread-reopen), and none on another resolved one.
function replyVerb(th) {
  if (th.status !== 'resolved') return 'Reply';
  const reopen = threadReplyAction(th);
  return reopen ? reopen.label : null;
}

// A reply joining the thread, to its latest message.
function saveReply(th, text) {
  const root = th.ev.body;
  saveMessage({ kind: 'reply', threadId: th.id, respondsTo: th.messages[th.messages.length - 1].body.id, anchorId: root.anchorId, target: root.target, quote: null }, text);
}

// The focused thread's messages: its first with its chip and notes, then every reply; the receipt under its newest
// reviewer message, Edit on that message while it is unanswered, an edit history, and the dot on the agent's newest
// word while the thread waits on the reviewer.
function focusMessages(th) {
  const ctx = conversationContext(threadPlace());
  const place = ctx.place(th);
  const newest = newestReviewerIndex(th);
  const nodes = [];
  th.messages.forEach((message, index) => {
    const row = messageRow(th, index, ctx, null);
    nodes.push(row);
    const history = th.history && th.history.get(index);
    if (history && history.edits.length > 1) { // #model-edit-history: more than one edit lists every edit with its author
      const list = row.appendChild(document.createElement('ol'));
      list.className = 'hx-history';
      list.setAttribute('aria-label', 'Edit history');
      for (const edit of history.edits) {
        const entry = list.appendChild(document.createElement('li'));
        entry.innerHTML = '<span class="hx-who">' + esc(messageAuthor(state.authors, edit)) + '</span> ' + esc(edit.body.text || '');
      }
    }
    if (index === 0) {
      row.appendChild(spotChip(spotWords(th, place), () => goToSpot(th)));
      for (const note of threadNotes(th, place)) row.appendChild(note);
      const picture = boxPicture(th);
      if (picture) row.appendChild(picture);
    }
    if (index === th.messages.length - 1 && waitsOnReviewer(th)) row.querySelector('.hx-bubble').appendChild(waitingDot());
    if (message.actor === 'human' && message.body.id === th.latestHumanId && ['draft', 'pending'].includes(th.status)) {
      const actions = row.appendChild(document.createElement('div'));
      actions.className = 'hx-msg-actions';
      actions.innerHTML = '<button class="hx-btn" type="button" data-act="edit">Edit</button>';
      actions.firstChild.addEventListener('click', e => { e.stopPropagation(); startEdit(th, message); });
    }
    if (index === newest) row.appendChild(receiptOf(th));
  });
  return nodes;
}

// threads #box-picture-show: the picture its page made of a comment's boxed spot, under the comment's bubble in the
// focused thread, as evidence beside the words, never a way to the spot: the chip is. Only here is it loaded, by its
// file, from the spool that keeps the comment (review-service #store-pictures); no read of the threads fetches it. A
// comment the service has not stored yet shows the bytes it carries.
function boxPicture(th) {
  const b = th.ev.body;
  const sent = pictureOf(b.picture);
  const kept = !sent && typeof b.picture === 'string' && PICTURE_FILE.test(b.picture) ? b.picture : null;
  if (!sent && !kept) return null;
  // One figure while its thread stays focused: a figure drawn anew on each read would load again, its height gone
  // meanwhile, so the stream would lose its place.
  const key = th.id + '\n' + (kept || sent.data);
  if (focusView.picture && focusView.picture.key === key) return focusView.picture.figure;
  const figure = document.createElement('figure');
  figure.className = 'hx-picture';
  focusView.picture = { key, figure };
  const image = figure.appendChild(document.createElement('img'));
  image.addEventListener('load', () => pictureInView(figure), { once: true });
  const who = mineOf(th.ev) ? 'you' : messageAuthor(state.authors, th.ev);
  image.alt = 'The boxed spot as ' + who + ' saw it';
  image.decoding = 'async';
  if (sent) image.src = 'data:' + sent.type + ';base64,' + sent.data;
  else {
    const at = new URL('api/pictures', LAYER_SERVICE || COMMENT_UI ? SCRIPT_SERVICE : new URL('/', OWN_ADDRESS));
    const where = th.ev.scope ? { scope: th.ev.scope, subject: th.ev.subject || '' } : { dir: COMMENT_UI ? ui.dir : REVIEW_DIR };
    at.search = new URLSearchParams(Object.assign(where, { name: kept }));
    image.src = at.href;
  }
  figure.appendChild(document.createElement('figcaption')).textContent = mineOf(th.ev) ? 'What you saw when you wrote' : 'What ' + who + ' saw when they wrote';
  return figure;
}

// Once drawn, the picture in view in the focused thread's stream, which on a phone's sheet is short.
function pictureInView(figure) {
  const list = figure.closest('.hx-focused-stream');
  if (!list) return;
  const shown = list.getBoundingClientRect(), at = figure.getBoundingClientRect();
  if (at.bottom > shown.bottom) list.scrollTop += Math.min(at.bottom - shown.bottom, at.top - shown.top);
}

// The focused thread's one field: a reply joining the thread, or the edit of its newest reviewer message.
// threads #focus-reply: a one-line field that grows with its text, and Save.
function focusComposer(th, editing, verb) {
  const box = document.createElement('div');
  box.className = 'hx-composer';
  const textarea = box.appendChild(document.createElement('textarea'));
  textarea.rows = 1;
  textarea.placeholder = editing ? 'Edit…' : verb + '…';
  textarea.setAttribute('aria-label', editing ? 'Edit' : verb);
  textarea.value = editing ? editing.text || '' : focusView.text;
  // Text that fits keeps the field's own one-line height; only text past it sets a height. scrollHeight is whole
  // pixels and the one-line height is not (37.6 at 14px), so a height set for one line would differ from the one
  // drawn.
  const grow = () => {
    textarea.style.height = '';
    if (textarea.scrollHeight > textarea.clientHeight) textarea.style.height = (textarea.scrollHeight + textarea.offsetHeight - textarea.clientHeight) + 'px';
  };
  textarea.addEventListener('input', () => { if (editing) editing.text = textarea.value; else focusView.text = textarea.value; grow(); });
  requestAnimationFrame(grow); // once placed, for text it was drawn with
  const save = box.appendChild(document.createElement('button'));
  save.type = 'button';
  save.className = 'hx-btn pri';
  save.dataset.act = 'save';
  save.setAttribute('aria-label', editing ? 'Save edit' : 'Save reply');
  save.textContent = 'Save';
  textarea.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); save.click(); }
  });
  save.addEventListener('click', e => {
    e.stopPropagation();
    const text = textarea.value.trim();
    if (!text) return;
    if (editing) {
      state.composer = null;
      return saveMessage(editing, text);
    }
    focusView.text = '';
    textarea.value = '';
    grow();
    saveReply(th, text);
  });
  if (editing) {
    const cancel = box.appendChild(document.createElement('button'));
    cancel.type = 'button';
    cancel.className = 'hx-btn';
    cancel.dataset.act = 'cancel';
    cancel.textContent = 'Cancel';
    cancel.addEventListener('click', e => { e.stopPropagation(); state.composer = null; renderPanel(); });
  }
  return box;
}

// Back returns to the conversation where it was (`toConversation`, on a phone opening it); Escape or the veil close it.
function closeFocus(toConversation) {
  state.focusThread = null;
  state.focusQuestion = null;
  state.activeThread = null;
  if (state.composer && state.composer.kind === 'edit') state.composer = null;
  focusView.text = '';
  if (LAYER_SERVICE) return renderLayer(true);
  if (toConversation && NARROW.matches) openPanel(true);
  renderPanel();
  renderPins();
}

// threads #root-chip: a chip goes to its thread's spot, in view and ringed; on a phone the panel over the page closes.
function goToSpot(th) {
  state.activeThread = th.id;
  if (COMMENT_UI) { showOnPage(th); return renderPanel(); } // its held page brings it into view (#shell-msg-show)
  if (LAYER_SERVICE) {
    const item = liveItems().find(it => sameItem(it, th.ev));
    if (item) item.el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    if (item) itemMoment(item, th.spot); // threads #root-chip-moment: its video to the moment, paused
    return renderLayer(true);
  }
  showThreadStep(th); // threads #root-chip-part: both frames to the thread's step
  if (NARROW.matches) openPanel(false);
  renderPins();
  if (!showMoment(th)) scrollToThread(placedMark(th.ev)); // threads #root-chip-moment: the video at its moment, paused
  renderThreadHighlight();
}

// A new comment's composer: where it goes, the quote it holds, and its field. One card per composer while it shows: a
// draw of the conversation keeps it in place (drawChildren), so a live read never takes away the field the reviewer types
// in or the Save they press, as the focused thread's field stays (focusView).
const composerCards = new WeakMap();
function composerCard(c, where) {
  if (composerCards.has(c)) return composerCards.get(c);
  const d = document.createElement('div');
  d.className = 'hx-compose';
  d.innerHTML = '<div class="hx-anchor">' + esc(where) + '</div>' +
    (c.quote ? '<span class="hx-quote">“' + esc(c.quote) + '”</span>' : '');
  addComposer(d, c);
  composerCards.set(c, d);
  return d;
}

// A container's children drawn anew as `nodes`, in order; one it holds already and keeps never leaves the document, so
// the field in it keeps its focus, its caret, and a press begun on its button (a pressed button drawn anew gets no click).
function drawChildren(parent, nodes) {
  const kept = new Set(nodes);
  for (const child of [...parent.childNodes]) if (!kept.has(child)) child.remove();
  let next = parent.firstChild;
  for (const node of nodes) {
    if (node === next) next = node.nextSibling;
    else parent.insertBefore(node, next);
  }
}

// comment-layer #ui-from: a comment written on a host page shows its kind and a link to the address it was written
// from, on every page that shows its thread.
function hostSource(b) {
  const line = document.createElement('div');
  line.className = 'hx-host-from';
  if (b.kind) {
    const kind = line.appendChild(document.createElement('span'));
    kind.className = 'hx-host-kind';
    kind.textContent = String(b.kind);
  }
  const href = webAddress(b.from);
  if (href) {
    line.appendChild(document.createElement('span')).textContent = 'from';
    const link = line.appendChild(document.createElement('a'));
    link.href = href;
    link.target = '_blank';
    link.rel = 'noopener';
    link.title = href;
    link.textContent = href.replace(/^https?:\/\//, '');
    link.addEventListener('click', e => e.stopPropagation());
  }
  return line;
}

// An http(s) address, or null: a host's from address is shown as a link only when it is one.
function webAddress(value) {
  try {
    const url = new URL(String(value || ''), document.baseURI);
    return value && /^https?:$/.test(url.protocol) ? url.href : null;
  } catch (_) { return null; }
}

// A card's own resolve control and Resolve all post the same resolve event.
async function resolveThreads(threads) {
  for (const th of threads) {
    save({ id: humanId('s'), event: 'status', respondsTo: th.id, threadId: th.id, status: 'resolved', actor: 'human', createdAt: new Date().toISOString(), schemaVersion: 1 });
  }
  toast('Resolved');
  refresh();
}

function resolveThread(th) {
  return resolveThreads([th]);
}

// threads #focus: a message of the thread, its pin, or its dock button opens it focused; on a desktop in the panel,
// which opens if it was closed, and on a phone as a sheet under the spot, the panel over the page closed.
function selectThread(th, scroll) {
  if (LAYER_SERVICE) return selectLayerThread(th);
  // shell #shell-roles-held: a held page's pin is a pick; the comment UI page focuses its thread.
  if (shell.held) {
    state.activeThread = th.id;
    shell.shown = null;
    renderPins();
    renderThreadHighlight();
    return pickSpot(Object.assign({ thread: th.id }, markSpot(th)));
  }
  if (state.focusThread !== th.id) focusView.text = '';
  if (state.composer && state.composer.kind === 'edit' && state.composer.threadId !== th.id) state.composer = null;
  state.activeThread = state.focusThread = th.id;
  state.focusQuestion = null;
  if (scroll) showThreadStep(th);
  if (uiHost() && !sameItem(layer.open, th.ev)) layer.open = liveItems().find(item => sameItem(item, th.ev)) || layer.open;
  openPanel(!NARROW.matches);
  renderPanel();
  renderPins();
  if (scroll) scrollToThread(placedMark(th.ev));
  if (scroll && COMMENT_UI) showOnPage(th);
  renderThreadHighlight();
}

// A spot into view: centered, or on a phone under a focused thread's or question's sheet, near the top of the screen
// above it.
function scrollToThread(b) {
  if (mockup.dirs && findAnchor(b.anchorId)?.matches('[data-mockup-directions]')) return scrollToSpot(mockup.stage); // a Pick
  if (PROTOTYPE_ROUTE) return void showInFrames(b.anchorId, b.target);
  scrollToSpot(findAnchor(b.anchorId));
}

function scrollToSpot(el) {
  if (!inView(el)) return;
  if (!NARROW.matches || !(state.focusThread || state.focusQuestion)) return el.scrollIntoView({ block: 'center', behavior: 'smooth' });
  const line = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--hx-top-space')) || 0;
  window.scrollBy({ top: el.getBoundingClientRect().top - line - 12, behavior: 'smooth' });
}

function scrollToJevAnchor(anchorId) {
  if (location.hash.slice(1) === encodeURIComponent(anchorId)) arriveAtAddress();
  else location.hash = encodeURIComponent(anchorId);
}

// A resolved orphan is settled (#settle-orphan): it shows only its resolved indicator, no Jev guess.
function openOrphanHint(th) {
  return th.status === 'resolved' ? null : jevItem('orphan', th.id);
}

// An unsure orphan or resolved answer shows nothing (jev-suggestions #state-unsure); only Jev unavailable is named.
function threadJevNotice(...items) {
  return items.find(item => item && item.state === 'unavailable') || null;
}

function orphanHintElement(th, orphanHint) {
  if (!orphanHint || orphanHint.state !== 'label' || !orphanHint.target) return null;
  const hint = document.createElement('div');
  hint.className = 'hx-orphan-hint';
  const copy = document.createElement('span');
  copy.textContent = 'Possibly moved to: #' + orphanHint.target;
  const go = document.createElement('button');
  go.type = 'button';
  go.className = 'hx-btn';
  go.dataset.act = 'orphan-go';
  go.textContent = 'Go to';
  go.addEventListener('click', e => { e.stopPropagation(); goToJevTarget(orphanHint.target); });
  const move = document.createElement('button');
  move.type = 'button';
  move.className = 'hx-btn pri';
  move.dataset.act = 'orphan-move';
  move.textContent = state.movingOrphans.has(th.id) ? 'Moving…' : 'Move comment here';
  move.disabled = state.movingOrphans.has(th.id);
  move.addEventListener('click', e => { e.stopPropagation(); moveOrphan(th, orphanHint.target); });
  hint.append(copy, go, move);
  return hint;
}

// Move comment here and Move all post the same comment and resolve events per thread.
async function moveOrphans(moves) {
  moves = moves.filter(m => m.th && m.target && !state.movingOrphans.has(m.th.id));
  if (!moves.length) return;
  for (const { th } of moves) state.movingOrphans.add(th.id);
  renderPanel();
  let moved = 0;
  try {
    for (const { th, target } of moves) {
      const original = th.ev.body || {};
      const text = th.messages[0].body.text;
      const quote = original.quote || text || '';
      save(Object.assign({
        id: humanId('u'), event: 'comment', anchorId: target, target: null,
        quote, text: text || 'Moved comment', actor: 'human',
        createdAt: new Date().toISOString(), schemaVersion: 1,
      }, laneOf(original) ? { lane: laneOf(original) } : {}, // the same message: its lane and stage stay
      typeof original.stage === 'string' ? { stage: original.stage } : {}));
      save({
        id: humanId('s'), event: 'status', respondsTo: th.id, threadId: th.id,
        status: 'resolved', actor: 'human', createdAt: new Date().toISOString(), schemaVersion: 1,
      });
      moved++;
    }
    toast(moves.length === 1 ? 'Comment moved to #' + moves[0].target : 'Moved ' + moved + ' comments');
    state.activeThread = null;
    await refresh();
  } catch (_) {
    toast('Could not move comment');
    if (moved) await refresh();
  } finally {
    for (const { th } of moves) state.movingOrphans.delete(th.id);
    renderPanel();
    renderPins();
  }
}

function moveOrphan(th, target) {
  return moveOrphans([{ th, target }]);
}

const chartInfoFor = b => {
  const t = b.target || {};
  return (t.chartKey && state.charts.get(t.chartKey)) || state.charts.get(b.anchorId)
    || [...state.charts.values()].find(i => i.anchor === b.anchorId);
};

function textTargetRect(holder, needle) {
  const range = textRange(holder, needle);
  const rect = range && range.getBoundingClientRect();
  return rect && (rect.width || rect.height) ? rect : null;
}

// Where a quote sits in a block's or a host item's text (comment-layer #anchor-quote): the range of its first match.
function textRange(holder, needle) {
  needle = String(needle || '');
  if (!needle) return null;
  const walker = document.createTreeWalker(holder, NodeFilter.SHOW_TEXT);
  const parts = [];
  let text = '', node;
  while ((node = walker.nextNode())) { parts.push({ node, start: text.length, end: text.length + node.data.length }); text += node.data; }
  const start = text.indexOf(needle);
  if (start < 0) return null;
  const end = start + needle.length;
  const a = parts.find(p => start >= p.start && start < p.end);
  const z = parts.find(p => end > p.start && end <= p.end) || a;
  if (!a || !z) return null;
  const range = document.createRange();
  range.setStart(a.node, start - a.start);
  range.setEnd(z.node, end - z.start);
  return range;
}

function chartDatum(info, t) {
  const seriesIndex = t.seriesIndex == null ? 0 : t.seriesIndex;
  const series = (Array.isArray(info.config.series) ? info.config.series : [info.config.series])[seriesIndex] || {};
  let dataIndex = t.dataIndex;
  if (dataIndex == null) {
    const axes = Array.isArray(info.config.xAxis) ? info.config.xAxis : [info.config.xAxis];
    const axis = axes[series.xAxisIndex || 0] || axes[0] || {};
    dataIndex = (axis.data || []).map(String).indexOf(String(t.key));
  }
  if (dataIndex == null || dataIndex < 0) return null;
  let value = (series.data || [])[dataIndex];
  if (value && typeof value === 'object' && !Array.isArray(value) && value.value !== undefined) value = value.value;
  return { seriesIndex, dataIndex, value };
}

function chartItemRect(info, datum) {
  try {
    const item = info.chart.getModel().getSeriesByIndex(datum.seriesIndex).getData().getItemGraphicEl(datum.dataIndex);
    if (!item) return null;
    const rect = item.getBoundingRect().clone();
    const transform = item.getComputedTransform ? item.getComputedTransform() : item.transform;
    if (transform) rect.applyTransform(transform);
    const host = info.el.getBoundingClientRect();
    const sx = host.width / (info.chart.getWidth() || host.width || 1);
    const sy = host.height / (info.chart.getHeight() || host.height || 1);
    return { left: host.left + rect.x * sx, top: host.top + rect.y * sy, width: rect.width * sx, height: rect.height * sy };
  } catch { return null; }
}

let activeChartHighlight = null;
function syncChartHighlight(info, t) {
  let action = null;
  if (info && t && t.type === 'datum') {
    const datum = chartDatum(info, t);
    if (datum) action = { type: 'highlight', seriesIndex: datum.seriesIndex, dataIndex: datum.dataIndex };
  } else if (info && t && t.type === 'legend') action = { type: 'highlight', seriesName: t.key };
  const key = action ? info.chart.id + ':' + JSON.stringify(action) : '';
  if (activeChartHighlight && activeChartHighlight.key === key) return;
  if (activeChartHighlight) {
    try { activeChartHighlight.chart.dispatchAction({ type: 'downplay' }); } catch {}
    activeChartHighlight = null;
  }
  if (action) {
    try { info.chart.dispatchAction(action); activeChartHighlight = { chart: info.chart, key }; } catch {}
  }
}

function threadTargetRect(b, holder) {
  const t = b.target;
  if (!t) { syncChartHighlight(null, null); return holder.getBoundingClientRect(); }
  if (t.type === 'element') {
    syncChartHighlight(null, null);
    const el = resolveElement(holder, t.key);
    return el ? el.getBoundingClientRect() : holder.getBoundingClientRect();
  }
  if (t.type === 'text') {
    syncChartHighlight(null, null);
    return textTargetRect(holder, t.key || b.quote) || holder.getBoundingClientRect();
  }
  const info = chartInfoFor(b);
  syncChartHighlight(info, t);
  if (!info) return holder.getBoundingClientRect();
  const host = info.el.getBoundingClientRect();
  if (t.type === 'datum') {
    const datum = chartDatum(info, t);
    if (datum) {
      const graphic = chartItemRect(info, datum);
      if (graphic) return graphic;
      try {
        const point = info.chart.convertToPixel({ seriesIndex: datum.seriesIndex }, Array.isArray(datum.value) ? datum.value : [t.key, datum.value]);
        return { left: host.left + point[0] - 8, top: host.top + point[1] - 8, width: 16, height: 16 };
      } catch {}
    }
  }
  if (t.type === 'axis-x') {
    try { const x = info.chart.convertToPixel({ xAxisIndex: 0 }, t.key); return { left: host.left + x - 8, top: host.bottom - 20, width: 16, height: 16 }; } catch {}
  }
  if (t.type === 'axis-y') {
    try { const y = info.chart.convertToPixel({ yAxisIndex: 0 }, +String(t.key).replace(/[,\s]/g, '')); return { left: host.left + 2, top: host.top + y - 8, width: 18, height: 16 }; } catch {}
  }
  if (t.type === 'target') {
    try { const y = info.chart.convertToPixel({ yAxisIndex: 0 }, +String(t.key).replace(/[,\s]/g, '')); return { left: host.left + 4, top: host.top + y - 2, width: host.width - 8, height: 4 }; } catch {}
  }
  return host;
}

// #anchoring-states: where a thread's mark sits now. The service's place (place.py) wins: its
// block, for a text target its surviving text, for an element target its current key; a gone mark
// sits on the block alone. An event without a place, or unplaced, sits by its own anchor and quote.
function placedMark(e) {
  // mockups #prototype-marks: a comment on a prototype resolves by its region's selector, target, and quote on the
  // route as it now runs, never in the mockup file.
  if (PROTOTYPE_ROUTE) return e.body;
  const b = e.body, p = e.place;
  if (!p || !p.anchorId) return b;
  if (p.state === 'gone') return { ...b, anchorId: p.anchorId, target: null };
  const type = b.target && b.target.type;
  const target = type === 'text' ? { type: 'text', key: p.quote }
    : type === 'element' ? (p.key ? { type: 'element', key: p.key } : null)
    : p.anchorId === b.anchorId ? b.target : null;
  return { ...b, anchorId: p.anchorId, target };
}

const PLACE_NOTICES = { changed: 'Text changed since this comment', gone: 'Text removed' };

function renderThreadHighlight() {
  let ring = document.querySelector('.hx-thread-ring');
  if (PROTOTYPE_ROUTE) return void (ring && ring.remove(), prototypeMarks()); // its frames ring it in their content
  const th = state.activeThread && state.threads.get(state.activeThread);
  // a held page rings the spot the shell's show named while no thread is active (shell #shell-msg-show); a
  // thread at a video's moment shows by its rail mark and its box instead (threads #video-rail)
  const b = th ? showsAtStep(th) && !onVideoRail(th) && placedMark(th.ev) : shell.shown;
  const holder = b && document.querySelector('[data-anchor="' + b.anchorId + '"]');
  if (!holder || (MOCKUP_SPEC && (offView(holder) || !holder.getClientRects().length))) { // a frame the toggle hides
    syncChartHighlight(null, null);
    if (ring) ring.hidden = true;
    return;
  }
  const rect = threadTargetRect(b, holder);
  if (!rect) return;
  ring = ring || document.body.appendChild(Object.assign(document.createElement('div'), { className: 'hx-thread-ring' }));
  ring.hidden = false;
  ring.style.left = (rect.left - 4) + 'px';
  ring.style.top = (rect.top - 4) + 'px';
  ring.style.width = (Math.max(rect.width, 8) + 8) + 'px';
  ring.style.height = (Math.max(rect.height, 8) + 8) + 'px';
}

function pinPos(b, holder) {
  const t = b.target;
  if (!t) return cornerPos(holder);
  const info = chartInfoFor(b);
  if (info && ['datum', 'axis-x', 'axis-y', 'target'].includes(t.type)) {
    try {
      const hR = holder.getBoundingClientRect(), cR = info.el.getBoundingClientRect();
      if (t.type === 'datum') {
        // series/data indexes position exactly on any grid; the key-based path is the
        // legacy fallback for events recorded before indexes were captured
        if (t.seriesIndex != null && t.dataIndex != null) {
          const sList = Array.isArray(info.config.series) ? info.config.series : [info.config.series];
          let d = sList[t.seriesIndex]?.data?.[t.dataIndex];
          if (d && typeof d === 'object' && !Array.isArray(d) && d.value !== undefined) d = d.value;
          const [x, y] = info.chart.convertToPixel({ seriesIndex: t.seriesIndex }, Array.isArray(d) ? d : [t.key, d]);
          return { top: cR.top - hR.top + y - 26, left: cR.left - hR.left + x - 12 };
        }
        const xa = Array.isArray(info.config.xAxis) ? info.config.xAxis[0] : info.config.xAxis;
        const i = (xa.data || []).indexOf(t.key);
        const v = (Array.isArray(info.config.series) ? info.config.series[0] : info.config.series).data[i];
        const [x, y] = [info.chart.convertToPixel({ xAxisIndex: 0 }, t.key), info.chart.convertToPixel({ yAxisIndex: 0 }, v)];
        return { top: cR.top - hR.top + y - 26, left: cR.left - hR.left + x - 12 };
      }
      const num = +String(t.key).replace(/[,\s]/g, ''); // axis keys can arrive locale-formatted ("1,000")
      const x = t.type === 'axis-x' ? info.chart.convertToPixel({ xAxisIndex: 0 }, t.key) : 6;
      const y = (t.type === 'axis-y' || t.type === 'target') ? info.chart.convertToPixel({ yAxisIndex: 0 }, num) : info.el.clientHeight - 24;
      return { top: cR.top - hR.top + y - 12, left: cR.left - hR.left + x - 12 };
    } catch { /* fall through */ }
  }
  if (t.type === 'text') {
    // beside the text's first line, in the block's pin corner or column, so it covers no text
    const r = textTargetRect(holder, t.key || b.quote);
    const corner = cornerPos(holder);
    if (r) {
      const top = Math.round(r.top - holder.getBoundingClientRect().top - holder.clientTop);
      return corner.column ? { column: true, top: Math.max(corner.top, top) } : { top, left: corner.left };
    }
    return corner;
  }
  if (t.type === 'element') {
    const el = resolveElement(holder, t.key);
    if (el) {
      const hR = holder.getBoundingClientRect(), eR = el.getBoundingClientRect();
      return { top: eR.top - hR.top - 4, left: Math.min(shownWidth(holder) - 28, eR.right - hR.left - 12) };
    }
  }
  return cornerPos(holder); // orphan/text fallback
}

// A pin's place is in screen pixels: in a zoomed mockup frame the pin keeps its own size (mockups #review-fit), so a
// block's width is taken as shown.
function shownWidth(holder) {
  return holder.clientWidth * (holder.currentCSSZoom || 1);
}

// Block corner, or, when the block has its own Jev marker, the block's reserved right column
// directly below the marker's tap pad (its ::before), so the pin covers neither the marker nor text.
function cornerPos(holder) {
  const marker = holder.querySelector(holder.tagName === 'TR' ? ':scope > :is(td,th) > .hx-jev-marker' : ':scope > .hx-jev-marker');
  if (!marker) return { top: 4, left: shownWidth(holder) - 30 };
  const pad = parseFloat(getComputedStyle(marker, '::before').bottom) || 0;
  const top = marker.getBoundingClientRect().bottom - pad - holder.getBoundingClientRect().top - holder.clientTop;
  return { column: true, top: Math.ceil(top) }; // right edge by CSS, so a table's later reflow keeps it
}

function renderPins() {
  document.querySelectorAll('.hx-pin').forEach(p => p.remove());
  renderVideos(); // threads #video-rail: a video's threads mark its rail
  if (PROTOTYPE_ROUTE) return prototypeMarks(); // its held frames draw them in their own content (#prototype-route)
  let n = 0;
  for (const th of state.threads.values()) {
    n++;
    const b = placedMark(th.ev);
    const holder = findAnchor(b.anchorId);
    if (!holder || onVideoRail(th)) continue; // a thread at a video's moment is pinned on its rail (threads #video-rail)
    // mockups #step-threads-pin: on a mockup page a pin shows only while its thread's step shows, and only on a shown part
    // (an answer's block sits in the page's questions, which show in the conversation).
    if (MOCKUP_SPEC && (!showsAtStep(th) || !holder.getClientRects().length)) continue;
    const pos = pinPos(b, holder);
    const pin = document.createElement('button');
    pin.className = 'hx-pin' + (state.activeThread === th.id ? ' active' : '');
    pin.dataset.s = th.status;
    pin.dataset.thread = th.id;
    const hinted = looksResolved(th);
    pin.textContent = '';
    const number = document.createElement('span');
    number.className = 'hx-pin-number';
    number.textContent = th.number || n; // a held page's numbers are the comment UI page's (#shell-msg-marks)
    pin.appendChild(number);
    if (hinted) {
      pin.dataset.jev = 'resolved';
      const marker = document.createElement('span');
      marker.className = 'hx-pin-jev';
      marker.textContent = 'Looks resolved';
      pin.appendChild(marker);
    }
    if (waitsOnReviewer(th)) { // threads #unread
      pin.dataset.waiting = '';
      pin.appendChild(waitingDot());
    }
    pin.title = (hinted ? 'Looks resolved · ' : '') + label(b);
    pin.setAttribute('aria-label', (hinted ? 'Looks resolved: ' : '') + label(b));
    pin.style.top = pos.top + 'px';
    if (pos.column) pin.dataset.column = '';
    const pinSize = NARROW.matches ? 44 : 24;
    if (!pos.column) pin.style.left = Math.max(0, Math.min(pos.left, shownWidth(holder) - pinSize)) + 'px';
    pin.addEventListener('click', e => { e.stopPropagation(); selectThread(th, true); });
    holder.appendChild(pin);
  }
  renderQuestionMarks();
}

function renderBadges() {
  document.querySelectorAll('.hx-badge').forEach(b => b.remove());
  // Embed hosts are live product UI and mockup frames keep the mockup's own look: injecting
  // badge text into their headings pollutes the page being reviewed. Spec pages keep the badges.
  if (EMBED_REVIEW_DIR || MOCKUP_SPEC) return;
  const changed = new Set();
  for (const e of state.events) if (e.actor === 'agent' && e.body.change && e.body.anchorId) changed.add(e.body.anchorId);
  for (const a of changed) {
    const h = document.querySelector('[data-anchor="' + a + '"] h2, [data-anchor="' + a + '"] h1');
    if (!h || h.querySelector('.hx-badge')) continue;
    const s = document.createElement('span');
    s.className = 'hx-badge';
    s.textContent = 'Updated by agent';
    h.appendChild(s);
  }
}

/* ---------------- a moment of a video (threads #video-moment) ----------------
 * Every video a reviewed page shows is a comment target at a moment (#video-target): in comment mode a click marks the
 * moment it shows, pausing it first, and a drag across it first draws a box on its frame. Under each video with threads
 * a rail as wide as it marks each thread at its moment, and while the video is paused at a thread's moment its box shows
 * on the frame (#video-rail). A host page's video gives the moment its item's comment marks, and keeps the host's look:
 * no rail and no box (comment-layer #ui-place).
 */
const MOMENT_NEAR = 0.05; // seconds: a video paused this near a thread's moment shows its box
const videos = { watched: new WeakSet(), rails: new WeakMap(), boxes: new WeakMap(), drag: null, swallow: 0, frame: 0 };

// A moment in minutes and seconds, as a chip and the rail read it.
function clockWords(seconds) {
  const whole = Math.max(0, Math.floor(seconds));
  return Math.floor(whole / 60) + ':' + String(whole % 60).padStart(2, '0');
}

// threads #root-chip-moment, #root-chip-box: what a chip reads after its spot's name: the moment, then Box.
function momentParts(spot) {
  if (!spot) return [];
  return [typeof spot.moment === 'number' ? clockWords(spot.moment) : null, spot.box ? 'Box' : null].filter(Boolean);
}

// mockups #round-video-comment: a round's video block is anchored by its round and viewport, so a comment on it is kept
// with the round it was made on.
const ROUND_ANCHOR = /^round-([0-9a-f]{7,40})-(desktop|phone)$/;
function roundName(anchorId) {
  const m = ROUND_ANCHOR.exec(String(anchorId || ''));
  return m ? 'Round ' + m[1].slice(0, 7) : null;
}

// The video a spot is on: the element its target names in its block, or the block itself.
function spotVideo(anchorId, target) {
  const holder = anchorId ? findAnchor(anchorId) : null;
  if (!holder || holder.tagName === 'VIDEO') return holder;
  const el = target && target.type === 'element' ? resolveElement(holder, target.key) : null;
  return el && el.tagName === 'VIDEO' ? el : null;
}
function threadVideo(th) {
  const b = placedMark(th.ev);
  return spotVideo(b.anchorId, b.target);
}
// A thread at a moment of a video the page shows: its rail mark is its pin.
function onVideoRail(th) {
  return Boolean(th.spot && th.spot.moment !== null && !LAYER_SERVICE && !COMMENT_UI && threadVideo(th));
}

// A video comment mode marks: one in an anchored block of a spec or mockup page, a round's included.
function markedVideo(el) {
  const video = el instanceof Element ? el.closest('video') : null;
  return video && !LAYER_SERVICE && !COMMENT_UI && holderOf(video) ? video : null;
}

// Where the video draws its frame in its box, as its object-fit places it (a video's default, contain).
function videoFrame(video) {
  const r = video.getBoundingClientRect();
  const w = video.videoWidth, h = video.videoHeight, fit = getComputedStyle(video).objectFit;
  if (!w || !h || fit === 'fill') return { left: r.left, top: r.top, width: r.width, height: r.height };
  const scale = (fit === 'cover' ? Math.max : Math.min)(r.width / w, r.height / h);
  const width = w * scale, height = h * scale;
  return { left: r.left + (r.width - width) / 2, top: r.top + (r.height - height) / 2, width, height };
}

// #video-fields: a box dragged from one point to another, as fractions of the frame, inside it; a drag too small to see
// is a click.
function dragBox(video, x0, y0, x1, y1) {
  const f = videoFrame(video);
  const inside = (v, start, size) => Math.min(start + size, Math.max(start, v));
  const left = inside(Math.min(x0, x1), f.left, f.width), right = inside(Math.max(x0, x1), f.left, f.width);
  const top = inside(Math.min(y0, y1), f.top, f.height), bottom = inside(Math.max(y0, y1), f.top, f.height);
  if (right - left < 6 || bottom - top < 6 || !f.width || !f.height) return null;
  const fraction = v => Math.min(1, Math.max(0, Math.round(v * 10000) / 10000));
  return { left: fraction((left - f.left) / f.width), top: fraction((top - f.top) / f.height),
    width: fraction((right - left) / f.width), height: fraction((bottom - top) / f.height) };
}

// One box on a video's frame, placed in the video's block in screen pixels (in a zoomed mockup frame it keeps its own
// scale, as pins do).
function placeBox(el, video, box) {
  const f = videoFrame(video);
  const parent = el.offsetParent;
  const p = parent ? parent.getBoundingClientRect() : { left: -scrollX, top: -scrollY };
  const dx = parent ? parent.clientLeft - parent.scrollLeft : 0, dy = parent ? parent.clientTop - parent.scrollTop : 0;
  Object.assign(el.style, { left: (f.left + box.left * f.width - p.left - dx) + 'px', top: (f.top + box.top * f.height - p.top - dy) + 'px',
    width: (box.width * f.width) + 'px', height: (box.height * f.height) + 'px' });
}

function boxNode(video) {
  const holder = holderOf(video);
  const node = Object.assign(document.createElement('div'), { className: 'hx-video-box' });
  node.setAttribute('aria-hidden', 'true');
  (holder === video ? video.parentElement : holder).appendChild(node);
  return node;
}

// #video-target: a comment on the video at the moment it shows, with the box dragged, if any; a video in a mockup frame
// also carries the step its frames show (mockups #step-threads-carry).
function commentOnVideo(video, box) {
  const holder = holderOf(video);
  if (!video.paused) video.pause();
  const desc = holder === video ? null : elementDescriptor(video, holder);
  const spot = Object.assign(video.closest('[data-mockup-frame]') ? pageStep() : {}, { moment: Math.round(video.currentTime * 1000) / 1000 }, box ? { box } : {});
  openComposer(holder.dataset.anchor, desc ? { type: 'element', key: desc.key } : null, (desc && desc.quote) || null, '', spot);
  scheduleVideos();
}

// In comment mode a press on a video starts a box and a drag draws it; released, the box's comment opens and the click
// that ends the drag marks nothing more. A press with no drag is a click (onDocClick).
function listenVideoDrag() {
  document.addEventListener('pointerdown', e => {
    const video = state.commentMode && e.button === 0 ? markedVideo(e.target) : null;
    if (!video) return;
    e.preventDefault(); // no text selection and no seek of the video's own controls: the drag draws a box
    videos.drag = { video, id: e.pointerId, x: e.clientX, y: e.clientY, node: null };
    try { video.setPointerCapture(e.pointerId); } catch (_) { /* a pointer that ended already */ }
  }, true);
  document.addEventListener('pointermove', e => {
    const d = videos.drag;
    if (!d || e.pointerId !== d.id) return;
    const box = dragBox(d.video, d.x, d.y, e.clientX, e.clientY);
    if (!box) return void (d.node && (d.node.hidden = true));
    d.node = d.node || boxNode(d.video);
    d.node.hidden = false;
    placeBox(d.node, d.video, box);
  }, true);
  document.addEventListener('pointerup', e => {
    const d = videos.drag;
    if (!d || e.pointerId !== d.id) return;
    videos.drag = null;
    if (d.node) d.node.remove();
    const box = dragBox(d.video, d.x, d.y, e.clientX, e.clientY);
    if (!box) return;
    videos.swallow = performance.now() + 500;
    commentOnVideo(d.video, box);
  }, true);
  document.addEventListener('pointercancel', () => {
    if (videos.drag && videos.drag.node) videos.drag.node.remove();
    videos.drag = null;
  }, true);
}

// A video to a moment, paused: shown first when it sits under Earlier rounds; its box shows once it is there.
function seekVideo(video, moment) {
  if (video.closest('.hx-rounds-earlier[hidden]')) document.querySelector('#hx-rounds .hx-round-earlier')?.click();
  video.pause();
  if (video.currentTime !== moment) video.currentTime = moment;
  scheduleVideos();
}

// threads #root-chip-moment: a thread on a video, its video at its moment, paused and in view; false for any other.
function showMoment(th) {
  const video = th && th.spot && th.spot.moment !== null ? threadVideo(th) : null;
  if (!video) return false;
  seekVideo(video, th.spot.moment);
  scrollToSpot(video);
  return true;
}

// threads #video-target on a host page: the moment an item's paused video shows, one played or moved to it; with `to`, a
// thread's spot, that video to the thread's moment instead (#root-chip-moment).
function itemMoment(item, to = null) {
  const shown = [...item.el.querySelectorAll('video')];
  if (to) return void (typeof to.moment === 'number' && shown[0] && seekVideo(shown[0], to.moment));
  const video = shown.find(v => v.paused && (v.currentTime > 0 || v.played.length > 0));
  return video ? { moment: Math.round(video.currentTime * 1000) / 1000 } : {};
}

function scheduleVideos() {
  cancelAnimationFrame(videos.frame);
  videos.frame = requestAnimationFrame(renderVideos);
}

function watchVideo(video) {
  if (videos.watched.has(video)) return;
  videos.watched.add(video);
  for (const type of ['play', 'pause', 'seeking', 'seeked', 'loadedmetadata', 'durationchange', 'ended', 'resize']) video.addEventListener(type, scheduleVideos);
}

// #video-rail: each reviewed video's rail of its threads, numbered as their pins are, and its boxes.
function renderVideos() {
  if (LAYER_SERVICE || COMMENT_UI) return;
  const on = new Map();
  let n = 0;
  for (const th of state.threads.values()) {
    n++;
    if (!th.spot || th.spot.moment === null || !showsAtStep(th)) continue;
    const video = threadVideo(th);
    if (!video) continue;
    if (!on.has(video)) on.set(video, []);
    on.get(video).push({ th, number: th.number || n });
  }
  for (const video of document.querySelectorAll('video')) {
    if (!markedVideo(video)) continue;
    watchVideo(video);
    renderRail(video, on.get(video) || []);
    renderBoxes(video, on.get(video) || []);
  }
}

function renderRail(video, entries) {
  let rail = videos.rails.get(video);
  if (!entries.length) {
    if (rail) rail.remove();
    return void videos.rails.delete(video);
  }
  if (!rail || rail.previousElementSibling !== video) {
    if (rail) rail.remove();
    rail = document.createElement('div');
    rail.className = 'hx-video-rail';
    rail.setAttribute('role', 'group');
    rail.setAttribute('aria-label', 'Threads on this video');
    video.after(rail);
    videos.rails.set(video, rail);
  }
  rail.style.width = video.offsetWidth + 'px';
  rail.style.marginLeft = '0px';
  if (rail.offsetParent === video.offsetParent) rail.style.marginLeft = (video.offsetLeft - rail.offsetLeft) + 'px';
  const duration = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : null;
  const key = JSON.stringify([duration, entries.map(({ th, number }) => [th.id, number, th.status, th.spot.moment, Boolean(th.spot.box),
    state.activeThread === th.id, waitsOnReviewer(th)])]);
  if (rail.dataset.key === key) return;
  rail.dataset.key = key;
  rail.replaceChildren();
  if (!duration) return; // its marks wait for its length
  for (const { th, number } of entries) {
    const mark = rail.appendChild(document.createElement('button'));
    mark.type = 'button';
    mark.className = 'hx-video-mark' + (state.activeThread === th.id ? ' active' : '');
    mark.dataset.s = th.status;
    mark.dataset.thread = th.id;
    mark.textContent = number;
    mark.style.left = Math.min(100, th.spot.moment / duration * 100) + '%';
    const words = momentParts(th.spot).join(' · ');
    mark.title = words;
    mark.setAttribute('aria-label', 'Thread ' + number + ' at ' + words);
    if (waitsOnReviewer(th)) mark.appendChild(waitingDot()); // threads #unread
    mark.addEventListener('click', e => {
      e.stopPropagation();
      seekVideo(video, th.spot.moment);
      selectThread(th, false);
      if (NARROW.matches) scrollToSpot(video); // above the focused thread's sheet (threads #focus-phone)
    });
  }
  rail.appendChild(Object.assign(document.createElement('span'), { className: 'hx-video-time', textContent: clockWords(duration) }));
}

// While the video is paused at a thread's moment its box shows on the frame, and so does the box of the comment being
// written on it.
function renderBoxes(video, entries) {
  const shown = [];
  if (video.paused && !video.seeking) {
    const near = moment => typeof moment === 'number' && Math.abs(moment - video.currentTime) < MOMENT_NEAR;
    for (const { th } of entries) if (th.spot.box && near(th.spot.moment)) shown.push(th.spot.box);
    const c = state.composer;
    if (c && c.kind === 'comment' && c.box && near(c.moment) && spotVideo(c.anchorId, c.target) === video) shown.push(c.box);
  }
  const nodes = (videos.boxes.get(video) || []).filter(node => node.isConnected);
  while (nodes.length > shown.length) nodes.pop().remove();
  while (nodes.length < shown.length) nodes.push(boxNode(video));
  shown.forEach((box, i) => placeBox(nodes[i], video, box));
  videos.boxes.set(video, nodes);
}

function handoff() {
  if (uiHost()) return handoffLayer(); // a held host page's drafts, as on the page (#ui-handoff)
  const reviewed = reviewHandoffState(state.threads, openTbdCount(), state.reviewer.browser, mockupOpen(state.mockups));
  const action = pageHandoffState(reviewed);
  if (action.tbd) return nextTbd();
  if (state.handoffPosting || !action.enabled) return;
  state.handoffPosting = true; // until this hand-off's send settles: a double tap writes one
  // Hand off lists this browser's drafts (#live-handoff); Accept spec writes the existing hand-off without a list, and
  // accepts the spec's mockup set with it (mockups #accept-set).
  const listed = action.finish ? acceptedMockups(state.mockups) : { events: handoffEvents(state.events, state.reviewer.browser) };
  save(Object.assign({ id: humanId('h'), event: 'handoff', anchorId: '', target: null, quote: null, text: 'batch from ' + state.transport.mode, actor: 'human', createdAt: new Date().toISOString(), schemaVersion: 1 }, listed));
  toast(action.finish ? 'Spec accepted' : 'Handed off ' + action.drafts + ' comment' + (action.drafts === 1 ? '' : 's') + ', agent notified');
  refresh().finally(() => { state.handoffPosting = false; });
}

// TBD open and Next TBD share one step and one position (state.lastTbd).
function nextTbd() {
  const open = openTbdMarkers(document.querySelectorAll('[data-spec-tbd]'));
  if (open.length) jumpToTbd(advanceTbd(state, open));
}

function jumpToTbd(el) {
  if (NARROW.matches) openPanel(false); // a narrow screen's open panel covers the spec
  if (!el.matches('a[href],button,input,select,textarea,[tabindex]')) el.setAttribute('tabindex', '-1');
  el.scrollIntoView({ block: 'center', behavior: 'smooth' });
  el.focus({ preventScroll: true });
}

const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

let toastTimer;
function toast(msg) {
  const holder = LAYER_SERVICE ? layer.root : document.body; // the layer's own look stays in its overlay
  if (!holder) return;
  let el = holder.querySelector('#hx-toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'hx-toast';
    el.className = 'hx-toast';
    holder.appendChild(el);
  }
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2600);
}

/* ---------------- saving: page-named events through the outbox ----------------
 * review-state #live-save, #offline. Every human event is named by this page, kept in
 * browser storage for this spool until the service stores it, shown at once, and sent
 * in order; a resend after a lost response stores nothing new.
 */
const OUTBOX_PREFIX = 'spec-chat:outbox:';
const OUTBOX_KEY = OUTBOX_PREFIX + REVIEW_DIR;
const LOADED_KEY = 'spec-chat:events:' + REVIEW_DIR;
let pageOutbox = null; // this page's own outbox when its storage cannot keep or read it
let flushing = null;

function reviewStorage() {
  try { return window.localStorage; } catch (_) { return frameStore; }
}

// This page's reviewer: only a page with no storage takes the browser id its address passes (review-state #model-fields).
function pageReviewer() {
  const storage = reviewStorage();
  return reviewerIdentity(storage, Math.random, storage === frameStore ? addressBrowser(location.hash) : null);
}

// served-pages #served-storage: a page with no origin has no browser storage, so it keeps its fruit name and outbox in
// its frame's name, which lasts as long as the host's frame: across reloads and moves to the service's other pages in it.
// The name holds each review service's items apart (the layer's service, or the page's at its own address), so another
// service's page never gets them; any later page in the frame can read it, so a page's write keeps of every other
// service only its reviewer (browser id and fruit name) and its unsent changes: once that service's page opens in the
// frame again, it is the same reviewer, its unsent changes send, and its hand-off lists every draft it saved. A name
// the host gives its frame, before the page loads or after, is the host's for the rest of the page's life, even if the
// host clears it (its links and forms may target it): from then on the page keeps nothing there and serves its items
// from its own memory, so its unsent changes still send until it reloads.
const FRAME_NAME = 'spec-chat:';
const FRAME_SERVICE = (LAYER_SERVICE || OWN_ADDRESS).origin;
const plainObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
let frameLeft = null; // the frame name as this page last read or wrote it; null before its first use
let frameServices = {}; // every service's items as this page last read or wrote them
let frameHosts = false; // the host has named the frame

// Whether the name is the host's: at the page's first use, a name not under the prefix; after it, any name but the one
// the page last left there, since only the host changes it under the page.
function frameNamedByHost() {
  if (frameHosts) return true;
  const name = window.name;
  if (frameLeft !== null) return frameHosts = name !== frameLeft;
  if (name && !name.startsWith(FRAME_NAME)) return frameHosts = true;
  frameLeft = name;
  try {
    const services = JSON.parse(name.slice(FRAME_NAME.length));
    if (plainObject(services)) frameServices = services;
  } catch (_) { /* under the prefix, anything but a plain object holds no items */ }
  return false;
}

// This service's items: in the frame name, or in the page's memory once the name is the host's.
function frameItems() {
  frameNamedByHost();
  if (!Object.hasOwn(frameServices, FRAME_SERVICE) || !plainObject(frameServices[FRAME_SERVICE])) frameServices[FRAME_SERVICE] = {};
  return frameServices[FRAME_SERVICE];
}

// An outbox holding changes its service has not stored yet.
function unsentChanges(key, value) {
  if (!key.startsWith(OUTBOX_PREFIX) || typeof value !== 'string') return false;
  try {
    const entries = JSON.parse(value);
    return Array.isArray(entries) && entries.length > 0;
  } catch (_) { return false; }
}

// Of another service's items, what the frame keeps: its reviewer and its unsent changes.
const reviewerAndUnsent = items => Object.fromEntries(Object.entries(items).filter(([k, v]) =>
  (k === REVIEWER_KEY && typeof v === 'string') || unsentChanges(k, v)));

const frameStore = {
  getItem: key => {
    const items = frameItems();
    return Object.hasOwn(items, key) && typeof items[key] === 'string' ? items[key] : null;
  },
  setItem: (key, value) => {
    const items = frameItems();
    items[key] = String(value);
    if (frameHosts) return;
    const others = Object.entries(frameServices).filter(([service, kept]) => service !== FRAME_SERVICE && plainObject(kept))
      .map(([service, kept]) => [service, reviewerAndUnsent(kept)])
      .filter(([, kept]) => Object.keys(kept).length);
    frameServices = Object.fromEntries(others.concat([[FRAME_SERVICE, items]]));
    window.name = frameLeft = FRAME_NAME + JSON.stringify(frameServices);
  },
};

function outboxEntries() {
  if (pageOutbox) return pageOutbox;
  try { return JSON.parse(reviewStorage().getItem(OUTBOX_KEY) || '[]'); } catch (_) { return pageOutbox = []; }
}

function setOutbox(entries) {
  if (!pageOutbox) {
    try { reviewStorage().setItem(OUTBOX_KEY, JSON.stringify(entries)); return; } catch (_) { /* fall back to this page */ }
  }
  pageOutbox = entries;
}

// A layer entry also names the scope and subject it is sent with, and the store it is known to go to.
const outboxEvent = entry => Object.assign({ actor: 'human', name: entry.name, body: entry.body },
  entry.scope ? { scope: entry.scope, subject: entry.subject, store: entry.store } : {});
// The comment UI page's outbox holds every page's it was relayed: the context shows its own page's alone.
const inContext = entry => !COMMENT_UI || (entry.scope ? uiHost() && layerItems().some(item => item.scope === entry.scope)
  : !uiHost() && entry.dir === ui.dir);
const outboxEvents = () => outboxEntries().filter(inContext).map(outboxEvent);

function save(body, route) {
  // mockups #prototype-marks: a comment on a prototype, and an edit repeating it, carries no version, as a host page's
  // does; its hand-off still names the page it was made on, which review-host reviewed reads.
  const mark = PROTOTYPE_ROUTE && ['comment', 'edit'].includes(body.event);
  const event = Object.assign({}, body, { browser: state.reviewer.browser, author: state.reviewer.author }, state.version && !mark ? { version: state.version } : {});
  state.lastStamp = nextStamp(state.lastStamp, Date.now(), Math.random);
  const entry = Object.assign({ name: eventName(event, state.lastStamp), body: event },
    LAYER_SERVICE || uiHost() ? layerRoute(event, route) : COMMENT_UI ? { dir: ui.dir } : {});
  setOutbox(outboxEntries().concat([entry]));
  ingest([outboxEvent(entry)]);
  flushOutbox();
}

async function drainOutbox() {
  for (let entry; (entry = outboxEntries()[0]);) {
    let result;
    try { result = await state.transport.postEvent(entry); } catch (_) { state.outboxBlocked = true; return; }
    setOutbox(outboxEntries().filter(waiting => waiting.name !== entry.name));
    if (result === 'refused') forgetEvent(entry.name);
  }
  state.outboxBlocked = false;
}

function flushOutbox() {
  if (!flushing) flushing = drainOutbox().finally(() => { flushing = null; renderOffline(); });
  return flushing;
}

// The service refused the event outright; no resend can store it.
function forgetEvent(name) {
  state.events = state.events.filter(e => !(e.actor === 'human' && e.name === name));
  state.seenNames.delete('human/' + name);
  ingest([], true);
  toast('The review service refused a change; it was not saved');
}

// The service's copy of the review, kept only in browser storage: a frame's name holds just this reviewer's own work.
function keepLoaded(events) {
  if (events.length === state.storedCount) return;
  state.storedCount = events.length;
  try { window.localStorage.setItem(LOADED_KEY, JSON.stringify(events)); } catch (_) { /* the page still shows them */ }
}

function loadedEvents() {
  try { return eventList(JSON.parse(window.localStorage.getItem(LOADED_KEY) || '[]')); } catch (_) { return []; }
}

function renderOffline() {
  const el = LAYER_SERVICE ? layer.root && layer.root.querySelector('#hx-offline') : document.getElementById('hx-offline');
  if (!el) return;
  el.textContent = state.outboxBlocked ? offlineNotice(outboxEntries().length) : '';
  el.hidden = !el.textContent;
}

// The ETag of the spec text this page shows, from its own address; served directly, the browser's copy of this document.
// The page's answer as it loaded, read once from the cache the page loaded from: its version, its text, and on a mockup
// page its lane's commit and prototype (#prototype-updated, #prototype-route).
let servedAnswer = null;
function servedPage() {
  servedAnswer = servedAnswer || fetch(OWN_ADDRESS.href, { cache: 'force-cache' }).catch(() => null);
  return servedAnswer;
}

async function shownVersion() {
  const answer = await servedPage();
  const tag = answer && answer.headers.get('ETag');
  return tag ? tag.replace(/^W\//, '').replace(/"/g, '') : null;
}

/* ---------------- loops ---------------- */
async function refresh() {
  if (shell.held) return flushOutbox(); // shell #shell-ui-page-only: a held page reads no thread
  if (LAYER_SERVICE || uiHost()) return refreshLayer();
  if (COMMENT_UI && !ui.dir) return;
  flushOutbox(); // #offline-reopen: waiting events are sent first; the read never waits on a pending save
  requestMockups();
  const dir = ui.dir;
  try {
    const listed = await state.transport.listEvents(threadLanes(state.threads.values()));
    if (dir !== ui.dir) return; // the shell relayed another page meanwhile
    const wake = Array.isArray(listed) ? null : listed.wake;
    const events = Array.isArray(listed) ? listed : listed.events;
    state.lanes = (!Array.isArray(listed) && listed.stages) || { lane: null, stages: {} };
    if (!COMMENT_UI) keepLoaded(events);
    ingest(events.concat(outboxEvents()));
    if (COMMENT_UI) { focusPicked(); sendMarks(); }
    const agentEvents = state.events.filter(e => e.actor === 'agent');
    const observation = handoffObservation(state.events, Date.now());
    document.getElementById('hx-agent').textContent = handoffAgentText(observation, wake, agentEvents[agentEvents.length - 1]);
    status('connected · ' + state.transport.label + ' · ' + state.threads.size + ' threads');
    if (location.hash.includes('hxdebug') && !state._beaconed) {
      state._beaconed = true;
      fetch('/hxdebug/threads=' + state.threads.size + '/pins=' + document.querySelectorAll('.hx-pin').length + '/charts=' + state.charts.size).catch(() => {});
    }
  } catch (e) {
    if (!state.eventsRendered && dir === ui.dir) ingest((COMMENT_UI ? [] : loadedEvents()).concat(outboxEvents()));
    status('event sync failed: ' + e.message);
  }
  renderOffline();
}

async function watchSpec() {
  let m, commit = null;
  try {
    if (PROTOTYPE_ROUTE) { // its answer names the lane branch commit too (mockups #prototype-updated)
      const answer = await fetch(OWN_ADDRESS.pathname, { method: 'HEAD' }); // a 304 names the commit too
      m = new Date(answer.headers.get('Last-Modified') || 0).getTime();
      commit = answer.headers.get('X-Spec-Chat-Commit');
    } else m = await state.transport.specModified();
  } catch (_) { return; } // offline: the next poll checks
  const moved = Boolean(commit && proto.commit && commit !== proto.commit);
  if ((moved || (m && state.specMtime && m > state.specMtime)) && !document.getElementById('hx-banner')) {
    const b = document.createElement('div');
    b.className = 'hx-banner';
    b.id = 'hx-banner';
    // mockups #review-banner, #prototype-updated: the page keeps what it loaded until Reload.
    b.innerHTML = (moved ? 'Prototype' : MOCKUP_SPEC ? 'Mockup' : 'Spec') + ' updated by agent <button>Reload</button>';
    b.querySelector('button').addEventListener('click', () => location.reload());
    document.body.appendChild(b);
  }
  if (m) state.specMtime = state.specMtime || m;
}

/* ---------------- reader's place ----------------
 * Per spec path: the nearest data-anchor at the viewport top plus the offset
 * past its top, in localStorage. Restored once, after render; an address anchor
 * wins, and a reader who already moved is never re-scrolled. Every storage call
 * is wrapped: no storage means no kept place, never a broken page.
 */
const PLACE_KEY = 'hx-place:' + OWN_ADDRESS.pathname;

function readPlace(storage) {
  try {
    const place = JSON.parse(storage.getItem(PLACE_KEY));
    return place && typeof place.anchor === 'string' && Number.isFinite(place.offset) ? place : null;
  } catch (_) { return null; }
}

function writePlace(storage, place) {
  try {
    if (place) storage.setItem(PLACE_KEY, JSON.stringify(place));
    else storage.removeItem(PLACE_KEY);
  } catch (_) { /* full, sandboxed, or disabled: the place is simply not kept */ }
}

// spec-page-design #top-line: the page shows from under the fixed top line, so a place is measured from there: the space
// the body starts under (--hx-top-space), which holds while the phone panel hides the line.
function pageTop() {
  return parseFloat(document.documentElement.style.getPropertyValue('--hx-top-space')) || 0;
}

// The block at the screen's top: of a mockup's frames, one the toggle shows, the other laid out unseen (offView).
function currentPlace() {
  if (window.scrollY <= 0) return null;
  const top = pageTop();
  let place = null;
  for (const el of document.querySelectorAll('[data-anchor]')) {
    const r = el.getBoundingClientRect();
    if (!r.height || r.top > top || (place && top - r.top >= place.offset) || offView(el)) continue;
    place = { anchor: el.dataset.anchor, offset: Math.round(top - r.top) };
  }
  return place;
}

// Scrolls a place back under the page's top: its block `offset` above it, as currentPlace measured.
function goToPlace(place) {
  const el = place && inView(findAnchor(place.anchor));
  if (el) window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY + place.offset - pageTop());
}

// Call before render; returns the after-render step that restores, then keeps, the place.
// An address anchor names a [data-anchor]; specs carry no ids, so the browser never scrolls to it.
function addressPlace() {
  let anchor = location.hash.slice(1);
  try { anchor = decodeURIComponent(anchor); } catch (_) { /* keep raw */ }
  return anchor && findAnchor(anchor) ? { anchor, offset: 0 } : null;
}

function keepPlace() {
  if (EMBED_REVIEW_DIR) return () => {};
  let storage = null;
  try { storage = window.localStorage; } catch (_) { /* sandboxed frame */ }
  let moved = false;
  const intent = () => { moved = true; };
  const intents = ['wheel', 'touchstart', 'keydown', 'pointerdown'];
  for (const t of intents) window.addEventListener(t, intent, { capture: true, passive: true });
  return () => {
    for (const t of intents) window.removeEventListener(t, intent, true);
    goToPlace(moved ? null : addressPlace() || (storage && readPlace(storage)));
    if (!storage) return;
    let timer = 0;
    const save = () => { clearTimeout(timer); timer = 0; writePlace(storage, currentPlace()); };
    window.addEventListener('scroll', () => { if (!timer) timer = setTimeout(save, 250); }, { passive: true });
    window.addEventListener('pagehide', save);
  };
}

/* ---------------- boot ---------------- */
(async function boot() {
  if (COMMENT_UI) return bootCommentUI();
  if (LAYER_SERVICE) return bootLayer();
  mountStyle();
  const restorePlace = keepPlace();
  const httpPage = !EMBED_REVIEW_DIR && ['http:', 'https:'].includes(OWN_ADDRESS.protocol);
  if (httpPage) state.versionAsked = shownVersion(); // a held page publishes the version it shows (#shell-msg-publish)
  // Accept spec waits until the set is told; a mockup page is no spec, so nothing waits on it: it asks its spec's set for
  // Previous and Next alone (mockups #review-chrome, #next-screen)
  if (httpPage) Object.assign(state, MOCKUP_SPEC ? { mockupAsk: true } : { mockups: null, mockupAsk: true });
  // Deferred script: the DOM is the served spec until mountUI; compare it, never refetch it.
  if (httpPage) state.range.loaded = anchorSignatures(document);
  mountUI();
  if (MOCKUP_SPEC) mountMockup();
  if (httpPage) applyIssueFocus();
  if (httpPage) { listenHost(SHELL_PAGE); if (!MOCKUP_SPEC) requestEvidence(); }
  await hydrateIslands();
  adoptForeignCharts();
  restorePlace();
  window.addEventListener('pagehide', saveTyped); // review-state #offline-leave, after the kept place's (saveTyped)
  renderHighlight();
  window.addEventListener('hashchange', arriveAtAddress);
  // spec scripts can create/recreate charts at any time; rescan when canvases appear
  let adoptTimer = null;
  new MutationObserver(muts => {
    if (!muts.some(m => [...m.addedNodes].some(n => n.nodeType === 1 && (n.tagName === 'CANVAS' || (n.querySelector && n.querySelector('canvas')))))) return;
    clearTimeout(adoptTimer);
    adoptTimer = setTimeout(adoptForeignCharts, 200);
  }).observe(document.body, { childList: true, subtree: true });
  if (OWN_ADDRESS.protocol === 'file:' && !('showDirectoryPicker' in window)) {
    document.getElementById('hx-mode').disabled = true;
    status('view-only — this browser cannot annotate file:// pages; use Chrome/Edge, or serve via review-serve.py over http://');
    console.warn('[spec-chat] showDirectoryPicker unavailable; file:// annotation needs the File System Access API (Chromium). Run review-serve.py and open the http://localhost URL instead.');
    return;
  }
  state.transport = OWN_ADDRESS.protocol === 'file:' ? fsaTransport() : httpTransport();
  state.reviewer = pageReviewer();
  if (httpPage) state.version = await state.versionAsked;

  if (state.transport.mode === 'fsa') {
    const btn = document.getElementById('hx-connect');
    const repick = document.getElementById('hx-repick');
    const restored = await state.transport.tryRestore();
    if (restored !== 'granted') {
      btn.hidden = false;
      btn.textContent = restored === 'prompt' ? 'Resume review' : 'Connect review folder';
      status(restored === 'prompt'
        ? 'view-only — resume the saved folder, or choose a different ancestor of this spec'
        : 'view-only — pick or drop \u201c' + suggestedGrant() + '\u201d to connect');
      const connected = () => { btn.hidden = true; repick.hidden = true; startLoops(); };
      const resumeReview = async () => {
        btn.textContent = 'Waiting for browser approval…';
        btn.disabled = true;
        status('waiting for browser edit approval… if no prompt is visible, switch to the browser’s permission window');
        try {
          if (await state.transport.resume()) connected();
          else {
            btn.textContent = 'Resume review';
            btn.disabled = false;
            status('edit access was not granted — resume again or choose a different folder');
          }
        } catch (e) {
          btn.textContent = 'Resume review';
          btn.disabled = false;
          status('resume failed: ' + e.message);
        }
      };
      const chooseFolder = async options => {
        status('choose the folder, then approve “Allow this site to edit files?” — the browser may open it as a separate window');
        await state.transport.connect(options);
        connected();
      };
      if (restored === 'prompt') {
        repick.hidden = false;
        let mouseResumeAt = 0;
        btn.addEventListener('pointerdown', e => {
          if (e.pointerType !== 'mouse') return;
          mouseResumeAt = performance.now();
          resumeReview();
        });
        btn.addEventListener('click', e => {
          if (e.detail > 0 && performance.now() - mouseResumeAt < 1000) return;
          resumeReview();
        });
        repick.addEventListener('click', async () => {
          try {
            repick.textContent = 'Waiting for browser approval…';
            repick.disabled = true;
            await chooseFolder({ useLastDir: false });
          }
          catch (e) {
            repick.textContent = 'Choose different folder';
            repick.disabled = false;
            status('connect failed: ' + e.message);
          }
        });
      } else {
        btn.addEventListener('click', async () => {
          try {
            btn.textContent = 'Waiting for browser approval…';
            btn.disabled = true;
            await chooseFolder();
          }
          catch (e) {
            btn.textContent = 'Connect review folder';
            btn.disabled = false;
            status('connect failed: ' + e.message);
          }
        });
      }
      // picker-free path: drag any ancestor folder (home, Documents, repo) onto the page
      btn.title = 'Pick or drop \u201c' + suggestedGrant() + '\u201d (or any folder above the spec) \u2014 remembered for every spec beneath it. Spec lives in: ' + decodeURIComponent(OWN_ADDRESS.pathname).replace(/\/[^/]*$/, '');
      document.addEventListener('dragover', e => { if (!state.loopsStarted) e.preventDefault(); });
      document.addEventListener('drop', async e => {
        if (state.loopsStarted) return;
        e.preventDefault();
        const item = [...(e.dataTransfer.items || [])].find(i => i.kind === 'file');
        if (!item || !item.getAsFileSystemHandle) return;
        try {
          const h = await item.getAsFileSystemHandle();
          if (!h || h.kind !== 'directory') { toast('Drop a folder, not a file — any parent folder of the spec works'); return; }
          const r = await state.transport.adopt(h);
          if (r === 'ok') connected();
          else toast(r === 'wrong' ? 'That folder isn\u2019t above this spec \u2014 drop \u201c' + suggestedGrant() + '\u201d instead' : 'Write access declined');
        } catch {}
      });
      return;
    }
  }
  startLoops();
})();

function startLoops() {
  if (state.loopsStarted) return; // auto-resume and the connect button can both win
  state.loopsStarted = true;
  status(state.transport.mode === 'fsa' ? 'connected · local folder' : 'connected · review-serve');
  refresh();
  watchSpec();
  setInterval(refresh, 2000);
  window.addEventListener('online', refresh);
  setInterval(watchSpec, 5000);
  // Pins live inside anchored holders; a host framework re-rendering a holder (React
  // remounts, spec scripts rebuilding DOM) silently drops them. Redraw is idempotent.
  setInterval(() => { renderPins(); renderThreadHighlight(); }, 2000);
  // Markers are placed before pins, which step clear of them.
  window.addEventListener('resize', () => { document.querySelectorAll('.hx-jev-marker').forEach(placeJevMarker); renderPins(); renderThreadHighlight(); });
}

/* ---------------- mockup page (mockups.spec.html) ----------------
 * A screen of a spec's mockup set (MOCKUP_SPEC) is reviewed by the spec page's own review, on the spec page's chrome
 * (#review-same, #review-chrome): the top line links the spec and, while the mockup changed since the base, offers
 * Before and After (#before-table), and under it the step bar of a mockup with a walkthrough (#walkthrough); Hand off
 * never becomes Accept spec, Next TBD shows only while one of its questions is open, and no Jev or evidence shows. The
 * two frames keep the mockup's own look, side by side or stacked, each zoomed whole into its space (#review-fit), and
 * every element in them is a comment target (#review-targets).
 */
// The frames into one stage where the first sat, desktop first, each in a slot with its label and a fit box it is zoomed
// in; a first round's directions around them (#directions-page). Fitted at once, so a kept place or an address is
// restored onto the fitted page. The frames are the mockup's own, never another direction's (#markup-frame).
function mountMockup() {
  const frames = MOCKUP_VIEWPORTS.map(([viewport, name]) => PROTOTYPE_ROUTE ? prototypeFrame(viewport, name)
    : document.querySelector('[data-mockup-frame="' + viewport + '"]:not([data-direction])'));
  mockup.styles = [...document.querySelectorAll('style, link[rel~="stylesheet"]')]
    .filter(sheet => !sheet.hasAttribute('data-hx-chrome') && !sheet.closest('[data-mockup-frame]'));
  const stage = document.createElement('div');
  stage.className = 'hx-mockup';
  const first = frames.find(frame => frame && frame.parentNode); // a prototype's frames are new: the stage goes last
  if (first) first.parentNode.insertBefore(stage, first);
  else document.body.appendChild(stage);
  mockup.stage = stage;
  mockup.slots = MOCKUP_VIEWPORTS.map(([viewport, name], index) => mockupSlot(stage, viewport, name, frames[index]));
  mountDirections();
  fitMockup();
  if (PROTOTYPE_ROUTE) mountPrototype();
  // The space changes with the window and as the panel opens or closes (a body class); pins follow a new zoom.
  const refit = () => { if (fitMockup()) { renderPins(); renderThreadHighlight(); } };
  window.addEventListener('resize', refit);
  new MutationObserver(refit).observe(document.body, { attributes: true, attributeFilter: ['class'] });
  mountWalkthrough();
}

// One frame's slot in `into`: its label and the fit box it is zoomed in.
function mockupSlot(into, viewport, name, frame) {
  const slot = into.appendChild(document.createElement('div'));
  slot.className = 'hx-mockup-slot';
  slot.dataset.viewport = viewport;
  slot.setAttribute('role', 'group');
  slot.setAttribute('aria-label', name);
  const label = slot.appendChild(document.createElement('div'));
  label.className = 'hx-mockup-label';
  label.textContent = name;
  const fit = slot.appendChild(document.createElement('div'));
  fit.className = 'hx-mockup-fit';
  if (frame) fit.appendChild(frame);
  return { viewport, slot, fit, frame };
}

// Every frame's slot the page shows: the mockup's own, then each other direction's (#directions-page).
function allSlots() {
  return [...mockup.slots, ...(mockup.dirs || []).flatMap(d => d.slots || [])];
}

// #review-fit: each frame zoomed to its slot's width, never above its own size. Side by side the slots share the row in
// proportion to the frames' own widths (CSS), so both zoom alike; stacked, each takes the screen's width. True when a
// zoom changed.
function fitMockup() {
  for (const s of allSlots()) s.slot.style.setProperty('--hx-natural', s.fit.offsetWidth + 'px');
  let changed = false;
  for (const s of allSlots()) {
    const natural = s.fit.offsetWidth; // its own width: offsetWidth is not zoomed
    const zoom = String(natural ? Math.min(1, Math.floor(s.slot.clientWidth / natural * 1e4) / 1e4) : 1);
    if (s.fit.style.getPropertyValue('--hx-zoom') === zoom) continue;
    s.fit.style.zoom = zoom;
    s.fit.style.setProperty('--hx-zoom', zoom); // pins and the arrival ring keep their own size (CSS)
    changed = true;
  }
  return changed;
}

// #review-chrome: a mockup page's Hand off never becomes Accept spec or TBD open: it hands off this browser's drafts, with
// Next TBD beside it while one of its questions is open.
function pageHandoffState(action) {
  return reviewsMockup() ? { ...action, finish: false, tbd: false, nextTbd: action.openTbds > 0, enabled: action.drafts > 0 } : action;
}

// The mockup's spec, beside its set's folder, at the same compared range (#review-chrome).
function mockupSpecAddress() {
  return new URL('../../' + encodeURIComponent(MOCKUP_SPEC) + '.spec.html' + OWN_ADDRESS.search, OWN_ADDRESS).href;
}

// #next-screen: Previous and Next around the screen's place in its spec's set, in the order the Screens index lists it,
// at the start of the range bar's row, after the spec link, and on a phone at the end of the row under the range (CSS).
// Shown only while the set told holds more than one screen and lists this page; Previous on the first and Next on the
// last are disabled. Each opens its screen at once as that screen's Screens index row does (mockupHref, read on the
// press, so at the base this page compares then), what was typed going as drafts as the page is left (#next-screen-press,
// saveTyped), and its tooltip names the screen and its key, none on a phone (spec-page-design #keys-rule). Drawn again
// only when what it shows changes (#next-screen-live).
function renderPager(set) {
  mockup.set = set;
  const own = addressPath(OWN_ADDRESS.href);
  const at = set.findIndex(m => addressPath(mockupHref(m.page, null)) === own);
  const near = at < 0 || set.length < 2 ? null : [set[at - 1] || null, set[at + 1] || null];
  const row = document.querySelector('#hx-range-bar .hx-range-row');
  const drawn = near && row ? JSON.stringify([at, set.length, near.map(m => m && [m.page, m.screen]), NARROW.matches]) : '';
  if (drawn === mockup.pager) return;
  mockup.pager = drawn;
  document.getElementById('hx-pager')?.remove();
  if (!drawn) return;
  const pager = document.createElement('span');
  pager.className = 'hx-pager';
  pager.id = 'hx-pager';
  pager.setAttribute('role', 'group');
  pager.setAttribute('aria-label', 'Screens');
  const control = (id, word, key, icon, to) => {
    const button = pager.appendChild(document.createElement('button'));
    button.type = 'button';
    button.id = id;
    button.innerHTML = ICONS[icon];
    button.disabled = !to;
    button.setAttribute('aria-label', word + ' screen' + (to ? ', ' + to.screen : ''));
    if (to && !NARROW.matches) button.title = word + ' screen: ' + to.screen + ' (' + key + ')';
    if (to) button.addEventListener('click', () => followLink(mockupHref(to.page, null)));
  };
  control('hx-pager-previous', 'Previous', '←', 'chevron-left', near[0]);
  pager.appendChild(Object.assign(document.createElement('span'), { className: 'hx-pager-place', textContent: (at + 1) + ' of ' + set.length }));
  control('hx-pager-next', 'Next', '→', 'chevron-right', near[1]);
  row.insertBefore(pager, row.firstChild);
}

// An address's path in words, so two spellings of one page compare equal.
function addressPath(href) {
  const path = new URL(href).pathname;
  try { return decodeURIComponent(path); } catch (_) { return path; }
}

// #next-screen-keys: where the arrow keys already work they keep working: a text field, the comment box and the base
// picker among them, a control the arrows move in, the step bar, a video, and anything that scrolls sideways around the
// focus. A prototype's frame keeps every key it is given, so none of its keys reaches this page.
function arrowsWork(target) {
  if (!(target instanceof Element)) return false;
  if (target.isContentEditable || target.closest('textarea,select,input:not([type=button],[type=submit],[type=reset],'
    + '[type=checkbox],[type=image],[type=file],[type=color]),video,audio,[role=slider],[role=spinbutton],[role=listbox],'
    + '[role=radiogroup],[role=tablist],[role=menu],[role=menubar],[role=tree],[role=treegrid],[role=grid],[role=textbox],'
    + '[role=combobox],#hx-range-picker,#hx-steps')) return true;
  for (let el = target; el && el !== document.body && el !== document.documentElement; el = el.parentElement) {
    if (el.scrollWidth > el.clientWidth && /^(auto|scroll)$/.test(getComputedStyle(el).overflowX)) return true;
  }
  return false;
}

// The Left and Right arrow keys, with no modifier key, press Previous and Next; the tooltips follow the screen's width.
if (MOCKUP_SPEC) {
  document.addEventListener('keydown', e => {
    if ((e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') || e.defaultPrevented || e.isComposing
      || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || arrowsWork(e.target)) return;
    const control = document.getElementById(e.key === 'ArrowLeft' ? 'hx-pager-previous' : 'hx-pager-next');
    if (!control || control.disabled) return;
    e.preventDefault();
    control.click();
  });
  NARROW.addEventListener('change', () => renderPager(mockup.set));
}

// The page as served, read once from the cache the page loaded from.
function shownMockupText() {
  mockup.shown = mockup.shown || servedPage()
    .then(response => response && response.ok ? response.clone().text() : null).catch(() => null);
  return mockup.shown;
}

// What a mockup shows, to compare two of its texts: its head's elements but meta, which the service adds as it serves,
// and its body.
function mockupSignature(doc) {
  return [...doc.head.children].filter(el => el.tagName !== 'META').map(el => el.outerHTML).join('') + doc.body.outerHTML;
}

// #before-base, #before-table: the top line by how the mockup compares with the base its page compares. Absent there, it
// reads New since the base; the same, the compared range alone; differing, the compared range, then Before and After,
// After pressed. A new base shows the current frames again.
async function showMockupBase(baseline) {
  const request = ++mockup.request;
  showMockupBefore(false);
  mockup.base = null;
  mockup.differs = false;
  document.getElementById('hx-mockup-view')?.remove(); // back once this base is compared
  document.getElementById('hx-spec-link')?.setAttribute('href', mockupSpecAddress());
  const copy = document.getElementById('hx-range-copy');
  if (baseline.html == null) {
    if (copy) {
      copy.textContent = 'New since the base';
      copy.removeAttribute('aria-label');
      copy.title = rangeBarText(baseline);
    }
    return;
  }
  if (PROTOTYPE_ROUTE) {
    // #before-prototype: Before plays the round video recorded at the compared base, offered only while the spool
    // holds one.
    const rounds = await readRounds();
    if (request !== mockup.request) return;
    mockup.base = rounds.find(round => baseline.base && baseline.base.startsWith(round.commit)) || null;
    mockup.differs = Boolean(mockup.base);
    return renderMockupView();
  }
  const base = new DOMParser().parseFromString(baseline.html, 'text/html');
  const text = await shownMockupText();
  if (request !== mockup.request) return;
  mockup.base = base;
  // A page whose served text cannot be read again is taken as changed, so Before stays offered.
  mockup.differs = text === null || mockupSignature(new DOMParser().parseFromString(text, 'text/html')) !== mockupSignature(base);
  renderMockupView();
}

// Before and After in the range bar, before Change base, shown while the mockup differs from the base it was compared with.
function renderMockupView() {
  const row = document.querySelector('#hx-range-bar .hx-range-row');
  if (!row) return;
  let view = document.getElementById('hx-mockup-view');
  if (!view) {
    view = document.createElement('span');
    view.className = 'hx-mockup-view';
    view.id = 'hx-mockup-view';
    view.setAttribute('role', 'group');
    view.setAttribute('aria-label', 'Mockup at the base or now');
    for (const [id, text, before] of [['hx-mockup-before', 'Before', true], ['hx-mockup-after', 'After', false]]) {
      const button = view.appendChild(document.createElement('button'));
      button.type = 'button';
      button.id = id;
      button.textContent = text;
      button.addEventListener('click', () => showMockupBefore(before));
    }
    row.insertBefore(view, document.getElementById('hx-range-change'));
  }
  view.hidden = !mockup.differs;
  document.getElementById('hx-mockup-before').setAttribute('aria-pressed', String(mockup.before));
  document.getElementById('hx-mockup-after').setAttribute('aria-pressed', String(!mockup.before));
}

// #before-changed: Before shows both frames as the mockup was at the base, under its style sheets then, with no pins and
// no comment control (CSS); After returns the current frames with their threads.
function showMockupBefore(before) {
  before = Boolean(before && mockup.base && mockup.differs);
  if (before !== mockup.before) {
    mockup.before = before;
    if (shell.held) publishPage(); // Before shows no step to comment at (shell #shell-msg-publish)
    if (before) {
      setCommentMode(false);
      if (state.composer) { state.composer = null; renderPanel(); }
    }
    if (PROTOTYPE_ROUTE) showRoundBefore(before);
    else for (const s of mockup.slots) {
      const base = before && mockup.base.querySelector('[data-mockup-frame="' + s.viewport + '"]:not([data-direction])');
      const frame = before ? base && document.importNode(base, true) : s.frame;
      if (before && frame) frame.querySelectorAll('script').forEach(el => el.remove());
      s.fit.replaceChildren(...(frame ? [frame] : []));
    }
    mockup.styles.forEach(sheet => { sheet.disabled = before; });
    document.querySelectorAll('[data-hx-before]').forEach(el => el.remove());
    const chrome = document.querySelector('style[data-hx-chrome]');
    for (const sheet of before && !PROTOTYPE_ROUTE ? mockup.base.querySelectorAll('style, link[rel~="stylesheet"]') : []) {
      if (sheet.closest('[data-mockup-frame]')) continue;
      const copy = document.importNode(sheet, true);
      copy.setAttribute('data-hx-before', '');
      document.head.insertBefore(copy, chrome); // the mockup's sheets come before the chrome's, as served
    }
    document.body.classList.toggle('hx-mockup-before', before);
    renderViewport(); // the pick's two frames alone, side by side (#directions-page)
    fitMockup();
    // #step-threads-before: the other version's walkthrough, at the step showing when it holds it, else its first.
    const showing = showingStep();
    if (!PROTOTYPE_ROUTE) mockup.steps = walkthroughOf(before ? mockup.base : document); // a round video is no walkthrough
    renderStepBar();
    if (!before || !PROTOTYPE_ROUTE) showStep(Math.max(0, stepIndex(showing && showing.name)), { still: true });
    renderHighlight();
  }
  if (document.getElementById('hx-mockup-view')) renderMockupView();
}

/* ---------------- a first round's directions (mockups #directions) ----------------
 * A mockup whose file names two or three directions in its directions list (#markup-directions) shows them side by side
 * in its frames' place, each in one frame under its name and the line saying what it gives up, the agent's pick with its
 * reason; one Desktop and Phone toggle above them switches every frame between the two viewports (#directions-page). The
 * pick is the mockup itself, its two frames the page's own; every other direction is another route of the prototype, or
 * its own frames naming it, shown as it loads (#directions-pick-only). Pick saves the reviewer's answer on the list as an
 * answer button does, and the newest stands (#directions-pick).
 */
// #markup-directions: a version's directions in order: each one's key, short name, gives-up line, the agent's pick's
// reason, and another direction's route, with its item in the list; none while the list names fewer than two. The pick
// is the one carrying data-pick, else the first.
function directionsOf(doc) {
  const list = doc.querySelector('[data-mockup-directions]');
  const items = list ? [...list.querySelectorAll('[data-direction]')] : [];
  if (items.length < DIRECTIONS_SHOWN) return [];
  const pick = items.find(item => item.hasAttribute('data-pick')) || items[0];
  const holder = holderOf(list);
  return items.map(item => ({
    key: item.dataset.direction.trim(), name: sourceText(item), item, pick: item === pick,
    givesUp: (item.dataset.givesUp || '').trim(), reason: item === pick ? (item.dataset.pick || '').trim() : '',
    route: item === pick ? null : (item.dataset.route || '').trim() || null,
    target: holder ? elementDescriptor(item, holder).key : null, // where its Pick's answer is saved
  }));
}

// #directions-page: the toggle, then a row of the directions, each one's head (its name, Agent's pick on the agent's,
// Your pick, Pick), the agent's reason, its gives-up line, and its frames: the pick's the page's own two, each other's
// its own pair. Each column holds both viewports, the one the toggle does not show laid out unseen, so the pick's
// walkthrough, threads, and fit run on both as with no directions, a prototype's frame reaching the step showing as the
// toggle shows it (reachStep).
function mountDirections() {
  const dirs = directionsOf(document);
  if (!dirs.length) return;
  mockup.dirs = dirs;
  const el = (tag, className, text) => Object.assign(document.createElement(tag), { className }, text ? { textContent: text } : {});
  const toggle = el('span', 'hx-dirs-toggle');
  toggle.setAttribute('role', 'group');
  toggle.setAttribute('aria-label', 'Viewport');
  for (const [viewport, name] of MOCKUP_VIEWPORTS) {
    const button = toggle.appendChild(el('button', 'hx-btn', name));
    button.type = 'button';
    button.dataset.viewport = viewport;
    button.addEventListener('click', () => { mockup.view = viewport; mockup.arrival = null; renderViewport(); });
  }
  const row = el('div', 'hx-dir-row');
  for (const d of dirs) {
    const column = row.appendChild(el('section', d.pick ? 'hx-dir' : 'hx-dir hx-dir-other'));
    column.setAttribute('aria-label', d.name);
    const head = column.appendChild(el('div', 'hx-dir-head'));
    head.appendChild(el('span', 'hx-dir-name', d.name));
    if (d.pick) head.appendChild(el('span', 'hx-dir-tag hx-dir-agent', 'Agent\'s pick'));
    d.yours = head.appendChild(el('span', 'hx-dir-tag hx-dir-yours', 'Your pick'));
    d.button = head.appendChild(el('button', 'hx-btn hx-dir-pick', 'Pick'));
    d.button.type = 'button';
    d.button.setAttribute('aria-label', 'Pick ' + d.name);
    d.button.addEventListener('click', () => pickDirection(d));
    if (d.reason) column.appendChild(el('p', 'hx-dir-why', d.reason));
    if (d.givesUp) {
      const gives = column.appendChild(el('p', 'hx-dir-gives'));
      gives.append(el('b', '', 'Gives up:'), ' ' + d.givesUp);
    }
    const frames = column.appendChild(el('div', 'hx-dir-frames'));
    if (d.pick) mockup.slots.forEach(s => frames.appendChild(s.slot));
    else d.slots = MOCKUP_VIEWPORTS.map(([viewport, name]) => Object.assign(mockupSlot(frames, viewport, name, directionFrame(d, viewport, name)), { other: true }));
  }
  mockup.stage.classList.add('hx-directions');
  mockup.stage.append(toggle, row);
  NARROW.addEventListener('change', renderViewport); // the toggle starts at the screen's width until the reviewer presses it
  renderPicks();
  renderViewport();
}

// Another direction's frame at a viewport: the frame its drawn mockup draws naming it, else its route on the running
// prototype, framed as the pick's is (#directions-file). No spot of it takes a comment (#directions-pick-only), so
// neither it nor any part of it names an anchor: none is a comment target, Tab stop, pin, place, or arrival.
function directionFrame(d, viewport, name) {
  const frame = PROTOTYPE_ROUTE ? prototypeFrame(viewport, d.name + ' · ' + name)
    : [...document.querySelectorAll('[data-mockup-frame][data-direction]')]
      .find(frame => frame.dataset.mockupFrame === viewport && frame.dataset.direction.trim() === d.key);
  if (!frame) return null;
  for (const el of [frame, ...frame.querySelectorAll('[data-anchor]')]) el.removeAttribute('data-anchor');
  frame.dataset.direction = d.key;
  return frame;
}

// The viewport the directions show: the one the reviewer toggled to, else Desktop on a desktop and Phone on a phone.
function shownViewport() {
  return mockup.view || (NARROW.matches ? 'phone' : 'desktop');
}

// A frame the toggle hides, laid out unseen.
function offView(el) {
  return Boolean(el && el.closest('.hx-mockup-slot[data-off]'));
}

// #directions-page: the toggle's viewport pressed and every column's frame at it; while Before shows, the pick's two frames
// at the base, side by side as with no directions (CSS). Refitted, with the marks that follow; a frame of the pick it
// shows anew reaches the step showing, one shown already being there (#prototype-steps).
function renderViewport() {
  if (!mockup.dirs) return;
  const shown = shownViewport();
  const hidden = mockup.slots.filter(s => offView(s.slot));
  mockup.stage.querySelectorAll('.hx-dirs-toggle button').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.viewport === shown)));
  for (const s of allSlots()) s.slot.toggleAttribute('data-off', !mockup.before && s.viewport !== shown);
  fitMockup();
  renderPins();
  renderThreadHighlight();
  if (PROTOTYPE_ROUTE && mockup.step >= 0 && hidden.some(s => !offView(s.slot))) mockup.reaching = reachStep(mockup.step);
}

// #directions-page: every arrival at a spot (an address, the kept place, a thread, a question, a spot a shell shows) at
// the viewport that shows it: a part of a frame the toggle hides, or that frame, switches the toggle to its viewport; a
// spot in a prototype's frames is asked of them in turn (showInFrames), and an arrival here ends that one, its switch
// put back first. The spot, for its caller to bring into view.
function inView(el) {
  endArrival();
  if (offView(el)) {
    mockup.view = el.closest('.hx-mockup-slot').dataset.viewport;
    renderViewport();
  }
  return el;
}

// #directions-pick: the reviewer's pick, the direction the newest answer on the directions list is on, as an answer
// button's chosen mark derives (answerBlocks); on a held page, the one its shell's marks name. Null with none.
function pickedDirection() {
  const holder = holderOf(document.querySelector('[data-mockup-directions]'));
  if (!holder) return null;
  if (shell.held) return mockup.dirs.find(d => d.name === shell.answers.get(holder.dataset.anchor)) || null;
  let picked = null;
  for (const th of state.threads.values()) { // fold order: the last is the newest comment
    const b = placedMark(th.ev);
    if (b.anchorId === holder.dataset.anchor && b.target && b.target.type === 'element') picked = mockup.dirs.find(d => d.target === b.target.key) || picked;
  }
  return picked;
}

// #directions-pick: the reviewer's pick reads Your pick, and every direction but the standing pick, the reviewer's when
// there is one, else the agent's, offers Pick.
function renderPicks() {
  if (!mockup.dirs) return;
  const yours = pickedDirection();
  const standing = yours || mockup.dirs.find(d => d.pick);
  for (const d of mockup.dirs) {
    d.yours.hidden = d !== yours;
    d.button.hidden = d === standing;
  }
}

// #directions-pick: one press saves the reviewer's answer on the directions list, its words the direction's name, on
// the direction's item, as an answer button saves its own (saveAnswer). Until the review state loads the standing pick
// is unknown, so the press waits for it, as an answer button's does.
function pickDirection(d) {
  const holder = holderOf(document.querySelector('[data-mockup-directions]'));
  if (!holder || !d.target || !state.eventsRendered || d.button.hidden) return;
  saveAnswer(holder, d.item, d.name);
}

/* ---------------- a mockup's walkthrough, threads at a step, and questions (mockups #walkthrough, #step-threads,
 * #mockup-questions) ----------------
 * A drawn mockup's walkthrough is its <ol data-mockup-steps>: each <li data-step> one step, its caption its text, and in
 * data-step-by the parts whose press reaches it (#markup-steps). The step bar under the top line holds Play and every
 * step; the runtime marks each frame with the steps reached so far in data-at, the showing one last, and the mockup's
 * own style draws each step from it (#markup-at), with its own motion. A comment carries the step showing, and its pin
 * shows only at that step (#step-threads). Each open question in <aside data-mockup-questions>, an anchored TBD block
 * naming the parts it is about and its step (#markup-questions), shows a question mark on those parts while its step
 * shows, and Next TBD steps through them (#mockup-questions). Both lists show in the chrome, never as the page's own.
 */
function mountWalkthrough() {
  document.querySelectorAll('[data-mockup-steps],[data-mockup-questions],[data-mockup-regions],[data-mockup-directions]').forEach(el => el.classList.add('hx-mockup-markup'));
  mockup.steps = walkthroughOf(document);
  renderStepBar();
  // #walkthrough-address: the page opens at the step its address names, else at the first.
  showStep(Math.max(0, stepIndex(OWN_ADDRESS.searchParams.get('step'))), { still: true, keepAddress: true });
  // Pins, question marks, and the ring follow the parts once a step's motion settles.
  const settle = () => {
    clearTimeout(mockup.settle);
    mockup.settle = setTimeout(() => { renderPins(); renderThreadHighlight(); }, 30);
  };
  mockup.stage.addEventListener('transitionend', settle);
  mockup.stage.addEventListener('animationend', settle);
}

// #markup-steps: a version's walkthrough in order, each step's name, its caption, the parts whose press reaches it, and
// on a prototype the actions that reach it from the step before (#markup-actions), the list of them no caption.
function walkthroughOf(doc) {
  const list = doc && doc.querySelector('[data-mockup-steps]');
  return list ? [...list.querySelectorAll('[data-step]')].map(item => ({
    name: (item.getAttribute('data-step') || '').trim(),
    caption: sourceText(item, UNREAD + ',ul,ol'),
    by: (item.getAttribute('data-step-by') || '').split(/\s+/).filter(Boolean),
    actions: [...item.querySelectorAll('[data-press],[data-type]')].map(action => action.hasAttribute('data-press')
      ? { press: action.getAttribute('data-press') } : { type: action.getAttribute('data-type'), text: action.getAttribute('data-text') || '' })
      .filter(action => (action.press || action.type || '').trim()),
  })).filter(step => step.name) : [];
}

// Declarations, not constants: boot mounts the page before the constants below it are set.
function stepIndex(name) {
  return name ? mockup.steps.findIndex(step => step.name === name) : -1;
}

function showingStep() {
  return mockup.steps[mockup.step] || null;
}

// A step's name in words: one written as lowercase words joined by hyphens, as data-at holds names, reads as words.
function stepWords(name) {
  return /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name) ? sentenceCase(name.replace(/-/g, ' ')) : name;
}

// threads #root-chip-part: a step as a chip names it, by number and name; a step the mockup no longer holds, removed.
function stepChip(name) {
  const index = stepIndex(name);
  return index >= 0 ? 'Step ' + (index + 1) + ' ' + stepWords(name) : 'Step removed · ' + stepWords(name);
}

// A part of a mockup by its name: its anchor in words; a prototype's region by its name in its file (#prototype-regions).
function partName(part) {
  return isRegion(part) ? sourceText(part) || part.dataset.anchor : sentenceCase(part.dataset.anchor.replace(/-/g, ' '));
}

// A prototype mockup's region, as its file lists it.
function isRegion(el) {
  return Boolean(PROTOTYPE_ROUTE && el && el.closest('[data-mockup-regions]'));
}

// #step-threads-carry: a comment saved on a mockup page with a walkthrough carries the step its frames show.
function pageStep() {
  if (COMMENT_UI) return ui.mockup && ui.page.step ? { step: ui.page.step } : {}; // the step its held page shows
  return MOCKUP_SPEC && !mockup.before && showingStep() ? { step: showingStep().name } : {};
}

// #step-threads-pin, #step-threads-any: a thread shows at its step alone; one with no step, or whose step the mockup no
// longer holds, at every step.
function showsAtStep(th) {
  const index = MOCKUP_SPEC && th.spot ? stepIndex(th.spot.step) : -1;
  return index < 0 || index === mockup.step;
}

// Both frames to the step a thread was written at; the walkthrough stops playing there.
function showThreadStep(th) {
  const index = MOCKUP_SPEC && th.spot ? stepIndex(th.spot.step) : -1;
  if (index < 0) return;
  stopPlay();
  if (index !== mockup.step) showStep(index);
}

// #walkthrough-step, #markup-at: one step in both frames, each marked with the steps reached so far, the showing one
// last. `still` draws it at once, as the page opens or a version changes; the address names the step showing.
function showStep(index, { still = false, keepAddress = false, pressed = null } = {}) {
  const step = mockup.steps[index];
  mockup.step = step ? index : -1;
  const at = step ? mockup.steps.slice(0, index + 1).map(s => s.name).join(' ') : null;
  if (still) {
    mockup.stage.classList.add('hx-mockup-still');
    requestAnimationFrame(() => requestAnimationFrame(() => mockup.stage.classList.remove('hx-mockup-still')));
  }
  for (const s of mockup.slots) {
    const frame = s.fit.querySelector('[data-mockup-frame]');
    if (!frame) continue;
    if (at === null) frame.removeAttribute('data-at');
    else frame.setAttribute('data-at', at);
  }
  if (!keepAddress) {
    const url = new URL(location.href);
    if (step) url.searchParams.set('step', step.name); else url.searchParams.delete('step');
    history.replaceState(history.state, '', url.href);
  }
  // #prototype-steps: a prototype's frames reach it by its actions; one the reviewer pressed through is there already.
  if (PROTOTYPE_ROUTE && step) mockup.reaching = reachStep(index, pressed);
  updateStepBar();
  renderPins();
  renderThreadHighlight();
  if (shell.held) publishPage(); // the showing step (shell #shell-msg-publish)
}

// #walkthrough-bar: Play, then every step by number and name, built once per walkthrough so a redraw keeps its buttons.
function renderStepBar() {
  let bar = document.getElementById('hx-steps');
  const key = JSON.stringify(mockup.steps.map(step => step.name));
  if (!mockup.steps.length) {
    if (bar) bar.remove();
    mockup.bar = '';
    return;
  }
  if (!bar) {
    bar = document.createElement('div');
    bar.className = 'hx-steps';
    bar.id = 'hx-steps';
    bar.setAttribute('role', 'group');
    bar.setAttribute('aria-label', 'Walkthrough');
    const line = document.getElementById('hx-top-line');
    if (line) line.appendChild(bar); else mockup.stage.parentNode.insertBefore(bar, mockup.stage);
  }
  if (mockup.bar !== key) {
    mockup.bar = key;
    const play = document.createElement('button');
    play.type = 'button';
    play.id = 'hx-play';
    play.className = 'hx-step hx-play';
    play.textContent = 'Play';
    play.addEventListener('click', () => { if (mockup.play) stopPlay(); else playWalkthrough(); });
    const steps = mockup.steps.map((step, index) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'hx-step';
      button.dataset.step = step.name;
      button.innerHTML = '<span class="hx-step-n">' + (index + 1) + '</span><span class="hx-step-name">' + esc(stepWords(step.name)) + '</span><span class="hx-step-count" hidden></span>';
      button.addEventListener('click', () => { stopPlay(); showStep(index); });
      return button;
    });
    const caption = document.createElement('span');
    caption.className = 'hx-step-caption';
    caption.id = 'hx-step-caption';
    caption.setAttribute('aria-live', 'polite');
    bar.replaceChildren(play, ...steps, caption);
  }
  updateStepBar();
}

// The step showing pressed with its caption, each step's count of open questions and threads, and Play pressed while
// the walkthrough plays.
function updateStepBar() {
  const bar = document.getElementById('hx-steps');
  if (!bar) return;
  const counts = stepCounts();
  bar.querySelectorAll('.hx-step[data-step]').forEach((button, index) => {
    button.setAttribute('aria-pressed', String(index === mockup.step));
    const count = button.querySelector('.hx-step-count');
    const n = counts.get(button.dataset.step) || 0;
    count.hidden = !n;
    count.textContent = n ? String(n) : '';
    button.setAttribute('aria-label', 'Step ' + (index + 1) + ' ' + stepWords(button.dataset.step) + (n ? ', ' + n + ' open' : ''));
  });
  const play = document.getElementById('hx-play');
  play.setAttribute('aria-pressed', String(Boolean(mockup.play)));
  const caption = showingStep() ? showingStep().caption : '';
  const shown = document.getElementById('hx-step-caption');
  if (shown.textContent !== caption) shown.textContent = caption;
  // On a phone the bar scrolls within itself: the step showing into its view, never the page sideways.
  const pressed = bar.querySelector('.hx-step[aria-pressed="true"]');
  if (pressed && bar.scrollWidth > bar.clientWidth) {
    const left = pressed.offsetLeft - bar.offsetLeft, right = left + pressed.offsetWidth;
    if (left < bar.scrollLeft) bar.scrollLeft = left - 8;
    else if (right > bar.scrollLeft + bar.clientWidth) bar.scrollLeft = right - bar.clientWidth + 8;
  }
}

// #walkthrough-bar: each step's open questions and open threads; an answer counts with its question.
function stepCounts() {
  const counts = new Map();
  if (!mockup.steps.length || mockup.before) return counts;
  const questions = pageQuestions();
  const asked = new Set(questions.map(q => q.anchor));
  const add = name => { if (stepIndex(name) >= 0) counts.set(name, (counts.get(name) || 0) + 1); };
  questions.forEach(q => add(q.step));
  for (const th of state.threads.values()) if (th.status !== 'resolved' && !asked.has(th.ev.body.anchorId)) add(th.spot && th.spot.step);
  return counts;
}

// #walkthrough-step: Play shows every step in order from the first, holding each until its motion ends and its caption
// can be read, then stops on the last and offers Play again.
async function playWalkthrough() {
  const run = mockup.play = {};
  for (let index = 0; index < mockup.steps.length && mockup.play === run; index++) {
    showStep(index);
    await motionEnds();
    if (mockup.play !== run) return;
    const words = mockup.steps[index].caption.split(/\s+/).filter(Boolean).length;
    await new Promise(done => setTimeout(done, Math.max(1500, 300 * words)));
  }
  if (mockup.play === run) stopPlay();
}

function stopPlay() {
  if (!mockup.play) return;
  mockup.play = null;
  updateStepBar();
}

// The motion a step starts in the frames: each transition and animation that ends, once begun.
async function motionEnds() {
  if (PROTOTYPE_ROUTE) return mockup.reaching; // each action waits for the motion before it ends (#prototype-steps)
  await new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)));
  const moving = (document.getAnimations ? document.getAnimations() : []).filter(animation => {
    const target = animation.effect && animation.effect.target;
    return target && mockup.stage.contains(target) && Number.isFinite(animation.effect.getComputedTiming().endTime);
  });
  await Promise.all(moving.map(animation => animation.finished.catch(() => {})));
}

// #walkthrough-press: outside comment mode, pressing a part a step names in data-step-by shows that step, the next such
// after the one showing, else the first; the runtime's own marks on a part press nothing.
function pressStep(e) {
  if (!MOCKUP_SPEC || !mockup.steps.length || !(e.target instanceof Element) || e.target.closest(OVERLAY)) return false;
  const frame = e.target.closest('[data-mockup-frame]');
  if (!frame || !mockup.stage.contains(frame)) return false;
  const parts = new Set();
  for (let el = e.target; el && el !== frame.parentElement; el = el.parentElement) if (el.dataset.anchor) parts.add(el.dataset.anchor);
  const reached = mockup.steps.map((step, index) => step.by.some(anchor => parts.has(anchor)) ? index : -1).filter(index => index >= 0);
  if (!reached.length) return false;
  stopPlay();
  const next = reached.find(index => index > mockup.step);
  showStep(next === undefined ? reached[0] : next);
  return true;
}

// A question's step is showing: one asked at no step, or at a step the walkthrough lacks, shows at every step.
function askedNow(q) {
  const index = stepIndex(q.step);
  return index < 0 || index === mockup.step;
}

// The parts a question is about, in the frames.
function questionParts(q) {
  return q.about.map(findAnchor).filter(part => part && (mockup.stage.contains(part) || isRegion(part)));
}

// threads #questions: a question on a mockup page names its part and its step.
function questionSpot(q) {
  if (COMMENT_UI) return q.spot || '#' + q.anchor; // the words its held page published
  if (!MOCKUP_SPEC) return blockSpot(q.block).chip;
  const [part] = questionParts(q);
  return [part ? partName(part) : blockSpot(q.block).chip, q.step ? stepChip(q.step) : null].filter(Boolean).join(' · ');
}

// #mockup-questions-show: while its step shows, a question mark on each part an open question is about, pressing it
// opens the question focused; the focused question's parts ringed (#mockup-questions-next).
function renderQuestionMarks() {
  document.querySelectorAll('.hx-qmark').forEach(mark => mark.remove());
  document.querySelectorAll('.hx-asked').forEach(part => part.classList.remove('hx-asked'));
  if (!MOCKUP_SPEC || mockup.before || PROTOTYPE_ROUTE) return; // a prototype's frames draw its question marks
  for (const q of pageQuestions()) {
    if (!askedNow(q)) continue;
    for (const part of questionParts(q)) {
      if (q.anchor === state.focusQuestion) part.classList.add('hx-asked');
      const mark = part.appendChild(document.createElement('button'));
      mark.type = 'button';
      mark.className = 'hx-qmark';
      mark.dataset.question = q.anchor;
      mark.textContent = '?';
      mark.title = q.text;
      mark.setAttribute('aria-label', 'Question: ' + q.text);
      mark.addEventListener('click', e => { e.stopPropagation(); focusQuestion(q.anchor); });
    }
  }
}

// #mockup-questions-show: a question opens focused: on a desktop alone in the panel, on a phone as a sheet under its part.
function focusQuestion(anchor) {
  if (state.composer && state.composer.kind === 'edit') state.composer = null;
  state.focusQuestion = mockup.question = anchor;
  state.focusThread = state.activeThread = null;
  focusView.text = '';
  openPanel(!NARROW.matches);
  renderPanel();
  renderPins();
}

// #mockup-questions-next: a mockup page's Next TBD steps through its open questions in page order.
function nextQuestion() {
  const questions = pageQuestions();
  const at = questions.findIndex(q => q.anchor === (state.focusQuestion || mockup.question));
  if (questions.length) goToQuestion(questions[(at + 1) % questions.length]);
}

// #mockup-questions-next: both frames to the question's step, its part into view ringed, and the question focused.
function goToQuestion(q) {
  const index = stepIndex(q.step);
  stopPlay();
  if (index >= 0 && index !== mockup.step) showStep(index);
  focusQuestion(q.anchor);
  if (PROTOTYPE_ROUTE) return void showInFrames(q.about[0]);
  const parts = questionParts(q);
  scrollToSpot(parts.find(part => !offView(part)) || parts[0]);
}

// The focused question: its bubble with its answer buttons and Reply, its chip, and its answers under it.
function renderFocusedQuestion(card, q) {
  delete card.dataset.thread;
  card.dataset.question = q.anchor;
  card.querySelector(':scope > .hx-composer')?.remove();
  focusView.composer = '';
  const entry = conversationEntries(state.threads, [q]).find(e => e.kind === 'question');
  card.querySelector('.hx-focused-stream').replaceChildren(questionEntry(entry, conversationContext(threadPlace())));
}

/* ---------------- a prototype mockup (mockups #prototype, #round-video) ----------------
 * A prototype mockup's page frames its route on the lane's running prototype, which the review service serves at the
 * lane's prototype origin (#prototype-route), once at each viewport (#frame-viewports), each frame as tall as the route's
 * content. The page holds each frame as a shell does, the service's own origin being a shell for the prototype routes
 * under their mockup pages (shell #shell-setting-own), and the two speak only by the shell messages across their origins
 * (shell #shell-msg-prototype): a frame draws no comment UI, points while comment mode is on, picks what the reviewer
 * clicks, draws the marks the page sends it in its own content, and says its size. The page reaches a step by loading the
 * route and having it do every action up to it (#prototype-steps), follows a reviewer pressing through, and shows each
 * round's videos under the frames (#round-video).
 */
// One frame of the route at its viewport's width, a screen tall until the route's content is taller.
function prototypeFrame(viewport, name) {
  const frame = document.createElement('iframe');
  frame.className = 'hx-proto-frame';
  frame.dataset.mockupFrame = viewport;
  frame.dataset.anchor = viewport; // a comment on no region marks its frame (#prototype-regions)
  frame.title = name;
  frame.style.width = (viewport === 'phone' ? 390 : 1440) + 'px';
  frame.style.height = screenHeight(viewport) + 'px';
  return frame;
}

function screenHeight(viewport) {
  return viewport === 'phone' ? 844 : 900;
}

// #prototype-route, #prototype-down: the route at the lane's prototype origin, as the page's answer names it; with no
// prototype origin or address, or a route that does not say hello back once loaded, the page reads Prototype not running
// with the route: a prototype that does not answer, or a front that does not answer the origin (review-service
// #prototype-front), loads a page that runs no layer.
const PROTOTYPE_HELLO_MS = 5000;
async function mountPrototype() {
  // each frame's id, which its hello carries and the route sends back on each message (shell #shell-msg-prototype)
  for (const [i, s] of allSlots().entries()) {
    Object.assign(s, { id: 'frame-' + (i + 1), at: -1, held: false, ever: false, sent: '', chain: Promise.resolve(), act: null,
      shown: null, waiters: [], loads: 0, hold: 0 });
    s.ready = new Promise(done => { s.loaded = done; });
  }
  window.addEventListener('message', frameMessage);
  NARROW.addEventListener('change', renderRounds);
  readRounds();
  const answer = await servedPage();
  const named = (answer && answer.headers.get('X-Spec-Chat-Prototype')) || '';
  proto.commit = answer && answer.headers.get('X-Spec-Chat-Commit');
  try { proto.base = /^https?:\/\//.test(named) ? new URL(named) : null; } catch (_) { proto.base = null; }
  if (!proto.base) return prototypeDown(); // none: no prototype origin, or no address names the prototype
  const route = new URL(PROTOTYPE_ROUTE.replace(/^\/+/, ''), proto.base);
  for (const s of mockup.slots) {
    s.frame.addEventListener('load', () => frameLoaded(s));
    s.frame.src = route.href;
  }
  // #directions-file: each other direction's route on the same prototype, framed as the pick's, as it loads
  for (const d of mockup.dirs || []) for (const s of d.route ? d.slots : []) {
    s.frame.addEventListener('load', () => frameLoaded(s));
    s.frame.src = new URL(d.route.replace(/^\/+/, ''), proto.base).href;
  }
}

function prototypeDown() {
  if (proto.down) return;
  proto.down = true;
  for (const s of mockup.slots) {
    if (s.act) s.act.answer(-1);
    if (s.shown) s.shown(false);
    for (const waiter of (s.waiters || []).splice(0)) waiter(false);
  }
  const note = document.createElement('div');
  note.className = 'hx-proto-down';
  note.setAttribute('role', 'status');
  note.innerHTML = '<strong>Prototype not running</strong><code></code>';
  note.querySelector('code').textContent = PROTOTYPE_ROUTE;
  mockup.stage.replaceChildren(note);
  for (const s of mockup.slots) { s.frame = null; s.loaded(); }
}

// A message to a frame's route, only while it runs at the lane's prototype origin (shell #shell-msg-prototype).
function toFrame(s, message) {
  if (s.frame && proto.base) s.frame.contentWindow.postMessage(message, proto.base.origin);
}

// Each load of a frame: said hello to, with its id, the viewport it shows, and the mockup's regions, so the route is held
// (shell #shell-msg-hello, #shell-msg-prototype); it publishes, then sizes the frame. A load of another origin says
// nothing, the hello going nowhere. Another direction's frame is said hello to alike, so its route shows no review
// chrome of its own, and followed no further: the page holds the pick's frames alone (frameMessage), so it shows its
// route as it loads (#directions-pick-only).
function frameLoaded(s) {
  s.held = false;
  s.sent = '';
  const load = ++s.loads;
  // #review-settle: each load is a screen tall until its route says its size: a page of another origin, which says
  // none, stays so, even after a route that left for it unsaid, its message lost with the page it left
  s.frame.style.height = screenHeight(s.viewport) + 'px';
  // #review-settle: the slot's hold, as the page loads the route again, ends once the route sizes its frame, or two
  // frames after a load that says nothing
  requestAnimationFrame(() => requestAnimationFrame(() => { if (s.loads === load) releaseSlot(s); }));
  toFrame(s, { type: 'spec-chat-host', opens: ['comments'], id: s.id, frame: s.viewport, regions: s.other ? [] : regionsOf(document) });
  if (load === 1 && !s.other) setTimeout(() => { if (!s.ever) prototypeDown(); }, PROTOTYPE_HELLO_MS); // #prototype-down
  if (s.loaded) s.loaded();
}

// #review-settle: the page keeps its place while the route loads again, the slot as tall as it was until the next load.
function holdSlot(s) {
  s.hold = s.loads + 1;
  s.slot.style.minHeight = s.slot.getBoundingClientRect().height + 'px';
}

function releaseSlot(s) {
  if (s.loads >= s.hold) s.slot.style.minHeight = '';
}

// #review-settle, shell #shell-msg-prototype: the frame is the screen's height and what the route's content runs past
// that screen, as its size says, a screen pixel more, as zoomed whole the viewport is the frame's height rounded to the
// screen's pixels, so that rounding never cuts the content; its zoom follows widths alone. The route says its size only
// as its content changes, never as it lays out anew for the frame's own height, so the frame never grows to an answer of
// its own growth.
function sizeFrame(s, past) {
  const screen = screenHeight(s.viewport);
  const pixel = 1 / ((parseFloat(s.fit.style.zoom) || 1) * window.devicePixelRatio);
  const height = (past > 0 ? Math.ceil(screen + past + pixel) : screen) + 'px';
  releaseSlot(s);
  if (s.frame.style.height === height) return;
  s.frame.style.height = height;
  renderPins();
}

// #prototype-steps: each frame the page shows to the step at `index`, from the step it shows by the next steps' actions,
// else from the route loaded again; a frame the reviewer pressed through is there already. A later step asked stops
// this. A frame the page does not show, the directions' toggle hiding it (#directions-page) or Before showing in its
// place, is drawn by no browser at another origin, so it does nothing until shown, and reaches the step showing then
// (renderViewport, showMockupBefore).
function reachStep(index, pressed) {
  const run = ++proto.reach;
  return Promise.all(mockup.slots.map(s => {
    if (s === pressed || !s.ready || mockup.stage.hidden || offView(s.slot)) return null;
    s.chain = s.chain.then(() => reachInFrame(s, index, run)).catch(() => {});
    return s.chain;
  }));
}

async function reachInFrame(s, index, run) {
  await s.ready;
  if (run !== proto.reach || !s.frame) return;
  if (!(Number.isInteger(s.at) && s.at <= index)) await reloadFrame(s);
  for (let i = s.at + 1; i <= index && s.frame; i++) {
    if (run !== proto.reach) return; // left between steps: the next asks the route from the step it shows
    if (!(await actInFrame(s, mockup.steps[i].actions, nextActions(i)))) return void (s.at = null);
    s.at = i;
  }
}

// The actions that reach the step after the one at `index`, which a reviewer pressing through does (#prototype-steps).
function nextActions(index) {
  return mockup.steps[index + 1] ? mockup.steps[index + 1].actions : [];
}

// The route loaded again by the frame's own element, wherever the frame went, laid out at its screen's height as its
// script reads it loading, never at a height the frame grew to for the last load (#prototype-steps, #review-settle).
async function reloadFrame(s) {
  s.at = -1;
  holdSlot(s);
  s.frame.style.height = screenHeight(s.viewport) + 'px';
  s.held = false;
  s.frame.src = s.frame.src;
  await untilHeld(s);
}

// Once the frame's route is held, as it publishes after the hello; false when it does not within 15 s.
function untilHeld(s) {
  if (s.held) return Promise.resolve(true);
  return new Promise(done => {
    const timer = setTimeout(() => { s.waiters = s.waiters.filter(w => w !== waiter); done(false); }, 15000);
    const waiter = held => { clearTimeout(timer); done(held !== false); };
    s.waiters.push(waiter);
  });
}

// shell #shell-msg-prototype: the route does the actions in order, each after the motion before it ends, and answers the
// number it did; `next` tells it what a reviewer pressing through does. An act goes to a held document, and again to
// each document held next until one answers it (frameMessage): a document leaving, or loaded and not yet held, does
// none, and a route that begins to leave as an action loads a page answers what it did, the rest going to the document
// held next, or to it again should it stay. False when no route answers.
async function actInFrame(s, actions, next) {
  let left = actions;
  for (;;) {
    const done = await new Promise(answer => {
      const timer = setTimeout(() => answer(-1), 30000);
      s.act = { message: { type: 'spec-chat-act', actions: left, next }, answer: n => { clearTimeout(timer); answer(n); } };
      if (s.held) toFrame(s, s.act.message);
    });
    s.act = null;
    if (done < 0) return false;
    if (done >= left.length) return true;
    left = left.slice(done);
  }
}

// #prototype-steps, #walkthrough-press: outside comment mode a reviewer presses through the prototype as through the
// product: the route says when a press or typing did the actions the next step names, in either frame, and the step bar
// moves to it as the other frame follows; any other action leaves the step bar on no step until the reviewer picks one.
function pressedThrough(s, reached) {
  if (state.commentMode || mockup.before) return;
  if (reached && Number.isInteger(s.at) && mockup.steps[s.at + 1]) {
    const at = ++s.at;
    s.chain = s.chain.then(() => actInFrame(s, [], nextActions(at))).catch(() => {}); // what reaches the step after
    stopPlay();
    return showStep(at, { pressed: s });
  }
  s.at = null;
  stopPlay();
  if (mockup.step >= 0) showStep(-1);
}

// shell #shell-msg-point: each held frame points while comment mode is on, and stops when it turns off.
function pointFrames(on) {
  for (const s of mockup.slots) if (s.held) toFrame(s, on ? { type: 'spec-chat-point' } : { type: 'spec-chat-point', off: true });
}

// A held frame's messages, only from the lane's prototype origin: its publish, once held, its size, its answer to an
// act, a reviewer's press through it, and its picks (shell #shell-msg-publish, #shell-msg-prototype, #shell-msg-pick).
// A message carrying an id is taken as from the frame that id names, never by its source window: one the route sends
// as its document leaves, as a press whose link loads the next step's page, can reach the page with no source window
// (#shell-msg-prototype). One with no id, as an older route's, is taken as from its source window
// (#shell-fields-tolerant).
// Another direction's frame is held and sized alike, and no more: no step, press, pick, or mark of it is followed
// (#directions-pick-only).
function frameMessage(e) {
  const data = e.data;
  if (!proto.base || e.origin !== proto.base.origin || !plainObject(data)) return;
  const byId = typeof data.id === 'string';
  const s = allSlots().find(slot => slot.frame && (byId ? slot.id === data.id : slot.frame.contentWindow === e.source));
  if (!s) return;
  if (data.type === 'spec-chat-size') return void (Number.isFinite(data.past) && sizeFrame(s, data.past));
  if (s.other) return void (data.type === 'spec-chat-publish' && (s.held = s.ever = true));
  if (data.type === 'spec-chat-publish') return frameHeld(s);
  if (data.type === 'spec-chat-acted') return void (s.act && s.act.answer(Number.isInteger(data.done) && data.done > 0 ? data.done : 0));
  if (data.type === 'spec-chat-show') return void (s.shown && s.shown(data.shown === true));
  if (data.type === 'spec-chat-pressed') return void pressedThrough(s, data.reached === true);
  if (data.type === 'spec-chat-pick') framePick(data);
}

// A new document of the frame's route held, as it publishes after the hello: it is told the act no document answered
// yet, else, at a step, the next step's actions, so a reviewer pressing through a page a step's action loaded is
// followed (#prototype-steps); then pointing and the marks.
function frameHeld(s) {
  s.held = s.ever = true;
  s.sent = '';
  for (const waiter of s.waiters.splice(0)) waiter(true);
  if (s.act) toFrame(s, s.act.message);
  else if (Number.isInteger(s.at)) {
    s.chain = s.chain.then(() => (s.act || !Number.isInteger(s.at) ? null : actInFrame(s, [], nextActions(s.at)))).catch(() => {});
  }
  if (state.commentMode) pointFrames(true);
  prototypeMarks();
}

// #shell-fields-pick: a pin focuses its thread, a question mark its question, and any other spot opens the composer on
// it at the step showing (#prototype-marks), as a click in a drawn frame does.
function framePick(data) {
  const th = state.threads.get(str(data.thread, 200));
  if (th) return selectThread(th, false);
  const anchor = str(data.anchor, 400);
  if (!anchor) return;
  if (pageQuestions().some(q => q.anchor === anchor)) return focusQuestion(anchor);
  openComposer(anchor, targetOf(data.target), str(data.quote) || null);
  if (state.composer) Object.assign(state.composer, pageStep());
}

// #prototype-marks, shell #shell-msg-marks: each held frame draws the threads at the step showing, by number and
// review status, each at its region or its frame, and the open questions asked at that step on their regions; it rings
// the active thread's spot and the focused question's regions, at the chrome's size on this page's screen at its frame's
// zoom (zoom, size: a frame's own width is no screen's).
function prototypeMarks() {
  if (!PROTOTYPE_ROUTE) return;
  const threads = [];
  let n = 0;
  for (const th of state.threads.values()) {
    n++;
    if (mockup.before || !showsAtStep(th)) continue;
    const b = th.ev.body;
    threads.push({ id: th.id, number: th.number || n, status: th.status, anchor: b.anchorId, target: b.target || null,
      quote: b.quote || null, state: waitsOnReviewer(th) ? 'waiting' : th.status === 'resolved' ? 'resolved' : 'open' });
  }
  const questions = mockup.before ? [] : pageQuestions().filter(askedNow).map(q => ({ anchor: q.anchor, about: q.about, text: q.text }));
  const active = threads.some(t => t.id === state.activeThread) ? state.activeThread : null;
  const asked = questions.some(q => q.anchor === state.focusQuestion) ? state.focusQuestion : null;
  for (const s of mockup.slots) {
    if (!s.held || !s.frame) continue;
    const zoom = parseFloat(s.fit.style.getPropertyValue('--hx-zoom')) || 1;
    const message = { type: 'spec-chat-marks', threads, answers: [], questions, active, asked, zoom, size: NARROW.matches ? 44 : 24 };
    const text = JSON.stringify(message);
    if (s.sent === text) continue;
    s.sent = text;
    toFrame(s, message);
  }
}

// A spot into view (shell #shell-msg-show), once the frames shown reach the step showing: each held frame in turn is
// asked, the one the directions' toggle shows before the one it hides, until one shows it, bringing its element into
// view, its own frame scrolling the page that holds it, and answering. A frame the toggle hides holds no step until
// shown, so the toggle switches to it, and it reaches the step showing, before it is asked; when no frame shows the
// spot, the toggle goes back (endArrival). One arrival at a time: a later one, here or at any spot (inView), ends it at
// its next wait, its switch put back first; the reviewer's press of the toggle ends it as the press leaves it.
async function showInFrames(anchor, target) {
  endArrival();
  const arrival = mockup.arrival = { view: mockup.view }, ours = () => mockup.arrival === arrival;
  await mockup.reaching;
  const ask = s => new Promise(answer => {
    const timer = setTimeout(() => answer(false), 2000);
    s.shown = yes => { clearTimeout(timer); s.shown = null; answer(yes); };
    toFrame(s, { type: 'spec-chat-show', anchor, target: target || null });
  });
  for (const s of [...mockup.slots].sort((a, b) => offView(a.frame) - offView(b.frame))) {
    if (!ours()) return;
    if (!s.held || !s.frame) continue;
    if (offView(s.frame)) {
      mockup.view = s.viewport;
      renderViewport();
      await mockup.reaching; // shown, it reaches the step showing (renderViewport)
      if (!ours()) return;
    }
    if (await ask(s)) return void (ours() && (mockup.arrival = null)); // the toggle stays where the spot shows
  }
  if (ours()) endArrival();
}

// The arrival in the frames ended (showInFrames), the toggle back at the viewport it found.
function endArrival() {
  const arrival = mockup.arrival;
  mockup.arrival = null;
  if (arrival && mockup.view !== arrival.view) {
    mockup.view = arrival.view;
    renderViewport();
  }
}

// #prototype-regions: the regions a prototype mockup's file lists, each its anchor and the selector finding it.
function regionsOf(doc) {
  return [...doc.querySelectorAll('[data-mockup-regions] [data-anchor][data-select]')]
    .map(item => ({ anchor: item.dataset.anchor, select: item.dataset.select }));
}

// A region on the route as it now runs: what its selector finds.
function regionIn(doc, regions, anchor) {
  const region = regions.find(r => r.anchor === anchor);
  try { return region ? doc.querySelector(region.select) : null; } catch (_) { return null; }
}

// #round-video-store: the round videos the lane's recorder stored in the mockup's spool, newest first, read once.
function readRounds() {
  proto.roundsRead = proto.roundsRead || fetch(apiPath('rounds?' + new URLSearchParams({ path: OWN_ADDRESS.pathname })))
    .then(answer => answer.ok ? answer.json() : null)
    .then(answer => answer && Array.isArray(answer.rounds) ? answer.rounds.filter(roundEntry) : [])
    .catch(() => [])
    .then(rounds => { proto.rounds = rounds; renderRounds(); return rounds; });
  return proto.roundsRead;
}

function roundEntry(round) {
  const files = viewport => !round[viewport] || (plainObject(round[viewport]) && str(round[viewport].video, 200));
  return plainObject(round) && /^[0-9a-f]{7,40}$/.test(str(round.commit, 40)) && (round.desktop || round.phone)
    && files('desktop') && files('phone');
}

function roundFile(name) {
  return apiPath('rounds?' + new URLSearchParams({ path: OWN_ADDRESS.pathname, file: name }));
}

// #round-video-show: the newest round's video under the frames, on a phone its phone video when there is one, with its
// step captions and its round's commit; each older round's under Earlier rounds, newest first.
function renderRounds() {
  document.getElementById('hx-rounds')?.remove();
  if (!proto.rounds.length || !mockup.stage) return;
  const section = document.createElement('section');
  section.className = 'hx-rounds';
  section.id = 'hx-rounds';
  section.setAttribute('aria-label', 'Round videos');
  const [newest, ...older] = proto.rounds;
  section.appendChild(roundBlock(newest, 'This round’s video', older.length));
  if (older.length) {
    const list = section.appendChild(document.createElement('div'));
    list.className = 'hx-rounds-earlier';
    list.id = 'hx-rounds-earlier';
    list.hidden = !proto.earlier;
    older.forEach(round => list.appendChild(roundBlock(round, 'Round ' + round.commit.slice(0, 7))));
  }
  mockup.stage.after(section);
  renderVideos();
}

// One round's video, a real <video> with its step captions, and what it is: its title, its commit, its viewport.
function roundBlock(round, title, earlier = 0) {
  const viewport = (NARROW.matches && round.phone) || !round.desktop ? 'phone' : 'desktop';
  const files = round[viewport];
  const block = document.createElement('div');
  block.className = 'hx-round';
  block.dataset.commit = round.commit;
  block.dataset.viewport = viewport;
  block.dataset.anchor = 'round-' + round.commit + '-' + viewport; // mockups #round-video-comment
  const player = block.appendChild(document.createElement('div')); // the video, and its rail under it (threads #video-rail)
  player.className = 'hx-round-player';
  const video = player.appendChild(document.createElement('video'));
  video.className = 'hx-round-video';
  Object.assign(video, { controls: true, playsInline: true, preload: 'metadata', src: roundFile(files.video) });
  video.setAttribute('aria-label', title);
  if (files.captions) {
    const track = video.appendChild(document.createElement('track'));
    Object.assign(track, { kind: 'captions', label: 'Steps', srclang: document.documentElement.lang || 'en', default: true, src: roundFile(files.captions) });
    // Chromium shows no cue until a video first plays or seeks, so loaded it seeks where it stands: it shows that
    // moment's caption, its first step's, before any play.
    video.addEventListener('loadedmetadata', () => { video.currentTime = video.currentTime; }, { once: true });
  }
  const meta = block.appendChild(document.createElement('div'));
  meta.className = 'hx-round-meta';
  meta.innerHTML = '<span class="hx-round-title"></span><span>Recorded from <code></code>, ' + viewport + '</span>'
    + (files.captions ? '<span class="hx-round-note">Captions from the walkthrough’s steps</span>' : '');
  meta.querySelector('.hx-round-title').textContent = title;
  meta.querySelector('code').textContent = round.commit.slice(0, 7);
  meta.querySelector('code').title = round.commit;
  if (earlier) {
    const button = meta.appendChild(document.createElement('button'));
    button.type = 'button';
    button.className = 'hx-btn hx-round-earlier';
    button.textContent = 'Earlier rounds (' + earlier + ')';
    button.setAttribute('aria-expanded', String(proto.earlier));
    button.setAttribute('aria-controls', 'hx-rounds-earlier');
    button.addEventListener('click', () => {
      proto.earlier = !proto.earlier;
      button.setAttribute('aria-expanded', String(proto.earlier));
      document.getElementById('hx-rounds-earlier').hidden = !proto.earlier;
    });
  }
  return block;
}

// #before-prototype: Before plays the round recorded at the compared base in the frames' place; After returns them.
function showRoundBefore(before) {
  document.getElementById('hx-round-before')?.remove();
  mockup.stage.hidden = before;
  if (!before) return;
  const block = roundBlock(mockup.base, 'The round at the base');
  delete block.dataset.anchor; // Before takes no comment (#before-changed)
  block.id = 'hx-round-before';
  block.classList.add('hx-round-before');
  mockup.stage.before(block);
}

/* ---------------- a prototype route its mockup page holds (mockups #prototype-route) ----------------
 * A page at a lane's prototype origin runs this runtime as the comment layer the service adds to each prototype page, or
 * as its own, Spec Chat's own pages run as a lane's prototype (review-service #prototype-proxy). Under its mockup page,
 * at the review service's own origin, it is held (shell #shell-setting-own) once that page's hello names its frame and
 * its regions, and speaks with it only by the shell messages (#shell-msg-prototype): every element in it is a comment
 * target, its regions found by the selectors its mockup file lists, a pointed click a pick naming its region, or its
 * frame, and the target and quote in it; it draws the threads and questions its page's marks name in its own content, so
 * they move with it; it says what its content runs past its screen, does the actions its page asks, and says when a
 * reviewer pressing through it does the next step's.
 */
// The prototype origin the page runs at: the one the layer's tag names, whatever <base> the page sets, else its own
// address's (served-pages #served-own-address).
function prototypeOrigin() {
  return (PROTOTYPE_BASE || OWN_ADDRESS).origin;
}

// #shell-setting-own: a mockup page's hello, naming regions, from the review service whose lane's prototype origin this
// page runs at: that origin is the service's own with <slug>-prototype. before its host name, at its scheme and port, and
// for a service private on loopback, under localhost. Which slugs name a lane is the service's to say, as it serves
// the origin only for one.
function ownPrototype(origin, data) {
  if (!Array.isArray(data.regions)) return false;
  let at, parent;
  try { at = new URL(prototypeOrigin()); parent = new URL(origin); } catch (_) { return false; }
  const [label, ...rest] = at.hostname.split('.');
  const host = rest.join('.');
  if (!label.endsWith('-prototype') || !host || parent.protocol !== at.protocol || parent.port !== at.port) return false;
  return parent.hostname === host || (host === 'localhost' && /^(localhost|127(\.\d{1,3}){3}|\[::1\])$/.test(parent.hostname));
}

// The hello's id, its frame, the viewport it shows, and its regions, each {anchor, select} (#shell-fields-tolerant).
function holdPrototype(data) {
  protoHeld.on = true;
  protoHeld.id = str(data.id, 40);
  protoHeld.frame = str(data.frame, 40) || 'desktop';
  protoHeld.regions = data.regions.filter(r => plainObject(r) && str(r.anchor, 400) && str(r.select, 400))
    .map(r => ({ anchor: r.anchor, select: r.select }));
}

function prototypeMessage(data) {
  if (data.type === 'spec-chat-point') return pointPrototype(data.off !== true);
  if (data.type === 'spec-chat-marks') {
    protoHeld.marks = data;
    return placePrototypeMarks();
  }
  if (data.type === 'spec-chat-act') return actPrototype(data);
  if (data.type === 'spec-chat-show') return showInPrototype(data);
}

// #review-settle, shell #shell-msg-prototype: on hello, and whenever its content's height changes, what its content runs
// past the screen its frame shows, so its page sizes the frame to it. Laid out anew for the frame's own height, as by a
// root as tall as its viewport, or its script sizing it from its viewport, the content is read as it answered and stays
// as it is, so the frame never grows to an answer of its own growth (readPrototype).
function followPrototype() {
  protoHeld.view = innerHeight;
  protoHeld.past = null;
  sayPrototypeSize();
  if (protoHeld.sizes) return;
  protoHeld.sizes = new ResizeObserver(laidOut);
  for (const el of [document.documentElement, document.body]) if (el) protoHeld.sizes.observe(el);
  // Content that changes inside a root or body as tall as its viewport changes no size observed above: any change the
  // route makes in its page is read too, its own marks aside.
  new MutationObserver(records => { if (records.some(r => !layerOwns(r.target))) laidOut(); })
    .observe(document.documentElement, { childList: true, subtree: true, attributes: true, characterData: true });
  window.addEventListener('resize', laidOut);
  if (window.visualViewport) window.visualViewport.addEventListener('resize', laidOut);
  if (!LAYER_SERVICE) document.addEventListener('scroll', placePrototypeMarks, true); // the layer places its marks itself
  window.addEventListener('beforeunload', leavingPrototype);
  window.addEventListener('pagehide', leavePrototype);
  window.addEventListener('pageshow', e => { if (e.persisted) stayPrototype(); }); // shown again from the browser's cache
}

function layerOwns(node) {
  const el = node && (node.nodeType === 1 ? node : node.parentElement);
  return Boolean(el && el.closest('[data-spec-chat]'));
}

// A size read in the next frame, never in the observation itself: growing the frame resizes what was observed.
function laidOut() {
  if (protoHeld.telling) return;
  cancelAnimationFrame(protoHeld.reading);
  protoHeld.reading = requestAnimationFrame(readPrototype);
}

// The frame's own height is no change of its content: its script sizing it from its viewport, as from innerHeight or a
// --vh property, is told the height at once, as the browser tells it in a later frame, and the content read as it answered
// stays as it is. Any other change of its content's height is said.
function readPrototype() {
  if (innerHeight === protoHeld.view) sayPrototypeSize();
  else {
    protoHeld.view = innerHeight;
    protoHeld.telling = true;
    for (const target of [window, window.visualViewport]) if (target) target.dispatchEvent(new Event('resize'));
    protoHeld.telling = false;
    protoHeld.past = prototypePast();
  }
  if (!LAYER_SERVICE) placePrototypeMarks();
}

function sayPrototypeSize() {
  const past = prototypePast();
  if (past === protoHeld.past) return;
  protoHeld.past = past;
  toShell({ type: 'spec-chat-size', past });
}

// What the content runs past the screen its frame shows, a scrollbar across the frame counted: read from what scrolls the
// content, its root, or in quirks mode, a route with no doctype, its body; with nothing past its viewport, from the root's
// own height, the root and body read as tall as their content, never stretched to a frame grown for it, by a height
// of 100% or in quirks mode, so content shorter than that frame shrinks it, as far as the screen (mockups
// #frame-viewports); a minimum height they set stays, as content laid out against its viewport is cut by its excess,
// never chased (readPrototype). The read sees them so for that moment alone: nothing is drawn so, and no size the route
// observes changes.
const OWN_SHEETS = typeof CSSStyleSheet === 'function' && 'adoptedStyleSheets' in Document.prototype; // constructed sheets
const NATURAL = OWN_SHEETS ? new CSSStyleSheet() : null;
if (NATURAL) NATURAL.replaceSync('html,body{height:fit-content!important}');
function prototypePast() {
  const root = document.scrollingElement || document.documentElement;
  if (NATURAL && !document.adoptedStyleSheets.includes(NATURAL)) document.adoptedStyleSheets = [...document.adoptedStyleSheets, NATURAL];
  if (NATURAL) NATURAL.disabled = false;
  let content = root.scrollHeight;
  if (content <= root.clientHeight) content = Math.min(content, Math.ceil(document.documentElement.getBoundingClientRect().height));
  const bar = innerHeight - root.clientHeight;
  if (NATURAL) NATURAL.disabled = true;
  return content + bar - screenHeight(protoHeld.frame);
}

// #prototype-steps: a load starts as the route begins to leave, and the route does no act while it does: an act it is
// doing answers what it did, the action that loads the page counted, while the page that holds it still knows it as its
// frame's, and the next document does the rest. A load that ends with no new page, as a download, leaves it here, so
// after longer than the service waits on a prototype's answer it does acts again.
function leavingPrototype() {
  protoHeld.leaving = true;
  answerLeaving();
  clearTimeout(protoHeld.staying);
  protoHeld.staying = setTimeout(stayPrototype, 15000);
}

function stayPrototype() {
  clearTimeout(protoHeld.staying);
  protoHeld.leaving = false;
  protoHeld.stayed();
}

function stillHere() {
  return protoHeld.leaving ? new Promise(done => { protoHeld.stayed = done; }) : Promise.resolve();
}

function answerLeaving() {
  if (protoHeld.acting) toShell({ type: 'spec-chat-acted', done: protoHeld.acting.done });
  protoHeld.acting = null;
}

// As the route leaves, an act it was doing answers what it did, and its content goes with it, so the page frames the next
// load at the screen's height.
function leavePrototype() {
  protoHeld.leaving = true;
  answerLeaving();
  toShell({ type: 'spec-chat-size', past: 0 });
}

// #prototype-steps, shell #shell-msg-prototype: each action in order, after the motion before it ends, then the number
// done; what a reviewer pressing through did of them already, as the next step's actions it was told, is done.
function actPrototype(data) {
  const actions = (Array.isArray(data.actions) ? data.actions : []).map(stepAction);
  const next = (Array.isArray(data.next) ? data.next : []).map(stepAction).filter(Boolean);
  protoHeld.acts = protoHeld.acts.then(() => doActions(actions, next)).catch(() => {});
}

function stepAction(action) {
  if (!plainObject(action)) return null;
  if (str(action.press, 400)) return { press: action.press };
  return str(action.type, 400) ? { type: action.type, text: typeof action.text === 'string' ? action.text : '' } : null;
}

async function doActions(actions, next) {
  await stillHere(); // a document leaving does no act: the document held next is asked it again
  const pressed = protoHeld.pressing;
  let done = 0;
  while (done < pressed.done && done < actions.length && JSON.stringify(actions[done]) === JSON.stringify(pressed.next[done])) done++;
  const acting = protoHeld.acting = { done };
  for (let i = done; i < actions.length; i++) {
    acting.done = i + 1; // an action that loads a page is done as the route leaves, which answers it
    doAction(actions[i]);
    await motionSettles();
    if (protoHeld.acting !== acting) return; // answered as it began to leave
  }
  protoHeld.acting = null;
  protoHeld.pressing = { next, done: 0 };
  toShell({ type: 'spec-chat-acted', done: actions.length });
}

// One action, as a step's list names it (#markup-actions): press what a selector finds, or type text into it.
function doAction(action) {
  let el = null;
  try { el = action && document.querySelector(action.press || action.type); } catch (_) { /* no such selector */ }
  if (el && action.press) el.click();
  else if (el) typeInto(el, action.text);
}

function typeInto(el, text) {
  el.focus();
  if (el.isContentEditable) el.textContent = text;
  else {
    // the element's own value setter, which a product's framework follows
    const own = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value');
    if (own && own.set) own.set.call(el, text); else el.value = text;
  }
  el.dispatchEvent(new InputEvent('input', { bubbles: true, data: text, inputType: 'insertText' }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
}

// The motion an action starts, once it ends: each transition and animation that ends, at most 5 s; a page it loads, once
// it does, the route leaving answering then, or once the route stays. A frame no browser draws, hidden or scrolled wholly
// out of view, gets no animation frame and moves no motion on, so the clock stands in for both: for the frame drawn, and
// for each motion's finish at its own end.
async function motionSettles() {
  await new Promise(done => setTimeout(done, 50));
  await stillHere();
  await new Promise(done => { setTimeout(done, 100); requestAnimationFrame(() => requestAnimationFrame(done)); });
  const moving = (document.getAnimations ? document.getAnimations() : [])
    .filter(animation => animation.effect && Number.isFinite(animation.effect.getComputedTiming().endTime));
  const ends = Math.min(5000, Math.max(0, ...moving.map(motionLeft)) + 100);
  await Promise.race([Promise.all(moving.map(animation => animation.finished.catch(() => {}))), new Promise(done => setTimeout(done, ends))]);
}

// The milliseconds until an animation ends at its own rate: played forward at its end time, backward at its start; one
// at no rate, never. A product's reverse() keeps its new rate pending until the animation is ready, which in a frame no
// browser draws it never is, and no API reads a pending rate: such a motion is timed forward, at most the 5 s cap.
function motionLeft(animation) {
  const timing = animation.effect.getComputedTiming();
  const rate = animation.playbackRate, at = timing.localTime || 0;
  return rate > 0 ? (timing.endTime - at) / rate : rate < 0 ? at / -rate : Infinity;
}

// #prototype-steps, #walkthrough-press: with pointing off, a reviewer's press or typing that does the next step's
// actions says it reached that step; any other action says it did not. A click on nothing that acts is none; typing into
// the field the next action types in goes on toward its text.
const PROTOTYPE_ACTS = 'a[href],button,input,select,textarea,label,summary,[role=button],[role=link],[role=tab],[role=menuitem],[role=checkbox],[role=radio],[role=switch],[contenteditable]';
for (const type of ['click', 'input']) document.addEventListener(type, e => {
  const t = e.target;
  if (!protoHeld.on || protoHeld.pointing || !e.isTrusted || !(t instanceof Element) || t.closest('[data-spec-chat]')) return;
  const pressing = protoHeld.pressing;
  const action = pressing.next[pressing.done];
  const hits = selector => { try { return Boolean(selector && t.closest(selector)); } catch (_) { return false; } };
  if (action && action.press && type === 'click' && hits(action.press)) return pressedOn();
  if (action && action.type && hits(action.type)) {
    if (type === 'input' && (t.isContentEditable ? t.textContent : t.value) === action.text) pressedOn();
    return;
  }
  if (type === 'click' && !hits(PROTOTYPE_ACTS)) return;
  protoHeld.pressing = { next: [], done: 0 };
  toShell({ type: 'spec-chat-pressed', reached: false });
}, true);

function pressedOn() {
  const pressing = protoHeld.pressing;
  if (++pressing.done < pressing.next.length) return;
  protoHeld.pressing = { next: [], done: 0 };
  toShell({ type: 'spec-chat-pressed', reached: true });
}

// shell #shell-msg-show: a spot into view, its element, else its region, where the route shows it, answering whether it
// did, so its page asks the next frame when it does not.
function showInPrototype(data) {
  const holder = prototypeHolder(str(data.anchor, 400));
  const target = holder && targetOf(data.target);
  const el = target && target.type === 'element' ? resolveElement(holder, target.key) || holder : holder;
  const shown = Boolean(el && el.getClientRects().length);
  if (shown) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
  toShell({ type: 'spec-chat-show', shown });
}

function pointPrototype(on) {
  protoHeld.pointing = on;
  if (!on && layer.root) layer.root.querySelector('.hx-point-ring')?.remove();
}

// The region a point marks: the innermost listed region holding it, else its frame (#prototype-regions).
function regionOf(el) {
  let best = { anchor: protoHeld.frame, el: document.body };
  for (const region of protoHeld.regions) {
    const at = regionIn(document, protoHeld.regions, region.anchor);
    if (at && at.contains(el) && (best.el === document.body || best.el.contains(at))) best = { anchor: region.anchor, el: at };
  }
  return best;
}

function prototypeHolder(anchor) {
  return anchor === protoHeld.frame ? document.body : regionIn(document, protoHeld.regions, anchor);
}

function pickInPrototype(el) {
  const region = regionOf(el);
  const selection = window.getSelection && window.getSelection();
  const text = selection && !selection.isCollapsed ? String(selection).trim() : '';
  let target = null, quote = null;
  if (text && region.el.contains(selection.anchorNode)) {
    target = { type: 'text', key: text.slice(0, 40) };
    quote = text;
    selection.removeAllRanges();
  } else {
    const desc = elementDescriptor(el, region.el);
    if (desc) { target = { type: 'element', key: desc.key }; quote = desc.quote || null; }
  }
  pointPrototype(false);
  pickSpot({ anchor: region.anchor, target, quote });
}

document.addEventListener('pointermove', e => {
  if (!protoHeld.pointing || !(e.target instanceof Element) || e.target.closest('[data-spec-chat]')) return;
  const root = layerOverlay();
  const ring = root.querySelector('.hx-point-ring') || root.appendChild(Object.assign(document.createElement('div'), { className: 'hx-thread-ring hx-point-ring' }));
  placeRing(ring, markedElement(e.target, regionOf(e.target).el).getBoundingClientRect(), (protoHeld.marks && protoHeld.marks.zoom) || 1);
}, true);
for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) document.addEventListener(type, e => {
  if (!protoHeld.pointing || !(e.target instanceof Element) || e.target.closest('[data-spec-chat]')) return;
  e.preventDefault(); // pointing suspends the route: what the reviewer clicks is marked, never pressed
  e.stopImmediatePropagation();
  if (type === 'click') pickInPrototype(e.target);
}, true);
document.addEventListener('keydown', e => { if (protoHeld.pointing && e.key === 'Escape') pointPrototype(false); }, true);

function placeRing(ring, rect, zoom) {
  Object.assign(ring.style, { left: (rect.left - 4) + 'px', top: (rect.top - 4) + 'px', width: (Math.max(rect.width, 8) + 8) + 'px',
    height: (Math.max(rect.height, 8) + 8) + 'px', borderWidth: (2 / zoom) + 'px' });
}

// The marks its page sent, placed where the route shows their spots now: a pin per thread, a question mark per region
// an open question is about, and the ring.
function placePrototypeMarks() {
  const root = layerOverlay();
  root.querySelectorAll('.hx-proto-mark').forEach(mark => mark.remove());
  const data = protoHeld.marks;
  if (!data) return;
  const zoom = Number.isFinite(data.zoom) && data.zoom > 0 ? data.zoom : 1;
  const add = (el, className) => { el.className = className + ' hx-proto-mark'; return root.appendChild(el); };
  const spotRect = t => {
    const holder = prototypeHolder(str(t.anchor, 400));
    const target = holder && targetOf(t.target);
    const el = target && target.type === 'element' ? resolveElement(holder, target.key) || holder : holder;
    const rect = (target && target.type === 'text' && textTargetRect(holder, target.key || str(t.quote))) || (el && el.getBoundingClientRect());
    return rect && (rect.width || rect.height) ? rect : null;
  };
  const size = data.size === 44 ? 44 : 24; // the page's screen's, never this frame's phone width (#review-fit)
  const pinSize = size / zoom;
  for (const t of (Array.isArray(data.threads) ? data.threads : []).filter(t => plainObject(t) && str(t.id, 200))) {
    const rect = spotRect(t);
    if (!rect) continue;
    const pin = add(document.createElement('button'), 'hx-pin' + (data.active === t.id ? ' active' : ''));
    pin.type = 'button';
    pin.dataset.s = MARK_STATUSES.includes(t.status) ? t.status : 'pending';
    pin.dataset.thread = t.id;
    const number = pin.appendChild(document.createElement('span'));
    number.className = 'hx-pin-number';
    number.textContent = String(Number.isInteger(t.number) && t.number > 0 ? t.number : '');
    if (t.state === 'waiting') { pin.dataset.waiting = ''; pin.appendChild(waitingDot()); }
    const at = targetOf(t.target);
    pin.setAttribute('aria-label', 'Thread ' + number.textContent + (at ? ': ' + (str(t.quote) || at.key) : ''));
    const left = Math.max(0, Math.min(rect.right - pinSize / 2, window.innerWidth - pinSize));
    Object.assign(pin.style, { zoom: String(1 / zoom), left: (left * zoom) + 'px', top: (Math.max(0, rect.top - pinSize / 4) * zoom) + 'px' });
    for (const name of ['width', 'height']) pin.style.setProperty(name, size + 'px', 'important'); // over the chrome's own
    pin.addEventListener('click', e => { e.stopPropagation(); pickSpot({ thread: t.id, anchor: str(t.anchor, 400), target: at, quote: str(t.quote) || null }); });
    if (data.active === t.id) placeRing(add(document.createElement('div'), 'hx-thread-ring'), rect, zoom);
  }
  for (const q of (Array.isArray(data.questions) ? data.questions : []).filter(q => plainObject(q) && str(q.anchor, 400))) {
    for (const about of strList(q.about)) {
      const rect = spotRect({ anchor: about });
      if (!rect) continue;
      const box = add(document.createElement('div'), 'hx-proto-box');
      Object.assign(box.style, { left: rect.left + 'px', top: rect.top + 'px', width: rect.width + 'px', height: rect.height + 'px' });
      const mark = box.appendChild(document.createElement('button'));
      mark.type = 'button';
      mark.className = 'hx-qmark';
      mark.textContent = '?';
      mark.title = str(q.text);
      mark.setAttribute('aria-label', 'Question: ' + str(q.text));
      mark.style.zoom = String(1 / zoom);
      for (const [name, value] of [['width', size], ['height', size], ['top', -size / 2], ['right', -size / 2]]) mark.style.setProperty(name, value + 'px', 'important');
      mark.addEventListener('click', e => { e.stopPropagation(); pickSpot({ anchor: q.anchor }); });
      if (data.asked === q.anchor) placeRing(add(document.createElement('div'), 'hx-thread-ring'), rect, zoom);
    }
  }
}

/* ---------------- pages a shell holds (shell.spec.html#shell) ----------------
 * One script, two roles (#shell-roles): a spec page, a mockup page, or a host page loading the layer is alone, drawing
 * its own comment UI, until its parent says hello with comments from an origin the box names as a shell; held, it draws
 * no comment UI, publishes what it shows, draws the pins the shell's marks name in its own content, and turns a pointed
 * click, a pin, or an answer button into a pick. The comment UI page (COMMENT_UI) draws the conversation of the page
 * the shell relayed last, and alone reads and writes its threads (#shell-ui-page). Every message is plain data with the
 * fields #shell-fields names (#shell-protocol-plain): each is checked as text, never drawn as markup or loaded as an
 * address, and a field a receiver does not know is ignored (#shell-fields-tolerant).
 */
function str(value, max = 4000) { return typeof value === 'string' && value.length <= max ? value : ''; }
function strList(value, max = 400) { return Array.isArray(value) ? value.map(v => str(v, max)).filter(Boolean) : []; }
// A spot's target, a part of its block: a type and a key, and a chart datum's indexes and chart.
function targetOf(value) {
  if (!plainObject(value) || !str(value.type, 40) || typeof value.key !== 'string') return null;
  const target = { type: value.type, key: str(value.key, 400) };
  for (const k of ['seriesIndex', 'dataIndex']) if (Number.isInteger(value[k])) target[k] = value[k];
  if (str(value.chartKey, 400)) target.chartKey = value.chartKey;
  return target;
}
// #shell-fields-spot: a host page's thing's subject, else an anchor and a target; the quote; the step, moment, and box.
function spotOf(data) {
  const spot = str(data.subject) ? { subject: data.subject } : { anchor: str(data.anchor, 400), target: targetOf(data.target) };
  return Object.assign(spot, { quote: str(data.quote) || null }, spotFields(data));
}

// #shell-setting-rule: the origins the box names as a shell, which the review service names on its comment UI page's
// answer; none while it does not answer, and asked again at the next hello or message, so a page loaded while its
// service was down is held once it is up.
let shellsAsked = null;
function namedShells() {
  if (shellsAsked) return shellsAsked;
  const at = new URL('comments', SCRIPT_SERVICE || new URL('/', OWN_ADDRESS));
  const asked = fetch(at.href, { method: 'HEAD' })
    .then(r => { if (!r.ok) throw new Error('no answer'); return (r.headers.get('X-Spec-Chat-Shells') || '').split(/\s+/).filter(Boolean); })
    .catch(() => { if (shellsAsked === asked) shellsAsked = null; return []; });
  shellsAsked = asked;
  return asked;
}

// #shell-setting-obey: only the parent at a named origin holds the page; under any other it stays alone. A prototype
// route its mockup page holds publishes its address, then its size (#shell-msg-prototype).
async function shellHello(origin, data) {
  const own = ownPrototype(origin, data);
  if (!own && !(await namedShells()).includes(origin)) return;
  if (own) holdPrototype(data);
  hostBridge.comments = origin;
  if (!shell.held) becomeHeld();
  shell.published = shell.answered = ''; // every hello is a new load of the shell's: publish again
  if (own) return void (publishPage(), followPrototype());
  if (LAYER_SERVICE) return publishLayer();
  await state.versionAsked;
  publishPage();
}

// A held prototype route sends back on each message the id its hello carried, so its page knows the frame even with no
// source window (#shell-msg-prototype); a hello with none, as an older page's, gets none (#shell-fields-tolerant).
function toShell(message) {
  if (hostBridge.comments) window.parent.postMessage(protoHeld.id ? { ...message, id: protoHeld.id } : message, hostBridge.comments);
}
function pickSpot(spot) { toShell(Object.assign({ type: 'spec-chat-pick' }, spot)); }

// review-state #offline-leave: text typed and not yet saved, in the comment box, the focused thread's reply box, or its
// Reply and reopen box, saved as drafts, each into the outbox in browser storage at once, so nothing waits on the network
// and a page opened later sends it first (#offline-outbox, #offline-reopen); an Edit box left as it opened saves nothing.
// The one save as the page is left, by any way out (pagehide: a link, Previous or Next, an arrow key, Back, closing the
// tab), and as it is held (shell #shell-roles-held, #shell-roles-late). The boxes are emptied first, so a page
// shown again from the browser's cache shows the drafts and no box holding their text. Each boot listens once its page
// is mounted, a spec or mockup page after its kept place's own listener: a pagehide listener added before that one has
// Chromium's own scroll on a reload overtake the kept place (reading-place #acceptance-place-restore).
function saveTyped() {
  const c = state.composer;
  const th = state.focusThread && state.threads.get(state.focusThread);
  const text = c && (c.text || '').trim();
  const reply = th && replyVerb(th) && focusView.text.trim();
  state.composer = null;
  focusView.text = '';
  if (reply) focusView.composer = '';
  if (text && !(c.kind === 'edit' && text === c.opened.trim())) saveMessage(c, text);
  if (reply) saveReply(th, reply);
}

// #shell-roles-held, #shell-roles-late: text typed in its composer saved as a draft, then its comment UI removed.
function becomeHeld() {
  shell.held = true; // before the saves: a held page reads no thread, so its outbox only sends them
  saveTyped();
  state.focusThread = state.focusQuestion = state.activeThread = null;
  state.threads = new Map();
  state.eventsRendered = false;
  if (LAYER_SERVICE) return void (layer.open = null, renderLayer(true));
  setCommentMode(false);
  closeFocusView();
  for (const el of document.querySelectorAll('.hx-panel, .hx-thread-dock, .hx-toolbar, #hx-toast')) el.remove();
  document.body.classList.remove('hx-panel-open', 'hx-focused-sheet');
  document.body.classList.add('hx-held');
  state.panelOpen = false;
  renderPins();
  renderChoices();
  renderThreadHighlight();
}

function publish(message) {
  const text = JSON.stringify(message);
  if (!shell.held || text === shell.published) return;
  shell.published = text;
  toShell(message);
}

// #shell-fields-publish: a spec or mockup page's own address, the version it shows, and its open TBD questions, each
// with its answer buttons' words and the parts it is about; a mockup page's steps and the one showing.
function publishPage() {
  if (protoHeld.on) return publish({ type: 'spec-chat-publish', page: OWN_ADDRESS.href }); // its page reads no more
  const questions = pageQuestions().map(q => ({ anchor: q.anchor, text: q.text,
    answers: q.buttons.map(button => button.dataset.specChoice.trim()), about: q.about, step: q.step }));
  const message = { type: 'spec-chat-publish', page: OWN_ADDRESS.href, version: state.version, questions };
  if (MOCKUP_SPEC) {
    message.steps = mockup.steps.map(step => ({ name: step.name, caption: step.caption }));
    message.step = !mockup.before && showingStep() ? showingStep().name : null;
  }
  publish(message);
}

// A host page's: each anchored item as its anchor attributes give it (#anchor-table).
function publishLayer() {
  if (protoHeld.on) return publishPage();
  const items = layerItems().map(item => ({ subject: item.subject, scope: item.scope, kind: item.kind, label: item.label,
    from: item.from, starters: item.starters, lane: item.lane }));
  publish({ type: 'spec-chat-publish', page: OWN_ADDRESS.href, items });
}

function shellMessage(data) {
  if (!shell.held) return;
  if (protoHeld.on) return prototypeMessage(data);
  if (data.type === 'spec-chat-point') return LAYER_SERVICE ? pointLayer(data.off !== true) : setCommentMode(data.off !== true);
  if (data.type === 'spec-chat-show') return showShellSpot(data);
  if (data.type === 'spec-chat-marks') return takeMarks(data);
}

const MARK_STATUSES = ['draft', 'pending', 'acknowledged', 'resolved'];
// #shell-fields-marks: a thread as a held page draws it: its spot, number, review status, and whether it waits. A host
// page's thread is its item's by subject.
function markThread(t) {
  const spot = spotOf(t);
  const item = spot.subject && layerItems().find(it => it.subject === spot.subject);
  const quoted = spot.subject && spot.quote ? { type: 'text', key: spot.quote.slice(0, 40) } : null; // a thing's quote is its text's
  const body = { anchorId: spot.anchor || '', target: spot.target || quoted, quote: spot.quote };
  return { id: t.id, number: Number.isInteger(t.number) && t.number > 0 ? t.number : 0,
    status: MARK_STATUSES.includes(t.status) ? t.status : t.state === 'resolved' ? 'resolved' : 'pending',
    waiting: t.state === 'waiting', messages: [], spot: commentSpot(t), draftBy: new Set(),
    ev: { name: '', place: null, scope: item ? item.scope : '', subject: spot.subject || '', body } };
}

function takeMarks(data) {
  const list = Array.isArray(data.threads) ? data.threads.filter(t => plainObject(t) && str(t.id, 200)) : [];
  state.threads = new Map(list.map(t => [t.id, markThread(t)]));
  shell.answers = new Map((Array.isArray(data.answers) ? data.answers : []).filter(a => plainObject(a) && str(a.anchor, 400) && str(a.answer))
    .map(a => [a.anchor, a.answer]));
  if (state.activeThread && !state.threads.has(state.activeThread)) state.activeThread = null;
  state.eventsRendered = true;
  if (LAYER_SERVICE) renderLayer(true);
  else {
    renderPins();
    renderChoices();
    renderThreadHighlight();
  }
  answerMarks();
}

// #shell-fields-marks-back: the threads whose quote the page's text no longer holds.
function answerMarks() {
  const missing = [];
  for (const th of state.threads.values()) {
    const b = th.ev.body;
    if (!b.quote || !b.target || b.target.type !== 'text') continue;
    const el = LAYER_SERVICE ? (liveItems().find(it => sameItem(it, th.ev)) || {}).el : findAnchor(b.anchorId);
    if (!el || !textRange(el, b.quote)) missing.push(th.id);
  }
  const text = JSON.stringify(missing);
  if (text === shell.answered) return;
  shell.answered = text;
  toShell({ type: 'spec-chat-marks', missing });
}

// #shell-msg-show: one spot into view and ringed; a thread's at its step, as its chip does alone (threads #root-chip).
function showShellSpot(data) {
  const th = state.threads.get(str(data.thread, 200));
  const spot = spotOf(data);
  if (LAYER_SERVICE) {
    const item = liveItems().find(it => it.subject === (spot.subject || (th && th.ev.subject)));
    if (!item) return;
    layer.open = item; // no box shows while held: the open item's active thread is ringed
    state.activeThread = th ? th.id : null;
    item.el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    itemMoment(item, spot); // on a video, at its moment (threads #root-chip-moment)
    return renderLayer(true);
  }
  if (th) {
    shell.shown = null;
    state.activeThread = th.id;
    showThreadStep(th);
    renderPins();
    if (!showMoment(th)) scrollToThread(placedMark(th.ev));
    return renderThreadHighlight();
  }
  const holder = findAnchor(spot.anchor);
  if (!holder) return;
  const index = MOCKUP_SPEC && spot.step ? stepIndex(spot.step) : -1;
  if (index >= 0) { stopPlay(); if (index !== mockup.step) showStep(index); }
  state.activeThread = null;
  shell.shown = { anchorId: holder.dataset.anchor, target: spot.target, quote: spot.quote };
  renderPins();
  const video = typeof spot.moment === 'number' ? spotVideo(spot.anchor, spot.target) : null;
  if (video) seekVideo(video, spot.moment);
  scrollToSpot(video || holder);
  renderThreadHighlight();
}

// #shell-msg-point on a host page: the item under the pointer ringed; a click on it picks it, as its control does; a
// pick or Escape ends it.
function pointLayer(on) {
  shell.pointing = on;
  if (!layer.root) return;
  const ring = layer.root.querySelector('.hx-point-ring');
  if (ring && !on) ring.remove();
}
document.addEventListener('pointermove', e => {
  if (!shell.pointing || !layer.root) return;
  const item = e.target instanceof Element && liveItems().find(it => it.el.contains(e.target));
  let ring = layer.root.querySelector('.hx-point-ring');
  if (!item) return void (ring && ring.remove());
  ring = ring || layer.root.appendChild(Object.assign(document.createElement('div'), { className: 'hx-thread-ring hx-point-ring' }));
  const r = item.el.getBoundingClientRect();
  Object.assign(ring.style, { left: (r.left - 4) + 'px', top: (r.top - 4) + 'px', width: (r.width + 8) + 'px', height: (r.height + 8) + 'px' });
}, true);
document.addEventListener('click', e => {
  if (!shell.pointing) return;
  const item = e.target instanceof Element && liveItems().find(it => it.el.contains(e.target));
  if (!item) return;
  e.preventDefault();
  e.stopImmediatePropagation();
  pointLayer(false);
  openLayerComposer(item);
}, true);
document.addEventListener('keydown', e => { if (shell.pointing && e.key === 'Escape') pointLayer(false); });

// A thread's spot as marks, show, and pick carry it (#shell-fields-spot): a host page's thing's subject, else its anchor
// and target where the service places them now; its quote; its step, moment, and box.
function markSpot(th) {
  const b = placedMark(th.ev);
  const where = th.ev.subject ? { subject: th.ev.subject } : { anchor: b.anchorId, target: b.target || null };
  return Object.assign(where, { quote: b.quote || null }, spotFields(th.spot || {}));
}

/* ---------------- the comment UI page (#shell-ui-page) ---------------- */
// Declarations, not constants: boot draws the page before this block runs.
function uiHost() { return COMMENT_UI && Boolean(ui.page && ui.page.items); }
function reviewsMockup() { return Boolean(MOCKUP_SPEC) || (COMMENT_UI && ui.mockup); }
function contextAddress() { return COMMENT_UI && ui.page ? new URL(ui.page.page) : OWN_ADDRESS; }
// The page's own anchored element, as its review service serves it, on the comment UI page.
function uiAnchor(id) { return ui.doc && id ? ui.doc.querySelector('[data-anchor="' + cssId(id) + '"]') : null; }
// The name the conversation's head gives the page: a spec's title, a mockup page's, or a host page's open item.
function contextName() {
  if (uiHost()) return layer.open ? layer.open.label || layer.open.subject : '';
  const title = ui.doc && !ui.mockup && ui.doc.querySelector('article h1, h1[data-anchor="title"]');
  return title ? sourceText(title) : ui.doc ? String(ui.doc.title || '') : '';
}
// The page's open TBDs, which gate Hand off and Accept spec; on the comment UI page, the questions its page published.
function openTbdCount() { return COMMENT_UI ? pageQuestions().length : openTbdMarkers(document.querySelectorAll('[data-spec-tbd]')).length; }

// The threads the conversation shows: on the comment UI page for a host page, its open item's (threads #stream-context).
function contextThreads() {
  if (!uiHost()) return state.threads;
  return new Map(layer.open ? itemThreads(layer.open).map(th => [th.id, th]) : []);
}

// Where each thread's mark sits now: the service's place; Text removed where the held page answered that its text no
// longer holds the quote, as the layer reads it on a host page alone (#subjects-quote-gone).
function threadPlace() {
  if (LAYER_SERVICE) return th => layerPlace(th, layer.open && layer.open.el);
  // mockups #prototype-marks: a prototype's comment is placed on the route as it now runs, never in the mockup file
  if (PROTOTYPE_ROUTE) return () => null;
  if (COMMENT_UI) return th => ui.gone.has(th.id) ? { state: 'gone' } : th.ev.place || null;
  return th => th.ev.place;
}

function layerHandoffState() {
  const drafts = layerDrafts();
  return { drafts, openTbds: 0, finish: false, tbd: false, nextTbd: false, enabled: drafts > 0 };
}

// threads #root-chip on the comment UI page: the spot's words read from the page as its service serves it; a mockup
// part's at its step by the steps the page published.
function uiSpotWords(holder, th, quoted) {
  const moment = momentParts(th.spot);
  if (ui.mockup && th.spot && th.spot.step && holder.closest('[data-mockup-frame]')) return [partName(holder), stepChip(th.spot.step), ...moment].join(' · ');
  return blockWords(holder, quoted, moment);
}

// The questions its held page published, each answer by its words, keyed and quoted as the page's own button is, read
// from the page its service serves, so a press in the conversation saves the same answer comment as one on the page
// (threads #questions). The chosen one is the one the newest answer comment targets where the service places it.
function publishedQuestions() {
  return ((ui.page && ui.page.questions) || []).map(q => {
    const block = uiAnchor(q.anchor);
    const own = block ? [...block.querySelectorAll(ANSWER)].filter(button => !button.closest(OVERLAY)) : [];
    const buttons = q.answers.map(words => {
      const button = own.find(b => b.dataset.specChoice.trim() === words);
      const desc = button && elementDescriptor(button, block);
      return { anchor: q.anchor, choice: words, text: button ? sourceText(button) : words, key: desc ? desc.key : null, quote: desc ? desc.quote : null };
    });
    let chosen = null;
    for (const th of state.threads.values()) {
      const b = placedMark(th.ev);
      if (b.anchorId !== q.anchor) continue;
      const keyed = b.target && b.target.type === 'element' && buttons.find(a => a.key === b.target.key);
      chosen = keyed || (!b.target && buttons.find(a => a.choice === th.ev.body.text)) || chosen;
    }
    const words = block ? sourceText(block, UNREAD + ',' + ANSWER) : q.text;
    const about = q.about.map(uiAnchor).filter(Boolean);
    const chip = about.length ? partName(about[0]) : block ? blockSpot(block).chip : '#' + q.anchor;
    return Object.assign({}, q, { block: null, text: words || q.text, buttons, chosen, spot: [chip, q.step ? stepChip(q.step) : null].filter(Boolean).join(' · ') });
  });
}

// One published answer, pressed in its question's bubble or on its held page: the same answer comment, once.
function answerPublished(answer, target = null, quote = null) {
  const q = pageQuestions().find(question => question.anchor === answer.anchor);
  if (q && q.chosen && q.chosen.choice === answer.choice) return;
  const keyed = answer.key ? { type: 'element', key: answer.key } : target;
  saveMessage(Object.assign({ kind: 'comment', anchorId: answer.anchor, target: keyed, quote: answer.quote || quote }, pageStep()), answer.choice);
}

function uiSend(message) { if (ui.shell && ui.frame !== null) window.parent.postMessage(Object.assign(message, { frame: ui.frame }), ui.shell); }

// #shell-msg-show: the held page brings a thread's spot into view and rings it, at its step.
function showOnPage(th) {
  uiSend(Object.assign({ type: 'spec-chat-show', thread: th.id }, markSpot(th)));
}

// A question's block, at its step (#shell-review-actions-next).
function showQuestion(q) {
  if (ui.mockup) focusQuestion(q.anchor);
  uiSend(Object.assign({ type: 'spec-chat-show', anchor: q.anchor }, q.step ? { step: q.step } : {}));
}

function nextPublishedTbd() {
  const questions = pageQuestions();
  if (!questions.length) return;
  const at = questions.findIndex(q => q.anchor === ui.lastTbd);
  const q = questions[(at + 1) % questions.length];
  ui.lastTbd = q.anchor;
  showQuestion(q);
}

async function bootCommentUI() {
  await Promise.resolve(); // after the whole runtime has run
  mountStyle();
  mountCommentUI();
  state.transport = commentUITransport();
  state.reviewer = pageReviewer();
  state.loopsStarted = true;
  listenShellUI();
  setInterval(refresh, 2000);
  window.addEventListener('online', refresh);
  window.addEventListener('pagehide', saveTyped); // review-state #offline-leave
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    if (state.composer) { state.composer = null; renderPanel(); } else if (state.focusThread || state.focusQuestion) closeFocus(false);
  });
}

// The panel as the whole page: its head names the page and closes the comment UI; its one hand-off bar ends it, with
// Comment, which points on the held page (#shell-review-actions-rule, prompt-first-shaping #mobile-held).
function mountCommentUI() {
  document.body.classList.add('hx-comment-ui');
  const panel = document.createElement('aside');
  panel.className = 'hx-panel open';
  panel.setAttribute('aria-label', 'Comments');
  panel.hidden = true; // #shell-ui-page-check: nothing shows until its shell's first message
  panel.innerHTML = '<div class="hx-panel-head">Conversation <span class="hx-count" id="hx-count"></span> <span class="hx-sub" id="hx-agent"></span>'
    + '<button class="hx-panel-close" id="hx-ui-close" type="button" aria-label="Minimize the comment UI" title="Minimize">' + ICONS.close + '</button></div>'
    + '<div class="hx-panel-content" id="hx-panel-content"><div class="hx-stream" id="hx-stream"><div class="hx-threads" id="hx-threads"></div></div>'
    + '<div class="hx-handoff"><button class="hx-btn hx-point" id="hx-point" type="button">Comment</button><span class="hx-note" id="hx-drafts">0 drafts</span>'
    + '<span class="hx-offline" id="hx-offline" role="status" hidden></span><button class="hx-btn hx-next-tbd" id="hx-next-tbd" type="button" hidden>Next TBD</button>'
    + '<button class="hx-btn pri" id="hx-handoff" type="button">Hand off to agent →</button></div></div>';
  document.body.appendChild(panel);
  state.panelOpen = true;
  // #shell-msg-close: the human asks to put the comment UI away; the shell does.
  document.getElementById('hx-ui-close').addEventListener('click', () => { if (ui.shell) window.parent.postMessage({ type: 'spec-chat-close' }, ui.shell); });
  document.getElementById('hx-point').addEventListener('click', () => uiSend({ type: 'spec-chat-point' }));
  // TBD open takes Next TBD's step, as on the spec page.
  document.getElementById('hx-handoff').addEventListener('click', () => uiHost() || !pageHandoffState(renderHighlight()).tbd ? handoff() : nextPublishedTbd());
  document.getElementById('hx-next-tbd').addEventListener('click', nextPublishedTbd);
}

// Its own service's routes: a spec or mockup page's spool as a spec page reads it, a host page's scopes as the layer.
function commentUITransport() {
  const spool = httpTransport(), scopes = layerTransport();
  return { mode: 'comments', label: 'comment UI', ready: Promise.resolve(true), listEvents: spool.listEvents,
    postEvent: entry => entry.scope ? scopes.postEvent(entry) : spool.postEvent(entry) };
}

// #shell-ui-page-check: shell messages only from its parent window, at an origin the box names as a shell.
function listenShellUI() {
  if (window.parent === window) return;
  window.addEventListener('message', async event => {
    const data = event.data;
    if (event.source !== window.parent || !event.origin || event.origin === 'null') return;
    if (!plainObject(data) || !str(data.frame, 200)) return;
    if (!(await namedShells()).includes(event.origin)) return;
    ui.shell = event.origin;
    if (data.type === 'spec-chat-publish') takePublish(data);
    else if (data.type === 'spec-chat-pick') takePick(data);
    else if (data.type === 'spec-chat-marks') takeMarksAnswer(data);
  });
}

// #shell-ui-page-only: an address's path on this page's own review service, under the address it serves from, or null
// for any address elsewhere. A held spec or mockup page's text and threads are read at that path on this service only,
// never at an address a message names (#shell-protocol-plain).
function servicePath(at) {
  return at.origin === SCRIPT_SERVICE.origin && at.pathname.startsWith(SCRIPT_SERVICE.pathname)
    ? at.pathname.slice(SCRIPT_SERVICE.pathname.length) : null;
}

// #shell-fields-publish as plain data: the page's address, an http(s) one; a spec or mockup page's on this service,
// since only its spool and its page here are read; a host page's items, each with a subject, a scope, and a kind.
function publishedPage(data) {
  let at;
  try { at = new URL(str(data.page)); } catch (_) { return null; }
  if (!/^https?:$/.test(at.protocol)) return null;
  const page = { frame: data.frame, page: at.href, version: str(data.version, 200) || null };
  if (Array.isArray(data.items)) {
    page.items = data.items.filter(plainObject).map(item => ({ subject: str(item.subject), scope: str(item.scope, 400),
      kind: str(item.kind, 400).trim(), label: str(item.label, 400).trim() || null, from: webAddress(item.from) || at.href,
      starters: strList(item.starters), lane: str(item.lane, 400) || null })).filter(item => item.subject && item.scope && item.kind);
    return page;
  }
  page.path = servicePath(at);
  if (!page.path) return null;
  page.step = str(data.step, 200) || null;
  page.steps = (Array.isArray(data.steps) ? data.steps : []).filter(s => plainObject(s) && str(s.name, 200))
    .map(s => ({ name: s.name, caption: str(s.caption, 400) }));
  page.questions = (Array.isArray(data.questions) ? data.questions : []).filter(q => plainObject(q) && str(q.anchor, 400)).map(q => ({
    anchor: q.anchor, text: str(q.text), answers: strList(q.answers, 4000), about: strList(q.about), step: str(q.step, 200) || null }));
  return page;
}

// #shell-protocol-relay: the conversation is the context of the page whose publish the shell relayed last.
function takePublish(data) {
  const page = publishedPage(data);
  if (!page) return;
  ui.frames.set(page.frame, page);
  const same = ui.page && ui.frame === page.frame && ui.page.page === page.page && Boolean(ui.page.items) === Boolean(page.items);
  showPage(page, !same);
}

function showPage(page, fresh) {
  ui.frame = page.frame;
  ui.page = page;
  ui.sent = ''; // a page that published again drew nothing yet
  document.querySelector('.hx-panel').hidden = false;
  if (fresh) {
    ui.dir = page.items ? null : page.path + '.review';
    ui.mockup = !page.items && /\/mockups\/[^/]+\/[^/]+\.mockup\.html$/.test(page.path);
    ui.gone.clear();
    ui.lastTbd = ui.picked = null;
    ui.doc = null;
    Object.assign(state, { events: [], seenNames: new Set(), threads: new Map(), authors: new Map(), lanes: { lane: null, stages: {} }, focusThread: null,
      focusQuestion: null, activeThread: null, composer: null, openReplies: new Set(), eventsRendered: false,
      mockups: page.items || ui.mockup ? [] : null, mockupAsk: !page.items && !ui.mockup, mockupChips: '' });
    Object.assign(layer, { stores: new Map(), lanes: new Map(), open: null, subjects: '' });
    closeFocusView();
  }
  mockup.steps = page.steps || [];
  state.version = page.version;
  if (!page.items && (fresh || ui.docVersion !== page.version)) readPage(page);
  renderPanel();
  refresh();
}

// The held page as its service serves it, which this page reads only from its own service, at the page's path there:
// its blocks name each spot's chip and key each answer (threads #root-chip, #questions). Read again when the page
// publishes another version.
async function readPage(page) {
  ui.docVersion = page.version;
  try {
    const r = await fetch(new URL(page.path, SCRIPT_SERVICE).href);
    const doc = r.ok ? new DOMParser().parseFromString(await r.text(), 'text/html') : null;
    if (ui.page && ui.page.page === page.page) { ui.doc = doc; renderPanel(); sendMarks(); }
  } catch (_) { /* unreadable: chips name their anchors */ }
}

// #shell-fields-pick: a spot on the held page: a thread's pin focuses its thread, an answer saves it, an open question's
// block focuses the question, any other spot opens the composer on it; a host page's item opens its conversation, a
// page's own or one a page speaking the messages natively names (#shell-native). A box's picture, when the page made one,
// goes with the comment (threads #box-picture).
function takePick(data) {
  const page = ui.frames.get(data.frame);
  if (!page) return;
  ui.picked = null;
  if (page !== ui.page) showPage(page, true); // the human acts on another page the shell holds
  const id = str(data.thread, 200);
  const th = state.threads.get(id);
  // A pin on a page whose threads are not read yet, as when the shell relayed another page last: its thread is focused
  // once they are (focusPicked).
  const pending = Boolean(id && !th && !state.eventsRendered);
  if (pending) ui.picked = id;
  const spot = spotOf(data);
  if (page.items) {
    const item = liveItems().find(it => it.subject === (spot.subject || (th && th.ev.subject)));
    if (!item) return; // no row has its scope: it takes no comment
    layer.open = item;
    if (th) return selectThread(th, false);
    if (pending) return renderPanel();
    state.focusThread = state.activeThread = null;
    state.composer = Object.assign({ kind: 'comment', target: spot.quote ? { type: 'text', key: spot.quote.slice(0, 40) } : null, quote: spot.quote, text: '',
      starters: item.starters, host: { subject: item.subject, kind: item.kind, label: item.label, from: item.from },
      route: { scope: item.scope, subject: item.subject, store: storeOf(item) } }, spotFields(spot), pickedPicture(data, spot));
    return renderPanel();
  }
  if (th) return selectThread(th, false);
  if (pending || !spot.anchor) return;
  const q = pageQuestions().find(question => question.anchor === spot.anchor);
  if (q && str(data.answer)) {
    const answer = q.buttons.find(a => a.choice === data.answer) || { anchor: q.anchor, choice: data.answer, key: null, quote: null };
    return answerPublished(answer, spot.target, spot.quote);
  }
  if (q && !spot.target) return focusQuestion(spot.anchor);
  openComposer(spot.anchor, spot.target, spot.quote);
  Object.assign(state.composer, spotFields(spot), pickedPicture(data, spot));
}

// The thread a pin picked before its page's threads were read, focused once a read of them has: kept across that read,
// gone when the read holds no such thread.
function focusPicked() {
  const th = ui.picked && state.threads.get(ui.picked);
  ui.picked = null;
  if (th) selectThread(th, false);
}

// threads #box-picture-what: a pick's picture of its boxed spot, for its comment to carry; none for a spot with no box.
function pickedPicture(data, spot) {
  const picture = spot.box ? pictureOf(data.picture) : null;
  return picture ? { picture } : {};
}

// #shell-fields-marks-back: the threads whose quote the held page's text no longer holds.
function takeMarksAnswer(data) {
  if (data.frame !== ui.frame) return;
  const missing = strList(data.missing, 200);
  if (JSON.stringify(missing) === JSON.stringify([...ui.gone])) return;
  ui.gone = new Set(missing);
  renderPanel();
}

// #shell-fields-marks: each thread's spot, number, state, open, waiting, or resolved, and review status, and each
// question's chosen answer. Sent when it changes.
function sendMarks() {
  if (!ui.page || !state.eventsRendered) return; // until its threads are read, it tells the page nothing
  const threads = [...state.threads.values()].map((th, index) => Object.assign({ id: th.id, number: index + 1,
    state: th.status === 'resolved' ? 'resolved' : waitsOnReviewer(th) ? 'waiting' : 'open', status: th.status }, markSpot(th)));
  const answers = (uiHost() ? [] : pageQuestions()).filter(q => q.chosen).map(q => ({ anchor: q.anchor, answer: q.chosen.choice }));
  const message = { type: 'spec-chat-marks', threads, answers };
  const text = JSON.stringify(message);
  if (text === ui.sent) return;
  ui.sent = text;
  uiSend(message);
}

/* ---------------- comment layer (comment-layer.spec.html) ----------------
 * A host page loads this runtime from <url>/static/comment-layer.js (#load). Each element the host anchors with a
 * subject, a scope, and a kind (#anchor-table) gets a comment control, and the spec page's pins, thread cards,
 * composer, Hand off, fruit names, live read, and outbox above draw on that element, in an overlay of the layer's own
 * (#ui, #ui-style). Only items whose store the service names get a control, so every save names the store it goes to
 * and Hand off writes one hand-off into each (#ui-handoff): with the service unreachable, refusing this origin, or no
 * scope answered, nothing draws (#scope-rule, #ui-absent).
 */
const LAYER_ANCHOR = '[data-comment-subject][data-comment-scope][data-comment-kind]';
const LAYER_ATTRIBUTES = ['data-comment-subject', 'data-comment-scope', 'data-comment-kind', 'data-comment-label', 'data-comment-from', 'data-comment-starters', 'data-comment-lane'];
const LAYER_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5.5h14v10H10l-5 3v-13Z"></path><path d="M8 9h8M8 12h5"></path></svg>';
const layer = {
  host: null,        // the overlay's element, while any control shows
  root: null,        // its content, inside the overlay's shadow root
  stores: new Map(), // scope -> Map(subject -> store), as the service last named them; absent once no row has the scope
  lanes: new Map(),  // scope -> {lane, stages}, as its last read told them (stagesTold)
  open: null,        // the item whose thread box shows
  held: null,        // {el, text}: the selection inside an item as its control was pressed
  nodes: new Map(),  // placed controls, pins, and marks by key, reused so focus stays
  keys: new WeakMap(), count: 0, // each item element's part of its nodes' keys
  subjects: '',      // the anchored scopes and subjects as last read
  failures: 0,       // reads in a row the service did not answer
  readTimer: 0, scanTimer: 0, frame: 0,
  written: false,    // the host wrote to the page since the last placement (#ui-place)
  sizes: null,       // the ResizeObserver on the page and each anchored element (#ui-place)
  sized: new Set(),  // the anchored elements it observes
};

async function bootLayer() {
  await Promise.resolve(); // after the whole runtime has run
  const once = Symbol.for('spec-chat:comment-layer');
  if (window[once]) return; // a second copy of the layer on one page draws nothing more
  window[once] = true;
  if (document.readyState === 'loading') await new Promise(done => document.addEventListener('DOMContentLoaded', done, { once: true }));
  state.transport = layerTransport();
  state.reviewer = pageReviewer();
  listenHost(SHELL_PAGE); // a shell may hold the host page (shell #shell-roles)
  // #load-scan: the layer follows the elements the host adds, changes, or removes; only an added or removed element or
  // a changed anchor can change what it reads. #ui-place: any change the host makes in the page, such as hiding an
  // element above an item, may move an item without changing a size observed below, so the rows are placed in the
  // next frame, before it paints: once a frame however often the host writes, as it does streaming text.
  new MutationObserver(records => {
    if (records.some(r => r.type === 'childList' || LAYER_ATTRIBUTES.includes(r.attributeName))) scanLayer();
    layer.written = true;
    scheduleLayerPlace();
  }).observe(document.documentElement, { childList: true, subtree: true, attributes: true, characterData: true });
  // #ui-place: a CSS transition or animation the host runs moves items with no change to observe; its start asks for a
  // placement, and each placement reads whether one that can move an item runs or is paused (boxMoves). A script
  // animation announces nothing: the next scroll, resize, or live read places the rows where it moved them.
  for (const type of ['transitionrun', 'animationstart']) document.addEventListener(type, scheduleLayerPlace, true);
  // #ui-place: a row follows its element at once as the element, or the page that moves it, changes size: placed as the
  // size is read, before the frame paints.
  layer.sizes = new ResizeObserver(() => placeLayer());
  layer.sizes.observe(document.body);
  followSizes(layerItems());
  document.addEventListener('scroll', scheduleLayerPlace, true);
  window.addEventListener('resize', scheduleLayerPlace);
  window.addEventListener('online', readLayer);
  window.addEventListener('pagehide', saveTyped); // review-state #offline-leave
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && layer.open) closeLayerBox(); });
  readLayer();
}

// One live read, then the next in 2 s; a service that does not answer is asked less often, up to every 32 s.
function readLayer() {
  clearTimeout(layer.readTimer);
  refreshLayer().finally(() => {
    clearTimeout(layer.readTimer);
    layer.readTimer = setTimeout(readLayer, 2000 * 2 ** Math.min(layer.failures, 4));
  });
}

function scanLayer() {
  clearTimeout(layer.scanTimer);
  layer.scanTimer = setTimeout(() => {
    const items = layerItems();
    followSizes(items);
    if (shell.held) publishLayer(); // whenever what it shows changes (#shell-msg-publish)
    if (layerSubjects(items) !== layer.subjects) readLayer();
    else placeLayer();
  }, 100);
}

// The size observer watches the anchored elements on the page, and lets go of those the host removed.
function followSizes(items) {
  const els = new Set(items.map(item => item.el));
  for (const el of layer.sized) if (!els.has(el)) { layer.sizes.unobserve(el); layer.sized.delete(el); }
  for (const el of els) if (!layer.sized.has(el)) { layer.sizes.observe(el); layer.sized.add(el); }
}

// #ui-place: one placement in the next frame, before it paints, and one every frame after while the host writes to the
// page or a running or paused animation can move an item; none while nothing moves. A placement asked for is never put
// off: a host that writes in its own frame, which runs before the layer's, would put it off every frame; placing in
// each frame it writes keeps the layer's frame after the host's, so each placement follows that frame's writes.
function scheduleLayerPlace() {
  if (layer.frame || COMMENT_UI) return;
  layer.frame = requestAnimationFrame(() => {
    layer.frame = 0;
    if (layer.written || boxMoves()) scheduleLayerPlace();
    layer.written = false;
    placeLayer();
  });
}

// Properties whose motion only paints, as a blinking caret's or a hover's, so it moves no box the layer reads.
const PAINT_ONLY = /^(opacity|visibility|z-index|color|caret-color|accent-color|background.*|border(-(top|right|bottom|left|block|inline)(-(start|end))?)?-color|outline.*|box-shadow|text-shadow|text-decoration-color|text-emphasis-color|column-rule-color|filter|backdrop-filter|clip-path|mask.*|fill|fill-opacity|stroke|stroke-opacity|stroke-dasharray|stroke-dashoffset)$/;
// Properties that transform only their own element and what it holds, so a spinner's motion moves no item outside it.
const TRANSFORM_ONLY = /^(transform(-origin|-box|-style)?|translate|rotate|scale|perspective(-origin)?|offset-(path|distance|rotate|anchor|position))$/;
const KEYFRAME_TIMING = new Set(['offset', 'computedOffset', 'easing', 'composite']);
// Whether a running or paused animation can move an item: one whose keyframes change a property that does more than
// paint and more than transform, or that transforms an element, not a pseudo-element, that is or holds an item. Read in
// each frame that places, so an item the host moves into a moving element, or out of it, counts from the next frame. A
// paused one counts, as a pseudo-class such as :hover or :checked resumes it with no event; a finished or idle one, or
// one on an element the host removed, moves nothing.
function boxMoves() {
  return document.getAnimations().some(run => {
    const effect = run.effect;
    if (run.playState === 'finished' || run.playState === 'idle' || !effect?.target) return false;
    const moves = effect.getKeyframes().flatMap(Object.keys).filter(key => !KEYFRAME_TIMING.has(key))
      .map(key => key.startsWith('--') ? key : key.replace(/[A-Z]/g, c => '-' + c.toLowerCase()))
      .filter(property => !PAINT_ONLY.test(property));
    return moves.some(property => !TRANSFORM_ONLY.test(property)) || (moves.length > 0 && !effect.pseudoElement
      && Boolean(effect.target.matches(LAYER_ANCHOR) || effect.target.querySelector(LAYER_ANCHOR)));
  });
}

// #anchor-table: an anchored element as the layer reads it. A from address that is no web address gives way to the
// page's own. Its lane picks the stage each message about it carries (#anchor-lane), never its placement.
function layerItem(el) {
  const d = el.dataset;
  const kind = (d.commentKind || '').trim();
  if (!d.commentSubject || !d.commentScope || !kind) return null;
  return { el, subject: d.commentSubject, scope: d.commentScope, kind, label: (d.commentLabel || '').trim() || null,
    from: webAddress(d.commentFrom) || location.href,
    starters: (d.commentStarters || '').split(',').map(word => word.trim()).filter(Boolean), lane: (d.commentLane || '').trim() || null };
}

// On the comment UI page, the items its held host page published (shell #shell-msg-publish).
const layerItems = () => COMMENT_UI ? (ui.page && ui.page.items) || [] : [...document.querySelectorAll(LAYER_ANCHOR)].map(layerItem).filter(Boolean);
const storeOf = item => layer.stores.get(item.scope)?.get(item.subject);
// #scope-rule: a control only where the item's scope has a row; held, the page reads the rows and no thread, so the scope's
// row is all it knows (#shell-roles-held).
const liveItems = () => layerItems().filter(item => shell.held ? layer.stores.has(item.scope) : storeOf(item) !== undefined);
const sameItem = (a, b) => Boolean(a && b) && a.scope === b.scope && a.subject === b.subject;

function layerSubjects(items) {
  const byScope = new Map();
  for (const item of items) {
    if (!byScope.has(item.scope)) byScope.set(item.scope, new Set());
    byScope.get(item.scope).add(item.subject);
  }
  return JSON.stringify([...byScope].map(([scope, subjects]) => [scope, [...subjects].sort()]).sort());
}

function layerTransport() {
  const POST_TIMEOUT_MS = 10000;
  return {
    mode: 'layer', label: 'comment layer',
    // 'stored', or 'refused' (400, 403, 404, 409: no row has the scope now, or validation no resend fixes); throws
    // otherwise, so the outbox keeps the event and sends it again (review-state #offline-outbox).
    async postEvent(entry) {
      const url = new URL('api/events', SCRIPT_SERVICE);
      url.search = new URLSearchParams({ scope: entry.scope, subject: entry.subject, actor: 'human', name: entry.name });
      const abort = new AbortController();
      const timer = setTimeout(() => abort.abort(), POST_TIMEOUT_MS);
      let r;
      try {
        r = await fetch(url.href, { method: 'POST', body: JSON.stringify(entry.body), signal: abort.signal });
      } finally { clearTimeout(timer); }
      if (r.ok) return 'stored';
      if ([400, 403, 404, 409].includes(r.status)) return 'refused';
      throw new Error('review service answered ' + r.status);
    },
  };
}

// The threads on a scope's subjects, wherever kept, and the store each subject is kept in (comment-layer #subjects),
// and the stages of the lanes its items' anchors name and of its own (threads #stage-tag-source): {events, stores,
// stages}, or null when no row has the scope.
async function readScope(scope, subjects, lanes) {
  const url = new URL('api/events', SCRIPT_SERVICE);
  url.searchParams.set('scope', scope);
  for (const subject of subjects) url.searchParams.append('subject', subject);
  for (const lane of lanes) url.searchParams.append('lane', lane);
  const r = await fetch(url.href);
  if (r.status === 404) return null;
  if (!r.ok) throw new Error('review service answered ' + r.status);
  const answer = await r.json() || {};
  const events = Array.isArray(answer.events) ? answer.events : [];
  return { events: events.filter(e => e && e.body && typeof e.body === 'object' && typeof e.name === 'string'),
    stores: new Map(Object.entries(answer.stores || {}).filter(([, store]) => typeof store === 'string')), stages: stagesTold(r) };
}

async function refreshLayer() {
  flushOutbox(); // waiting events are sent first; the read never waits on a pending save
  const items = layerItems();
  const subjects = layerSubjects(items);
  const read = [];
  let failed = false, storesChanged = subjects !== layer.subjects;
  layer.subjects = subjects;
  await Promise.all(JSON.parse(subjects).map(async ([scope, list]) => {
    try {
      // shell #shell-ui-page-only: held, a page reads its scopes' rows, asking for no subject, so no thread (comment-layer #scope).
      const lanes = shell.held ? [] : [...new Set(items.filter(item => item.scope === scope && item.lane).map(item => item.lane)
        .concat(threadLanes([...state.threads.values()].filter(th => th.ev.scope === scope))))];
      const answer = await readScope(scope, shell.held ? [] : list, lanes);
      const before = JSON.stringify([...(layer.stores.get(scope) || [])]);
      if (answer) { layer.stores.set(scope, answer.stores); layer.lanes.set(scope, answer.stages); }
      else { layer.stores.delete(scope); layer.lanes.delete(scope); }
      if (JSON.stringify([...(layer.stores.get(scope) || [])]) !== before) storesChanged = true;
      for (const e of answer ? answer.events : []) read.push(Object.assign(e, { scope }));
    } catch (_) { failed = true; } // unreachable, or refusing this origin: a subject never answered stays without a control
  }));
  layer.failures = failed ? layer.failures + 1 : 0;
  if (shell.held) return void (storesChanged && placeLayer()); // the comment UI page reads the threads
  // A save this page holds learns its store and subject from the read.
  const held = new Map(state.events.map(e => [e.actor + '/' + e.name, e]));
  let learned = false;
  for (const e of read) {
    const mine = held.get(e.actor + '/' + e.name);
    if (!mine || mine === e) continue;
    if (mine.store !== e.store || mine.scope !== e.scope || (e.subject !== undefined && mine.subject !== e.subject)) {
      Object.assign(mine, { store: e.store, scope: e.scope }, e.subject !== undefined ? { subject: e.subject } : {});
      learned = true;
    }
  }
  if (COMMENT_UI && subjects !== layer.subjects) return; // the shell relayed another page meanwhile
  ingest(read.concat(outboxEvents()), storesChanged || learned);
  renderOffline();
  if (COMMENT_UI) { focusPicked(); return sendMarks(); }
  scheduleLayerPlace(); // a host's text may have moved under its marks since the last read
}

// Each store's own events fold apart (review-state #model-rule): a store's hand-offs cover only its drafts. Every save
// names the store the service named for its subject before it was written, so one not yet read back folds there too.
const layerStore = e => (e.scope || '') + '\n' + (e.store || '');

function foldLayerThreads(events) {
  const stores = new Map();
  for (const e of events) {
    const key = layerStore(e);
    if (!stores.has(key)) stores.set(key, []);
    stores.get(key).push(e);
  }
  const threads = [...stores.values()].flatMap(list => [...foldThreads(list).values()]);
  threads.sort((a, b) => a.ev.name < b.ev.name ? -1 : 1);
  return new Map(threads.map(th => [th.id, th]));
}

// Where a layer save is sent: a comment with its item's scope and subject, anything else with its thread's.
function layerRoute(event, route) {
  if (route) return { scope: route.scope, subject: route.subject, store: route.store };
  const th = state.threads.get(event.threadId) || state.threads.get(event.respondsTo);
  return th ? { scope: th.ev.scope, subject: th.ev.subject, store: th.ev.store } : {};
}

// #ui-handoff: this browser's drafts on the page, one hand-off into each store that holds them, each listing its own.
// The outbox is shared by every host page of this service in this browser: another page's waiting drafts stay drafts.
function handoffLayer() {
  if (state.handoffPosting) return;
  const items = liveItems();
  const stores = new Map();
  for (const e of state.events) {
    if (!items.some(item => item.scope === e.scope)) continue;
    const key = layerStore(e);
    if (!stores.has(key)) stores.set(key, []);
    stores.get(key).push(e);
  }
  const drafts = layerDrafts();
  let sent = 0;
  for (const list of stores.values()) {
    const mine = new Set(handoffEvents(list, state.reviewer.browser));
    const listed = list.filter(e => mine.has(e.body.id) && items.some(item => sameItem(item, e)));
    if (!listed.length) continue;
    const { scope, subject, store } = listed[0];
    save({ id: humanId('h'), event: 'handoff', anchorId: '', target: null, quote: null, text: 'batch from ' + state.transport.mode,
      events: listed.map(e => e.body.id), actor: 'human', createdAt: new Date().toISOString(), schemaVersion: 1 }, { scope, subject, store });
    sent++;
  }
  if (!sent) return;
  state.handoffPosting = true; // until this hand-off's send settles: a double tap writes one
  toast('Handed off ' + drafts + ' comment' + (drafts === 1 ? '' : 's') + ', agent notified');
  refresh().finally(() => { state.handoffPosting = false; });
}

// This browser's draft threads on the page's items.
function layerDrafts() {
  const browser = state.reviewer && state.reviewer.browser;
  const items = liveItems();
  return [...state.threads.values()].filter(th => items.some(item => sameItem(item, th.ev))
    && (th.draftBy.has(browser) || th.draftBy.has(''))).length;
}

const itemThreads = item => [...state.threads.values()].filter(th => th.ev.scope === item.scope && th.ev.subject === item.subject);

// #subjects-quote-gone: a quote its item's text no longer holds is Text removed; one it holds is placed there.
function layerPlace(th, el) {
  const b = th.ev.body;
  if (!b.quote || !b.target || b.target.type !== 'text') return null;
  return el && textRange(el, b.quote) ? null : { state: 'gone' };
}

// The text inside an item the human selected, as its text nodes hold it, so the quote is found there again.
function heldQuote(el) {
  const selection = window.getSelection && window.getSelection();
  if (!selection || !selection.rangeCount || selection.isCollapsed) return null;
  const range = selection.getRangeAt(0);
  if (!el.contains(range.startContainer) || !el.contains(range.endContainer)) return null;
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  let text = '';
  for (let node; (node = walker.nextNode());) {
    if (!range.intersectsNode(node)) continue;
    const start = node === range.startContainer ? range.startOffset : 0;
    const end = node === range.endContainer ? range.endOffset : node.data.length;
    text += node.data.slice(start, end);
  }
  return text.trim() || null;
}

function openLayerComposer(item) {
  const quote = layer.held && layer.held.el === item.el ? layer.held.text : heldQuote(item.el);
  layer.held = null;
  const moment = itemMoment(item); // threads #video-target: the moment its paused video shows
  // shell #shell-roles-held: a held host page's control is a pick; the comment UI page holds the composer.
  if (shell.held) return pickSpot(Object.assign({ subject: item.subject, quote }, moment));
  // #subjects-fields: every comment written on a host page carries its kind, label, and from address, and its subject.
  const host = { subject: item.subject, kind: item.kind, label: item.label, from: item.from };
  state.composer = Object.assign({ kind: 'comment', target: quote ? { type: 'text', key: quote.slice(0, 40) } : null, quote, text: '',
    starters: item.starters, host, route: { scope: item.scope, subject: item.subject, store: storeOf(item) } }, moment);
  state.activeThread = state.focusThread = null;
  layer.open = item;
  renderLayer(true);
}

// threads #focus: in the item's box, the thread focused over its conversation dimmed.
function selectLayerThread(th) {
  if (state.focusThread !== th.id) focusView.text = '';
  if (state.composer && state.composer.kind === 'edit' && state.composer.threadId !== th.id) state.composer = null;
  state.activeThread = state.focusThread = th.id;
  if (!sameItem(layer.open, th.ev)) layer.open = liveItems().find(item => sameItem(item, th.ev)) || layer.open;
  renderLayer(true);
}

function closeLayerBox() {
  layer.open = null;
  state.composer = null;
  state.activeThread = state.focusThread = null;
  renderLayer(true);
}

function layerOverlay() {
  if (layer.root) return layer.root;
  const host = document.createElement('div');
  host.setAttribute('data-spec-chat', 'comment-layer');
  host.style.cssText = 'all:initial;position:fixed;top:0;left:0;width:0;height:0;z-index:2147483000';
  const shadow = host.attachShadow({ mode: 'open' });
  const css = CSS.replace(/:root(\s*)\{/g, ':host$1{'); // the chrome's tokens and rules, on the overlay alone
  layer.root = document.createElement('div');
  layer.root.className = 'hx-layer';
  // A sheet of its own where the browser has one, which a host page's style-src does not hold back as it holds an inline
  // style back, so the layer looks as it does on a prototype page whose policy allows no inline style
  // (review-service #prototype-proxy).
  if (OWN_SHEETS) {
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(css);
    shadow.adoptedStyleSheets = [sheet];
    shadow.append(layer.root);
  } else shadow.append(Object.assign(document.createElement('style'), { textContent: css }), layer.root);
  layer.host = host;
  document.body.appendChild(host);
  return layer.root;
}

// The data changed (or the box opened, closed, or moved on): the box is drawn again; everything else is placed.
function renderLayer(changed) {
  if (changed && layer.root) renderLayerBox();
  placeLayer();
}

function renderLayerBox() {
  const root = layer.root;
  let box = root.querySelector('.hx-layer-box');
  if (shell.held) return void (box && box.remove()); // a held host page draws no comment box (#shell-roles-held)
  if (layer.open && !liveItems().some(item => sameItem(item, layer.open))) { layer.open = null; state.composer = null; state.focusThread = null; }
  if (!layer.open) { if (box) box.remove(); return; }
  const item = layer.open;
  if (!box) {
    box = root.appendChild(document.createElement('section'));
    box.className = 'hx-layer-box';
    box.setAttribute('role', 'dialog');
    box.appendChild(document.createElement('div')).className = 'hx-stream'; // kept while the box shows: its focused thread stays
  }
  const stream = box.querySelector(':scope > .hx-stream');
  box.setAttribute('aria-label', 'Comments on ' + (item.label || item.subject));
  const head = document.createElement('div');
  head.className = 'hx-layer-head';
  const title = head.appendChild(document.createElement('span'));
  title.className = 'hx-layer-title';
  title.textContent = item.label || item.subject;
  const kind = head.appendChild(document.createElement('span'));
  kind.className = 'hx-layer-kind';
  kind.textContent = item.kind;
  const close = head.appendChild(document.createElement('button'));
  close.type = 'button';
  close.className = 'hx-layer-close';
  close.setAttribute('aria-label', 'Close comments');
  close.textContent = '×';
  close.addEventListener('click', closeLayerBox);
  // An item's box is its conversation (comment-layer #ui-same, threads #stream-context): its threads in the order they
  // began, the newest at the bottom, then the composer of a new one.
  const nodes = [];
  const ctx = conversationContext(th => layerPlace(th, item.el));
  for (const th of itemThreads(item)) nodes.push(threadEntry(th, ctx));
  const c = state.composer;
  if (c && c.kind === 'comment' && c.route && sameItem(c.route, item)) nodes.push(composerCard(c, 'New comment'));
  if (!c || c.kind !== 'comment' || !sameItem(c.route, item)) {
    const start = document.createElement('button');
    start.type = 'button';
    start.className = 'hx-btn';
    start.dataset.act = 'comment';
    start.textContent = '✛ Comment';
    start.addEventListener('click', () => openLayerComposer(layer.open));
    nodes.push(start);
  }
  // Hand off, and no Accept spec: a host page hands off drafts only (comment-layer #ui-same).
  const bar = document.createElement('div');
  bar.className = 'hx-handoff';
  const drafts = layerDrafts();
  const note = bar.appendChild(document.createElement('span'));
  note.className = 'hx-note';
  note.id = 'hx-drafts';
  note.textContent = drafts + ' draft' + (drafts === 1 ? '' : 's');
  const offline = bar.appendChild(document.createElement('span'));
  offline.className = 'hx-offline';
  offline.id = 'hx-offline';
  offline.setAttribute('role', 'status');
  offline.hidden = true;
  const send = bar.appendChild(document.createElement('button'));
  send.type = 'button';
  send.className = 'hx-btn pri';
  send.id = 'hx-handoff';
  send.textContent = 'Hand off to agent →';
  send.disabled = !drafts;
  send.addEventListener('click', handoffLayer);
  // The stream and its conversation stay in the box, so a focused thread's field and a new comment's keep their typing
  // and their focus through a live read; the conversation stays where it was read, and at its bottom when it was there.
  const prior = stream.querySelector(':scope > .hx-threads');
  const stick = !prior || prior.scrollHeight - prior.scrollTop - prior.clientHeight < 8;
  const top = prior ? prior.scrollTop : 0;
  const list = prior || stream.insertBefore(Object.assign(document.createElement('div'), { className: 'hx-threads' }), stream.firstChild);
  drawChildren(list, nodes);
  for (const old of box.querySelectorAll(':scope > :not(.hx-stream,.hx-veil)')) old.remove();
  box.prepend(head);
  box.append(bar);
  list.scrollTop = stick ? list.scrollHeight : top;
  renderFocus(stream, box, false);
  renderOffline();
}

// What shows of an element: the part of it inside the viewport and every box that clips it, with the element's own box
// and the clip, the box it shows through; null when no part shows.
function visiblePart(el) {
  const box = el.getBoundingClientRect();
  if (!box.width && !box.height) return null;
  const clip = { top: 0, left: 0, bottom: innerHeight, right: innerWidth };
  for (let p = el.parentElement; p && p !== document.body && p !== document.documentElement; p = p.parentElement) {
    const style = getComputedStyle(p);
    if (style.overflowX === 'visible' && style.overflowY === 'visible') continue;
    const c = p.getBoundingClientRect();
    clip.top = Math.max(clip.top, c.top); clip.left = Math.max(clip.left, c.left);
    clip.bottom = Math.min(clip.bottom, c.bottom); clip.right = Math.min(clip.right, c.right);
  }
  const top = Math.max(box.top, clip.top), left = Math.max(box.left, clip.left);
  const bottom = Math.min(box.bottom, clip.bottom), right = Math.min(box.right, clip.right);
  return bottom - top >= 1 && right - left >= 1 ? { top, left, bottom, right, box, clip } : null;
}

// An item element's part of its nodes' keys: a control, pin, or mark stays with its element as the host adds, moves, or
// removes others, so a focused one keeps its item and its focus.
function itemKey(el) {
  if (!layer.keys.has(el)) layer.keys.set(el, ++layer.count);
  return layer.keys.get(el);
}

function placedNode(key, make) {
  let node = layer.nodes.get(key);
  if (!node || !node.isConnected) {
    node = make();
    layer.nodes.set(key, node);
    layer.root.appendChild(node);
  }
  return node;
}

function layerControl(key) {
  return placedNode(key, () => {
    const control = document.createElement('button');
    control.type = 'button';
    control.className = 'hx-layer-control';
    control.innerHTML = LAYER_ICON;
    // The selection the human made inside the item is the quote; the press itself would clear it.
    control.addEventListener('pointerdown', () => { layer.held = { el: control.item.el, text: heldQuote(control.item.el) }; });
    control.addEventListener('mousedown', e => e.preventDefault());
    control.addEventListener('click', () => openLayerComposer(control.item));
    return control;
  });
}

function layerPin(key) {
  return placedNode(key, () => {
    const pin = document.createElement('button');
    pin.type = 'button';
    pin.className = 'hx-pin';
    pin.appendChild(document.createElement('span')).className = 'hx-pin-number';
    pin.addEventListener('click', () => {
      if (shell.held) return pickSpot(Object.assign({ thread: pin.thread.id }, markSpot(pin.thread)));
      layer.open = pin.item;
      selectLayerThread(pin.thread);
    });
    return pin;
  });
}

// Controls and pins in a row at each item's own top right corner, while that corner shows (#ui-place); quote marks on
// the text they quote, inside what shows of it; the open thread's ring; the box beside its item.
function placeLayer() {
  if (protoHeld.on) return placePrototypeMarks(); // a held prototype route draws its page's marks (#prototype-route)
  const items = liveItems();
  if (!items.length) {
    if (layer.host) layer.host.remove();
    layer.host = layer.root = layer.open = null;
    layer.nodes.clear();
    return;
  }
  const fresh = !layer.root;
  layerOverlay();
  if (fresh && layer.open) renderLayerBox();
  const used = new Set();
  const size = NARROW.matches ? 44 : 24;
  const numbers = new Map([...state.threads.values()].map((th, index) => [th.id, th.number || index + 1]));
  let ring = null, boxed = null;
  items.forEach(item => {
    const part = visiblePart(item.el);
    if (!part) return;
    const name = item.label || item.subject;
    const at = itemKey(item.el);
    // #ui-place: the row sits 4 px in from the element's own top right corner and shows only while that corner and the
    // control's square are inside what the element shows through, so it is never moved onto the element's content.
    const { box, clip } = part;
    const top = box.top + 4;
    let right = box.right - 4;
    const row = box.top >= clip.top && box.right <= clip.right
      && top + size <= clip.bottom && right - size >= clip.left;
    if (row) {
      const control = layerControl('control:' + at);
      control.item = item;
      control.setAttribute('aria-label', 'Comment on ' + name);
      control.title = 'Comment on ' + name;
      control.setAttribute('aria-expanded', String(sameItem(layer.open, item) && layer.open.el === item.el));
      used.add('control:' + at);
      control.style.top = top + 'px';
      control.style.left = (right - size) + 'px';
      right -= size + 4;
    }
    for (const th of itemThreads(item)) {
      const key = 'pin:' + at + ':' + th.id;
      if (row) {
        const pin = layerPin(key);
        pin.item = item;
        pin.thread = th;
        used.add(key);
        pin.dataset.s = th.status;
        pin.classList.toggle('active', state.activeThread === th.id);
        pin.firstChild.textContent = numbers.get(th.id);
        pin.toggleAttribute('data-waiting', waitsOnReviewer(th)); // threads #unread
        if (pin.hasAttribute('data-waiting') !== Boolean(pin.querySelector('.hx-dot'))) {
          if (pin.querySelector('.hx-dot')) pin.querySelector('.hx-dot').remove(); else pin.appendChild(waitingDot());
        }
        pin.title = 'Thread ' + numbers.get(th.id) + ' on ' + name + ' · ' + th.status;
        pin.setAttribute('aria-label', 'Open thread ' + numbers.get(th.id) + ': ' + name + ', ' + th.status);
        pin.style.top = top + 'px';
        pin.style.left = (right - size) + 'px';
        right -= size + 4;
      }
      const b = th.ev.body;
      const range = b.quote && b.target && b.target.type === 'text' ? textRange(item.el, b.quote) : null;
      if (range) {
        [...range.getClientRects()].forEach((rect, line) => {
          const t = Math.max(rect.top, part.top), l = Math.max(rect.left, part.left);
          const h = Math.min(rect.bottom, part.bottom) - t, w = Math.min(rect.right, part.right) - l;
          if (h < 1 || w < 1) return;
          const markKey = 'mark:' + at + ':' + th.id + ':' + line;
          const mark = placedNode(markKey, () => Object.assign(document.createElement('div'), { className: 'hx-quote-mark' }));
          used.add(markKey);
          Object.assign(mark.style, { top: t + 'px', left: l + 'px', width: w + 'px', height: h + 'px' });
        });
      }
      if (state.activeThread === th.id && sameItem(layer.open, item) && (!ring || layer.open.el === item.el)) {
        ring = range ? range.getBoundingClientRect() : item.el.getBoundingClientRect();
        // threads #root-chip-box: a box on a thing with no video, drawn on the thing's view, its element's box
        boxed = th.spot && th.spot.box && th.spot.moment === null ? { at: item.el.getBoundingClientRect(), box: th.spot.box } : null;
      }
    }
  });
  if (ring) {
    used.add('ring');
    const node = placedNode('ring', () => Object.assign(document.createElement('div'), { className: 'hx-thread-ring' }));
    Object.assign(node.style, { left: (ring.left - 4) + 'px', top: (ring.top - 4) + 'px',
      width: (Math.max(ring.width, 8) + 8) + 'px', height: (Math.max(ring.height, 8) + 8) + 'px' });
  }
  if (boxed) {
    used.add('thing-box');
    const node = placedNode('thing-box', () => Object.assign(document.createElement('div'), { className: 'hx-video-box hx-thing-box' }));
    node.setAttribute('aria-hidden', 'true');
    const { at, box } = boxed;
    Object.assign(node.style, { left: (at.left + box.left * at.width) + 'px', top: (at.top + box.top * at.height) + 'px',
      width: (box.width * at.width) + 'px', height: (box.height * at.height) + 'px' });
  }
  for (const [key, node] of layer.nodes) if (!used.has(key)) { node.remove(); layer.nodes.delete(key); }
  placeLayerBox(items);
}

// Beside its item, below it or above it, on the screen; a sheet across a phone's width.
function placeLayerBox(items) {
  const box = layer.root && layer.root.querySelector('.hx-layer-box');
  if (!box || !layer.open) return;
  if (!layer.open.el.isConnected) { // the host drew the item again: the box follows the new element
    const again = items.find(item => sameItem(item, layer.open));
    if (again) layer.open = again;
  }
  const style = box.style;
  if (NARROW.matches) {
    Object.assign(style, { left: '8px', right: '8px', bottom: '8px', top: 'auto' });
    return;
  }
  Object.assign(style, { right: 'auto', bottom: 'auto' });
  const r = layer.open.el.getBoundingClientRect();
  const { width, height } = box.getBoundingClientRect();
  const below = r.bottom + 8, above = r.top - 8 - height;
  const top = below + height <= innerHeight - 8 ? below : above >= 8 ? above : Math.max(8, innerHeight - 8 - height);
  style.top = Math.round(top) + 'px';
  style.left = Math.round(Math.max(8, Math.min(r.left, innerWidth - 8 - width))) + 'px';
}

/* ---------------- reading view ----------------
 * This block owns the audience toggle and its HTTP-only Jev request. It never
 * changes document order or removes clauses. Git focus and reading view are
 * mutually exclusive display modes.
 */
function clearReadingAudience() {
  document.querySelectorAll('[data-hx-audience]').forEach(el => delete el.dataset.hxAudience);
}

function clearGitFocusForReading() {
  document.body.classList.remove('hx-focus-active');
  document.querySelectorAll('[data-hx-focus],[data-hx-focus-root]').forEach(el => {
    delete el.dataset.hxFocus;
    delete el.dataset.hxFocusRoot;
  });
  document.querySelectorAll('.hx-focus-error').forEach(el => el.remove());
  if (OWN_ADDRESS.searchParams.get('focus') === 'changes') rewriteAddress(query => query.delete('focus'));
}

function setReadingView(on) {
  const next = Boolean(on);
  if (next === state.readingView && !next) {
    clearReadingAudience();
    return;
  }
  if (next) clearGitFocusForReading();
  state.readingView = next;
  document.body.classList.toggle('hx-reading-active', next);
  const button = document.getElementById('hx-reading');
  if (button) {
    button.setAttribute('aria-pressed', String(next));
    button.textContent = next ? 'Reading view on' : 'Reading view';
  }
  if (!next) clearReadingAudience();
  if (next) {
    const base = (state.range.baseline && state.range.baseline.base) || OWN_ADDRESS.searchParams.get('base');
    if (base) requestJev(base);
  } else {
    renderJev();
  }
}

function mountReadingView() {
  if (EMBED_REVIEW_DIR || MOCKUP_SPEC || !['http:', 'https:'].includes(OWN_ADDRESS.protocol) || document.getElementById('hx-reading')) return;
  const toolbar = document.querySelector('.hx-toolbar');
  if (!toolbar) return;
  const style = document.createElement('style');
  // Internals take the document's muted color, which the shared stylesheet and the runtime's document CSS set in both modes.
  style.textContent = `.hx-reading-active [data-hx-audience="internals"]{color:var(--spec-muted)!important}
.hx-reading-active [data-hx-audience="internals"] :is(a,code,strong,em,span){color:inherit!important}
`;
  document.head.appendChild(style);
  const button = document.createElement('button');
  button.id = 'hx-reading';
  button.type = 'button';
  button.textContent = 'Reading view';
  button.setAttribute('aria-pressed', 'false');
  button.addEventListener('click', () => setReadingView(!state.readingView));
  toolbar.insertBefore(button, document.getElementById('hx-status'));
  document.body.classList.toggle('hx-reading-active', state.readingView);
}

const readingClassObserver = new MutationObserver(() => {
  if (state.readingView && document.body.classList.contains('hx-focus-active')) setReadingView(false);
  else document.body.classList.toggle('hx-reading-active', state.readingView);
});
if (!LAYER_SERVICE) {
  readingClassObserver.observe(document.body, { attributes: true, attributeFilter: ['class'] });
  setTimeout(mountReadingView, 0);
}

})();
