import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

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
  href: `file:///workspace/specs/${specFile}`,
  pathname: `/workspace/specs/${specFile}`,
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

const restoredDirectory = new DirectoryHandle('specs', 'prompt');
restoredDirectory.files.add(specFile);
stored.set('scope-root', restoredDirectory);
const restoredTransport = fsaTransport();
assert.equal(await restoredTransport.tryRestore(), 'prompt', 'an expired stored handle requires a gesture regrant');
assert.equal(await restoredTransport.resume(), true, 'the stored handle can resume without selecting the folder again');
assert.equal(restoredDirectory.permissionRequests, 1, 'resume requests write permission exactly once');
assert.equal(restoredTransport.connected, true, 'successful regrant reconnects the transport');

stored.clear();
const pickedDirectory = new DirectoryHandle('specs');
pickedDirectory.files.add(specFile);
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
const nestedSpecDirectory = new DirectoryHandle('specs');
nestedSpecDirectory.files.add(specFile);
pickedAncestor.directories.set('specs', nestedSpecDirectory);
window.showDirectoryPicker = async () => pickedAncestor;
const ancestorPickerTransport = fsaTransport();
assert.equal(await ancestorPickerTransport.connect({ useLastDir: false }), 'connected');
assert.equal(ancestorPickerTransport.connected, true, 'selecting the suggested ancestor resolves the nested spec directory');
assert.equal(stored.get('scope-root'), pickedAncestor, 'the selected ancestor is persisted as the reusable grant scope');

assert.match(runtime, /id="hx-repick"/, 'reconnect UI exposes a separate folder-picker fallback');
assert.match(runtime, /state\.transport\.resume\(\)/, 'reconnect UI uses persisted-handle regrant');
assert.match(runtime, /Allow this site to edit files/, 'picker UI names the browser permission step explicitly');
assert.match(runtime, /:where\(body\)\{margin:0;/, 'runtime document defaults remain lower-specificity than a spec theme');

console.log('runtime FSA transport tests passed');
