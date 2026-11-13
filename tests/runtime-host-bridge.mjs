// Host bridge tab steps (host-bridge #bridge-title, #bridge-open-tab, #bridge-new-tab-click, #bridge-tab-proof)
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

function page({ platform = 'MacIntel', framed = true, title = 'Demo spec', href = ORIGIN + '/docs/specs/a.spec.html', shell = undefined } = {}) {
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
  api.listenHost(shell);
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

// #bridge-new-tab-click names the page's own address (served-pages #served-own-address): served at another
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

// #bridge-tab-pages, review-index #index-gates-provider: the index's lane Proof link speaks step 2 with its bundle
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

// #bridge-hello-check, shell #shell-roles-held: "comments" turns on only the shell messages. A page that can be
// held takes the hello with its origin, which it checks is a named shell before it sets hostBridge.comments; from then
// on, every other message from that parent at that origin is the shell's, and from any other source or origin none is.
{
  const heard = { hellos: [], messages: [] };
  const shell = { hello: origin => heard.hellos.push(origin), message: data => heard.messages.push(data) };
  const p = page({ shell });
  const point = { type: 'spec-chat-point', frame: 'f1' };
  p.hello(point);
  assert.deepEqual(heard, { hellos: [], messages: [] }, 'before the hello, a shell message is no one\'s');
  p.hello({ type: 'spec-chat-host', opens: ['comments'] }, p.parent, 'null');
  p.hello({ type: 'spec-chat-host', opens: ['comments'] }, {}, HOST);
  assert.deepEqual(heard.hellos, [], 'only the parent window, at a real origin');
  p.hello({ type: 'spec-chat-host', opens: ['comments'] });
  assert.deepEqual(heard.hellos, [HOST], 'the hello, with its origin');
  assert.deepEqual(p.posted, [], 'comments turns on neither the title nor evidence');
  assert.equal(p.click(same, { metaKey: true }), false, 'nor new-tab clicks');
  p.hello(point);
  assert.deepEqual(heard.messages, [], 'until the page sets the named shell, its messages are no one\'s');
  p.api.hostBridge.comments = HOST; // the page found HOST named as a shell
  p.hello(point);
  p.hello(point, p.parent, 'https://elsewhere.example');
  p.hello(point, {}, HOST);
  assert.deepEqual(heard.messages, [point], 'the shell\'s messages from that parent at that origin alone');
  // The index runs the same block with no shell: a comments hello there turns nothing on.
  const index = page();
  index.hello({ type: 'spec-chat-host', opens: ['comments'] });
  assert.deepEqual(index.posted, []);
  assert.equal(index.api.hostBridge.comments, null);
}

// shell #shell-setting-rule: the origins the service names on its comment UI page's answer, asked once and kept;
// an ask that fails, the service down or answering no list, names none and is asked again at the next hello, so a page
// loaded while its service was down is held once it is up.
{
  const from = runtime.indexOf('let shellsAsked = null;');
  const code = runtime.slice(from, runtime.indexOf('\n}\n', runtime.indexOf('function namedShells', from)) + 3);
  const answers = [], asked = [];
  const fetch = async (url, init) => { asked.push(init.method + ' ' + url); const answer = answers.shift(); if (answer instanceof Error) throw answer; return answer; };
  const namedShells = Function('fetch', 'SCRIPT_SERVICE', 'OWN_ADDRESS', code + '; return namedShells;')(
    fetch, new URL(ORIGIN + '/s/'), new URL(ORIGIN + '/s/docs/specs/a.spec.html'));
  answers.push(new TypeError('Failed to fetch'), { ok: false, headers: { get: () => null } },
    { ok: true, headers: { get: name => name === 'X-Spec-Chat-Shells' ? 'https://shell.example  https://two.example' : null } });
  assert.deepEqual(await namedShells(), [], 'the service down: none');
  assert.deepEqual(await namedShells(), [], 'asked again: no list answered, none');
  assert.deepEqual(await namedShells(), ['https://shell.example', 'https://two.example'], 'asked again: the origins it names');
  assert.deepEqual(await namedShells(), ['https://shell.example', 'https://two.example'], 'then kept');
  assert.deepEqual(asked, Array(3).fill('HEAD ' + ORIGIN + '/s/comments'), 'three asks, none once answered');
}

// shell #shell-ui-page-only under a public base with a path: the comment UI page, served there, reads a held
// page at its path under the service's own address alone, never under the origin's root or another path.
{
  const from = runtime.indexOf('function servicePath(at)');
  const code = runtime.slice(from, runtime.indexOf('\n}\n', from) + 3);
  const servicePath = Function('SCRIPT_SERVICE', code + '; return servicePath;')(new URL('https://box.example/review/'));
  const path = href => servicePath(new URL(href));
  assert.equal(path('https://box.example/review/abc1/docs/specs/a.spec.html'), 'abc1/docs/specs/a.spec.html');
  assert.equal(path('https://box.example/abc1/docs/specs/a.spec.html'), null, "the origin's root is not the service");
  assert.equal(path('https://box.example/reviewer/abc1/docs/specs/a.spec.html'), null, 'nor another path');
  assert.equal(path('https://other.example/review/abc1/docs/specs/a.spec.html'), null, 'nor another origin');
}

console.log('runtime host bridge tests passed');
