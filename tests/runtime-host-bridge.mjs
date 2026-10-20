// Host bridge tab steps (criterion-evidence #bridge-title, #bridge-open-tab, #bridge-new-tab-click, #bridge-tab-proof)
// and the index's lane Proof link (#bridge-tab-pages): the page's message and click handling given a parent window,
// a hello, and a click.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
const START = '/* ---------------- host bridge';
const END = '/* ---------------- end host bridge ---------------- */';
const start = runtime.indexOf(START);
const end = runtime.indexOf(END, start);
assert.ok(start >= 0 && end > start, 'runtime marks its host bridge block');
const block = runtime.slice(start, end + END.length);

const ORIGIN = 'https://specs.example';
const HOST = 'https://bb.example';

function page({ platform = 'MacIntel', framed = true, title = 'Demo spec', href = ORIGIN + '/docs/specs/a.spec.html' } = {}) {
  const posted = [];
  const windowListeners = {};
  const docListeners = {};
  const parent = { postMessage: (data, origin) => posted.push([data, origin]) };
  const window = { addEventListener: (type, fn) => (windowListeners[type] ||= []).push(fn) };
  window.parent = framed ? parent : window;
  const document = { title, baseURI: ORIGIN + '/docs/specs/a.spec.html',
    addEventListener: (type, fn, capture) => (docListeners[type] ||= []).push(fn) };
  const location = { origin: new URL(href).origin, href };
  const navigator = { platform };
  const api = Function('window', 'document', 'location', 'navigator', block + '; return { listenHost, hostBridge };')(
    window, document, location, navigator);
  api.listenHost();
  return {
    posted, parent, api,
    hello(data, source = parent, origin = HOST) { (windowListeners.message || []).forEach(fn => fn({ data, source, origin })); },
    click(href, extra = {}, bundle = null) {
      const anchor = { getAttribute: k => (k === 'href' ? href : k === 'data-evidence-bundle' ? bundle : null) };
      anchor.closest = sel => (sel === 'a[href]' || (sel === 'a[data-evidence-bundle]' && bundle !== null) ? anchor : null);
      const target = { closest: sel => anchor.closest(sel) };
      let prevented = false;
      const event = { type: 'click', button: 0, metaKey: false, ctrlKey: false, altKey: false, shiftKey: false, target,
        defaultPrevented: false, preventDefault() { prevented = true; this.defaultPrevented = true; }, ...extra };
      for (const fn of docListeners[event.type] || []) fn(event);
      return prevented;
    },
  };
}

const tab = { type: 'spec-chat-host', opens: ['tab'] };
const same = '/docs/specs/b.spec.html';
const sameAbs = ORIGIN + same;

// #bridge-tab-proof first failing test: Cmd+click on a same-origin link after a tab hello sends open-tab;
// nothing for a plain click or before the hello.
{
  const p = page();
  assert.equal(p.click(same, { metaKey: true }), false, 'before the hello: browser behavior');
  assert.deepEqual(p.posted, []);
  p.hello(tab);
  assert.deepEqual(p.posted, [[{ type: 'spec-chat-title', title: 'Demo spec' }, HOST]], '#acceptance-tab-title');
  p.posted.length = 0;
  assert.equal(p.click(same), false, '#acceptance-tab-plain');
  assert.deepEqual(p.posted, []);
  assert.equal(p.click(same, { metaKey: true }), true, '#acceptance-tab-open');
  assert.deepEqual(p.posted, [[{ type: 'spec-chat-open-tab', href: sameAbs }, HOST]]);
  p.posted.length = 0;
  // relative link resolves against the page
  assert.equal(p.click('b.spec.html#x', { metaKey: true }), true);
  assert.deepEqual(p.posted, [[{ type: 'spec-chat-open-tab', href: ORIGIN + '/docs/specs/b.spec.html#x' }, HOST]]);
  p.posted.length = 0;
  // #acceptance-tab-middle: middle-click arrives as auxclick button 1
  assert.equal(p.click(same, { type: 'auxclick', button: 1 }), true);
  assert.deepEqual(p.posted, [[{ type: 'spec-chat-open-tab', href: sameAbs }, HOST]]);
  p.posted.length = 0;
  assert.equal(p.click(same, { type: 'auxclick', button: 2 }), false, 'right button is not a new-tab click');
  // Ctrl+click on macOS is not the new-tab gesture
  assert.equal(p.click(same, { ctrlKey: true }), false);
  // #acceptance-tab-other-site
  assert.equal(p.click('https://other.example/x', { metaKey: true }), false);
  assert.equal(p.click('https://other.example/x', { type: 'auxclick', button: 1 }), false);
  // a click already taken by another handler stays theirs
  assert.equal(p.click(same, { metaKey: true, defaultPrevented: true }), false);
  // a click not on a link
  const none = page(); none.hello(tab); none.posted.length = 0;
  assert.equal(none.click(null, { metaKey: true, target: { closest: () => null } }), false);
  assert.deepEqual(p.posted, []);
  assert.deepEqual(none.posted, []);
}

