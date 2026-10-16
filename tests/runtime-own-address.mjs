// review-service #served (#served-own-address, #served-table, #served-rewrite, #served-move): the runtime's own
// address, given a document whose base address differs from its location and one with none (#proof-served-seam).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
const slice = (from, to) => {
  const a = runtime.indexOf(from), b = runtime.indexOf(to, a);
  assert.ok(a >= 0 && b > a, 'runtime exposes ' + from);
  return runtime.slice(a, b);
};

const SERVICE = 'https://specs.example';
const HOST = 'https://bb.example/plugins/servlet/spec-chat/view?u=7'; // where a host serves the page

// A page's location; replaceState moves it, as the browser does.
function place(href) {
  const location = { assigned: [] };
  let current = new URL(href);
  for (const key of ['pathname', 'search', 'hash', 'protocol', 'origin']) {
    Object.defineProperty(location, key, { get: () => current[key] });
  }
  Object.defineProperty(location, 'href', { get: () => current.href, set: value => location.assigned.push(value) });
  const history = { calls: [], replaceState(_, __, url) { history.calls.push(url); current = new URL(url, current); } };
  return { location, history };
}

// The boot constants and the HTTP transport, with no base element (base address = location) or with one.
function page({ href, base = null }) {
  const { location, history } = place(href);
  const document = { get baseURI() { return base || location.href; }, currentScript: null };
  const fetched = [];
  const fetch = async (url, init = {}) => {
    fetched.push((init.method || 'GET') + ' ' + url);
    return { ok: true, json: async () => [], headers: { get: () => null } };
  };
  const code = slice('// review-service #served-own-address', '\n\n/* ---------------- error overlay')
    + '\n' + slice('function httpTransport(', '\n\nfunction classifyAnchorSignatures(');
  const api = Function('document', 'location', 'fetch', code
    + '; return { OWN_ADDRESS, SPEC_FILE, REVIEW_DIR, transport: httpTransport() };')(document, location, fetch);
  return { ...api, location, history, document, fetched };
}

// #proof-served-first-test: a page whose base address names a spec other than its location's reads that spec's spool.
{
  const p = page({ href: SERVICE + '/docs/specs/a.spec.html#intro', base: SERVICE + '/docs/specs/b.spec.html' });
  assert.equal(p.OWN_ADDRESS.href, SERVICE + '/docs/specs/b.spec.html', 'own address is the base address');
  assert.equal(p.SPEC_FILE, 'b.spec.html');
  assert.equal(p.REVIEW_DIR, 'docs/specs/b.spec.html.review');
  await p.transport.listEvents();
  await p.transport.specModified();
  assert.deepEqual(p.fetched, [
    'GET /api/events?dir=' + encodeURIComponent('docs/specs/b.spec.html.review'),
    'HEAD /docs/specs/b.spec.html',
  ], 'requests name the base address spec; the browser resolves them against the base address origin');
}

// A page served at another address: location is the host's, base address the service's.
{
  const p = page({ href: HOST + '#served', base: SERVICE + '/docs/specs/b.spec.html?focus=changes&base=abc#ignored' });
  assert.equal(p.OWN_ADDRESS.href, SERVICE + '/docs/specs/b.spec.html?focus=changes&base=abc', 'fragment aside');
  assert.equal(p.OWN_ADDRESS.protocol, 'https:');
  assert.equal(p.OWN_ADDRESS.searchParams.get('base'), 'abc', 'focus and baseline come from its query');
  assert.equal(p.REVIEW_DIR, 'docs/specs/b.spec.html.review');
}

// No base element: own address is the location, fragment aside.
{
  const p = page({ href: SERVICE + '/docs/specs/a.spec.html?focus=changes#intro' });
  assert.equal(p.OWN_ADDRESS.href, SERVICE + '/docs/specs/a.spec.html?focus=changes');
  assert.equal(p.REVIEW_DIR, 'docs/specs/a.spec.html.review');
}

// Turning on focus on changes: choosing a base (#served-rewrite).
function focusPage({ href, base = null }) {
  const p = page({ href, base });
  const shown = [];
  const env = {
    OWN_ADDRESS: p.OWN_ADDRESS, location: p.location, history: p.history,
    state: { range: { loading: false, baseline: null, loaded: new Map() } },
    document: { getElementById: () => null, querySelectorAll: () => [], body: { classList: { remove: () => {} } } },
    setRangeError: () => {}, clearJev: () => {}, openRangePicker: () => {}, renderRangeBar: () => {},
    requestJev: base => shown.push('jev ' + base),
    markIssueFocus: (_, baseline) => shown.push('focus ' + baseline.base),
    fetchBaseline: async base => ({ base: base + '-full', html: null }),
    AbortController, setTimeout: () => 0, clearTimeout: () => {},
  };
  const code = slice('function rewriteAddress(', '\n\nfunction mountRangeBar(')
    + '\n' + slice('function clearGitFocusForReading(', '\n\nfunction setReadingView(');
  const api = Function(...Object.keys(env), code + '; return { selectRangeBase, clearGitFocusForReading };')(...Object.values(env));
  return { ...p, ...api, shown };
}

