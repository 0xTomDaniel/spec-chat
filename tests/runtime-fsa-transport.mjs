import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = readFileSync(resolve(root, 'skill/review-spec/assets/viz/runtime.js'), 'utf8');
const start = runtime.indexOf('function suggestedGrant()');
const end = runtime.indexOf('\n\n/* ---------------- islands', start);
assert.ok(start >= 0 && end > start, 'runtime exposes the FSA transport boundary');

const stored = new Map();
const request = result => {
  const value = { result };
  queueMicrotask(() => value.onsuccess?.());
  return value;
};
const database = {
  transaction() {
    return {
      objectStore() {
        return {
          get: key => request(stored.get(key)),
          put: (value, key) => {
            stored.set(key, value);
            return request(key);
          },
        };
      },
    };
  },
};
const indexedDB = {
  open() {
    const value = {};
    queueMicrotask(() => {
      value.result = database;
      value.onsuccess?.();
    });
    return value;
  },
};

class DirectoryHandle {
  constructor(name, permission = 'granted') {
    this.kind = 'directory';
    this.name = name;
    this.permission = permission;
    this.permissionRequests = 0;
    this.files = new Set();
    this.directories = new Map();
  }

  async getFileHandle(name) {
    if (!this.files.has(name)) throw new Error(`missing file ${name}`);
    return { kind: 'file', name };
  }

  async getDirectoryHandle(name) {
    const child = this.directories.get(name);
    if (!child) throw new Error(`missing directory ${name}`);
    return child;
  }

  async queryPermission() {
    return this.permission;
  }

  async requestPermission() {
    this.permissionRequests += 1;
    this.permission = 'granted';
    return this.permission;
  }
}

const specFile = 'example.spec.html';
const location = {
  href: `file:///workspace/docs/specs/${specFile}`,
  pathname: `/workspace/docs/specs/${specFile}`,
};
const navigator = {
  clipboard: { writeText: async () => {} },
  platform: 'test',
};
const window = { showDirectoryPicker: async () => { throw new Error('picker not configured'); } };
const fsaTransport = Function(
  'SPEC_FILE',
  'location',
  'indexedDB',
  'navigator',
  'window',
  'toast',
  `${runtime.slice(start, end)}; return fsaTransport;`,
)(specFile, location, indexedDB, navigator, window, () => {});

const restoredDirectory = new DirectoryHandle('docs', 'prompt');
const restoredSpecsDirectory = new DirectoryHandle('specs');
restoredSpecsDirectory.files.add(specFile);
restoredDirectory.directories.set('specs', restoredSpecsDirectory);
stored.set('scope-root', restoredDirectory);
const restoredTransport = fsaTransport();
assert.equal(await restoredTransport.tryRestore(), 'prompt', 'an expired stored handle requires a gesture regrant');
assert.equal(await restoredTransport.resume(), true, 'the stored handle can resume without selecting the folder again');
assert.equal(restoredDirectory.permissionRequests, 1, 'resume requests write permission exactly once');
assert.equal(restoredTransport.connected, true, 'successful regrant reconnects the transport');

stored.clear();
const pickedDirectory = new DirectoryHandle('docs');
const pickedSpecsDirectory = new DirectoryHandle('specs');
pickedSpecsDirectory.files.add(specFile);
pickedDirectory.directories.set('specs', pickedSpecsDirectory);
let pickerOptions;
window.showDirectoryPicker = async options => {
  pickerOptions = options;
  return pickedDirectory;
};
const pickerTransport = fsaTransport();
assert.equal(await pickerTransport.tryRestore(), 'none');
assert.equal(await pickerTransport.connect({ useLastDir: false }), 'connected');
assert.equal(pickerOptions.mode, 'readwrite', 'the picker requests the write grant needed by the review spool');
assert.equal(pickerTransport.connected, true, 'the directory picker remains an explicit fallback');

