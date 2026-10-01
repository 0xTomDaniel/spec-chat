// spec-chat runtime v0.1 — hydrates semantic islands and mounts the annotation layer.
// spec-chat-capabilities: changed-root-focus custom-style-focus diff-visibility-control finish-review git-focus manual-resume-status mobile-pre-wrap mobile-review next-tbd reopen-thread semantic-islands shared-style-ownership spec-acceptance tbd-later
// Transports: FSA (file://, primary) | HTTP review-serve (http(s)://, secondary).
// Same spools, same event schema either way. See DESIGN.md.
// Classic script, NOT a module: browsers CORS-block module scripts on file:// pages,
// and file:// is the primary transport. Specs load it with <script defer src=...>.
// Embed mode: a host app (SPA or any live page) loads the same script with
// data-review-dir="<repo-relative>.review" on the tag. That pins one spool for the whole
// app (routes change location.pathname, which normally names the spool), skips the
// document-presentation CSS (the host owns its look; only the hx-* overlay ships), and
// disables spec-mtime watching (there is no spec file to go stale). HTTP transport only.
(function () {
'use strict';

const SPEC_FILE = decodeURIComponent(location.pathname.split('/').pop());
// document.currentScript is only valid during the initial synchronous run — capture now.
const EMBED_REVIEW_DIR = (document.currentScript && document.currentScript.dataset && document.currentScript.dataset.reviewDir) || null;
const REVIEW_DIRNAME = SPEC_FILE + '.review';
// The spool this page reviews; also keys its outbox and last-loaded events in browser storage.
const REVIEW_DIR = EMBED_REVIEW_DIR || location.pathname.replace(/^\//, '') + '.review';
const RUNTIME_URL = (document.currentScript && document.currentScript.src) || new URL('./.viz/runtime.js', document.baseURI).href;
const VENDOR = { echarts: new URL('./vendor/echarts-5.5.1.min.js', RUNTIME_URL).href };

/* ---------------- error overlay (headless-debuggable) ---------------- */
window.addEventListener('error', e => {
  // Browsers intentionally redact some foreign/extension failures to this
  // detail-free signature. It cannot identify a spec-chat fault and must not
  // cover a still-working review surface with a fatal-looking overlay.
  if (e.message === 'Script error.' && !e.filename && !e.lineno && !e.colno && !e.error) return;
  overlay('error', e.message + ' @ ' + (e.filename || '').split('/').pop() + ':' + e.lineno);
});
window.addEventListener('unhandledrejection', e => overlay('rejection', String(e.reason)));
function overlay(kind, msg) {
  let el = document.getElementById('hx-errors');
  if (!el) {
    el = document.createElement('div');
    el.id = 'hx-errors';
    el.style.cssText = 'position:fixed;bottom:0;left:0;right:0;background:#8b1a1a;color:#fff;font:12px monospace;padding:6px 10px;z-index:9999;white-space:pre-wrap;';
    document.body.appendChild(el);
  }
  el.textContent += kind + ': ' + msg + '\n';
}

/* ---------------- state ---------------- */
const state = {
  transport: null,       // {mode, ready, listEvents, postEvent, specModified, label}
  events: [],            // [{actor, name, body}] sorted by name
  seenNames: new Set(),
  threads: new Map(),    // root comment id -> {id, ev, messages:[], status, latestHumanId}
  expandedResolved: new Set(), // resolved thread ids the human explicitly reopened
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
  jev: { status: 'idle', items: [], levels: {}, offer: null, base: null, request: 0 }, // levels: the server's mark kind -> level table
  evidence: { criteria: null, levels: {}, hostOrigin: null }, // criteria: anchor -> entry once /api/evidence answers, null shows nothing;
  // hostOrigin: the BB plugin frame that announced itself
  readingView: false,
  movingOrphans: new Set(),
  handoffPosting: false,
  reviewer: null,        // {browser, author}: this browser's id and fruit name (#model-author-assign)
  authors: new Map(),    // author key -> shown name (#model-author-shown)
  version: null,         // SHA-256 of the spec text this page shows: its ETag (#model-fields)
  lastStamp: null,       // last event-name stamp this page chose
  outboxBlocked: false,  // a send failed; the offline notice shows until the outbox drains
  storedCount: -1,       // events last kept in browser storage
};

/* ---------------- transports ---------------- */
function httpTransport() {
  const dir = REVIEW_DIR;
  const POST_TIMEOUT_MS = 10000; // a save that has not answered by then is waiting, not stored
  return {
    mode: 'http', label: EMBED_REVIEW_DIR ? 'review-serve (embedded)' : 'review-serve',
    ready: Promise.resolve(true),
    async listEvents() {
      const r = await fetch('/api/events?dir=' + encodeURIComponent(dir));
      return { events: await r.json(), wake: r.headers.get('X-Spec-Chat-Wake') || null };
    },
    // 'stored' (also a resend of the same bytes), 'refused' (400, 403, 409: validation no resend fixes);
    // throws on anything else, a hung save included, so the outbox keeps the event and sends it again (#offline-outbox).
    async postEvent(entry) {
      const abort = new AbortController();
      const timer = setTimeout(() => abort.abort(), POST_TIMEOUT_MS);
      let r;
      try {
        r = await fetch('/api/events?dir=' + encodeURIComponent(dir) + '&actor=human&name=' + encodeURIComponent(entry.name), { method: 'POST', body: JSON.stringify(entry.body), signal: abort.signal });
      } finally { clearTimeout(timer); }
      if (r.ok) return 'stored';
      if (r.status === 400 || r.status === 403 || r.status === 409) return 'refused';
      throw new Error('review service answered ' + r.status);
    },
    async specModified() {
      if (EMBED_REVIEW_DIR) return null; // no spec file behind a live app; watchSpec stays quiet
      const r = await fetch(location.pathname, { method: 'HEAD' });
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
  const params = new URLSearchParams({ path: location.pathname.replace(/^\//, '') });
  if (base) params.set('base', base);
  return params;
}

// The change type's two answers (jev-suggestions #change-type).
const JEV_TYPE_LABELS = {
  behavior: 'Behavior',
  'no-behavior-change': 'No behavior change',
};

function jevParams(base) {
  const params = new URLSearchParams({ path: location.pathname.replace(/^\//, ''), base: String(base || '') });
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
      text: item.text == null ? null : String(item.text), // a rule's criterion text (project-rules #mark-rule-text)
      escalated: item.escalated === true,
    })) : [],
    levels: result ? result.levels : null, // the server's levels table (#markers-levels-source); markLevel reads it
    offer: result && result.offer && typeof result.offer === 'object' && Number(result.offer.count) > 0 ? result.offer : null,
  };
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
  state.jev.offer = null;
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
  if (!['http:', 'https:'].includes(location.protocol) || !base) return;
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
    const same = refresh && status === state.jev.status
      && JSON.stringify([result.items, result.levels, result.offer])
      === JSON.stringify([state.jev.items, state.jev.levels, state.jev.offer]);
    state.jev.status = status;
    state.jev.items = result.items;
    state.jev.levels = result.levels;
    state.jev.offer = result.offer;
    scheduleJevPoll();
    if (same) return;
    renderJev();
    renderPanel();
    renderPins();
  } catch (_) {
    if (request !== state.jev.request) return;
    if (refresh) { scheduleJevPoll(); return; }
    state.jev.status = 'unavailable';
    state.jev.items = [];
    state.jev.levels = {};
    state.jev.offer = null;
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
    const response = await fetch('/api/evidence?' + new URLSearchParams({ path: location.pathname.replace(/^\//, '') }));
    const result = response.ok ? await response.json() : null;
    const criteria = result && result.criteria;
    if (!criteria || typeof criteria !== 'object' || Array.isArray(criteria)) return;
    state.evidence.levels = result.levels;
    state.evidence.criteria = criteria;
    renderJev();
  } catch (_) {}
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
  if (EMBED_REVIEW_DIR || location.protocol === 'file:') return;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const requestedBase = new URLSearchParams(location.search).get('base');
    const baseline = await fetchBaseline(requestedBase, controller.signal);
    state.range.baseline = baseline;
    renderRangeBar(baseline);
    requestJev(baseline.base);
    markIssueFocus(state.range.loaded, baseline);
  } catch (error) {
    const copy = document.getElementById('hx-range-copy');
    if (copy) copy.textContent = 'Compared range unavailable.';
    if (new URLSearchParams(location.search).get('focus') === 'changes') showFocusError();
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
    markIssueFocus(state.range.loaded, baseline);
    renderRangeBar(baseline);
    requestJev(baseline.base || requested);
    const url = new URL(location.href);
    url.searchParams.set('focus', 'changes');
    url.searchParams.set('base', baseline.base || requested);
    history.replaceState(null, '', url.pathname + url.search + url.hash);
    openRangePicker(false);
  } catch (error) {
    setRangeError(error.name === 'AbortError' ? 'Commit lookup timed out.' : 'That commit could not be resolved locally.');
  } finally {
    clearTimeout(timeout);
    state.range.loading = false;
    if (apply) apply.disabled = false;
  }
}

function mountRangeBar() {
  if (EMBED_REVIEW_DIR || !['http:', 'https:'].includes(location.protocol) || document.getElementById('hx-range-bar')) return;
  const bar = document.createElement('section');
  bar.className = 'hx-range-bar';
  bar.id = 'hx-range-bar';
  bar.setAttribute('aria-label', 'Compared commit range');
  bar.innerHTML = '<div class="hx-range-row"><p id="hx-range-copy" class="hx-range-copy">Loading compared range…</p><button id="hx-range-change" class="hx-range-change" type="button" aria-expanded="false" aria-controls="hx-range-picker">Change base</button></div><div id="hx-range-picker" class="hx-range-picker" hidden><div id="hx-range-commits" class="hx-range-commits"></div><form id="hx-range-form" class="hx-range-form"><label for="hx-range-input">Paste a commit id</label><div><input id="hx-range-input" name="base" autocomplete="off" spellcheck="false"><button id="hx-range-apply" type="submit">Apply</button></div><p id="hx-range-error" class="hx-range-error" role="alert" hidden></p></form></div>';
  const article = document.querySelector('article.spec');
  if (article) article.parentNode.insertBefore(bar, article);
  else document.body.insertBefore(bar, document.body.firstChild);
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
  const segs = decodeURIComponent(location.pathname).split('/').filter(Boolean);
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
      const segs = decodeURIComponent(location.pathname).split('/').filter(Boolean);
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
      try { await store('readwrite', s => s.put(specDir, location.href)); } catch {}
      try { await store('readwrite', s => s.put(h, 'last-dir')); } catch {}
      if (alsoScope) try { await store('readwrite', s => s.put(h, 'scope-root')); } catch {}
    },
    async tryRestore() {
      try {
        let h = await store('readonly', s => s.get(location.href));
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
      const dir = decodeURIComponent(location.pathname).replace(/\/[^/]*$/, '');
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

const TARGETABLE = 'h1, h2, h3, h4, h5, h6, p, li, ul, ol, table, tr, td, th, blockquote, pre, code, nav, figcaption, button, input, select, textarea, label, a, output, summary, [data-render-target]';
const cssId = id => window.CSS && window.CSS.escape ? window.CSS.escape(id) : id.replace(/([^a-zA-Z0-9_-])/g, '\\$1');

function elementDescriptor(el, holder) {
  if (el.closest && el.closest('svg')) return svgDescriptor(el, holder);
  let node = el;
  while (node && node !== holder && !node.matches(TARGETABLE)) node = node.parentElement;
  if (!node || node === holder) return null;
  const isFig = node.hasAttribute('data-render-target');
  const tag = node.tagName.toLowerCase();
  const quote = (node.textContent || node.value || '').trim().replace(/\s+/g, ' ').slice(0, 60);
  // ids are hand-authored and survive spec edits better than positional indexes
  if (!isFig && node.id && holder.querySelectorAll(tag + '#' + cssId(node.id)).length === 1) {
    return { key: tag + '#' + node.id, quote };
  }
  const sel = isFig ? '[data-render-target]' : tag;
  const peers = [...holder.querySelectorAll(sel)];
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
    return holder.querySelector(sel + '#' + cssId(m[2]));
  }
  m = /^([a-z][a-z0-9-]*|title|breadcrumbs|figure)\[(\d+)\]$/.exec(key);
  if (!m) return null;
  const sel = m[1] === 'title' ? 'h1' : m[1] === 'breadcrumbs' ? 'nav' : m[1] === 'figure' ? '[data-render-target]' : m[1];
  return [...holder.querySelectorAll(sel)][+m[2] - 1] || null;
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
  if (!state.commentMode || e.target.closest('.hx-pin,.hx-jev-marker,.hx-jev-pop,.hx-panel,.hx-toolbar,.hx-range-bar,.hx-service-index-link,#hx-errors')) return;
  const holder = holderOf(e.target);
  if (!holder) return;
  if (e.target.tagName === 'CANVAS') return; // canvas clicks are the chart's business: marks via chart events, blanks via zrender
  // comment mode suspends the page: no link navigation, label toggling, or spec-script handlers
  e.preventDefault();
  e.stopImmediatePropagation();
  const sel = window.getSelection();
  const selTxt = sel ? String(sel).trim() : '';
  if (selTxt) {
    openComposer(holder.dataset.anchor, { type: 'text', key: selTxt.slice(0, 40) }, selTxt);
    sel.removeAllRanges();
    return;
  }
  const desc = elementDescriptor(e.target, holder);
  if (desc) openComposer(holder.dataset.anchor, { type: 'element', key: desc.key }, desc.quote);
  else openComposer(holder.dataset.anchor, null, null);
}

/* ---------------- events -> threads ---------------- */
// Events sorted by file name; meaning follows references (#model-order): an event whose
// referent sorts later waits for it, so a skewed clock never drops or detaches a message.
function foldThreads(events) {
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
      const th = { id: b.id, ev: e, messages: [e], status: humanStatus(e), latestHumanId: b.id, history: new Map(), draftBy: new Set() };
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
        th.status = b.status || 'acknowledged';
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
      if (th) th.status = b.status;
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

// #model-author-assign: a random id and a fruit name, created once and kept in this browser.
const FRUITS = ['Mango', 'Papaya', 'Lychee', 'Guava', 'Rambutan', 'Pineapple', 'Passion Fruit', 'Dragon Fruit', 'Durian', 'Jackfruit', 'Mangosteen', 'Starfruit', 'Soursop', 'Coconut', 'Banana', 'Tamarind', 'Longan', 'Feijoa', 'Cherimoya', 'Sapodilla', 'Kiwano', 'Pitanga', 'Jabuticaba', 'Salak'];
function reviewerIdentity(storage, random) {
  const KEY = 'spec-chat:reviewer';
  try {
    const kept = JSON.parse(storage.getItem(KEY) || 'null');
    if (kept && typeof kept.browser === 'string' && kept.browser && FRUITS.includes(kept.author)) return kept;
  } catch (_) { /* no storage: a name for this page only */ }
  let browser = '';
  while (browser.length < 20) browser += Math.floor(random() * 36).toString(36);
  const fresh = { browser, author: FRUITS[Math.min(FRUITS.length - 1, Math.floor(random() * FRUITS.length))] };
  try { storage.setItem(KEY, JSON.stringify(fresh)); } catch (_) { /* nothing to keep it in */ }
  return fresh;
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

function resolvedThreadCollapsed(thread, expandedResolved) {
  return thread.status === 'resolved' && !expandedResolved.has(thread.id);
}

function threadReplyAction(thread) {
  const message = thread.messages[thread.messages.length - 1];
  if (!message || message.actor !== 'agent') return null;
  return { label: thread.status === 'resolved' ? 'Reply and reopen' : '↩ Reply', message };
}

function commentModeShortcut(e) {
  const target = e.target || {};
  return String(e.key || '').toLowerCase() === 'c'
    && !e.defaultPrevented && !e.repeat
    && !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey
    && !target.isContentEditable
    && !/^(textarea|input|select)$/i.test(target.tagName || '');
}

function threadDockEntries(threads) {
  return [...threads.values()].map((thread, index) => ({ thread, number: index + 1 })).reverse();
}

function acknowledgedReplyCount(threads) {
  return [...threads.values()].filter(thread => thread.status === 'acknowledged').length;
}

// With open TBDs the action is TBD open once settled; until then Next TBD sits beside Hand off.
// `browser` counts only that browser's draft threads: its Hand off hands off only its own drafts.
function reviewHandoffState(threads, openTbds = 0, browser = null) {
  const values = [...threads.values()];
  const drafts = values.filter(thread => browser ? thread.draftBy?.has(browser) || thread.draftBy?.has('') : thread.status === 'draft').length;
  const unsent = values.some(thread => thread.status === 'draft');
  const settled = !unsent && values.every(thread => thread.status === 'resolved');
  const finish = !openTbds && settled;
  const tbd = openTbds > 0 && settled;
  return { drafts, openTbds, finish, tbd, nextTbd: openTbds > 0 && !tbd, enabled: drafts > 0 || finish || tbd };
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
  const openTbds = openTbdMarkers(document.querySelectorAll('[data-spec-tbd]'));
  const handoffState = reviewHandoffState(state.threads, openTbds.length, state.reviewer?.browser);
  const addressed = addressPlace();
  renderTbdHighlight(document, tbdHighlightBlocks(openTbds).concat(addressed ? [findAnchor(addressed.anchor)] : []));
  return handoffState;
}

// Arrival at an anchor (a conflict link on this or another spec, or Go to) scrolls to it and highlights it as an open TBD is.
function arriveAtAddress() {
  renderHighlight();
  const addressed = addressPlace();
  if (addressed) findAnchor(addressed.anchor).scrollIntoView({ block: 'center', behavior: 'smooth' });
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

function handoffAgentText(observation, wake, last) {
  if (observation && wake === 'failed') return '· wake failed; send a new chat message to resume';
  if (observation === 'waiting' || (observation === 'queued' && wake === 'deferred')) return '· handed off, waiting for agent';
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
    state.threads = foldThreads(state.events);
    state.authors = authorNames(state.events);
    for (const id of state.expandedResolved) {
      if (state.threads.get(id)?.status !== 'resolved') state.expandedResolved.delete(id);
    }
  }
  renderPanel();
  renderPins();
  renderBadges();
}

function jevDisplayLabel(item) {
  if (!item) return '';
  if (item.state === 'unsure') return 'unsure';
  if (item.state === 'unavailable') return 'Jev unavailable';
  return item.label || '';
}

function corpusFlags(items) {
  const labels = { contradicts: 'Contradicts', overlaps: 'Overlaps', oversteps: 'Oversteps' };
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
      target: item.target == null ? null : String(item.target) });
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
  if (path && path !== location.pathname.replace(/^\//, '')) {
    location.href = (path.startsWith('/') ? path : '/' + path) + (anchor ? '#' + anchor : '');
    return;
  }
  scrollToJevAnchor(anchor || value);
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
  return new URLSearchParams(location.search).get('focus') === 'changes' || document.body.classList.contains('hx-focus-active');
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

// project-rules #dismiss-where: one click that Jev is wrong. Hides the dropped rule notes at once, ends any read in
// flight, records through the offer's record route, then re-reads /api/jev and shows what the server returns: a
// recorded dismissal stays gone, a refused or failed one comes back.
function jevDismissAction(label, body, drop) {
  return { label, run: () => {
    const base = state.jev.base;
    state.jev.request += 1;
    state.jev.items = state.jev.items.filter(item => !(item.kind === 'rule' && drop(item)));
    renderJev();
    const reread = () => { if (base === state.jev.base) requestJev(base, true); };
    fetch('/api/jev/offer?' + new URLSearchParams({ path: location.pathname.replace(/^\//, '') }),
      { method: 'POST', body: JSON.stringify(body) }).then(reread, reread);
  } };
}

// project-rules #marks: a missed rule is its fixed word linking the rule, not muted, then the rule's criterion text
// (#mark-rule-text), with Ask to cover, Not here, and Dismiss rule; a check
// escalated to the LLM and still unanswered is a wheel in the marker's place; a failed LLM fallback is the neutral
// Jev unavailable note naming the rule (#q-fallback); anything else shows nothing.
function jevRuleNote(item) {
  if (!item.id || !item.word) return null;
  if (item.state === 'pending') {
    return item.escalated ? { anchor: item.id, group: 'rule', state: 'pending', text: 'checking ' + item.word + '\u2026' } : null;
  }
  if (item.state !== 'unavailable' && (item.state !== 'label' || item.label !== 'missed')) return null;
  const link = corpusTargetLink(item.target);
  if (!link) return null;
  const hash = link.text.indexOf('#');
  const rule = link.text.slice(hash) + ' from ' + (link.text.slice(0, Math.max(hash, 0)).split('/').pop() || item.word + '.spec.html');
  if (item.state === 'unavailable') {
    return { anchor: item.id, group: 'neutral', state: 'unavailable', text: 'Jev unavailable', level: null,
      sentence: 'Jev could not check whether this spec needs ' + rule };
  }
  return { anchor: item.id, group: 'rule', state: 'label', text: item.word + '?', href: link.href, level: item.level || null,
    sentence: 'This spec may need ' + rule, quote: item.text,
    actions: [jevDraftAction('Ask to cover', 'Cover ' + link.text + ' with a criterion, or add one line saying why it does not apply.'),
      jevDismissAction('Not here', { dismiss: 'here', rule: item.text, record: item.record },
        other => other.id === item.id && other.text === item.text),
      jevDismissAction('Dismiss rule', { dismiss: 'rule', rule: item.text }, other => other.text === item.text)] };
}

function jevSuggestionNotes() {
  if (state.jev.status !== 'on') return [];
  const notes = [];
  const gaps = [];
  for (const item of state.jev.items) {
    const note = item.kind === 'rule' ? jevRuleNote(item) : null;
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
  const parts = [];
  const walk = node => {
    for (const child of node.childNodes) {
      if (child.nodeType === 3) parts.push(child.nodeValue);
      else if (child.nodeType === 1 && !child.matches('[data-anchor],.hx-jev-marker,.hx-pin,.hx-badge,script,style')) walk(child);
    }
  };
  walk(element);
  return parts.join('').split(/\s+/).filter(Boolean).join(' ');
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

// Plugin bridge (criterion-evidence #plugin-bridge): IDs, never URLs, read from the service's bundle page address.
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

function evidenceOpen(bundle, criterion) {
  if (!bundle) return null;
  return () => {
    if (!state.evidence.hostOrigin) return false;
    window.parent.postMessage(criterion ? { type: 'spec-chat-open-evidence', bundle, criterion } : { type: 'spec-chat-open-evidence', bundle }, state.evidence.hostOrigin);
    return true;
  };
}

function listenEvidenceHost() {
  if (window.parent === window) return;
  window.addEventListener('message', event => {
    const data = event.data;
    if (event.source !== window.parent || !event.origin || event.origin === 'null') return;
    if (!data || data.type !== 'spec-chat-host' || !Array.isArray(data.opens) || !data.opens.includes('evidence')) return;
    state.evidence.hostOrigin = event.origin;
  });
}

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
    acceptNode: node => node.nodeValue.trim() && !node.parentElement.closest('.hx-jev-marker,.hx-pin,.hx-badge,script,style') ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP,
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
  if (window.matchMedia('(max-width: 640px)').matches) return;
  const anchor = marker.getBoundingClientRect();
  if (!marker.isConnected || anchor.bottom < 0 || anchor.top > innerHeight) { closeJevPopover(); return; }
  const controls = [...document.querySelectorAll('.hx-toolbar,.hx-thread-dock,.hx-panel.open,.hx-service-index-link,.hx-banner')]
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

// project-rules #bootstrap-offer: one page note after warm-up. Reconcile drafts one comment; sending it or
// dismissing the note records the offer, after which the server never returns it again for this project.
function jevOfferNote(offer) {
  const note = document.createElement('div');
  note.className = 'hx-jev-note hx-jev-offer';
  const count = Number(offer.count);
  note.appendChild(document.createElement('span')).textContent =
    count + (count === 1 ? ' existing spec misses' : ' existing specs miss') + ' project rules: reconcile?';
  const specs = Array.isArray(offer.specs) ? offer.specs : [];
  // Disclosure toggle: collapsed detail list of each spec and its missed rules.
  const toggle = note.appendChild(document.createElement('button'));
  toggle.type = 'button';
  toggle.className = 'hx-disclosure';
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-label', 'Show spec details');
  toggle.textContent = '\u25b8';
  const detail = note.appendChild(document.createElement('div'));
  detail.className = 'hx-jev-offer-detail';
  detail.hidden = true;
  for (const entry of specs) {
    const row = detail.appendChild(document.createElement('div'));
    row.className = 'hx-jev-offer-spec';
    const specLink = row.appendChild(document.createElement('a'));
    specLink.href = '/' + String(entry.spec || '');
    specLink.textContent = String(entry.spec || '');
    for (const r of (Array.isArray(entry.rules) ? entry.rules : [])) {
      const ruleLink = row.appendChild(document.createElement('a'));
      ruleLink.href = '/' + String(r.target || '');
      ruleLink.className = 'hx-jev-offer-rule';
      ruleLink.textContent = String(r.word || '') + '?';
    }
  }
  toggle.addEventListener('click', event => {
    event.stopPropagation();
    const expanded = detail.hidden;
    detail.hidden = !expanded;
    toggle.setAttribute('aria-expanded', String(expanded));
    toggle.textContent = expanded ? '\u25be' : '\u25b8';
    toggle.setAttribute('aria-label', expanded ? 'Hide spec details' : 'Show spec details');
  });
  const record = action => {
    state.jev.offer = null;
    renderJev();
    fetch('/api/jev/offer?' + new URLSearchParams({ path: location.pathname.replace(/^\//, '') }),
      { method: 'POST', body: JSON.stringify({ offer: action }) }).catch(() => {});
  };
  const lines = specs.map(entry => [String(entry.spec || ''),
    ...(Array.isArray(entry.rules) ? entry.rules : []).map(r => String(r.word || '') + '? ' + String(r.target || ''))].join(' '));
  const reconcile = note.appendChild(document.createElement('button'));
  reconcile.type = 'button';
  reconcile.className = 'hx-btn';
  reconcile.textContent = 'Reconcile';
  reconcile.addEventListener('click', event => {
    event.stopPropagation();
    const anchor = document.querySelector('[data-anchor]');
    openComposer(anchor ? anchor.dataset.anchor : '', null, null,
      ['Reconcile each spec with its missed rules:', ...lines].join('\n'), () => record('sent'));
  });
  const dismiss = note.appendChild(document.createElement('button'));
  dismiss.type = 'button';
  dismiss.className = 'hx-jev-offer-dismiss';
  dismiss.textContent = '\u00d7';
  dismiss.setAttribute('aria-label', 'Dismiss');
  dismiss.addEventListener('click', event => { event.stopPropagation(); record('dismissed'); });
  return note;
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
  if (state.jev.status === 'on' && state.jev.offer) pageNote(jevOfferNote(state.jev.offer));

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
// owns its own look, so embed mode ships the hx-* overlay CSS alone.
const DOC_CSS = `
/* document presentation — the spec file stays lean; the dialect's look lives here */
:where(body){margin:0;background:#faf9f6;color:#22242a;padding-bottom:100px}
article.spec{max-width:720px;margin:0 auto;padding:40px 24px;font:16.5px/1.65 "Iowan Old Style","Palatino Linotype",Georgia,serif}
article.spec header{border-bottom:1px solid #e2e0d8;padding-bottom:16px;margin-bottom:28px}
article.spec h1{font-size:29px;line-height:1.2;margin:0 0 8px;letter-spacing:-.01em}
article.spec h2{font-size:20px;margin:26px 0 10px}
article.spec nav{font:12px system-ui;color:#8b8e98}
article.spec p{margin:0 0 10px;max-width:62ch}
article.spec pre{white-space:pre-wrap;overflow-wrap:anywhere}
article.spec a{color:#12897c}
[data-render-target]{border:1px solid #e2e0d8;border-radius:8px;background:#fff;margin:6px 0 10px}
@media(prefers-color-scheme:dark){
:where(body){background:#17191d;color:#e8e7e2}
article.spec header{border-color:#33363c}
article.spec nav{color:#74767e}
article.spec a{color:#34a899}
[data-render-target]{border-color:#33363c;background:#1d2024}
body [data-hx-jev-type=no-behavior-change]{color:#b9c0ca!important}
}
@media(max-width:640px){
:where(body){padding-bottom:calc(112px + env(safe-area-inset-bottom))}
article.spec{padding:24px 16px}
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
.hx-focus-error{position:fixed;top:calc(12px + env(safe-area-inset-top));left:50%;transform:translateX(-50%);max-width:calc(100vw - 24px);box-sizing:border-box;padding:8px 12px;border-radius:8px;background:#8b1a1a;color:#fff;font:600 12px system-ui;z-index:970;box-shadow:0 6px 20px rgba(30,30,40,.25)}
@media(prefers-color-scheme:dark){
body.hx-focus-active [data-hx-focus=unchanged]:not(:has([data-hx-focus=changed])):not([data-hx-focus=unchanged]:not(:has([data-hx-focus=changed])) *):not(tr):not(td):not(th):not(script):not(style)::after{background:rgba(0,0,0,calc(.6*var(--hx-veil,1)))}
body.hx-focus-active tr[data-hx-focus=unchanged]:not([data-hx-focus=unchanged]:not(:has([data-hx-focus=changed])) *) > :is(td,th)::after{background:rgba(0,0,0,calc(.6*var(--hx-veil,1)))}
}
`;
const CSS = `
/* ui tokens */
:root{--ui-font:'Inter Variable',Inter,system-ui,sans-serif;--ui-mono:ui-monospace,SFMono-Regular,Menlo,monospace;--ui-text-xs:12px;--ui-text-sm:14px;--ui-text-md:16px;--ui-text-lg:20px;--ui-page:#ffffff;--ui-surface:#ffffff;--ui-ink:#333333;--ui-muted:#525252;--ui-border:#dfdfdf;--ui-focus:#262626;--ui-link:#333333;--ui-action:#262626;--ui-pass:#005c32;--ui-pass-soft:#e6efea;--ui-fail:#a5000f;--ui-fail-soft:#f6e6e7;--ui-attention:#b0540e;--ui-attention-soft:#faf4ef;--ui-draft:#b0540e;--ui-draft-soft:#faf4ef;--ui-ack:#1d4ed8;--ui-ack-soft:#eff3ff;--ui-resolved:#005c32;--ui-resolved-soft:#e6efea;--ui-error:#a5000f;--ui-marker:#767b85;--ui-marker-important:#d1242f;--ui-marker-pass:#1a7f37;--ui-pin-ring:rgba(176,84,14,0.25);--ui-shadow:rgba(51,51,51,0.14);--ui-radius:8px;--ui-radius-sm:6px;--ui-radius-pill:999px;--ui-space-1:4px;--ui-space-2:8px;--ui-space-3:12px;--ui-space-4:16px;--ui-space-5:24px;--ui-space-6:32px}
/* range bar */
.hx-range-bar{box-sizing:border-box;max-width:720px;margin:var(--ui-space-3) auto 0;padding:var(--ui-space-2) 10px;border:1px solid rgba(29,78,216,0.3);border-radius:var(--ui-radius);background:var(--ui-ack-soft);color:var(--ui-ink);font:var(--ui-text-sm)/1.4 var(--ui-font)}
.hx-range-row{display:flex;align-items:center;gap:10px;min-width:0}
.hx-range-copy{flex:1 1 auto;min-width:0;margin:0;overflow-wrap:anywhere}
.hx-range-id{font-weight:600;font-family:var(--ui-mono)}
.hx-range-change{flex:0 0 auto;min-height:var(--ui-space-6);padding:var(--ui-space-2) var(--ui-space-3);border:1px solid var(--ui-border);border-radius:var(--ui-radius-sm);background:var(--ui-surface);color:var(--ui-ink);font:600 var(--ui-text-xs) var(--ui-font);cursor:pointer}
.hx-range-change:hover{background:var(--ui-page)}
.hx-range-change:focus-visible,.hx-range-commit:focus-visible,.hx-range-form input:focus-visible,.hx-range-form button:focus-visible{outline:2px solid var(--ui-focus);outline-offset:2px}
.hx-range-picker{margin-top:var(--ui-space-2);padding-top:var(--ui-space-2);border-top:1px solid rgba(29,78,216,0.3)}
.hx-range-commits{display:grid;gap:3px;max-height:270px;overflow:auto}
.hx-range-commit{display:grid;grid-template-columns:5.5em 6.5em minmax(0,1fr);gap:var(--ui-space-2);align-items:baseline;width:100%;padding:var(--ui-space-1) var(--ui-space-2);border:1px solid transparent;border-radius:var(--ui-space-1);background:transparent;color:var(--ui-ink);text-align:left;font:var(--ui-text-xs)/1.35 var(--ui-font);cursor:pointer}
.hx-range-commit:hover,.hx-range-commit[data-selected=true]{border-color:var(--ui-resolved);background:var(--ui-resolved-soft)}
.hx-range-commit strong{font:600 var(--ui-text-xs) var(--ui-mono)}
.hx-range-commit time{color:var(--ui-muted);font-variant-numeric:tabular-nums}
.hx-range-commit span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hx-range-empty{margin:0;padding:var(--ui-space-1) var(--ui-space-2);color:var(--ui-muted)}
.hx-range-form{margin-top:var(--ui-space-2);padding-top:var(--ui-space-2);border-top:1px solid rgba(29,78,216,0.3)}
.hx-range-form label{display:block;margin-bottom:var(--ui-space-1);font-size:11px;font-weight:600;color:var(--ui-ink)}
.hx-range-form>div{display:flex;gap:var(--ui-space-1)}
.hx-range-form input{box-sizing:border-box;min-width:0;flex:1 1 auto;padding:7px var(--ui-space-2);border:1px solid var(--ui-border);border-radius:var(--ui-radius-sm);background:var(--ui-surface);color:var(--ui-ink);font:var(--ui-text-sm) var(--ui-mono)}
.hx-range-form button{flex:0 0 auto;padding:7px var(--ui-space-3);border:1px solid var(--ui-ink);border-radius:var(--ui-radius-sm);background:var(--ui-ink);color:var(--ui-page);font:600 var(--ui-text-xs) var(--ui-font);cursor:pointer}
.hx-range-form button:disabled{cursor:wait;opacity:.5}
.hx-range-error{margin:var(--ui-space-1) 0 0;color:var(--ui-error);font-size:var(--ui-text-xs)}
@media(prefers-color-scheme:dark){
:root{--ui-page:#1a1a1a;--ui-surface:#242424;--ui-ink:#e0e0e0;--ui-muted:#999999;--ui-border:#3a3a3a;--ui-focus:#cccccc;--ui-link:#e0e0e0;--ui-action:#cccccc;--ui-pass:#3daa6e;--ui-pass-soft:#1a2e22;--ui-fail:#ffb4ab;--ui-fail-soft:#2e1a1c;--ui-attention:#e5873a;--ui-attention-soft:#2e2218;--ui-draft:#e5873a;--ui-draft-soft:#2e2218;--ui-ack:#6b8aed;--ui-ack-soft:#1a2040;--ui-resolved:#3daa6e;--ui-resolved-soft:#1a2e22;--ui-error:#ffb4ab;--ui-pin-ring:rgba(229,135,58,0.25);--ui-shadow:rgba(0,0,0,0.4)}
}
@media(max-width:640px){
.hx-range-bar{margin:var(--ui-space-2) var(--ui-space-4) 0;padding:var(--ui-space-2)}
.hx-range-row{align-items:flex-start;flex-wrap:wrap;gap:var(--ui-space-1)}
.hx-range-copy{flex:1 1 100%}
.hx-range-change{min-height:44px}
.hx-range-picker{margin-top:var(--ui-space-1)}
.hx-range-commits{max-height:none}
.hx-range-commit{grid-template-columns:5.5em 6.5em minmax(0,1fr);padding:var(--ui-space-2) var(--ui-space-1);min-height:44px}
.hx-range-form input,.hx-range-form button{min-height:44px}
.hx-range-form input{font-size:var(--ui-text-md)}
}
@media(max-width:800px){
.hx-range-bar{margin-top:64px}
}
/* toolbar */
.hx-toolbar{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);display:flex;gap:var(--ui-space-1);align-items:center;background:var(--ui-surface);border:1px solid var(--ui-border);border-radius:var(--ui-radius);box-shadow:0 8px 28px var(--ui-shadow);padding:var(--ui-space-1);z-index:900;font:var(--ui-text-sm) var(--ui-font)}
.hx-toolbar button{font:600 var(--ui-text-xs) var(--ui-font);border:none;background:transparent;border-radius:var(--ui-radius-sm);padding:var(--ui-space-2) var(--ui-space-3);cursor:pointer;color:var(--ui-ink)}
.hx-toolbar button:disabled{cursor:default;opacity:.45}
.hx-mobile-handoff,.hx-mobile-next-tbd{display:none}
.hx-toolbar button[aria-pressed=true]{background:var(--ui-draft-soft);color:var(--ui-draft)}
.hx-toolbar .hx-status{color:var(--ui-muted);font-size:var(--ui-text-xs);padding:0 10px}
.hx-toolbar .hx-offline{font-size:var(--ui-text-xs);font-weight:600;padding:0 10px}
/* index link */
.hx-service-index-link{position:fixed;top:var(--ui-space-3);left:var(--ui-space-3);z-index:1000;display:inline-flex;align-items:center;min-height:44px;box-sizing:border-box;padding:var(--ui-space-2) var(--ui-space-3);border:1px solid var(--ui-border);border-radius:var(--ui-radius);background:rgba(255,255,255,.96);box-shadow:0 5px 18px var(--ui-shadow);color:var(--ui-resolved);font:600 var(--ui-text-xs)/1 var(--ui-font);text-decoration:none;backdrop-filter:blur(8px)}
.hx-service-index-link:hover{background:var(--ui-page);border-color:var(--ui-muted);color:var(--ui-resolved)}
.hx-service-index-link:focus-visible{outline:2px solid var(--ui-focus);outline-offset:2px}
/* panel */
.hx-panel{position:fixed;top:0;right:0;width:330px;height:100vh;background:var(--ui-page);color:var(--ui-ink);border-left:1px solid var(--ui-border);z-index:800;display:none;flex-direction:column;font:var(--ui-text-sm) var(--ui-font);box-shadow:none}
.hx-panel.open{display:flex;box-shadow:-8px 0 30px var(--ui-shadow)}
body.hx-panel-open{padding-right:330px}
.hx-panel-head{position:relative;min-height:44px;padding:14px var(--ui-space-4) 14px 52px;box-sizing:border-box;border-bottom:1px solid var(--ui-border);font-weight:600}
.hx-panel-head .hx-sub{font-weight:400;font-size:11px;color:var(--ui-muted)}
.hx-panel-toggle{position:absolute;top:var(--ui-space-2);left:7px;width:var(--ui-space-6);height:var(--ui-space-6);border:1px solid var(--ui-border);background:var(--ui-surface);border-radius:var(--ui-radius-sm);color:var(--ui-muted);cursor:pointer;font:var(--ui-text-lg)/1 var(--ui-font);display:grid;place-items:center;padding:0}
.hx-panel-toggle:hover{background:var(--ui-page);color:var(--ui-ink)}
.hx-panel-gear{position:absolute;top:var(--ui-space-2);right:10px;width:var(--ui-space-6);height:var(--ui-space-6);border:1px solid var(--ui-border);background:var(--ui-surface);border-radius:var(--ui-radius-sm);color:var(--ui-muted);cursor:pointer;font:14px/1 var(--ui-font);display:grid;place-items:center;padding:0}
.hx-panel-gear:hover{background:var(--ui-page);color:var(--ui-ink)}
.hx-panel-gear[aria-expanded=true]{background:var(--ui-draft-soft);color:var(--ui-draft);border-color:var(--ui-draft)}
.hx-settings{padding:var(--ui-space-3) var(--ui-space-4);border-bottom:1px solid var(--ui-border);background:var(--ui-surface)}
.hx-set-row{display:grid;grid-template-columns:1fr auto;gap:2px 10px;align-items:center;font:600 var(--ui-text-xs) var(--ui-font);color:var(--ui-ink)}
.hx-set-row input[type=range]{grid-column:1/-1;width:100%;margin:var(--ui-space-1) 0 0;accent-color:var(--ui-resolved);min-height:24px;touch-action:manipulation}
.hx-set-val{font:600 var(--ui-text-xs) var(--ui-mono);color:var(--ui-muted)}
.hx-set-note{margin:7px 0 0;font:400 11px/1.45 var(--ui-font);color:var(--ui-muted)}
.hx-panel-content{display:flex;flex:1;min-height:0;flex-direction:column}
/* dock */
.hx-thread-dock{position:fixed;top:var(--ui-space-3);right:var(--ui-space-3);z-index:850;display:flex;flex-direction:column;gap:var(--ui-space-1);padding:var(--ui-space-1);background:rgba(255,255,255,.94);border:1px solid var(--ui-border);border-radius:11px;box-shadow:0 5px 18px var(--ui-shadow);font:var(--ui-text-xs) var(--ui-font);transition:opacity .15s,transform .15s;backdrop-filter:blur(8px)}
body.hx-panel-open .hx-thread-dock{opacity:0;transform:translateX(10px);pointer-events:none}
.hx-dock-open,.hx-dock-thread{position:relative;width:var(--ui-space-6);height:var(--ui-space-6);box-sizing:border-box;border:1px solid var(--ui-border);background:var(--ui-surface);border-radius:var(--ui-radius-sm);color:var(--ui-muted);cursor:pointer;display:grid;place-items:center;padding:0}
.hx-dock-open:hover,.hx-dock-thread:hover{background:var(--ui-page);border-color:var(--ui-muted);color:var(--ui-ink)}
.hx-dock-open svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.hx-unread-badge{position:absolute;top:-6px;right:-6px;min-width:17px;height:17px;box-sizing:border-box;border:2px solid var(--ui-page);border-radius:var(--ui-radius-pill);padding:0 3px;background:var(--ui-ack);color:var(--ui-page);display:grid;place-items:center;font:700 9px/1 var(--ui-font);box-shadow:0 1px 4px var(--ui-shadow)}
.hx-unread-badge[hidden]{display:none}
.hx-dock-threads{display:flex;flex-direction:column;gap:var(--ui-space-1);max-height:calc(100vh - 76px);overflow-y:auto;scrollbar-width:none}
.hx-dock-threads:not(:empty){border-top:1px solid var(--ui-border);padding-top:var(--ui-space-1)}
.hx-dock-threads::-webkit-scrollbar{display:none}
.hx-dock-thread{font:600 11px/1 var(--ui-mono)}
.hx-dock-thread[data-s=draft],.hx-dock-thread[data-s=pending]{border-color:var(--ui-draft);color:var(--ui-draft);background:var(--ui-draft-soft)}
.hx-dock-thread[data-s=acknowledged]{border-color:var(--ui-ack);color:var(--ui-ack);background:var(--ui-ack-soft)}
.hx-dock-thread[data-s=resolved]{border-color:var(--ui-resolved);color:var(--ui-resolved);background:var(--ui-resolved-soft)}
.hx-dock-thread.active{box-shadow:0 0 0 2px var(--ui-pin-ring)}
/* threads */
.hx-threads{flex:1;overflow-y:auto;padding:10px var(--ui-space-3);display:flex;flex-direction:column;gap:var(--ui-space-2)}
.hx-thread{background:var(--ui-surface);border:1px solid var(--ui-border);border-radius:var(--ui-radius);padding:10px var(--ui-space-3);cursor:pointer}
.hx-thread.resolved-collapsed{padding:var(--ui-space-2) 10px}
.hx-thread.active{border-color:var(--ui-draft);box-shadow:0 0 0 1px var(--ui-draft)}
.hx-thread-summary{display:flex;align-items:center;gap:var(--ui-space-1);min-width:0}
.hx-anchor{flex:1;font-family:var(--ui-mono);font-size:var(--ui-text-xs);color:var(--ui-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hx-pill{font-size:var(--ui-text-xs);font-weight:600;text-transform:uppercase;letter-spacing:.05em;border-radius:var(--ui-radius-pill);padding:2px var(--ui-space-2)}
.hx-disclosure{border:0;background:transparent;color:var(--ui-muted);border-radius:var(--ui-radius-sm);padding:0 2px;cursor:pointer;font:15px/1 var(--ui-font)}
.hx-disclosure:hover{background:var(--ui-page);color:var(--ui-ink)}
.hx-thread-preview{margin-top:5px;color:var(--ui-muted);font-size:var(--ui-text-xs);line-height:1.3;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hx-pill[data-s=draft],.hx-pill[data-s=pending]{color:var(--ui-draft);background:var(--ui-draft-soft)}
.hx-pill[data-s=acknowledged]{color:var(--ui-ack);background:var(--ui-ack-soft)}
.hx-pill[data-s=resolved]{color:var(--ui-resolved);background:var(--ui-resolved-soft)}
.hx-msg{margin-top:7px;font-size:var(--ui-text-sm);line-height:1.45}
.hx-who{font-size:10px;font-weight:600;color:var(--ui-muted);text-transform:uppercase}
.hx-history{margin:4px 0 0;padding-left:18px;font-size:var(--ui-text-xs);color:var(--ui-muted)}
.hx-quote{display:block;border-left:2px solid var(--ui-border);padding-left:7px;color:var(--ui-muted);font-style:italic;font-size:var(--ui-text-xs);margin:2px 0}
.hx-msg-actions{display:flex;gap:var(--ui-space-1);margin-top:3px}
.hx-msg-actions .hx-btn{font-size:var(--ui-text-xs);padding:3px var(--ui-space-2);margin-top:2px}
/* composer */
.hx-composer textarea{width:100%;min-height:56px;font:var(--ui-text-sm) var(--ui-font);border:1px solid var(--ui-border);border-radius:var(--ui-radius-sm);padding:var(--ui-space-1) var(--ui-space-2);box-sizing:border-box;margin-top:var(--ui-space-1);background:var(--ui-surface);color:var(--ui-ink)}
/* buttons */
.hx-btn{font:600 var(--ui-text-xs) var(--ui-font);border:1px solid var(--ui-border);background:var(--ui-surface);border-radius:var(--ui-radius-sm);padding:var(--ui-space-2) var(--ui-space-3);cursor:pointer;margin:var(--ui-space-1) var(--ui-space-1) 0 0;color:var(--ui-ink);min-height:var(--ui-space-6)}
.hx-btn.pri{background:var(--ui-ink);color:var(--ui-page);border-color:var(--ui-ink)}
/* handoff bar */
.hx-handoff{border-top:1px solid var(--ui-border);padding:var(--ui-space-3) var(--ui-space-4);display:flex;justify-content:space-between;align-items:center}
.hx-handoff .hx-note{font-size:var(--ui-text-xs);color:var(--ui-muted)}
.hx-handoff .hx-next-tbd{margin-left:auto}
/* pins */
.hx-pin{position:absolute;z-index:700;width:24px;height:24px;border-radius:50% 50% 50% 4px;border:none;cursor:pointer;font:600 var(--ui-text-xs) var(--ui-font);color:#fff;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 8px var(--ui-shadow)}
.hx-pin[data-s=draft],.hx-pin[data-s=pending]{background:var(--ui-draft)}
.hx-pin[data-s=acknowledged]{background:var(--ui-ack)}
.hx-pin[data-s=resolved]{background:var(--ui-surface);color:var(--ui-resolved);border:2px solid var(--ui-resolved)}
.hx-pin.active{box-shadow:0 0 0 3px var(--ui-pin-ring),0 2px 8px var(--ui-shadow)}
[data-anchor]{position:relative}
/* A block with a marker and a corner pin reserves a right column: marker on the first line, pin below it. */
[data-anchor]:has(> .hx-pin[data-column]){padding-right:30px;min-height:calc(.5lh + 42px)}
[data-anchor]:has(> .hx-pin[data-column]) > .hx-jev-marker,.hx-pin[data-column]{left:auto;right:0}
tr[data-anchor]:has(> .hx-pin[data-column]) > :has(> .hx-jev-marker){box-sizing:content-box;padding-right:30px;height:calc(.5lh + 42px)}
body.hx-comment [data-anchor]{cursor:copy}
body.hx-comment [data-anchor]:hover:not(:has(:is(h1,h2,h3,h4,h5,h6,p,li,ul,ol,table,tr,td,th,blockquote,pre,code,nav,figcaption,button,input,select,textarea,label,a,output,summary,svg,[data-render-target]):hover)){outline:2px dashed var(--ui-draft);outline-offset:6px}
body.hx-comment [data-anchor] :is(h1,h2,h3,h4,h5,h6,p,li,td,th,blockquote,pre,code,nav,figcaption,button,label,a,output,summary):hover{outline:1.5px dashed var(--ui-draft);outline-offset:4px;border-radius:2px}
body.hx-comment [data-anchor] svg, body.hx-comment [data-anchor] svg *{cursor:copy}
body.hx-comment [data-anchor] :is(button,input,select,textarea,label,a,summary){cursor:copy}
[data-render-target]{position:relative}
.hx-ring{position:absolute;border:2px dashed var(--ui-draft);border-radius:var(--ui-space-1);pointer-events:none;z-index:650}
.hx-thread-ring{position:fixed;border:2px solid var(--ui-draft);border-radius:5px;pointer-events:none;z-index:750;box-shadow:0 0 0 3px rgba(176,84,14,.18);transition:left .12s,top .12s,width .12s,height .12s}
body.hx-comment [data-render-target]:hover{border:1.5px dashed var(--ui-draft)}
body.hx-comment [data-render-target] canvas{cursor:copy!important}
.hx-tbd-open{outline:2px solid var(--ui-draft);outline-offset:var(--ui-space-1)}
/* badge */
.hx-badge{font:600 var(--ui-text-xs) var(--ui-font);text-transform:uppercase;letter-spacing:.04em;color:var(--ui-resolved);background:var(--ui-resolved-soft);border-radius:var(--ui-radius-pill);padding:2px 7px;margin-left:var(--ui-space-2);vertical-align:middle}
/* jev note */
.hx-jev-note{box-sizing:border-box;max-width:720px;margin:var(--ui-space-3) auto 0;padding:var(--ui-space-1) 10px;border:1px solid rgba(176,84,14,0.4);border-radius:var(--ui-radius);background:var(--ui-draft-soft);color:var(--ui-draft);font:600 var(--ui-text-xs)/1.35 var(--ui-font)}
[data-hx-jev-type=no-behavior-change]{color:#586069!important}
[data-hx-jev-type=no-behavior-change] :is(h1,h2,h3,h4,h5,h6,p,li,td,th,blockquote,code,strong,em,a){color:inherit!important}
/* jev markers */
.hx-jev-marker{position:absolute;top:.35em;left:calc(100% + 10px);z-index:640;box-sizing:border-box;width:10px;height:10px;margin:0;padding:0;border:0;border-radius:50%;background:var(--ui-marker);color:#ffffff;cursor:pointer;display:grid;place-items:center;font:800 10px/1 var(--ui-font)}
.hx-jev-marker::before{content:"";position:absolute;inset:-10px 0 -10px -16px}
.hx-jev-marker[data-attention=true]{width:14px;height:14px;background:var(--ui-marker-important)}
.hx-jev-marker[data-attention=true]::after{content:"!"}
.hx-jev-marker[data-passed=true]{width:14px;height:14px;background:var(--ui-marker-pass)}
.hx-jev-marker[data-warning=true]{background:#e5534b}
.hx-jev-marker[data-passed=true]::after{content:"\\2713"}
.hx-jev-marker[data-inset=true]{left:auto;right:0}
.hx-jev-marker:hover,.hx-jev-marker[aria-expanded=true]{box-shadow:0 0 0 3px rgba(29,78,216,.22)}
.hx-jev-marker:focus-visible{outline:2px solid var(--ui-focus);outline-offset:2px}
.hx-jev-marker[data-pending=true]{background:transparent;border:2px solid var(--ui-marker);border-right-color:transparent;animation:hx-jev-spin 1s linear infinite}
@keyframes hx-jev-spin{to{transform:rotate(360deg)}}
/* jev offer */
.hx-jev-offer{display:flex;align-items:center;flex-wrap:wrap;gap:var(--ui-space-2)}
.hx-jev-offer span{flex:1 1 auto}
.hx-jev-offer .hx-btn{margin:0;font-size:var(--ui-text-xs);padding:3px var(--ui-space-2)}
.hx-jev-offer-dismiss{margin:0;padding:0 var(--ui-space-1);border:0;background:none;color:inherit;font:600 14px/1 var(--ui-font);cursor:pointer}
.hx-jev-offer-detail{width:100%;font:var(--ui-text-xs)/1.5 var(--ui-font);padding-left:var(--ui-space-1)}
.hx-jev-offer-detail[hidden]{display:none}
.hx-jev-offer-spec{margin:2px 0}
.hx-jev-offer-spec a{color:var(--ui-link);text-decoration:underline;text-underline-offset:2px}
.hx-jev-offer-rule{margin-left:var(--ui-space-2);font-size:var(--ui-text-xs);color:var(--ui-muted)}
/* jev popover */
.hx-jev-pop{position:fixed;z-index:880;box-sizing:border-box;width:max-content;min-width:180px;max-width:min(340px,calc(100vw - 16px));max-height:calc(100vh - 16px);overflow:auto;padding:var(--ui-space-2) 10px;border:1px solid var(--ui-ink);border-radius:var(--ui-radius);background:var(--ui-surface);color:var(--ui-ink);box-shadow:0 8px 28px var(--ui-shadow);font:var(--ui-text-sm)/1.4 var(--ui-font)}
.hx-jev-pop[hidden]{display:none}
.hx-jev-pop-notes{margin:0;padding:0;list-style:none;display:grid;gap:var(--ui-space-1)}
.hx-jev-pop-note{display:grid;gap:var(--ui-space-1)}
.hx-jev-pop-text{font-weight:600;color:var(--ui-ink);overflow-wrap:anywhere}
.hx-jev-pop-note[data-attention=true] .hx-jev-pop-text{color:var(--ui-fail)}
.hx-jev-pop-note[data-group=neutral] .hx-jev-pop-text{font-weight:600;color:var(--ui-muted)}
a.hx-jev-pop-text{text-decoration:underline;text-underline-offset:2px}
.hx-jev-pop-quote{color:var(--ui-ink);overflow-wrap:anywhere}
.hx-jev-pop-sentence{display:none;font-size:var(--ui-text-xs);color:var(--ui-muted);overflow-wrap:anywhere}
.hx-jev-pop[data-measure] .hx-jev-pop-sentence,.hx-jev-pop-note:hover .hx-jev-pop-sentence,.hx-jev-pop-note:focus-within .hx-jev-pop-sentence{display:block}
.hx-jev-pop-meta{display:flex;flex-wrap:wrap;align-items:baseline;gap:var(--ui-space-1) 10px;font-size:var(--ui-text-xs);color:var(--ui-muted);overflow-wrap:anywhere}
.hx-jev-pop-link{color:var(--ui-ack);text-decoration:underline;text-underline-offset:2px}
.hx-jev-pop-diff{font-size:var(--ui-text-xs);color:var(--ui-ink);overflow-wrap:anywhere}
.hx-jev-pop-diff del{text-decoration:none;background:var(--ui-fail-soft);border-radius:2px;padding:0 1px}
.hx-jev-pop-diff ins{text-decoration:none;background:var(--ui-pass-soft);border-radius:2px;padding:0 1px}
.hx-jev-pop-diff-legend{font-size:10px;color:var(--ui-muted);margin-bottom:2px}
.hx-jev-pop-diff-legend del{text-decoration:none;background:var(--ui-fail-soft);border-radius:2px;padding:0 2px}
.hx-jev-pop-diff-legend ins{text-decoration:none;background:var(--ui-pass-soft);border-radius:2px;padding:0 2px}
.hx-jev-pop-actions{display:flex;flex-wrap:wrap;gap:var(--ui-space-1)}
.hx-jev-pop-actions .hx-btn{margin:0;font-size:var(--ui-text-xs);padding:var(--ui-space-1) var(--ui-space-2);border-color:var(--ui-ack);background:var(--ui-surface);color:var(--ui-ack)}
.hx-jev-pop-batch{display:inline-flex;align-items:center;gap:var(--ui-space-1);white-space:nowrap}
.hx-jev-pop-more{margin:0;padding:0 2px;border:0;background:none;font:inherit;font-size:var(--ui-text-xs);color:var(--ui-ack);text-decoration:underline;text-underline-offset:2px;cursor:pointer;align-self:center}
/* jev thread label */
.hx-jev-thread-label{flex:0 0 auto;color:var(--ui-resolved);background:var(--ui-resolved-soft);border-radius:var(--ui-radius-pill);padding:2px var(--ui-space-1);font-size:9px;font-weight:600;white-space:nowrap}
.hx-jev-thread-label[data-state=unsure]{color:var(--ui-draft);background:var(--ui-draft-soft)}
.hx-jev-thread-label[data-state=unavailable]{color:var(--ui-error);background:var(--ui-fail-soft)}
/* orphan hint */
.hx-orphan-hint{display:flex;align-items:center;flex-wrap:wrap;gap:var(--ui-space-1);margin:7px 0;padding:7px var(--ui-space-2);border-left:3px solid var(--ui-draft);background:var(--ui-draft-soft);color:var(--ui-draft);font-size:var(--ui-text-xs);line-height:1.35}
.hx-orphan-hint>span{flex:1 1 100%}
.hx-place-notice{margin:7px 0;padding:7px var(--ui-space-2);border-left:3px solid var(--ui-draft);background:var(--ui-draft-soft);color:var(--ui-draft);font-size:var(--ui-text-xs);line-height:1.35}
.hx-orphan-hint .hx-btn{margin:0;font-size:var(--ui-text-xs);padding:var(--ui-space-1) var(--ui-space-2)}
.hx-pin-jev{position:absolute;left:calc(100% + 4px);top:50%;transform:translateY(-50%);width:max-content;max-width:120px;color:var(--ui-resolved);background:var(--ui-resolved-soft);border:1px solid var(--ui-resolved);border-radius:var(--ui-radius-pill);padding:2px var(--ui-space-1);font:600 9px/1.1 var(--ui-font);white-space:nowrap;pointer-events:none}
/* banner */
.hx-banner{position:fixed;top:0;left:0;right:0;background:var(--ui-pass);color:var(--ui-page);font:600 var(--ui-text-sm) var(--ui-font);padding:var(--ui-space-2) var(--ui-space-4);z-index:950;display:flex;gap:14px;align-items:center;justify-content:center}
/* toast */
.hx-toast{position:fixed;bottom:76px;left:50%;transform:translateX(-50%);background:var(--ui-ink);color:var(--ui-page);font:600 var(--ui-text-xs) var(--ui-font);border-radius:var(--ui-radius);padding:9px var(--ui-space-4);box-shadow:0 8px 28px var(--ui-shadow);z-index:960;opacity:0;transition:opacity .25s;pointer-events:none}
.hx-toast.show{opacity:1}
.hx-banner button{font:inherit;border:1px solid var(--ui-page);background:transparent;color:var(--ui-page);border-radius:var(--ui-radius-sm);padding:3px var(--ui-space-3);cursor:pointer}
/* dark mode backdrop overrides */
@media(prefers-color-scheme:dark){
.hx-thread-dock{background:rgba(26,26,26,.94)}
.hx-service-index-link{background:rgba(26,26,26,.96)}
}
/* mobile */
@media(max-width:640px){
.hx-toolbar{left:var(--ui-space-3);right:var(--ui-space-3);bottom:calc(10px + env(safe-area-inset-bottom));transform:none;max-width:none;display:flex;flex-wrap:wrap;justify-content:stretch;gap:var(--ui-space-1);padding:var(--ui-space-1)}
.hx-toolbar button{min-height:44px;flex:1 1 auto;padding:var(--ui-space-2) var(--ui-space-3);touch-action:manipulation}
.hx-mobile-handoff,.hx-mobile-next-tbd:not([hidden]){display:block}
.hx-toolbar .hx-status{flex:1 0 100%;min-width:0;padding:2px var(--ui-space-2) var(--ui-space-1);overflow:hidden;text-align:center;text-overflow:ellipsis;white-space:nowrap}
.hx-panel{width:100vw;height:100dvh;max-height:100dvh;border-left:0;box-sizing:border-box;padding-bottom:calc(var(--hx-dock-space,0px) + 10px + env(safe-area-inset-bottom))}
body.hx-panel-open{padding-right:0;overflow:hidden}
.hx-panel-head{min-height:56px;padding:18px var(--ui-space-4) 14px 60px}
.hx-panel-toggle{top:var(--ui-space-1);left:var(--ui-space-1);width:44px;height:44px;touch-action:manipulation}
.hx-panel-gear{top:var(--ui-space-1);right:var(--ui-space-2);width:44px;height:44px;touch-action:manipulation}
.hx-thread-dock{top:calc(var(--ui-space-2) + env(safe-area-inset-top));right:var(--ui-space-2);padding:var(--ui-space-1)}
.hx-service-index-link{top:calc(var(--ui-space-2) + env(safe-area-inset-top));left:var(--ui-space-2);max-width:calc(100vw - 68px);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
body.hx-panel-open .hx-service-index-link{display:none}
.hx-dock-open,.hx-dock-thread{width:44px;height:44px;touch-action:manipulation}
.hx-dock-threads{max-height:calc(100dvh - 68px)}
.hx-threads{padding:var(--ui-space-3);overscroll-behavior:contain}
.hx-thread{padding:var(--ui-space-3)}
.hx-disclosure{min-width:44px;min-height:44px;touch-action:manipulation}
.hx-composer textarea{min-height:120px;font-size:var(--ui-text-md);padding:10px var(--ui-space-3)}
.hx-btn,.hx-msg-actions .hx-btn{min-height:44px;padding:var(--ui-space-2) var(--ui-space-3);touch-action:manipulation}
.hx-handoff{gap:var(--ui-space-2);flex-wrap:wrap;padding:10px var(--ui-space-3) calc(10px + env(safe-area-inset-bottom))}
.hx-handoff .hx-note{flex:1 1 auto}
.hx-handoff .hx-btn{flex:1 1 auto;margin:0}
.hx-pin{width:44px;height:44px;font-size:var(--ui-text-xs);touch-action:manipulation}
[data-anchor]:has(> .hx-pin[data-column]){padding-right:48px;min-height:calc(.5lh + 66px)}
tr[data-anchor]:has(> .hx-pin[data-column]) > :has(> .hx-jev-marker){padding-right:48px;height:calc(.5lh + 66px)}
.hx-pin-jev{left:auto;right:calc(100% + 4px);max-width:110px;text-align:right}
.hx-jev-note{margin:var(--ui-space-2) var(--ui-space-4) 0}
.hx-jev-marker::before{inset:-14px 0 -14px -28px}
.hx-jev-pop{left:var(--ui-space-3);right:var(--ui-space-3);top:auto;bottom:calc(10px + var(--hx-dock-space,64px) + var(--ui-space-2) + env(safe-area-inset-bottom));width:auto;max-width:none;max-height:40dvh;padding:10px var(--ui-space-3)}
.hx-jev-pop-actions .hx-btn,.hx-jev-pop-more{min-height:44px}
.hx-banner{align-items:flex-start;flex-wrap:wrap;padding:calc(var(--ui-space-2) + env(safe-area-inset-top)) var(--ui-space-3) var(--ui-space-2);text-align:center}
.hx-banner button{min-height:44px;padding:var(--ui-space-2) var(--ui-space-3);touch-action:manipulation}
.hx-toast{bottom:calc(112px + env(safe-area-inset-bottom));max-width:calc(100vw - 24px);box-sizing:border-box;text-align:center}
}
@media(prefers-reduced-motion:reduce){
.hx-thread-dock,.hx-thread-ring,.hx-toast{transition:none}
.hx-jev-marker[data-pending=true]{animation:none}
}`;

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

  const style = document.createElement('style');
  const sharedDocumentStyle = document.querySelector('link[rel~="stylesheet"][href*=".style/spec.css"]');
  style.textContent = (EMBED_REVIEW_DIR || sharedDocumentStyle ? '' : DOC_CSS) + FOCUS_CSS + CSS;
  document.head.appendChild(style);

  mountRangeBar();

  if (location.protocol === 'http:' || location.protocol === 'https:') {
    const indexLink = document.createElement('a');
    indexLink.className = 'hx-service-index-link';
    indexLink.href = new URL('/', location.href).href;
    indexLink.textContent = 'Back to Spec Chat index';
    indexLink.setAttribute('aria-label', 'Back to Spec Chat index');
    indexLink.dataset.specChatNavigation = 'index';
    document.body.insertBefore(indexLink, document.body.firstChild);
  }

  const bar = document.createElement('div');
  bar.className = 'hx-toolbar';
  bar.innerHTML = '<button id="hx-mode" aria-pressed="false">✛ Comment (C)</button><button class="hx-mobile-next-tbd" id="hx-mobile-next-tbd" type="button" hidden>Next TBD</button><button class="hx-mobile-handoff" id="hx-mobile-handoff" type="button" disabled>Hand off</button><button id="hx-connect" hidden>Connect review folder</button><button id="hx-repick" hidden>Choose different folder</button><span class="hx-offline" id="hx-offline" role="status" hidden></span><span class="hx-status" id="hx-status">starting…</span>';
  document.body.appendChild(bar);
  // The narrow sidebar ends above the fixed dock, so its last content stays reachable.
  new ResizeObserver(() => document.documentElement.style.setProperty('--hx-dock-space', bar.offsetHeight + 'px')).observe(bar);

  const dock = document.createElement('nav');
  dock.className = 'hx-thread-dock';
  dock.setAttribute('aria-label', 'Review conversations');
  dock.innerHTML = '<button class="hx-dock-open" id="hx-dock-open" type="button" aria-label="Open review sidebar"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5.5h14v10H10l-5 3v-13Z"></path><path d="M8 9h8M8 12h5"></path></svg><span class="hx-unread-badge" id="hx-unread-badge" aria-hidden="true" hidden></span></button><div class="hx-dock-threads" id="hx-dock-threads"></div>';
  document.body.appendChild(dock);

  const panel = document.createElement('aside');
  panel.className = 'hx-panel';
  panel.setAttribute('aria-label', 'Review sidebar');
  panel.innerHTML = '<div class="hx-panel-head">Review <span class="hx-sub" id="hx-agent"></span><button class="hx-panel-toggle" id="hx-panel-toggle" type="button" aria-expanded="false" aria-label="Open review sidebar">‹</button><button class="hx-panel-gear" id="hx-panel-gear" type="button" aria-expanded="false" aria-controls="hx-settings" aria-label="Review display settings" title="Display settings">⚙</button></div><div class="hx-panel-content" id="hx-panel-content" aria-hidden="true" inert><div class="hx-settings" id="hx-settings" hidden><label class="hx-set-row" for="hx-veil">Diff visibility<span class="hx-set-val" id="hx-veil-val">100%</span><input type="range" id="hx-veil" min="0" max="100" step="5" value="100"></label><p class="hx-set-note">How strongly unchanged blocks are dimmed and blurred. At 0 the whole spec reads at normal clarity.</p></div><div class="hx-threads" id="hx-threads"></div><div class="hx-handoff"><span class="hx-note" id="hx-drafts">0 drafts</span><button class="hx-btn hx-next-tbd" id="hx-next-tbd" type="button" hidden>Next TBD</button><button class="hx-btn pri" id="hx-handoff">Hand off to agent →</button></div></div>';
  document.body.appendChild(panel);

  document.getElementById('hx-mode').addEventListener('click', () => setCommentMode(!state.commentMode));
  document.getElementById('hx-mobile-handoff').addEventListener('click', handoff);
  document.getElementById('hx-mobile-next-tbd').addEventListener('click', nextTbd);
  document.getElementById('hx-dock-open').addEventListener('click', () => openPanel(true));
  document.getElementById('hx-panel-toggle').addEventListener('click', () => openPanel(!state.panelOpen));
  document.getElementById('hx-handoff').addEventListener('click', handoff);
  document.getElementById('hx-next-tbd').addEventListener('click', nextTbd);
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
    if (e.key === 'Escape') { state.composer = null; setCommentMode(false); renderPanel(); }
  });
  document.addEventListener('click', onDocClick, true); // capture: runs before spec-script handlers
  // suspend page interactivity while commenting; hover and text selection stay live
  // a comment-target block's own button role is not a control: selection inside it stays live
  const INTERACTIVE = 'button, input, select, textarea, label, a, summary, [role="button"]:not([data-hx-target]), [role="link"]';
  const suspend = e => {
    if (!state.commentMode) return;
    if (e.target.closest && e.target.closest('.hx-pin,.hx-jev-marker,.hx-jev-pop,.hx-panel,.hx-thread-dock,.hx-toolbar,.hx-range-bar,.hx-service-index-link,#hx-errors')) return;
    if (e.target.tagName === 'CANVAS') return;
    if (!holderOf(e.target)) return;
    // native drag/toggle on controls dies here; elsewhere only spec-script handlers die (selection survives)
    if (/^(pointerdown|mousedown|touchstart)$/.test(e.type)) {
      if (e.target.closest(INTERACTIVE)) { if (e.cancelable) e.preventDefault(); e.stopImmediatePropagation(); }
      return;
    }
    if (e.cancelable) e.preventDefault();
    e.stopImmediatePropagation();
  };
  for (const t of ['pointerdown', 'mousedown', 'touchstart', 'dblclick', 'auxclick', 'contextmenu', 'dragstart', 'submit', 'beforeinput', 'input', 'change']) {
    document.addEventListener(t, suspend, { capture: true, passive: false });
  }
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
    if (state.commentMode && t instanceof Element && !t.closest('.hx-pin,.hx-jev-marker,.hx-jev-pop,.hx-panel,.hx-thread-dock,.hx-toolbar,.hx-range-bar')) {
      const svg = t.closest && t.closest('[data-anchor] svg');
      if (svg && t !== svg) box = t.getBoundingClientRect();
      else if (!svg && t.closest && t.closest(INTERACTIVE) && holderOf(t)) box = t.closest(INTERACTIVE).getBoundingClientRect();
    }
    if (!box) { if (ring) ring.style.display = 'none'; return; }
    ring = ring || document.body.appendChild(Object.assign(document.createElement('div'), { className: 'hx-ring' }));
    ring.style.cssText = 'position:fixed;border:2px dashed #d98e04;border-radius:4px;pointer-events:none;z-index:650;display:block'
      + ';left:' + (box.left - 4) + 'px;top:' + (box.top - 4) + 'px;width:' + (Math.max(box.width, 8) + 8) + 'px;height:' + (Math.max(box.height, 8) + 8) + 'px';
  }, true);
  renderThreadDock();
}

// Comment mode makes every leaf anchored block a button Tab stop named by its text; off restores
// each block's own role and Tab order (data-hx-target holds them while on).
function setCommentTargets(on) {
  if (on) {
    for (const block of document.querySelectorAll('[data-anchor]:not([data-hx-target])')) {
      if (block.querySelector('[data-anchor]')) continue;
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
  state.commentMode = on;
  document.body.classList.toggle('hx-comment', on);
  setCommentTargets(on);
  document.getElementById('hx-mode').setAttribute('aria-pressed', String(on));
  if (on) adoptForeignCharts(); // catch charts the spec script created since the last scan
  for (const info of state.charts.values()) {
    try {
      const opt = info.chart.getOption() || {};
      const patch = {};
      // amber hover highlight on chart marks (canvas can't take CSS outlines); a single-element
      // series array only merges onto series[0], so build one entry per series
      const n = (opt.series || []).length || 1;
      patch.series = Array.from({ length: n }, () => ({ emphasis: { itemStyle: on ? { borderColor: '#d98e04', borderWidth: 3 } : { borderWidth: 0 } } }));
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
  if (on) openPanel(!window.matchMedia('(max-width: 640px)').matches);
}
function openPanel(open) {
  state.panelOpen = Boolean(open);
  document.querySelector('.hx-panel').classList.toggle('open', state.panelOpen);
  document.body.classList.toggle('hx-panel-open', state.panelOpen);
  const toggle = document.getElementById('hx-panel-toggle');
  toggle.setAttribute('aria-expanded', String(state.panelOpen));
  toggle.setAttribute('aria-label', state.panelOpen ? 'Collapse review sidebar' : 'Open review sidebar');
  toggle.textContent = state.panelOpen ? '›' : '‹';
  const content = document.getElementById('hx-panel-content');
  content.toggleAttribute('inert', !state.panelOpen);
  content.setAttribute('aria-hidden', String(!state.panelOpen));
  const dock = document.querySelector('.hx-thread-dock');
  dock.toggleAttribute('inert', state.panelOpen);
  dock.setAttribute('aria-hidden', String(state.panelOpen));
}
function status(msg) { document.getElementById('hx-status').textContent = msg; }

function openComposer(anchorId, target, quote, text = '', onSent = null) {
  state.composer = { kind: 'comment', anchorId, target, quote, text, onSent };
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
  // Live events re-render the panel; the composer keeps what was typed.
  textarea.addEventListener('input', () => { c.text = textarea.value; });
  textarea.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); box.querySelector('[data-act=save]').click(); }
  });
  box.querySelector('[data-act=save]').addEventListener('click', async e => {
    e.stopPropagation();
    const text = textarea.value.trim();
    if (!text) return;
    const common = { anchorId: c.anchorId, target: c.target, quote: c.quote || null, text, actor: 'human', createdAt: new Date().toISOString(), schemaVersion: 1 };
    let body;
    if (c.kind === 'reply') body = Object.assign(common, { id: humanId('u'), event: 'reply', respondsTo: c.respondsTo, threadId: c.threadId });
    else if (c.kind === 'edit') body = Object.assign(common, { id: humanId('e'), event: 'edit', supersedes: c.supersedes, threadId: c.threadId });
    else body = Object.assign(common, { id: humanId('u'), event: 'comment' });
    state.composer = null;
    save(body);
    if (c.onSent) c.onSent();
    toast((c.kind === 'edit' ? 'Edit' : c.kind === 'reply' ? 'Reply' : 'Comment') + ' saved as draft — hand off when ready');
    refresh();
  });
  box.querySelector('[data-act=cancel]').addEventListener('click', e => { e.stopPropagation(); state.composer = null; renderPanel(); });
  parent.appendChild(box);
  setTimeout(() => textarea.focus(), 0);
}

function startReply(th, agentMessage) {
  const root = th.ev.body;
  state.activeThread = th.id;
  state.composer = { kind: 'reply', threadId: th.id, respondsTo: agentMessage.body.id, anchorId: root.anchorId, target: root.target, quote: null, text: '' };
  renderPanel();
}

function startEdit(th, message) {
  const b = message.body;
  state.activeThread = th.id;
  state.composer = { kind: 'edit', threadId: th.id, supersedes: b.id, anchorId: b.anchorId || th.ev.body.anchorId, target: b.target || th.ev.body.target, quote: b.quote || null, text: b.text || '' };
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
  const wrap = document.getElementById('hx-threads');
  if (!wrap) return;
  wrap.innerHTML = '';
  if (state.composer && state.composer.kind === 'comment') {
    const c = state.composer;
    const d = document.createElement('div');
    d.className = 'hx-thread active';
    d.innerHTML = '<div class="hx-anchor">' + esc(label({ anchorId: c.anchorId, target: c.target })) + '</div>' +
      (c.quote ? '<span class="hx-quote">“' + esc(c.quote) + '”</span>' : '');
    addComposer(d, c);
    wrap.appendChild(d);
  }
  const threads = [...state.threads.values()].reverse();
  for (const th of threads) {
    const b = th.ev.body;
    const collapsed = resolvedThreadCollapsed(th, state.expandedResolved);
    const d = document.createElement('div');
    d.className = 'hx-thread' + (state.activeThread === th.id ? ' active' : '') + (collapsed ? ' resolved-collapsed' : '');
    // A resolved thread shows its own resolved indicator, never a resolved-in-spirit Jev label.
    const resolvedHint = th.status === 'resolved' ? null : jevItem('resolved', th.id);
    const orphanHint = openOrphanHint(th);
    const threadJevState = [orphanHint, resolvedHint].find(item => item && ['unsure', 'unavailable'].includes(item.state));
    d.innerHTML = '<div class="hx-thread-summary"><div class="hx-anchor">' + esc(label(b)) + '</div>' +
      '<span class="hx-pill" data-s="' + th.status + '">' + th.status + '</span>' +
      (looksResolved(th) ? '<span class="hx-jev-thread-label">Looks resolved</span>' : '') +
      (threadJevState ? '<span class="hx-jev-thread-label" data-state="' + threadJevState.state + '">' + esc(jevDisplayLabel(threadJevState)) + '</span>' : '') +
      (th.status === 'resolved' ? '<button class="hx-disclosure" data-act="disclosure" aria-expanded="' + String(!collapsed) + '" aria-label="' + (collapsed ? 'Show' : 'Hide') + ' resolved thread">' + (collapsed ? '▸' : '▾') + '</button>' : '') + '</div>' +
      (collapsed ? '<div class="hx-thread-preview">' + esc(th.messages[0].body.text || 'Resolved comment') + '</div>' : '');
    const notice = th.ev.place && PLACE_NOTICES[th.ev.place.state];
    if (notice) {
      const n = document.createElement('div');
      n.className = 'hx-place-notice';
      n.dataset.state = th.ev.place.state;
      n.innerHTML = esc(notice) + (b.quote ? '<span class="hx-quote">“' + esc(b.quote) + '”</span>' : '');
      d.appendChild(n);
    }
    const hint = orphanHintElement(th, orphanHint);
    if (hint) d.appendChild(hint);
    if (!collapsed) {
      th.messages.forEach((message, index) => {
        const m = message.body;
        const history = th.history && th.history.get(index);
        const item = document.createElement('div');
        item.className = 'hx-msg';
        item.dataset.messageId = m.id;
        item.innerHTML = '<span class="hx-who">' + esc(messageAuthor(state.authors, history ? history.original : message)) + '</span>' +
          (m.quote ? '<span class="hx-quote">“' + esc(m.quote) + '”</span>' : '') + '<span class="hx-text">' + esc(m.text || '') + '</span>';
        // #model-edit-history: more than one edit lists every edit with its author.
        if (history && history.edits.length > 1) {
          const list = document.createElement('ol');
          list.className = 'hx-history';
          list.setAttribute('aria-label', 'Edit history');
          for (const edit of history.edits) {
            const entry = document.createElement('li');
            entry.innerHTML = '<span class="hx-who">' + esc(messageAuthor(state.authors, edit)) + '</span> ' + esc(edit.body.text || '');
            list.appendChild(entry);
          }
          item.appendChild(list);
        }
        if (message.actor === 'human' && m.id === th.latestHumanId && ['draft', 'pending'].includes(th.status)) {
          const actions = document.createElement('div');
          actions.className = 'hx-msg-actions';
          actions.innerHTML = '<button class="hx-btn" data-act="edit">Edit</button>';
          actions.querySelector('button').addEventListener('click', e => { e.stopPropagation(); startEdit(th, message); });
          item.appendChild(actions);
        }
        d.appendChild(item);
      });
      const replyAction = threadReplyAction(th);
      if (replyAction) {
        const reply = document.createElement('button');
        reply.className = 'hx-btn';
        reply.dataset.act = 'reply';
        reply.textContent = replyAction.label;
        reply.addEventListener('click', e => { e.stopPropagation(); startReply(th, replyAction.message); });
        d.appendChild(reply);
      }
      if (th.status === 'acknowledged') {
        const resolve = document.createElement('button');
        resolve.className = 'hx-btn';
        resolve.dataset.act = 'resolve';
        resolve.textContent = '✓ Resolve';
        resolve.addEventListener('click', e => { e.stopPropagation(); resolveThread(th); });
        d.appendChild(resolve);
      }
      if (state.composer && state.composer.threadId === th.id) addComposer(d, state.composer);
    }
    d.addEventListener('click', () => selectThread(th, true));
    d.querySelector('[data-act=disclosure]')?.addEventListener('click', e => {
      e.stopPropagation();
      if (collapsed) selectThread(th, true);
      else {
        state.expandedResolved.delete(th.id);
        renderPanel();
        renderPins();
      }
    });
    wrap.appendChild(d);
  }
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
    wrap.appendChild(moveAll);
  }
  const hinted = threads.filter(looksResolved);
  if (hinted.length) {
    const all = document.createElement('button');
    all.className = 'hx-btn';
    all.dataset.act = 'resolve-all';
    all.textContent = 'Resolve all (' + hinted.length + ')';
    all.addEventListener('click', e => { e.stopPropagation(); resolveThreads(hinted); });
    wrap.appendChild(all);
  }
  const handoffState = renderHighlight();
  const drafts = handoffState.drafts;
  document.getElementById('hx-drafts').textContent = handoffState.finish ? 'Ready to accept' : drafts + ' draft' + (drafts === 1 ? '' : 's');
  const desktopHandoff = document.getElementById('hx-handoff');
  desktopHandoff.disabled = !handoffState.enabled;
  desktopHandoff.textContent = handoffState.finish ? 'Accept spec' : handoffState.tbd ? tbdCountLabel('TBD open', handoffState.openTbds) : 'Hand off to agent →';
  const mobileHandoff = document.getElementById('hx-mobile-handoff');
  mobileHandoff.disabled = !handoffState.enabled;
  mobileHandoff.textContent = handoffState.finish ? 'Accept spec' : handoffState.tbd ? tbdCountLabel('TBD open', handoffState.openTbds) : drafts ? 'Hand off (' + drafts + ')' : 'Hand off';
  for (const nextButton of [document.getElementById('hx-next-tbd'), document.getElementById('hx-mobile-next-tbd')]) {
    nextButton.hidden = !handoffState.nextTbd;
    nextButton.textContent = tbdCountLabel('Next TBD', handoffState.openTbds);
  }
  renderThreadDock();
  renderThreadHighlight();
}

// A card's own resolve control and Resolve all post the same resolve event.
async function resolveThreads(threads) {
  for (const th of threads) {
    save({ id: humanId('s'), event: 'status', respondsTo: th.id, threadId: th.id, status: 'resolved', actor: 'human', createdAt: new Date().toISOString(), schemaVersion: 1 });
    state.expandedResolved.delete(th.id);
  }
  toast('Resolved');
  refresh();
}

function resolveThread(th) {
  return resolveThreads([th]);
}

function selectThread(th, scroll) {
  state.activeThread = th.id;
  if (th.status === 'resolved') state.expandedResolved.add(th.id);
  openPanel(true);
  renderPanel();
  renderPins();
  if (scroll) scrollToThread(placedMark(th.ev));
  renderThreadHighlight();
}

function scrollToThread(b) {
  findAnchor(b.anchorId)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
}

function scrollToJevAnchor(anchorId) {
  if (location.hash.slice(1) === encodeURIComponent(anchorId)) arriveAtAddress();
  else location.hash = encodeURIComponent(anchorId);
}

// A resolved orphan is settled (#settle-orphan): it shows only its resolved indicator, no Jev guess.
function openOrphanHint(th) {
  return th.status === 'resolved' ? null : jevItem('orphan', th.id);
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
      save({
        id: humanId('u'), event: 'comment', anchorId: target, target: null,
        quote, text: text || 'Moved comment', actor: 'human',
        createdAt: new Date().toISOString(), schemaVersion: 1,
      });
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
  const rect = range.getBoundingClientRect();
  return rect.width || rect.height ? rect : null;
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
  const th = state.activeThread && state.threads.get(state.activeThread);
  const b = th && placedMark(th.ev);
  const holder = b && document.querySelector('[data-anchor="' + b.anchorId + '"]');
  if (!holder) {
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
      return { top: eR.top - hR.top - 4, left: Math.min(holder.clientWidth - 28, eR.right - hR.left - 12) };
    }
  }
  return cornerPos(holder); // orphan/text fallback
}

// Block corner, or, when the block has its own Jev marker, the block's reserved right column
// directly below the marker's tap pad (its ::before), so the pin covers neither the marker nor text.
function cornerPos(holder) {
  const marker = holder.querySelector(holder.tagName === 'TR' ? ':scope > :is(td,th) > .hx-jev-marker' : ':scope > .hx-jev-marker');
  if (!marker) return { top: 4, left: holder.clientWidth - 30 };
  const pad = parseFloat(getComputedStyle(marker, '::before').bottom) || 0;
  const top = marker.getBoundingClientRect().bottom - pad - holder.getBoundingClientRect().top - holder.clientTop;
  return { column: true, top: Math.ceil(top) }; // right edge by CSS, so a table's later reflow keeps it
}

function renderPins() {
  document.querySelectorAll('.hx-pin').forEach(p => p.remove());
  let n = 0;
  for (const th of state.threads.values()) {
    n++;
    const b = placedMark(th.ev);
    const holder = findAnchor(b.anchorId);
    if (!holder) continue;
    const pos = pinPos(b, holder);
    const pin = document.createElement('button');
    pin.className = 'hx-pin' + (state.activeThread === th.id ? ' active' : '');
    pin.dataset.s = th.status;
    const hinted = looksResolved(th);
    pin.textContent = '';
    const number = document.createElement('span');
    number.className = 'hx-pin-number';
    number.textContent = n;
    pin.appendChild(number);
    if (hinted) {
      pin.dataset.jev = 'resolved';
      const marker = document.createElement('span');
      marker.className = 'hx-pin-jev';
      marker.textContent = 'Looks resolved';
      pin.appendChild(marker);
    }
    pin.title = (hinted ? 'Looks resolved · ' : '') + label(b);
    pin.setAttribute('aria-label', (hinted ? 'Looks resolved: ' : '') + label(b));
    pin.style.top = pos.top + 'px';
    if (pos.column) pin.dataset.column = '';
    const pinSize = window.matchMedia('(max-width: 640px)').matches ? 44 : 24;
    if (!pos.column) pin.style.left = Math.max(0, Math.min(pos.left, holder.clientWidth - pinSize)) + 'px';
    pin.addEventListener('click', e => { e.stopPropagation(); selectThread(th, true); });
    holder.appendChild(pin);
  }
}

function renderBadges() {
  document.querySelectorAll('.hx-badge').forEach(b => b.remove());
  // Embed hosts are live product UI: injecting badge text into their headings
  // pollutes the page being reviewed. Spec pages keep the badges.
  if (EMBED_REVIEW_DIR) return;
  const changed = new Set();
  for (const e of state.events) if (e.actor === 'agent' && e.body.change && e.body.anchorId) changed.add(e.body.anchorId);
  for (const a of changed) {
    const h = document.querySelector('[data-anchor="' + a + '"] h2, [data-anchor="' + a + '"] h1');
    if (!h || h.querySelector('.hx-badge')) continue;
    const s = document.createElement('span');
    s.className = 'hx-badge';
    s.textContent = 'updated by agent';
    h.appendChild(s);
  }
}

function handoff() {
  const openTbds = openTbdMarkers(document.querySelectorAll('[data-spec-tbd]'));
  const action = reviewHandoffState(state.threads, openTbds.length, state.reviewer.browser);
  if (action.tbd) return nextTbd();
  if (state.handoffPosting || !action.enabled) return;
  state.handoffPosting = true; // until this hand-off's send settles: a double tap writes one
  // Hand off lists this browser's drafts (#live-handoff); Accept spec writes the existing hand-off without a list.
  const listed = action.finish ? {} : { events: handoffEvents(state.events, state.reviewer.browser) };
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
  if (!el.matches('a[href],button,input,select,textarea,[tabindex]')) el.setAttribute('tabindex', '-1');
  el.scrollIntoView({ block: 'center', behavior: 'smooth' });
  el.focus({ preventScroll: true });
}

const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

let toastTimer;
function toast(msg) {
  let el = document.getElementById('hx-toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'hx-toast';
    el.className = 'hx-toast';
    document.body.appendChild(el);
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
const OUTBOX_KEY = 'spec-chat:outbox:' + REVIEW_DIR;
const LOADED_KEY = 'spec-chat:events:' + REVIEW_DIR;
let pageOutbox = null; // this page's own outbox when browser storage cannot keep it
let flushing = null;

function reviewStorage() {
  try { return window.localStorage; } catch (_) { return null; }
}

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

const outboxEvents = () => outboxEntries().map(entry => ({ actor: 'human', name: entry.name, body: entry.body }));

function save(body) {
  const event = Object.assign({}, body, { browser: state.reviewer.browser, author: state.reviewer.author }, state.version ? { version: state.version } : {});
  state.lastStamp = nextStamp(state.lastStamp, Date.now(), Math.random);
  const entry = { name: eventName(event, state.lastStamp), body: event };
  setOutbox(outboxEntries().concat([entry]));
  ingest([{ actor: 'human', name: entry.name, body: event }]);
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

function keepLoaded(events) {
  if (events.length === state.storedCount) return;
  state.storedCount = events.length;
  try { reviewStorage().setItem(LOADED_KEY, JSON.stringify(events)); } catch (_) { /* the page still shows them */ }
}

function loadedEvents() {
  try { return JSON.parse(reviewStorage().getItem(LOADED_KEY) || '[]'); } catch (_) { return []; }
}

function renderOffline() {
  const el = document.getElementById('hx-offline');
  if (!el) return;
  el.textContent = state.outboxBlocked ? offlineNotice(outboxEntries().length) : '';
  el.hidden = !el.textContent;
}

// The ETag of the spec text this page shows, from the browser's copy of this document.
async function shownVersion() {
  try {
    const tag = (await fetch(location.href.split('#')[0], { cache: 'force-cache' })).headers.get('ETag');
    return tag ? tag.replace(/^W\//, '').replace(/"/g, '') : null;
  } catch (_) { return null; }
}

/* ---------------- loops ---------------- */
async function refresh() {
  flushOutbox(); // #offline-reopen: waiting events are sent first; the read never waits on a pending save
  try {
    const listed = await state.transport.listEvents();
    const wake = Array.isArray(listed) ? null : listed.wake;
    const events = Array.isArray(listed) ? listed : listed.events;
    keepLoaded(events);
    ingest(events.concat(outboxEvents()));
    const agentEvents = state.events.filter(e => e.actor === 'agent');
    const observation = handoffObservation(state.events, Date.now());
    document.getElementById('hx-agent').textContent = handoffAgentText(observation, wake, agentEvents[agentEvents.length - 1]);
    status('connected · ' + state.transport.label + ' · ' + state.threads.size + ' threads');
    if (location.hash.includes('hxdebug') && !state._beaconed) {
      state._beaconed = true;
      fetch('/hxdebug/threads=' + state.threads.size + '/pins=' + document.querySelectorAll('.hx-pin').length + '/charts=' + state.charts.size).catch(() => {});
    }
  } catch (e) {
    if (!state.eventsRendered) ingest(loadedEvents().concat(outboxEvents()));
    status('event sync failed: ' + e.message);
  }
  renderOffline();
}

async function watchSpec() {
  let m;
  try { m = await state.transport.specModified(); } catch (_) { return; } // offline: the next poll checks
  if (m && state.specMtime && m > state.specMtime && !document.getElementById('hx-banner')) {
    const b = document.createElement('div');
    b.className = 'hx-banner';
    b.id = 'hx-banner';
    b.innerHTML = 'Spec updated by agent <button>Reload</button>';
    b.querySelector('button').addEventListener('click', () => location.reload());
    document.body.appendChild(b);
  }
  if (m) state.specMtime = state.specMtime || m;
}

/* ---------------- ANN-233 reader's place ----------------
 * Per spec path: the nearest data-anchor at the viewport top plus the offset
 * past its top, in localStorage. Restored once, after render; an address anchor
 * wins, and a reader who already moved is never re-scrolled. Every storage call
 * is wrapped: no storage means no kept place, never a broken page.
 */
const PLACE_KEY = 'hx-place:' + location.pathname;

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

function currentPlace() {
  if (window.scrollY <= 0) return null;
  let place = null;
  for (const el of document.querySelectorAll('[data-anchor]')) {
    const r = el.getBoundingClientRect();
    if (!r.height || r.top > 0 || (place && -r.top >= place.offset)) continue;
    place = { anchor: el.dataset.anchor, offset: Math.round(-r.top) };
  }
  return place;
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
    const place = moved ? null : addressPlace() || (storage && readPlace(storage));
    const el = place && findAnchor(place.anchor);
    if (el) window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY + place.offset);
    if (!storage) return;
    let timer = 0;
    const save = () => { clearTimeout(timer); timer = 0; writePlace(storage, currentPlace()); };
    window.addEventListener('scroll', () => { if (!timer) timer = setTimeout(save, 250); }, { passive: true });
    window.addEventListener('pagehide', save);
  };
}

/* ---------------- boot ---------------- */
(async function boot() {
  const restorePlace = keepPlace();
  const httpPage = !EMBED_REVIEW_DIR && ['http:', 'https:'].includes(location.protocol);
  // Deferred script: the DOM is the served spec until mountUI; compare it, never refetch it.
  if (httpPage) state.range.loaded = anchorSignatures(document);
  mountUI();
  if (httpPage) applyIssueFocus();
  if (httpPage) { listenEvidenceHost(); requestEvidence(); }
  await hydrateIslands();
  adoptForeignCharts();
  restorePlace();
  renderHighlight();
  window.addEventListener('hashchange', arriveAtAddress);
  // spec scripts can create/recreate charts at any time; rescan when canvases appear
  let adoptTimer = null;
  new MutationObserver(muts => {
    if (!muts.some(m => [...m.addedNodes].some(n => n.nodeType === 1 && (n.tagName === 'CANVAS' || (n.querySelector && n.querySelector('canvas')))))) return;
    clearTimeout(adoptTimer);
    adoptTimer = setTimeout(adoptForeignCharts, 200);
  }).observe(document.body, { childList: true, subtree: true });
  if (location.protocol === 'file:' && !('showDirectoryPicker' in window)) {
    document.getElementById('hx-mode').disabled = true;
    status('view-only — this browser cannot annotate file:// pages; use Chrome/Edge, or serve via review-serve.py over http://');
    console.warn('[spec-chat] showDirectoryPicker unavailable; file:// annotation needs the File System Access API (Chromium). Run review-serve.py and open the http://localhost URL instead.');
    return;
  }
  state.transport = location.protocol === 'file:' ? fsaTransport() : httpTransport();
  state.reviewer = reviewerIdentity(reviewStorage(), Math.random);
  if (httpPage) state.version = await shownVersion();

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
      btn.title = 'Pick or drop \u201c' + suggestedGrant() + '\u201d (or any folder above the spec) \u2014 remembered for every spec beneath it. Spec lives in: ' + decodeURIComponent(location.pathname).replace(/\/[^/]*$/, '');
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

/* ---------------- ANN-108 reading view ----------------
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
  const url = new URL(location.href);
  if (url.searchParams.get('focus') === 'changes') {
    url.searchParams.delete('focus');
    history.replaceState(null, '', url.pathname + url.search + url.hash);
  }
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
    const base = (state.range.baseline && state.range.baseline.base) || new URLSearchParams(location.search).get('base');
    if (base) requestJev(base);
  } else {
    renderJev();
  }
}

function mountReadingView() {
  if (EMBED_REVIEW_DIR || !['http:', 'https:'].includes(location.protocol) || document.getElementById('hx-reading')) return;
  const toolbar = document.querySelector('.hx-toolbar');
  if (!toolbar) return;
  const style = document.createElement('style');
  style.textContent = `.hx-reading-active [data-hx-audience="internals"]{color:#586069!important}
.hx-reading-active [data-hx-audience="internals"] :is(a,code,strong,em,span){color:inherit!important}
` + (document.querySelector('link[rel~="stylesheet"][href*=".style/spec.css"]') ? '' :
    `@media(prefers-color-scheme:dark){.hx-reading-active [data-hx-audience="internals"]{color:#b9c0ca!important}}`);
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
readingClassObserver.observe(document.body, { attributes: true, attributeFilter: ['class'] });
setTimeout(mountReadingView, 0);

})();