// #acceptance-served-location: served at another address, the page leaves its location unchanged.
{
  const p = focusPage({ href: HOST + '#served', base: SERVICE + '/docs/specs/b.spec.html' });
  await p.selectRangeBase('abc');
  assert.deepEqual(p.shown, ['focus abc-full', 'jev abc-full'], 'the page shows the changes against the chosen baseline');
  assert.deepEqual(p.history.calls, [], 'no address rewrite');
  assert.deepEqual(p.location.assigned, []);
  assert.equal(p.location.href, HOST + '#served', 'location unchanged, so a reload opens the address the host served');
  assert.equal(p.OWN_ADDRESS.searchParams.get('focus'), 'changes', 'what the page shows follows the choice');
  assert.equal(p.OWN_ADDRESS.searchParams.get('base'), 'abc-full');
  p.clearGitFocusForReading();
  assert.deepEqual(p.history.calls, [], 'reading view leaves the location unchanged too');
  assert.equal(p.OWN_ADDRESS.searchParams.get('focus'), null);
}

// #acceptance-direct-rewrite: served directly, the page's address names focus on changes and its baseline.
{
  const p = focusPage({ href: SERVICE + '/docs/specs/a.spec.html?x=1#intro' });
  await p.selectRangeBase('abc');
  assert.deepEqual(p.history.calls, ['/docs/specs/a.spec.html?x=1&focus=changes&base=abc-full#intro']);
  assert.equal(p.location.href, SERVICE + '/docs/specs/a.spec.html?x=1&focus=changes&base=abc-full#intro');
  assert.equal(p.OWN_ADDRESS.href, p.location.href.split('#')[0], 'own address stays the location');
  await p.selectRangeBase('def');
  assert.equal(p.location.search, '?x=1&focus=changes&base=def-full', 'a second choice rewrites again');
  p.clearGitFocusForReading();
  assert.equal(p.location.href, SERVICE + '/docs/specs/a.spec.html?x=1&base=def-full#intro', 'reading view drops focus');
}

// #served-move: another spec's clause opens by following a link to it, never by setting the location.
function movePage({ href, base = null }) {
  const p = page({ href, base });
  const clicks = [];
  const scrolled = [];
  const body = { children: [], appendChild(el) { body.children.push(el); el.parentNode = body; return el; } };
  const document = {
    body,
    createElement: tag => {
      const el = { tagName: tag.toUpperCase(), href: '', parentNode: null,
        remove() { body.children = body.children.filter(child => child !== el); el.parentNode = null; },
        // A click bubbles to the document's listeners, as a host's link handling sees it, only from inside the page.
        click() { if (el.parentNode) clicks.push({ tagName: el.tagName, href: el.href }); } };
      return el;
    },
  };
  const goToJevTarget = Function('OWN_ADDRESS', 'document', 'location', 'scrollToJevAnchor',
    slice('function goToJevTarget(', '\n\n/* ---------------- Jev markers') + '; return goToJevTarget;')(
    p.OWN_ADDRESS, document, p.location, anchor => scrolled.push(anchor));
  return { ...p, goToJevTarget, clicks, scrolled, body };
}

// #acceptance-served-move: the host's link handling receives a link to that spec's clause.
{
  const p = movePage({ href: HOST, base: SERVICE + '/docs/specs/a.spec.html' });
  p.goToJevTarget('docs/specs/b.spec.html#b-rule');
  assert.deepEqual(p.clicks, [{ tagName: 'A', href: SERVICE + '/docs/specs/b.spec.html#b-rule' }], 'a link at its own address origin');
  assert.deepEqual(p.location.assigned, [], 'never sets its location');
  assert.deepEqual(p.body.children, [], 'the link leaves the page after the click');
  p.goToJevTarget('docs/specs/a.spec.html#a-rule');
  assert.deepEqual(p.scrolled, ['a-rule'], 'a clause in this spec moves within the page');
  assert.equal(p.clicks.length, 1);
}

// Served directly, the same link follows to the same address.
{
  const p = movePage({ href: SERVICE + '/docs/specs/a.spec.html' });
  p.goToJevTarget('/docs/specs/b.spec.html#b-rule');
  assert.deepEqual(p.clicks, [{ tagName: 'A', href: SERVICE + '/docs/specs/b.spec.html#b-rule' }]);
  assert.deepEqual(p.location.assigned, []);
}

// No location read decides what the page addresses (#served-own-address); its fragment, rewrite check, and reload stay.
{
  const reads = [...runtime.matchAll(/\blocation\.(\w+)/g)].map(m => m[1]);
  assert.deepEqual([...new Set(reads)].sort(), ['hash', 'href', 'reload'], 'location gives only its fragment, its href to rewrite, and reload');
  assert.doesNotMatch(runtime, /location\.href\s*=[^=]/, 'no script move sets the location');
}

console.log('ok runtime-own-address');