stored.clear();
const pickedAncestor = new DirectoryHandle('workspace');
const nestedDocsDirectory = new DirectoryHandle('docs');
const nestedSpecDirectory = new DirectoryHandle('specs');
nestedSpecDirectory.files.add(specFile);
nestedDocsDirectory.directories.set('specs', nestedSpecDirectory);
pickedAncestor.directories.set('docs', nestedDocsDirectory);
window.showDirectoryPicker = async () => pickedAncestor;
const ancestorPickerTransport = fsaTransport();
assert.equal(await ancestorPickerTransport.connect({ useLastDir: false }), 'connected');
assert.equal(ancestorPickerTransport.connected, true, 'selecting the suggested ancestor resolves the nested spec directory');
assert.equal(stored.get('scope-root'), pickedAncestor, 'the selected ancestor is persisted as the reusable grant scope');

assert.match(runtime, /id="hx-repick"/, 'reconnect UI exposes a separate folder-picker fallback');
assert.match(runtime, /state\.transport\.resume\(\)/, 'reconnect UI uses persisted-handle regrant');
assert.match(runtime, /Allow this site to edit files/, 'picker UI names the browser permission step explicitly');
assert.match(runtime, /:where\(body\)\{margin:0;/, 'runtime document defaults remain lower-specificity than a spec theme');

// review-state #acceptance-local-file-scan: a comment and hand-off saved through the granted folder land in the spec's
// spool on disk, and the owning agent's next scan reports that hand-off.
class DiskDirectory {
  constructor(path) { this.kind = 'directory'; this.path = path; }
  async queryPermission() { return 'granted'; }
  async getFileHandle(name, { create } = {}) {
    const file = join(this.path, name);
    if (create) writeFileSync(file, '', { flag: 'a' });
    statSync(file);
    return { kind: 'file', name, createWritable: async () => {
      let data = '';
      return { write: async chunk => { data += chunk; }, close: async () => writeFileSync(file, data) };
    } };
  }
  async getDirectoryHandle(name, { create } = {}) {
    const dir = join(this.path, name);
    if (create) mkdirSync(dir, { recursive: true });
    if (!statSync(dir).isDirectory()) throw new Error('not a directory ' + dir);
    return new DiskDirectory(dir);
  }
}
const disk = mkdtempSync(join(tmpdir(), 'spec-chat-fsa-'));
try {
  writeFileSync(join(disk, specFile), '<!doctype html><p data-anchor="a">A</p>');
  stored.clear();
  stored.set('scope-root', new DiskDirectory(disk));
  const diskTransport = Function('SPEC_FILE', 'REVIEW_DIRNAME', 'location', 'indexedDB', 'navigator', 'window', 'toast',
    `${runtime.slice(start, end)}; return fsaTransport;`)(specFile, specFile + '.review', location, indexedDB, navigator, window, () => {})();
  assert.equal(await diskTransport.tryRestore(), 'granted', 'a granted folder reconnects');
  const slice = (from, to) => runtime.slice(runtime.indexOf(from), runtime.indexOf(to, runtime.indexOf(from)));
  const { nextStamp, eventName } = Function(slice('function nextStamp(', '\n\nfunction offlineNotice(') + '; return { nextStamp, eventName };')();
  const comment = { event: 'comment', id: 'c1', anchor: 'a', text: 'Name the columns.' };
  const handoff = { event: 'handoff', id: 'h1', events: ['c1'] };
  const first = nextStamp(null, Date.now(), Math.random);
  const names = [eventName(comment, first), eventName(handoff, nextStamp(first, Date.now(), Math.random))];
  assert.equal(await diskTransport.postEvent({ name: names[0], body: comment }), 'stored');
  assert.equal(await diskTransport.postEvent({ name: names[1], body: handoff }), 'stored');
  const scan = execFileSync('sh', [resolve(root, 'skill/review-spec/scripts/watch-specs.sh'), disk, '.cursor-owner', '0', '1'],
    { encoding: 'utf8' });
  assert.deepEqual(scan.trim().split('\n').map(line => line.split('\t')[1]), names, 'the next scan reports the hand-off');
} finally {
  rmSync(disk, { recursive: true, force: true });
}

console.log('runtime FSA transport tests passed');
