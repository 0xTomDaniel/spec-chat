// spec-chat runtime v0.1 — hydrates semantic islands and mounts the annotation layer.
// spec-chat-capabilities: changed-root-focus custom-style-focus diff-visibility-control finish-review git-focus manual-resume-status mobile-pre-wrap mobile-review reopen-thread semantic-islands shared-style-ownership
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
  handoffPosting: false,
  range: { baseline: null, loading: false, pickerOpen: false },
  jev: { status: 'idle', items: [], base: null, request: 0 },
  readingView: false,
  movingOrphans: new Set(),
};

/* ---------------- transports ---------------- */
function httpTransport() {
  const dir = EMBED_REVIEW_DIR || location.pathname.replace(/^\//, '') + '.review';
  return {
    mode: 'http', label: EMBED_REVIEW_DIR ? 'review-serve (embedded)' : 'review-serve',
    ready: Promise.resolve(true),
    async listEvents() {
      const r = await fetch('/api/events?dir=' + encodeURIComponent(dir));
      return r.json();
    },
    async postEvent(body) {
      await fetch('/api/events?dir=' + encodeURIComponent(dir) + '&actor=human', { method: 'POST', body: JSON.stringify(body) });
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

const JEV_TYPE_LABELS = {
  scope: 'Scope',
  behavioral: 'Behavior',
  clarification: 'Clarification',
  cosmetic: 'Cosmetic',
};

function jevParams(base) {
  const params = new URLSearchParams({ path: location.pathname.replace(/^\//, ''), base: String(base || '') });
  const readingParams = { view: 'reading' };
  if ((typeof state !== 'undefined' && state.readingView) || new URLSearchParams(location.search).get('view') === 'reading') params.set('view', readingParams.view);
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
    })) : [],
  };
}

function jevItem(kind, id) {
  return state.jev.items.find(item => item.kind === kind && item.id === String(id)) || null;
}

function findAnchor(anchorId) {
  return [...document.querySelectorAll('[data-anchor]')].find(el => el.dataset.anchor === String(anchorId)) || null;
}

function clearJev() {
  state.jev.request += 1;
  state.jev.status = 'idle';
  state.jev.items = [];
  state.jev.base = null;
  renderJev();
  renderPanel();
  renderPins();
}

async function requestJev(base) {
  if (!['http:', 'https:'].includes(location.protocol) || !base) return;
  const request = ++state.jev.request;
  state.jev.status = 'loading';
  state.jev.base = String(base);
  renderJev();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 120000);
  try {
    const result = await fetchJev(base, controller.signal);
    if (request !== state.jev.request) return;
    state.jev.status = result.jev === 'off' ? 'off' : 'on';
    state.jev.items = result.items;
    renderJev();
    renderPanel();
    renderPins();
  } catch (_) {
    if (request !== state.jev.request) return;
    state.jev.status = 'unavailable';
    state.jev.items = [];
    renderJev();
    renderPanel();
    renderPins();
  } finally {
    clearTimeout(timeout);
  }
}

function markIssueFocus(currentText, baseline) {
  const parser = new DOMParser();
  const current = anchorSignatures(parser.parseFromString(currentText, 'text/html'));
  const prior = baseline.html === null ? null : anchorSignatures(parser.parseFromString(baseline.html, 'text/html'));
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
  document.body.classList.add('hx-focus-active');
}

async function fetchBaseline(base, includeCurrent = false, signal) {
  const params = baselineParams(base);
  const requests = [fetch('/api/baseline?' + params, { signal })];
  if (includeCurrent) requests.push(fetch(location.pathname, { cache: 'no-store', signal }));
  const responses = await Promise.all(requests);
  const baselineResponse = responses[0];
  if (!baselineResponse.ok || (includeCurrent && !responses[1].ok)) throw new Error('Git baseline unavailable');
  const baseline = await baselineResponse.json();
  if (includeCurrent) return { baseline, currentText: await responses[1].text() };
  return { baseline };
}

async function applyIssueFocus() {
  if (EMBED_REVIEW_DIR || location.protocol === 'file:' || new URLSearchParams(location.search).get('focus') !== 'changes') return;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const requestedBase = new URLSearchParams(location.search).get('base');
    const result = await fetchBaseline(requestedBase, true, controller.signal);
    state.range.baseline = result.baseline;
    renderRangeBar(result.baseline);
    requestJev(result.baseline.base);
    markIssueFocus(result.currentText, result.baseline);
  } catch (error) {
    const copy = document.getElementById('hx-range-copy');
    if (copy) copy.textContent = 'Compared range unavailable.';
    showFocusError();
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
    const result = await fetchBaseline(requested, true, controller.signal);
    const baseline = result.baseline;
    state.range.baseline = baseline;
    markIssueFocus(result.currentText, baseline);
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

async function loadRangeBar() {
  if (EMBED_REVIEW_DIR || !['http:', 'https:'].includes(location.protocol)) return;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const result = await fetchBaseline(null, false, controller.signal);
    state.range.baseline = result.baseline;
    renderRangeBar(result.baseline);
    requestJev(result.baseline.base);
  } catch (error) {
    const copy = document.getElementById('hx-range-copy');
    if (copy) copy.textContent = 'Compared range unavailable.';
  } finally {
    clearTimeout(timeout);
  }
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
    if (!values.has(key)) values.set(key, { anchor, side, verifies: false, unsure: false, unavailable: false });
    return values.get(key);
  };
  for (const item of Array.isArray(items) ? items : []) {
    if (!item || item.kind !== 'coverage') continue;
    const pair = coveragePair(item);
    if (!pair) continue;
    const story = ensure(pair.story, 'story');
    const criterion = ensure(pair.criterion, 'criterion');
    const verifies = item.state === 'label' && item.label === 'verifies';
    const unsure = item.state === 'unsure' || (item.state === 'label' && item.label === 'unsure');
    const unavailable = item.state === 'unavailable';
    for (const value of [story, criterion]) {
      value.verifies ||= verifies;
      value.unsure ||= unsure;
      value.unavailable ||= unavailable;
    }
  }
  return [...values.values()].flatMap(value => {
    if (!value.anchor) return [];
    if (value.verifies) return [];
    if (value.unsure) return [{ anchor: value.anchor, side: value.side, state: 'unsure', label: 'unsure' }];
    if (value.unavailable) return [{ anchor: value.anchor, side: value.side, state: 'unavailable', label: 'Jev unavailable' }];
    return [{ anchor: value.anchor, side: value.side, state: 'gap',
      label: value.side === 'story' ? 'No criterion covers this' : 'No story backs this' }];
  });
}

function coverageFlags(items) {
  return coverageGapFlags(items);
}

function anchorElement(anchor) {
  return [...document.querySelectorAll('[data-anchor]')].find(element => element.dataset.anchor === anchor) || null;
}

function clearJevCoverage() {
  document.querySelectorAll('.hx-jev-coverage').forEach(element => element.remove());
}

function renderJevCoverage(result) {
  clearJevCoverage();
  if (!result || result.jev !== 'on') return;
  for (const flag of coverageGapFlags(result.items)) {
    const holder = anchorElement(flag.anchor);
    if (!holder) continue;
    const note = document.createElement('span');
    note.className = 'hx-jev-coverage';
    note.dataset.state = flag.state;
    note.textContent = flag.label;
    note.setAttribute('role', 'status');
    const heading = holder.querySelector('h1,h2,h3,h4,h5,h6');
    (heading || holder).appendChild(note);
  }
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
    async postEvent(body) {
      const d = await t._dir('human', true);
      const name = String(Date.now() * 1e6 + Math.floor(Math.random() * 1e6)) + '-' + (body.event || 'event') + '-' + (body.id || 'x') + '.json';
      const fh = await d.getFileHandle(name, { create: true });
      const w = await fh.createWritable();
      await w.write(JSON.stringify(body));
      await w.close();
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
  if (!state.commentMode || e.target.closest('.hx-pin,.hx-panel,.hx-toolbar,.hx-range-bar,.hx-service-index-link,#hx-errors')) return;
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
function foldThreads(events) {
  const threads = new Map();
  let lastHandoff = '';
  for (const e of events) if (e.body.event === 'handoff') lastHandoff = e.name;
  const messageThread = new Map();
  const messageSlot = new Map();
  const humanStatus = e => e.name > lastHandoff ? 'draft' : 'pending';
  for (const e of events) {
    const b = e.body;
    if (b.event === 'comment' && e.actor === 'human') {
      const th = { id: b.id, ev: e, messages: [e], status: humanStatus(e), latestHumanId: b.id };
      threads.set(b.id, th);
      messageThread.set(b.id, b.id);
      messageSlot.set(b.id, { th, index: 0 });
      continue;
    }
    if (b.event === 'reply') {
      const threadId = b.threadId || messageThread.get(b.respondsTo) || (threads.has(b.respondsTo) ? b.respondsTo : null);
      const th = threadId && threads.get(threadId);
      if (!th) continue;
      const index = th.messages.push(e) - 1;
      messageThread.set(b.id, th.id);
      messageSlot.set(b.id, { th, index });
      if (e.actor === 'human') {
        th.latestHumanId = b.id;
        th.status = humanStatus(e);
      } else if (b.respondsTo === th.latestHumanId) {
        th.status = b.status || 'acknowledged';
      }
      continue;
    }
    if (b.event === 'edit' && e.actor === 'human') {
      const prior = messageSlot.get(b.supersedes);
      const threadId = b.threadId || messageThread.get(b.supersedes);
      const th = (prior && prior.th) || (threadId && threads.get(threadId));
      if (!th) continue;
      const index = prior ? prior.index : th.messages.length;
      th.messages[index] = e;
      if (index === 0) th.ev = e;
      messageThread.set(b.id, th.id);
      messageSlot.set(b.id, { th, index });
      th.latestHumanId = b.id;
      th.status = humanStatus(e);
      continue;
    }
    if (b.event === 'status') {
      const threadId = b.threadId || messageThread.get(b.respondsTo) || (threads.has(b.respondsTo) ? b.respondsTo : null);
      const th = threadId && threads.get(threadId);
      if (th) th.status = b.status;
    }
  }
  return threads;
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

function reviewHandoffState(threads, hasTbd = false) {
  const values = [...threads.values()];
  const drafts = values.filter(thread => thread.status === 'draft').length;
  const settled = drafts === 0 && values.every(thread => thread.status === 'resolved');
  const finish = !hasTbd && settled;
  const tbd = hasTbd && settled;
  return { drafts, finish, tbd, enabled: drafts > 0 || finish || tbd };
}

function handoffObservation(events, nowMs) {
  let handoff = null;
  for (const event of events) if (event.actor === 'human' && event.body.event === 'handoff') handoff = event;
  if (!handoff) return null;
  if (events.some(event => event.actor === 'agent' && event.name > handoff.name)) return null;
  const createdAt = Date.parse(handoff.body.createdAt || '');
  return Number.isFinite(createdAt) && nowMs - createdAt >= 30000 ? 'queued' : 'waiting';
}

function ingest(events) {
  let changed = false;
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
  const raw = String(item.label || '').toLowerCase();
  return JEV_TYPE_LABELS[raw] || item.label || '';
}

function corpusFlags(items) {
  const labels = { contradicts: 'Contradicts', overlaps: 'Overlaps', oversteps: 'Oversteps' };
  return (Array.isArray(items) ? items : []).flatMap(item => {
    if (!item || item.kind !== 'corpus' || !item.id) return [];
    if (item.state === 'unsure') return [{ anchor: String(item.id), state: 'unsure', label: 'unsure', target: null }];
    if (item.state !== 'label') return [];
    const label = labels[String(item.label || '').toLowerCase()];
    if (!label) return [];
    return [{ anchor: String(item.id), state: 'label', label,
      target: item.target == null ? null : String(item.target) }];
  });
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

function appendJevCorpusMarker(holder, flag) {
  const marker = document.createElement(flag.target ? 'a' : 'span');
  marker.className = 'hx-jev-corpus';
  marker.dataset.state = flag.state;
  const target = corpusTargetLink(flag.target);
  if (target && marker.tagName === 'A') {
    marker.href = target.href;
    marker.textContent = flag.label + ' ' + target.text;
  } else {
    marker.textContent = flag.label;
  }
  const heading = holder.querySelector('h1,h2,h3,h4,h5,h6') || holder;
  heading.appendChild(marker);
}

function appendJevMarker(holder, text, stateName) {
  const marker = document.createElement('span');
  marker.className = 'hx-jev-marker hx-jev-badge';
  marker.dataset.state = stateName;
  marker.textContent = text;
  const heading = holder.querySelector('h1,h2,h3,h4,h5,h6') || holder;
  heading.appendChild(marker);
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

function renderJev() {
  document.querySelectorAll('.hx-jev-badge').forEach(el => el.remove());
  document.querySelectorAll('.hx-jev-corpus').forEach(el => el.remove());
  document.querySelectorAll('.hx-jev-coverage').forEach(el => el.remove());
  document.querySelectorAll('[data-hx-audience]').forEach(el => delete el.dataset.hxAudience);
  document.querySelectorAll('[data-hx-jev-type]').forEach(el => {
    delete el.dataset.hxJevType;
    delete el.dataset.hxJevState;
  });
  document.querySelectorAll('.hx-jev-note').forEach(el => el.remove());
  if (EMBED_REVIEW_DIR) return;
  renderJevCoverage(state.jev.status === 'on' ? state.jev : null);

  if (state.jev.status === 'off' || state.jev.status === 'unavailable') {
    const note = document.createElement('p');
    note.className = 'hx-jev-note';
    note.textContent = state.jev.status === 'off' ? 'Jev off' : 'Jev unavailable';
    const article = document.querySelector('article.spec');
    if (article) article.parentNode.insertBefore(note, article);
    else document.body.insertBefore(note, document.body.firstChild);
  }

  const gitFocus = new URLSearchParams(location.search).get('focus') === 'changes' || document.body.classList.contains('hx-focus-active');
  const reading = state.readingView || new URLSearchParams(location.search).get('view') === 'reading';
  for (const item of state.jev.items) {
    if (!item.id || item.state === 'none') continue;
    const holder = findAnchor(item.id);
    if (!holder) continue;
    if (reading) {
      if (item.kind !== 'audience') continue;
      const allowedAudience = item.label !== 'for you' && item.label !== 'internals' ? false : true;
      if (item.state === 'label' && allowedAudience) {
        holder.dataset.hxAudience = item.label;
      }
      const audienceLabel = jevDisplayLabel(item);
      if (item.state !== 'label' || allowedAudience) {
        appendJevMarker(holder, audienceLabel, item.state);
      }
      continue;
    }
    if (!gitFocus || item.kind !== 'type') continue;
    const label = jevDisplayLabel(item);
    if (!label) continue;
    holder.dataset.hxJevType = String(item.label || item.state).toLowerCase();
    holder.dataset.hxJevState = item.state;
    appendJevMarker(holder, label, item.state);
  }
  if (!gitFocus) return;
  for (const flag of corpusFlags(state.jev.items)) {
    const holder = findAnchor(flag.anchor);
    if (holder) appendJevCorpusMarker(holder, flag);
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
body.hx-focus-active [data-hx-focus=unchanged] .hx-pin,body.hx-focus-active [data-hx-focus=unchanged] .hx-badge{opacity:1;filter:none;z-index:700}
.hx-focus-error{position:fixed;top:calc(12px + env(safe-area-inset-top));left:50%;transform:translateX(-50%);max-width:calc(100vw - 24px);box-sizing:border-box;padding:8px 12px;border-radius:8px;background:#8b1a1a;color:#fff;font:600 12px system-ui;z-index:970;box-shadow:0 6px 20px rgba(30,30,40,.25)}
@media(prefers-color-scheme:dark){
body.hx-focus-active [data-hx-focus=unchanged]:not(:has([data-hx-focus=changed])):not([data-hx-focus=unchanged]:not(:has([data-hx-focus=changed])) *):not(tr):not(td):not(th):not(script):not(style)::after{background:rgba(0,0,0,calc(.6*var(--hx-veil,1)))}
body.hx-focus-active tr[data-hx-focus=unchanged]:not([data-hx-focus=unchanged]:not(:has([data-hx-focus=changed])) *) > :is(td,th)::after{background:rgba(0,0,0,calc(.6*var(--hx-veil,1)))}
}
`;
const CSS = `
.hx-range-bar{box-sizing:border-box;max-width:720px;margin:12px auto 0;padding:8px 10px;border:1px solid #cbd5ee;border-radius:8px;background:#e8edf9;color:#171719;font:13px/1.4 system-ui,sans-serif}
.hx-range-row{display:flex;align-items:center;gap:10px;min-width:0}
.hx-range-copy{flex:1 1 auto;min-width:0;margin:0;overflow-wrap:anywhere}
.hx-range-id{font-weight:750;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.hx-range-change{flex:0 0 auto;min-height:32px;padding:6px 10px;border:1px solid #2947c7;border-radius:6px;background:#fff;color:#2947c7;font:700 12px system-ui,sans-serif;cursor:pointer}
.hx-range-change:hover{background:#f4f6ff}
.hx-range-change:focus-visible,.hx-range-commit:focus-visible,.hx-range-form input:focus-visible,.hx-range-form button:focus-visible{outline:3px solid #f59e0b;outline-offset:2px}
.hx-range-picker{margin-top:8px;padding-top:8px;border-top:1px solid #cbd5ee}
.hx-range-commits{display:grid;gap:3px;max-height:270px;overflow:auto}
.hx-range-commit{display:grid;grid-template-columns:5.5em 6.5em minmax(0,1fr);gap:8px;align-items:baseline;width:100%;padding:6px 8px;border:1px solid transparent;border-radius:4px;background:transparent;color:#171719;text-align:left;font:12px/1.35 system-ui,sans-serif;cursor:pointer}
.hx-range-commit:hover,.hx-range-commit[data-selected=true]{border-color:#167b68;background:#e7f3ef}
.hx-range-commit strong{font:700 12px ui-monospace,SFMono-Regular,Menlo,monospace}
.hx-range-commit time{color:#5a5a63;font-variant-numeric:tabular-nums}
.hx-range-commit span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hx-range-empty{margin:0;padding:6px 8px;color:#5a5a63}
.hx-range-form{margin-top:8px;padding-top:8px;border-top:1px solid #cbd5ee}
.hx-range-form label{display:block;margin-bottom:4px;font-size:11px;font-weight:700;color:#303036}
.hx-range-form>div{display:flex;gap:6px}
.hx-range-form input{box-sizing:border-box;min-width:0;flex:1 1 auto;padding:7px 8px;border:1px solid #aaa;border-radius:5px;background:#fff;color:#171719;font:13px ui-monospace,SFMono-Regular,Menlo,monospace}
.hx-range-form button{flex:0 0 auto;padding:7px 12px;border:1px solid #2947c7;border-radius:5px;background:#2947c7;color:#fff;font:700 12px system-ui,sans-serif;cursor:pointer}
.hx-range-form button:disabled{cursor:wait;opacity:.5}
.hx-range-error{margin:6px 0 0;color:#8b1a1a;font-size:12px}
@media(prefers-color-scheme:dark){
.hx-range-bar{border-color:#46547f;background:#242b45;color:#f3f4fa}
.hx-range-change{background:#17191d;color:#aebcff;border-color:#7d91ff}
.hx-range-change:hover{background:#303752}
.hx-range-picker,.hx-range-form{border-color:#46547f}
.hx-range-commit{color:#f3f4fa}
.hx-range-commit:hover,.hx-range-commit[data-selected=true]{border-color:#53b9a9;background:#1d433d}
.hx-range-commit time,.hx-range-empty{color:#b8bbc5}
.hx-range-form label{color:#e8e7e2}
.hx-range-form input{border-color:#666b78;background:#17191d;color:#f3f4fa}
.hx-range-error{color:#ff9a9a}
}
@media(max-width:640px){
.hx-range-bar{margin:8px 16px 0;padding:8px}
.hx-range-row{align-items:flex-start;flex-wrap:wrap;gap:6px}
.hx-range-copy{flex:1 1 100%}
.hx-range-change{min-height:44px}
.hx-range-picker{margin-top:6px}
.hx-range-commits{max-height:none}
.hx-range-commit{grid-template-columns:5.5em 6.5em minmax(0,1fr);padding:8px 6px;min-height:44px}
.hx-range-form input,.hx-range-form button{min-height:44px}
.hx-range-form input{font-size:16px}
}
@media(max-width:800px){
.hx-range-bar{margin-top:64px}
}
.hx-toolbar{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);display:flex;gap:4px;align-items:center;background:#fff;border:1px solid #ddd;border-radius:12px;box-shadow:0 8px 28px rgba(30,30,40,.14);padding:6px;z-index:900;font:13px system-ui}
.hx-toolbar button{font:600 12.5px system-ui;border:none;background:transparent;border-radius:8px;padding:8px 14px;cursor:pointer}
.hx-toolbar button:disabled{cursor:default;opacity:.45}
.hx-mobile-handoff{display:none}
.hx-toolbar button[aria-pressed=true]{background:#fbf3e2;color:#b47308}
.hx-toolbar .hx-status{color:#888;font-size:11.5px;padding:0 10px}
.hx-service-index-link{position:fixed;top:12px;left:12px;z-index:1000;display:inline-flex;align-items:center;min-height:44px;box-sizing:border-box;padding:8px 12px;border:1px solid #d9d8d3;border-radius:8px;background:rgba(255,255,255,.96);box-shadow:0 5px 18px rgba(30,30,40,.13);color:#087f73;font:650 12px/1 system-ui;text-decoration:none;backdrop-filter:blur(8px)}
.hx-service-index-link:hover{background:#f4f3ef;border-color:#aaa;color:#075f57}
.hx-service-index-link:focus-visible{outline:3px solid #f59e0b;outline-offset:3px}
.hx-panel{position:fixed;top:0;right:0;width:330px;height:100vh;background:#f4f3ef;border-left:1px solid #ddd;z-index:800;display:none;flex-direction:column;font:13px system-ui;box-shadow:none}
.hx-panel.open{display:flex;box-shadow:-8px 0 30px rgba(30,30,40,.12)}
body.hx-panel-open{padding-right:330px}
.hx-panel-head{position:relative;min-height:44px;padding:14px 16px 14px 52px;box-sizing:border-box;border-bottom:1px solid #ddd;font-weight:650}
.hx-panel-head .hx-sub{font-weight:400;font-size:11px;color:#888}
.hx-panel-toggle{position:absolute;top:8px;left:7px;width:30px;height:30px;border:1px solid #ccc;background:#fff;border-radius:7px;color:#555;cursor:pointer;font:18px/1 system-ui;display:grid;place-items:center;padding:0}
.hx-panel-toggle:hover{background:#e8e7e2;color:#222}
.hx-panel-gear{position:absolute;top:8px;right:10px;width:30px;height:30px;border:1px solid #ccc;background:#fff;border-radius:7px;color:#555;cursor:pointer;font:14px/1 system-ui;display:grid;place-items:center;padding:0}
.hx-panel-gear:hover{background:#e8e7e2;color:#222}
.hx-panel-gear[aria-expanded=true]{background:#fbf3e2;color:#b47308;border-color:#e3d3ab}
.hx-settings{padding:12px 16px;border-bottom:1px solid #ddd;background:#efeee9}
.hx-set-row{display:grid;grid-template-columns:1fr auto;gap:2px 10px;align-items:center;font:650 12px system-ui;color:#444}
.hx-set-row input[type=range]{grid-column:1/-1;width:100%;margin:4px 0 0;accent-color:#087f73;min-height:24px;touch-action:manipulation}
.hx-set-val{font:650 11.5px ui-monospace,Menlo,monospace;color:#777}
.hx-set-note{margin:7px 0 0;font:400 11px/1.45 system-ui;color:#888}
.hx-panel-content{display:flex;flex:1;min-height:0;flex-direction:column}
.hx-thread-dock{position:fixed;top:12px;right:12px;z-index:850;display:flex;flex-direction:column;gap:6px;padding:6px;background:rgba(255,255,255,.94);border:1px solid #d9d8d3;border-radius:11px;box-shadow:0 5px 18px rgba(30,30,40,.13);font:12px system-ui;transition:opacity .15s,transform .15s;backdrop-filter:blur(8px)}
body.hx-panel-open .hx-thread-dock{opacity:0;transform:translateX(10px);pointer-events:none}
.hx-dock-open,.hx-dock-thread{position:relative;width:32px;height:32px;box-sizing:border-box;border:1px solid #d4d3ce;background:#fff;border-radius:7px;color:#555;cursor:pointer;display:grid;place-items:center;padding:0}
.hx-dock-open:hover,.hx-dock-thread:hover{background:#f4f3ef;border-color:#aaa;color:#222}
.hx-dock-open svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.hx-unread-badge{position:absolute;top:-6px;right:-6px;min-width:17px;height:17px;box-sizing:border-box;border:2px solid #fff;border-radius:999px;padding:0 3px;background:#315fbd;color:#fff;display:grid;place-items:center;font:700 9px/1 system-ui;box-shadow:0 1px 4px rgba(20,20,30,.28)}
.hx-unread-badge[hidden]{display:none}
.hx-dock-threads{display:flex;flex-direction:column;gap:6px;max-height:calc(100vh - 76px);overflow-y:auto;scrollbar-width:none}
.hx-dock-threads:not(:empty){border-top:1px solid #e1e0dc;padding-top:6px}
.hx-dock-threads::-webkit-scrollbar{display:none}
.hx-dock-thread{font:700 11px/1 ui-monospace,monospace}
.hx-dock-thread[data-s=draft],.hx-dock-thread[data-s=pending]{border-color:#d98e04;color:#9a6100;background:#fff8e9}
.hx-dock-thread[data-s=acknowledged]{border-color:#315fbd;color:#264f9e;background:#edf2ff}
.hx-dock-thread[data-s=resolved]{border-color:#69a76b;color:#3d8c40;background:#f1f8f1}
.hx-dock-thread.active{box-shadow:0 0 0 2px rgba(217,142,4,.28)}
.hx-threads{flex:1;overflow-y:auto;padding:10px 12px;display:flex;flex-direction:column;gap:8px}
.hx-thread{background:#fff;border:1px solid #ddd;border-radius:8px;padding:10px 12px;cursor:pointer}
.hx-thread.resolved-collapsed{padding:8px 10px}
.hx-thread.active{border-color:#d98e04;box-shadow:0 0 0 1px #d98e04}
.hx-thread-summary{display:flex;align-items:center;gap:6px;min-width:0}
.hx-anchor{flex:1;font-family:ui-monospace,monospace;font-size:10.5px;color:#999;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hx-pill{font-size:9.5px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;border-radius:4px;padding:2px 6px}
.hx-disclosure{border:0;background:transparent;color:#888;border-radius:4px;padding:0 2px;cursor:pointer;font:15px/1 system-ui}
.hx-disclosure:hover{background:#e8e7e2;color:#444}
.hx-thread-preview{margin-top:5px;color:#777;font-size:11.5px;line-height:1.3;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hx-pill[data-s=draft],.hx-pill[data-s=pending]{color:#b47308;background:#fbf3e2}
.hx-pill[data-s=acknowledged]{color:#264f9e;background:#e8eefb}
.hx-pill[data-s=resolved]{color:#3d8c40;background:#e8f2e8}
.hx-msg{margin-top:7px;font-size:12.5px;line-height:1.45}
.hx-who{font-size:10px;font-weight:700;color:#999;text-transform:uppercase}
.hx-quote{display:block;border-left:2px solid #ddd;padding-left:7px;color:#999;font-style:italic;font-size:11.5px;margin:2px 0}
.hx-msg-actions{display:flex;gap:6px;margin-top:3px}
.hx-msg-actions .hx-btn{font-size:10.5px;padding:3px 8px;margin-top:2px}
.hx-composer textarea{width:100%;min-height:56px;font:12.5px system-ui;border:1px solid #ccc;border-radius:6px;padding:6px 8px;box-sizing:border-box;margin-top:6px}
.hx-btn{font:600 12px system-ui;border:1px solid #ccc;background:#fff;border-radius:6px;padding:5px 12px;cursor:pointer;margin:6px 6px 0 0}
.hx-btn.pri{background:#22242a;color:#fff;border-color:#22242a}
.hx-handoff{border-top:1px solid #ddd;padding:12px 16px;display:flex;justify-content:space-between;align-items:center}
.hx-handoff .hx-note{font-size:11.5px;color:#888}
.hx-pin{position:absolute;z-index:700;width:24px;height:24px;border-radius:50% 50% 50% 4px;border:none;cursor:pointer;font:600 11.5px system-ui;color:#fff;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 8px rgba(20,20,30,.3)}
.hx-pin[data-s=draft],.hx-pin[data-s=pending]{background:#d98e04}
.hx-pin[data-s=acknowledged]{background:#315fbd}
.hx-pin[data-s=resolved]{background:#fff;color:#3d8c40;border:2px solid #3d8c40}
.hx-pin.active{box-shadow:0 0 0 3px rgba(217,142,4,.3),0 2px 8px rgba(20,20,30,.3)}
[data-anchor]{position:relative}
body.hx-comment [data-anchor]{cursor:copy}
body.hx-comment [data-anchor]:hover:not(:has(:is(h1,h2,h3,h4,h5,h6,p,li,ul,ol,table,tr,td,th,blockquote,pre,code,nav,figcaption,button,input,select,textarea,label,a,output,summary,svg,[data-render-target]):hover)){outline:2px dashed #d98e04;outline-offset:6px}
body.hx-comment [data-anchor] :is(h1,h2,h3,h4,h5,h6,p,li,td,th,blockquote,pre,code,nav,figcaption,button,label,a,output,summary):hover{outline:1.5px dashed #d98e04;outline-offset:4px;border-radius:2px}
body.hx-comment [data-anchor] svg, body.hx-comment [data-anchor] svg *{cursor:copy}
body.hx-comment [data-anchor] :is(button,input,select,textarea,label,a,summary){cursor:copy}
[data-render-target]{position:relative}
.hx-ring{position:absolute;border:2px dashed #d98e04;border-radius:4px;pointer-events:none;z-index:650}
.hx-thread-ring{position:fixed;border:2px solid #d98e04;border-radius:5px;pointer-events:none;z-index:750;box-shadow:0 0 0 3px rgba(217,142,4,.18);transition:left .12s,top .12s,width .12s,height .12s}
body.hx-comment [data-render-target]:hover{border:1.5px dashed #d98e04}
body.hx-comment [data-render-target] canvas{cursor:copy!important}
.hx-badge{font:600 9.5px system-ui;text-transform:uppercase;letter-spacing:.04em;color:#0e7264;background:#e3f2f0;border-radius:4px;padding:2px 7px;margin-left:8px;vertical-align:middle}
.hx-jev-badge{font:700 10px/1 system-ui,sans-serif;text-transform:none;letter-spacing:0;border-radius:999px;padding:4px 8px;margin-left:8px;vertical-align:middle;white-space:nowrap}
.hx-jev-badge[data-state=label]{color:#204a43;background:#d9eee9}
.hx-jev-badge[data-state=unsure]{color:#78520a;background:#fff0c2}
.hx-jev-badge[data-state=unavailable]{color:#7b2525;background:#f8dddd}
.hx-jev-corpus{display:inline-block;margin-left:8px;color:#35405f;font:650 10px/1.3 system-ui,sans-serif;text-decoration:none;white-space:nowrap}
.hx-jev-corpus[href]{text-decoration:underline;text-underline-offset:2px}
.hx-jev-corpus[data-state=unsure]{color:#8b5c0b}
.hx-jev-note{box-sizing:border-box;max-width:720px;margin:12px auto 0;padding:6px 10px;border:1px solid #e0c77a;border-radius:7px;background:#fff7d6;color:#6d4b05;font:600 12px/1.35 system-ui,sans-serif}
[data-hx-jev-type=scope]{box-shadow:inset 4px 0 #b42318;background:rgba(180,35,24,.08)}
[data-hx-jev-type=behavioral]{box-shadow:inset 3px 0 #b45309;background:rgba(180,83,9,.06)}
[data-hx-jev-type=clarification]{box-shadow:inset 2px 0 #28756a;background:rgba(40,117,106,.045)}
[data-hx-jev-type=cosmetic]{opacity:.78;filter:saturate(.72)}
.hx-jev-target-flash{animation:hx-jev-flash 1.2s ease-out}
@keyframes hx-jev-flash{0%{box-shadow:0 0 0 4px rgba(41,71,199,.42)}100%{box-shadow:0 0 0 14px rgba(41,71,199,0)}}
.hx-jev-thread-label{flex:0 0 auto;color:#3d8c40;background:#e8f2e8;border-radius:4px;padding:2px 6px;font-size:9px;font-weight:700;white-space:nowrap}
.hx-jev-thread-label[data-state=unsure]{color:#78520a;background:#fff0c2}
.hx-jev-thread-label[data-state=unavailable]{color:#7b2525;background:#f8dddd}
.hx-orphan-hint{display:flex;align-items:center;flex-wrap:wrap;gap:4px;margin:7px 0;padding:7px 8px;border-left:3px solid #b47308;background:#fff8e9;color:#76500a;font-size:11.5px;line-height:1.35}
.hx-orphan-hint>span{flex:1 1 100%}
.hx-orphan-hint .hx-btn{margin:0;font-size:10.5px;padding:4px 8px}
.hx-pin-jev{position:absolute;left:calc(100% + 4px);top:50%;transform:translateY(-50%);width:max-content;max-width:120px;color:#3d8c40;background:#e8f2e8;border:1px solid #69a76b;border-radius:4px;padding:2px 4px;font:700 9px/1.1 system-ui,sans-serif;white-space:nowrap;pointer-events:none}
body.hx-focus-active [data-hx-jev-type=cosmetic]{opacity:.7;filter:saturate(.6)}
.hx-jev-coverage{display:inline-block;margin-left:8px;padding:2px 6px;border:1px solid #b7c1d8;border-radius:4px;background:#f4f6fb;color:#35405f;font:650 10px/1.3 system-ui,sans-serif;vertical-align:middle}
.hx-jev-coverage[data-state=unsure]{border-color:#c69b4d;background:#fff8e9;color:#8b5c0b}
.hx-jev-coverage[data-state=unavailable]{border-color:#c98282;background:#fff1f1;color:#8b1a1a}
@media(prefers-color-scheme:dark){
.hx-jev-coverage{border-color:#596480;background:#242b45;color:#d9e0ff}
.hx-jev-coverage[data-state=unsure]{border-color:#a77c32;background:#3c301d;color:#ffd98a}
.hx-jev-coverage[data-state=unavailable]{border-color:#a65d5d;background:#3a2020;color:#ffb4b4}
}
.hx-banner{position:fixed;top:0;left:0;right:0;background:#12897c;color:#fff;font:600 13px system-ui;padding:8px 16px;z-index:950;display:flex;gap:14px;align-items:center;justify-content:center}
.hx-toast{position:fixed;bottom:76px;left:50%;transform:translateX(-50%);background:#22242a;color:#faf9f6;font:600 12.5px system-ui;border-radius:8px;padding:9px 16px;box-shadow:0 8px 28px rgba(30,30,40,.3);z-index:960;opacity:0;transition:opacity .25s;pointer-events:none}
.hx-toast.show{opacity:1}
.hx-banner button{font:inherit;border:1px solid #fff;background:transparent;color:#fff;border-radius:6px;padding:3px 12px;cursor:pointer}
@media(max-width:640px){
.hx-toolbar{left:12px;right:12px;bottom:calc(10px + env(safe-area-inset-bottom));transform:none;max-width:none;display:flex;flex-wrap:wrap;justify-content:stretch;gap:4px;padding:6px}
.hx-toolbar button{min-height:44px;flex:1 1 auto;padding:8px 12px;touch-action:manipulation}
.hx-mobile-handoff{display:block}
.hx-toolbar .hx-status{flex:1 0 100%;min-width:0;padding:2px 8px 4px;overflow:hidden;text-align:center;text-overflow:ellipsis;white-space:nowrap}
.hx-panel{width:100vw;height:100dvh;max-height:100dvh;border-left:0}
body.hx-panel-open{padding-right:0;overflow:hidden}
.hx-panel-head{min-height:56px;padding:18px 16px 14px 60px}
.hx-panel-toggle{top:6px;left:6px;width:44px;height:44px;touch-action:manipulation}
.hx-panel-gear{top:6px;right:8px;width:44px;height:44px;touch-action:manipulation}
.hx-thread-dock{top:calc(8px + env(safe-area-inset-top));right:8px;padding:4px}
.hx-service-index-link{top:calc(8px + env(safe-area-inset-top));left:8px;max-width:calc(100vw - 68px);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hx-dock-open,.hx-dock-thread{width:44px;height:44px;touch-action:manipulation}
.hx-dock-threads{max-height:calc(100dvh - 68px)}
.hx-threads{padding:12px;overscroll-behavior:contain}
.hx-thread{padding:12px}
.hx-disclosure{min-width:44px;min-height:44px;touch-action:manipulation}
.hx-composer textarea{min-height:120px;font-size:16px;padding:10px 12px}
.hx-btn,.hx-msg-actions .hx-btn{min-height:44px;padding:8px 12px;touch-action:manipulation}
.hx-handoff{gap:8px;flex-wrap:wrap;padding:10px 12px calc(10px + env(safe-area-inset-bottom))}
.hx-handoff .hx-note{flex:1 1 auto}
.hx-handoff .hx-btn{flex:1 1 auto;margin:0}
.hx-pin{width:44px;height:44px;font-size:12px;touch-action:manipulation}
.hx-pin-jev{left:auto;right:calc(100% + 4px);max-width:110px;text-align:right}
.hx-jev-note{margin:8px 16px 0}
.hx-jev-badge{font-size:9px;padding:4px 6px;margin-left:5px}
.hx-banner{align-items:flex-start;flex-wrap:wrap;padding:calc(8px + env(safe-area-inset-top)) 12px 8px;text-align:center}
.hx-banner button{min-height:44px;padding:8px 12px;touch-action:manipulation}
.hx-toast{bottom:calc(112px + env(safe-area-inset-bottom));max-width:calc(100vw - 24px);box-sizing:border-box;text-align:center}
}
@media(prefers-reduced-motion:reduce){
.hx-thread-dock,.hx-thread-ring,.hx-toast{transition:none}
}
@media(prefers-color-scheme:dark){
.hx-toolbar,.hx-thread{background:#24272c;border-color:#3a3d42;color:#e8e7e2}
.hx-toolbar button{color:#e8e7e2}
.hx-panel{background:#1d2024;border-color:#3a3d42;color:#e8e7e2}
.hx-panel-head{border-color:#3a3d42}
.hx-handoff{border-color:#3a3d42}
.hx-btn{background:#24272c;color:#e8e7e2;border-color:#4a4d52}
.hx-btn.pri{background:#e8e7e2;color:#1d2024}
.hx-composer textarea{background:#17191d;color:#e8e7e2;border-color:#4a4d52}
.hx-disclosure:hover{background:#33363c;color:#e8e7e2}
.hx-panel-toggle{background:#24272c;color:#e8e7e2;border-color:#4a4d52}
.hx-panel-toggle:hover{background:#33363c;color:#fff}
.hx-panel-gear{background:#24272c;color:#e8e7e2;border-color:#4a4d52}
.hx-panel-gear:hover{background:#33363c;color:#fff}
.hx-settings{background:#1e2126;border-color:#3a3d42}
.hx-set-row{color:#cfd2d6}
.hx-set-val,.hx-set-note{color:#9aa0a6}
.hx-thread-dock{background:rgba(29,32,36,.94);border-color:#3a3d42}
.hx-dock-open,.hx-dock-thread{background:#24272c;border-color:#4a4d52;color:#d7d6d1}
.hx-dock-open:hover,.hx-dock-thread:hover{background:#33363c;border-color:#686b71;color:#fff}
.hx-unread-badge{border-color:#1d2024;background:#668fe5;color:#101722}
.hx-dock-threads:not(:empty){border-color:#3a3d42}
.hx-dock-thread[data-s=draft],.hx-dock-thread[data-s=pending]{background:#342d20;color:#e0a33b}
.hx-dock-thread[data-s=acknowledged]{background:#1f2f54;color:#91b2ff}
.hx-dock-thread[data-s=resolved]{background:#263728;color:#78b87a}
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
  bar.innerHTML = '<button id="hx-mode" aria-pressed="false">✛ Comment (C)</button><button class="hx-mobile-handoff" id="hx-mobile-handoff" type="button" disabled>Hand off</button><button id="hx-connect" hidden>Connect review folder</button><button id="hx-repick" hidden>Choose different folder</button><span class="hx-status" id="hx-status">starting…</span>';
  document.body.appendChild(bar);

  const dock = document.createElement('nav');
  dock.className = 'hx-thread-dock';
  dock.setAttribute('aria-label', 'Review conversations');
  dock.innerHTML = '<button class="hx-dock-open" id="hx-dock-open" type="button" aria-label="Open review sidebar"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5.5h14v10H10l-5 3v-13Z"></path><path d="M8 9h8M8 12h5"></path></svg><span class="hx-unread-badge" id="hx-unread-badge" aria-hidden="true" hidden></span></button><div class="hx-dock-threads" id="hx-dock-threads"></div>';
  document.body.appendChild(dock);

  const panel = document.createElement('aside');
  panel.className = 'hx-panel';
  panel.setAttribute('aria-label', 'Review sidebar');
  panel.innerHTML = '<div class="hx-panel-head">Review <span class="hx-sub" id="hx-agent"></span><button class="hx-panel-toggle" id="hx-panel-toggle" type="button" aria-expanded="false" aria-label="Open review sidebar">‹</button><button class="hx-panel-gear" id="hx-panel-gear" type="button" aria-expanded="false" aria-controls="hx-settings" aria-label="Review display settings" title="Display settings">⚙</button></div><div class="hx-panel-content" id="hx-panel-content" aria-hidden="true" inert><div class="hx-settings" id="hx-settings" hidden><label class="hx-set-row" for="hx-veil">Diff visibility<span class="hx-set-val" id="hx-veil-val">100%</span><input type="range" id="hx-veil" min="0" max="100" step="5" value="100"></label><p class="hx-set-note">How strongly unchanged blocks are dimmed and blurred. At 0 the whole spec reads at normal clarity.</p></div><div class="hx-threads" id="hx-threads"></div><div class="hx-handoff"><span class="hx-note" id="hx-drafts">0 drafts</span><button class="hx-btn pri" id="hx-handoff">Hand off to agent →</button></div></div>';
  document.body.appendChild(panel);

  document.getElementById('hx-mode').addEventListener('click', () => setCommentMode(!state.commentMode));
  document.getElementById('hx-mobile-handoff').addEventListener('click', handoff);
  document.getElementById('hx-dock-open').addEventListener('click', () => openPanel(true));
  document.getElementById('hx-panel-toggle').addEventListener('click', () => openPanel(!state.panelOpen));
  document.getElementById('hx-handoff').addEventListener('click', handoff);
  setupDiffVisibility();
  document.addEventListener('keydown', e => {
    if (commentModeShortcut(e)) setCommentMode(!state.commentMode);
    if (e.key === 'Escape') { state.composer = null; setCommentMode(false); renderPanel(); }
  });
  document.addEventListener('click', onDocClick, true); // capture: runs before spec-script handlers
  // suspend page interactivity while commenting; hover and text selection stay live
  const INTERACTIVE = 'button, input, select, textarea, label, a, summary, [role="button"], [role="link"]';
  const suspend = e => {
    if (!state.commentMode) return;
    if (e.target.closest && e.target.closest('.hx-pin,.hx-panel,.hx-thread-dock,.hx-toolbar,.hx-range-bar,.hx-service-index-link,#hx-errors')) return;
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
    if (state.commentMode && t instanceof Element && !t.closest('.hx-pin,.hx-panel,.hx-thread-dock,.hx-toolbar,.hx-range-bar')) {
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

function setCommentMode(on) {
  state.commentMode = on;
  document.body.classList.toggle('hx-comment', on);
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

function openComposer(anchorId, target, quote) {
  state.composer = { kind: 'comment', anchorId, target, quote, text: '' };
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
    await state.transport.postEvent(body);
    state.composer = null;
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
    const resolvedHint = jevItem('resolved', th.id);
    const orphanHint = jevItem('orphan', th.id);
    const threadJevState = [orphanHint, resolvedHint].find(item => item && ['unsure', 'unavailable'].includes(item.state));
    d.innerHTML = '<div class="hx-thread-summary"><div class="hx-anchor">' + esc(label(b)) + '</div>' +
      '<span class="hx-pill" data-s="' + th.status + '">' + th.status + '</span>' +
      (resolvedHint && resolvedHint.state === 'label' && resolvedHint.label === 'resolved in spirit' ? '<span class="hx-jev-thread-label">Looks resolved</span>' : '') +
      (threadJevState ? '<span class="hx-jev-thread-label" data-state="' + threadJevState.state + '">' + esc(jevDisplayLabel(threadJevState)) + '</span>' : '') +
      (th.status === 'resolved' ? '<button class="hx-disclosure" data-act="disclosure" aria-expanded="' + String(!collapsed) + '" aria-label="' + (collapsed ? 'Show' : 'Hide') + ' resolved thread">' + (collapsed ? '▸' : '▾') + '</button>' : '') + '</div>' +
      (collapsed ? '<div class="hx-thread-preview">' + esc(b.text || 'Resolved comment') + '</div>' : '');
    if (orphanHint && orphanHint.state === 'label' && orphanHint.target) {
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
      d.appendChild(hint);
    }
    if (!collapsed) {
      for (const message of th.messages) {
        const m = message.body;
        const item = document.createElement('div');
        item.className = 'hx-msg';
        item.dataset.messageId = m.id;
        item.innerHTML = '<span class="hx-who">' + (message.actor === 'human' ? 'You' : 'Agent') + '</span>' +
          (m.quote ? '<span class="hx-quote">“' + esc(m.quote) + '”</span>' : '') + esc(m.text || '');
        if (message.actor === 'human' && m.id === th.latestHumanId && ['draft', 'pending'].includes(th.status)) {
          const actions = document.createElement('div');
          actions.className = 'hx-msg-actions';
          actions.innerHTML = '<button class="hx-btn" data-act="edit">Edit</button>';
          actions.querySelector('button').addEventListener('click', e => { e.stopPropagation(); startEdit(th, message); });
          item.appendChild(actions);
        }
        d.appendChild(item);
      }
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
    d.querySelector('[data-act=resolve]')?.addEventListener('click', async e => {
      e.stopPropagation();
      await state.transport.postEvent({ id: humanId('s'), event: 'status', respondsTo: th.id, threadId: th.id, status: 'resolved', actor: 'human', createdAt: new Date().toISOString(), schemaVersion: 1 });
      state.expandedResolved.delete(th.id);
      toast('Resolved');
      refresh();
    });
    wrap.appendChild(d);
  }
  const handoffState = reviewHandoffState(state.threads, Boolean(document.querySelector('[data-spec-tbd]')));
  const drafts = handoffState.drafts;
  document.getElementById('hx-drafts').textContent = handoffState.finish ? 'Review complete' : drafts + ' draft' + (drafts === 1 ? '' : 's');
  const desktopHandoff = document.getElementById('hx-handoff');
  desktopHandoff.disabled = !handoffState.enabled;
  desktopHandoff.textContent = handoffState.finish ? 'Finish review' : handoffState.tbd ? 'TBD open' : 'Hand off to agent →';
  const mobileHandoff = document.getElementById('hx-mobile-handoff');
  mobileHandoff.disabled = !handoffState.enabled;
  mobileHandoff.textContent = handoffState.finish ? 'Finish review' : handoffState.tbd ? 'TBD open' : drafts ? 'Hand off (' + drafts + ')' : 'Hand off';
  renderThreadDock();
  renderThreadHighlight();
}

function selectThread(th, scroll) {
  state.activeThread = th.id;
  if (th.status === 'resolved') state.expandedResolved.add(th.id);
  openPanel(true);
  renderPanel();
  renderPins();
  if (scroll) scrollToThread(th.ev.body);
  renderThreadHighlight();
}

function scrollToThread(b) {
  findAnchor(b.anchorId)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
}

function scrollToJevAnchor(anchorId) {
  const holder = findAnchor(anchorId);
  if (!holder) return;
  holder.scrollIntoView({ block: 'center', behavior: 'smooth' });
  holder.classList.remove('hx-jev-target-flash');
  requestAnimationFrame(() => holder.classList.add('hx-jev-target-flash'));
}

async function moveOrphan(th, target) {
  if (!th || !target || state.movingOrphans.has(th.id)) return;
  state.movingOrphans.add(th.id);
  renderPanel();
  const original = th.ev.body || {};
  const quote = original.quote || original.text || '';
  try {
    await state.transport.postEvent({
      id: humanId('u'), event: 'comment', anchorId: target, target: null,
      quote, text: original.text || 'Moved comment', actor: 'human',
      createdAt: new Date().toISOString(), schemaVersion: 1,
    });
    await state.transport.postEvent({
      id: humanId('s'), event: 'status', respondsTo: th.id, threadId: th.id,
      status: 'resolved', actor: 'human', createdAt: new Date().toISOString(), schemaVersion: 1,
    });
    toast('Comment moved to #' + target);
    state.activeThread = null;
    await refresh();
  } catch (_) {
    toast('Could not move comment');
  } finally {
    state.movingOrphans.delete(th.id);
    renderPanel();
    renderPins();
  }
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

function renderThreadHighlight() {
  let ring = document.querySelector('.hx-thread-ring');
  const th = state.activeThread && state.threads.get(state.activeThread);
  const b = th && th.ev.body;
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
  if (!t) return { top: 4, left: holder.clientWidth - 30 };
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
  if (t.type === 'element') {
    const el = resolveElement(holder, t.key);
    if (el) {
      const hR = holder.getBoundingClientRect(), eR = el.getBoundingClientRect();
      return { top: eR.top - hR.top - 4, left: Math.min(holder.clientWidth - 28, eR.right - hR.left - 12) };
    }
  }
  return { top: 4, left: holder.clientWidth - 30 }; // orphan/text fallback: block corner
}

function renderPins() {
  document.querySelectorAll('.hx-pin').forEach(p => p.remove());
  let n = 0;
  for (const th of state.threads.values()) {
    n++;
    const b = th.ev.body;
    const holder = findAnchor(b.anchorId);
    if (!holder) continue;
    const pos = pinPos(b, holder);
    const pin = document.createElement('button');
    pin.className = 'hx-pin' + (state.activeThread === th.id ? ' active' : '');
    pin.dataset.s = th.status;
    const looksResolved = Boolean(jevItem('resolved', th.id) && jevItem('resolved', th.id).state === 'label' && jevItem('resolved', th.id).label === 'resolved in spirit');
    pin.textContent = '';
    const number = document.createElement('span');
    number.className = 'hx-pin-number';
    number.textContent = n;
    pin.appendChild(number);
    if (looksResolved) {
      pin.dataset.jev = 'resolved';
      const marker = document.createElement('span');
      marker.className = 'hx-pin-jev';
      marker.textContent = 'Looks resolved';
      pin.appendChild(marker);
    }
    pin.title = (looksResolved ? 'Looks resolved · ' : '') + label(b);
    pin.setAttribute('aria-label', (looksResolved ? 'Looks resolved: ' : '') + label(b));
    pin.style.top = pos.top + 'px';
    const pinSize = window.matchMedia('(max-width: 640px)').matches ? 44 : 24;
    pin.style.left = Math.max(0, Math.min(pos.left, holder.clientWidth - pinSize)) + 'px';
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

async function handoff() {
  const tbdEl = document.querySelector('[data-spec-tbd]');
  const action = reviewHandoffState(state.threads, Boolean(tbdEl));
  if (action.tbd) return jumpToTbd(tbdEl);
  if (state.handoffPosting || !action.enabled) return;
  state.handoffPosting = true;
  try {
    await state.transport.postEvent({ id: 'h' + Date.now().toString(36), event: 'handoff', anchorId: '', target: null, quote: null, text: 'batch from ' + state.transport.mode, actor: 'human', createdAt: new Date().toISOString(), schemaVersion: 1 });
    toast(action.finish ? 'Review finished' : 'Handed off ' + action.drafts + ' comment' + (action.drafts === 1 ? '' : 's') + ' — agent notified');
    refresh();
  } finally {
    state.handoffPosting = false;
  }
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

/* ---------------- loops ---------------- */
async function refresh() {
  try {
    ingest(await state.transport.listEvents());
    const agentEvents = state.events.filter(e => e.actor === 'agent');
    const last = agentEvents[agentEvents.length - 1];
    const observation = handoffObservation(state.events, Date.now());
    document.getElementById('hx-agent').textContent = observation === 'waiting'
      ? '· handed off, waiting for agent'
      : observation === 'queued'
        ? '· automatic wake did not occur; send a new chat message to resume'
        : last
          ? '· agent last event ' + new Date(last.body.createdAt).toLocaleTimeString()
          : '· no agent events yet';
    status('connected · ' + state.transport.label + ' · ' + state.threads.size + ' threads');
    if (location.hash.includes('hxdebug') && !state._beaconed) {
      state._beaconed = true;
      fetch('/hxdebug/threads=' + state.threads.size + '/pins=' + document.querySelectorAll('.hx-pin').length + '/charts=' + state.charts.size).catch(() => {});
    }
  } catch (e) { status('event sync failed: ' + e.message); }
}

async function watchSpec() {
  const m = await state.transport.specModified();
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

/* ---------------- boot ---------------- */
(async function boot() {
  mountUI();
  const httpPage = !EMBED_REVIEW_DIR && ['http:', 'https:'].includes(location.protocol);
  if (httpPage && new URLSearchParams(location.search).get('focus') === 'changes') applyIssueFocus();
  else if (httpPage) loadRangeBar();
  await hydrateIslands();
  adoptForeignCharts();
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
  setInterval(watchSpec, 5000);
  // Pins live inside anchored holders; a host framework re-rendering a holder (React
  // remounts, spec scripts rebuilding DOM) silently drops them. Redraw is idempotent.
  setInterval(() => { renderPins(); renderThreadHighlight(); }, 2000);
  window.addEventListener('resize', () => { renderPins(); renderThreadHighlight(); });
}

/* ---------------- ANN-108 reading view ----------------
 * This block owns the audience toggle and its HTTP-only Jev request. It never
 * changes document order or removes clauses. Git focus and reading view are
 * mutually exclusive display modes.
 */
const readingView = { active: false };

function readingAnchor(id) {
  const wanted = String(id || '');
  return [...document.querySelectorAll('[data-anchor]')].find(el => el.dataset.anchor === wanted) || null;
}

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
  if (next === readingView.active && !next) {
    clearReadingAudience();
    return;
  }
  if (next) clearGitFocusForReading();
  readingView.active = next;
  document.body.classList.toggle('hx-reading-active', next);
  const button = document.getElementById('hx-reading');
  if (button) {
    button.setAttribute('aria-pressed', String(next));
    button.textContent = next ? 'Reading view on' : 'Reading view';
  }
  if (!next) clearReadingAudience();
  state.readingView = next;
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
@media(prefers-color-scheme:dark){.hx-reading-active [data-hx-audience="internals"]{color:#b9c0ca!important}}`;
  document.head.appendChild(style);
  const button = document.createElement('button');
  button.id = 'hx-reading';
  button.type = 'button';
  button.textContent = 'Reading view';
  button.setAttribute('aria-pressed', 'false');
  button.addEventListener('click', () => setReadingView(!readingView.active));
  toolbar.insertBefore(button, document.getElementById('hx-status'));
  document.body.classList.toggle('hx-reading-active', readingView.active);
  const focusObserver = new MutationObserver(() => {
    if (readingView.active && document.body.classList.contains('hx-focus-active')) setReadingView(false);
  });
  focusObserver.observe(document.body, { attributes: true, attributeFilter: ['class'] });
  Object.defineProperty(readingView, 'mounted', { value: true, configurable: true });
}

const readingClassObserver = new MutationObserver(() => {
  if (readingView.mounted) document.body.classList.toggle('hx-reading-active', readingView.active);
});
readingClassObserver.observe(document.body, { attributes: true, attributeFilter: ['class'] });
setTimeout(mountReadingView, 0);

})();