// Ctrl+click elsewhere; Cmd (meta) is not the gesture there.
{
  const p = page({ platform: 'Linux x86_64' });
  p.hello(tab);
  p.posted.length = 0;
  assert.equal(p.click(same, { metaKey: true }), false);
  assert.equal(p.click(same, { ctrlKey: true }), true);
  assert.deepEqual(p.posted, [[{ type: 'spec-chat-open-tab', href: sameAbs }, HOST]]);
}

// #acceptance-tab-unframed: no listener, no message.
{
  const p = page({ framed: false });
  assert.equal(p.click(same, { metaKey: true }), false);
  assert.deepEqual(p.posted, []);
}

// #acceptance-tab-not-offered: an evidence-only hello turns on evidence, not tabs.
{
  const p = page();
  p.hello({ type: 'spec-chat-host', opens: ['evidence'] });
  assert.deepEqual(p.posted, [], 'no title without tab');
  assert.equal(p.click(same, { metaKey: true }), false);
  assert.deepEqual(p.posted, []);
  assert.equal(p.api.hostBridge.evidence, HOST);
  assert.equal(p.api.hostBridge.tab, null);
}

// #acceptance-tab-not-parent and #bridge-hello-check: wrong source, null origin, or bad opens are ignored.
{
  const p = page();
  p.hello(tab, {});
  p.hello(tab, p.parent, 'null');
  p.hello(tab, p.parent, '');
  p.hello({ type: 'spec-chat-host', opens: 'tab' });
  p.hello({ type: 'other', opens: ['tab'] });
  assert.deepEqual(p.posted, []);
  assert.equal(p.click(same, { metaKey: true }), false);
  assert.deepEqual(p.posted, []);
  assert.equal(p.api.hostBridge.evidence, null);
}

// Both offered: each step on.
{
  const p = page({ title: 42 });
  p.hello({ type: 'spec-chat-host', opens: ['evidence', 'tab'] });
  assert.deepEqual(p.posted, [[{ type: 'spec-chat-title', title: '42' }, HOST]], 'title is a string');
  assert.equal(p.api.hostBridge.evidence, HOST);
}

// #bridge-new-tab-click names the page's own address (review-service #served-own-address): served at another
// address, a link to its base address origin is a new-tab link, and one to its location's origin is not.
{
  const p = page({ href: HOST + '/plugins/servlet/spec-chat/view?u=7' });
  p.hello(tab);
  p.posted.length = 0;
  assert.equal(p.click(same, { metaKey: true }), true, 'a root path resolves against the base address');
  assert.deepEqual(p.posted, [[{ type: 'spec-chat-open-tab', href: sameAbs }, HOST]]);
  p.posted.length = 0;
  assert.equal(p.click(HOST + '/plugins/servlet/other', { metaKey: true }), false, 'the location origin is another origin');
  assert.deepEqual(p.posted, []);
}

// #bridge-tab-pages, lane-hosting #index-gates-provider: the index's lane Proof link speaks step 2 with its bundle
// alone after an evidence hello; before it, unframed, or after a tab-only hello, the link keeps the browser's behavior.
{
  const proof = 'https://evidence.example/bundles/b-1';
  const p = page();
  assert.equal(p.click(proof, {}, 'b-1'), false, 'before the hello: the link opens the bundle page');
  assert.deepEqual(p.posted, []);
  p.hello({ type: 'spec-chat-host', opens: ['tab'] });
  p.posted.length = 0;
  assert.equal(p.click(proof, {}, 'b-1'), false, 'a tab hello does not turn on evidence opens');
  assert.deepEqual(p.posted, []);
  p.hello({ type: 'spec-chat-host', opens: ['evidence'] });
  assert.equal(p.click(proof, {}, 'b-1'), true);
  assert.deepEqual(p.posted, [[{ type: 'spec-chat-open-evidence', bundle: 'b-1' }, HOST]]);
  p.posted.length = 0;
  assert.equal(p.click(proof), false, 'a link without a bundle keeps its own behavior');
  assert.equal(p.click(proof, { defaultPrevented: true }, 'b-1'), false, 'a click another handler took stays theirs');
  assert.deepEqual(p.posted, []);
  const unframed = page({ framed: false });
  assert.equal(unframed.click(proof, {}, 'b-1'), false);
  assert.deepEqual(unframed.posted, []);
}

console.log('runtime host bridge tests passed');
