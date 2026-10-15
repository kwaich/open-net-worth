import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdir, mkdtemp, readFile, readdir, realpath, rename, rm, symlink, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { initialBookRoot, inspectBookFolder, verifyConfirmedBookRoot, verifyRememberedBookRoot } from '../src/main/book-folder';
import { chooseBookFolder } from '../src/main/book-picker';
import { BookSwitcher } from '../src/main/book-switch';

test('native selection accepts complete and empty roots, normalizes data, refuses unsafe structures', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-book-shape-'));
  try {
    const root = join(directory, 'existing');
    await mkdir(join(root, 'data'), { recursive: true });
    await writeFile(join(root, 'data', 'onw.db'), 'synthetic database');
    assert.equal((await inspectBookFolder(join(root, 'data'))).root, await realpath(root));
    assert.equal((await inspectBookFolder(root)).existing, true);
    const empty = join(directory, 'empty');
    await mkdir(empty);
    assert.equal((await inspectBookFolder(empty)).existing, false);
    const flat = join(directory, 'legacy');
    await mkdir(flat);
    await writeFile(join(flat, 'onw.db'), 'untouched');
    await assert.rejects(inspectBookFolder(flat), /other files/);
    assert.equal(await readFile(join(flat, 'onw.db'), 'utf8'), 'untouched');
    if (process.platform !== 'win32') {
      await rm(join(root, 'data', 'onw.db'));
      await symlink(join(flat, 'onw.db'), join(root, 'data', 'onw.db'));
      await assert.rejects(inspectBookFolder(root), /regular files/);
      await rm(join(root, 'data'), { recursive: true });
      await symlink(flat, join(root, 'data'));
      await assert.rejects(inspectBookFolder(root), /regular files/);
    }
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('a folder with other files gets the book in a new subfolder; OS clutter does not count', async () => {
  const directory = await realpath(await mkdtemp(join(tmpdir(), 'onw-picker-nest-')));
  try {
    await writeFile(join(directory, '.DS_Store'), '');
    assert.equal((await inspectBookFolder(directory)).existing, false);
    await writeFile(join(directory, 'taxes.pdf'), 'synthetic');
    const seen: string[] = [];
    const nestedFlags: boolean[] = [];
    assert.deepEqual(await chooseBookFolder({ choose: async () => directory, confirm: async (folder, nested) => { seen.push(folder.root); nestedFlags.push(nested); return false; } }, async () => true, undefined), { cancelled: true, opened: false });
    assert.deepEqual(seen, [join(directory, 'Open Net Worth')]);
    assert.deepEqual(nestedFlags, [true]);
    assert.deepEqual((await readdir(directory)).sort(), ['.DS_Store', 'taxes.pdf']);
    let opened = '';
    await chooseBookFolder({ choose: async () => directory, confirm: async () => true }, async folder => { opened = folder.root; return true; }, undefined);
    assert.equal(opened, join(directory, 'Open Net Worth'));
    await writeFile(join(directory, 'Open Net Worth', 'notes.txt'), 'synthetic');
    await chooseBookFolder({ choose: async () => directory, confirm: async () => true }, async folder => { opened = folder.root; return true; }, undefined);
    assert.equal(opened, join(directory, 'Open Net Worth 2'));
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('native picker cancellation and explicit QA root never create or switch books', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-picker-cancel-'));
  try {
    let calls = 0;
    const open = async () => { calls++; return true; };
    assert.deepEqual(await chooseBookFolder({ choose: async () => null, confirm: async () => true }, open, undefined), { cancelled: true, opened: false });
    assert.deepEqual(await chooseBookFolder({ choose: async () => directory, confirm: async () => false }, open, undefined), { cancelled: true, opened: false });
    await assert.rejects(chooseBookFolder({ choose: async () => { calls++; return directory; }, confirm: async () => true }, open, '/qa/book'), /explicit QA/);
    assert.equal(calls, 0);
    assert.equal(initialBookRoot('/qa/book', '/remembered', '/default'), resolve('/qa/book'));
    assert.equal(initialBookRoot(undefined, '/remembered', '/default'), '/remembered');
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('confirmed book selection reports creation, opening, and restore preparation accurately', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-book-stage-'));
  try {
    for (const [name, existing, entry, expected] of [
      ['new', false, 'folder', 'creating'],
      ['existing', true, 'folder', 'opening'],
      ['restore', false, 'restore', 'restore'],
    ] as const) {
      const root = join(directory, name);
      await mkdir(root);
      if (existing) { await mkdir(join(root, 'data')); await writeFile(join(root, 'data/onw.db'), 'synthetic'); }
      const chosen = await inspectBookFolder(root);
      let stage: unknown;
      const backend = { bookRoot: root, setRoot() {}, async start() {}, async stop() {} };
      const preferences = { async activateBook() {}, async rememberBook() {} };
      const view = { async prepareBookSwitch(value?: unknown) { stage = value; return false; }, async loadApp() {}, async showRecovery() {} };
      const books = new BookSwitcher(backend, preferences, view, { async drain() {}, start() {} });
      await books.openInitial(chosen, entry);
      assert.equal(stage, expected);
    }
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('book switches serialize, drain old work, and persist only after a loaded candidate', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-book-switch-'));
  try {
    const paths = ['A', 'B', 'C'].map(name => join(directory, name));
    for (const path of paths) await mkdir(path);
    const roots = await Promise.all(paths.map(inspectBookFolder));
    const events: string[] = [];
    let running = true;
    const backend = { bookRoot: roots[0].root, setRoot(root: string) { assert.equal(running, false); this.bookRoot = root; events.push(`root:${root}`); }, async start() { assert.equal(running, false); running = true; events.push(`start:${this.bookRoot}`); }, async stop() { running = false; events.push('stop'); } };
    const preferences = { async activateBook(root: string) { events.push(`scope:${root}`); }, async rememberBook(root: string) { assert.equal(events.at(-1), 'loaded'); events.push(`persist:${root}`); } };
    const view = { async prepareBookSwitch() { events.push('recovery'); return false; }, async loadApp() { events.push('loaded'); }, async showRecovery() { events.push('failed'); } };
    const workers = { async drain() { events.push('drained'); }, start() { events.push('workers'); } };
    const books = new BookSwitcher(backend, preferences, view, workers);
    assert.deepEqual(await Promise.all([books.open(roots[1]), books.open(roots[2])]), [true, true]);
    assert.equal(backend.bookRoot, roots[2].root);
    assert.deepEqual(events.slice(0, 2), ['recovery', 'drained']);
    assert.ok(events.indexOf(`persist:${roots[1].root}`) < events.indexOf(`start:${roots[2].root}`));
    assert.equal(events.filter(event => event.startsWith('persist:')).length, 2);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('failed candidate startup, page load or persistence restores previous backend and scope', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-book-rollback-'));
  try {
    const previousPath = join(directory, 'previous');
    const targetPath = join(directory, 'candidate');
    await mkdir(previousPath); await mkdir(targetPath);
    const previous = (await inspectBookFolder(previousPath)).root;
    const target = await inspectBookFolder(targetPath);
    for (const failedStage of ['startup', 'load', 'persistence']) {
      const events: string[] = [];
      const backend = { bookRoot: previous, setRoot(root: string) { this.bookRoot = root; }, async start() { events.push(`start:${this.bookRoot}`); if (failedStage === 'startup' && this.bookRoot === target.root) throw new Error('candidate refused'); }, async stop() { events.push('stop'); } };
      let scope = previous;
      let remembered = previous;
      const preferences = { async activateBook(root: string) { scope = root; }, async rememberBook(root: string) { if (failedStage === 'persistence') throw new Error('disk refused'); remembered = root; } };
      const view = { async prepareBookSwitch() { events.push('clear-renderer'); return false; }, async loadApp() { if (failedStage === 'load' && backend.bookRoot === target.root) throw new Error('app load failed'); }, async showRecovery() { events.push('recovery'); } };
      const books = new BookSwitcher(backend, preferences, view, { async drain() {}, start() {} });
      await assert.rejects(books.open(target), /previous book has been kept/);
      assert.equal(backend.bookRoot, previous);
      assert.equal(scope, previous);
      assert.equal(remembered, previous);
      assert.equal(events.at(-1), `start:${previous}`);
      assert.equal(events.filter(event => event === 'clear-renderer').length, 2);
    }
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('failed rollback leaves recovery usable and does not poison the next serialized selection', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-book-recovery-'));
  try {
    const paths = ['previous', 'refused', 'working'].map(name => join(directory, name));
    for (const path of paths) await mkdir(path);
    const roots = await Promise.all(paths.map(inspectBookFolder));
    let recovery = false;
    const backend = { bookRoot: roots[0].root, setRoot(root: string) { this.bookRoot = root; }, async start() { if (this.bookRoot !== roots[2].root) throw new Error('Cannot open'); }, async stop() {} };
    let remembered = roots[0].root;
    const preferences = { async activateBook() {}, async rememberBook(root: string) { remembered = root; } };
    const view = { async prepareBookSwitch() { return false; }, async loadApp() {}, async showRecovery(starting: boolean) { recovery = !starting; } };
    const books = new BookSwitcher(backend, preferences, view, { async drain() {}, start() {} });
    await assert.rejects(books.open(roots[1]), /previous book has been kept/);
    assert.equal(recovery, true);
    assert.equal(remembered, roots[0].root);
    assert.equal(await books.open(roots[2]), true);
    assert.equal(remembered, roots[2].root);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('a remembered root replaced by a symlink cannot borrow another book scope', { skip: process.platform === 'win32' }, async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-remembered-root-'));
  try {
    const first = join(directory, 'A');
    const second = join(directory, 'B');
    await mkdir(first); await mkdir(second);
    const root = (await inspectBookFolder(first)).root;
    await rename(first, join(directory, 'original-A'));
    await symlink(second, first);
    await assert.rejects(verifyRememberedBookRoot(root), /points somewhere else/);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('first confirmed selection may use the suggested root and never rolls back to an unconfirmed book', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-first-book-'));
  try {
    const chosen = await inspectBookFolder(directory);
    const starts: string[] = [];
    let refused = true;
    let remembered: string | null = null;
    let entry: unknown;
    const backend = { bookRoot: chosen.root, setRoot(root: string) { this.bookRoot = root; }, async start() { starts.push(this.bookRoot); if (refused) throw new Error('Locked'); }, async stop() {} };
    const preferences = { async activateBook() {}, async rememberBook(root: string) { remembered = root; } };
    const view = { async prepareBookSwitch() { return false; }, async loadApp(value?: unknown) { entry = value; }, async showRecovery() {} };
    const books = new BookSwitcher(backend, preferences, view, { async drain() {}, start() {} });
    await assert.rejects(books.openInitial(chosen), /Locked/);
    assert.deepEqual(starts, [chosen.root]);
    assert.equal(remembered, null);
    refused = false;
    assert.equal(await books.openInitial(chosen), true);
    assert.equal(remembered, chosen.root);
    assert.equal(entry, 'folder');
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('initial restore rejects a populated destination before starting or changing book scope', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-first-restore-'));
  try {
    await mkdir(join(directory, 'data'));
    await writeFile(join(directory, 'data/onw.db'), 'untouched');
    const chosen = await inspectBookFolder(directory);
    let calls = 0;
    const backend = { bookRoot: '/unconfirmed', setRoot() { calls++; }, async start() { calls++; }, async stop() { calls++; } };
    const preferences = { async activateBook() { calls++; }, async rememberBook() { calls++; } };
    const view = { async prepareBookSwitch() { calls++; return false; }, async loadApp() { calls++; }, async showRecovery() { calls++; } };
    const books = new BookSwitcher(backend, preferences, view, { async drain() { calls++; }, start() { calls++; } });
    await assert.rejects(books.openInitial(chosen, 'restore'), /empty folder/);
    assert.equal(calls, 0);
    assert.equal(await readFile(join(directory, 'data/onw.db'), 'utf8'), 'untouched');
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('retry after a changed initial selection cannot start the unconfirmed default book', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-first-retry-'));
  try {
    const defaultPath = join(directory, 'default');
    const chosenPath = join(directory, 'chosen');
    await mkdir(defaultPath); await mkdir(chosenPath);
    const fallback = await inspectBookFolder(defaultPath);
    const chosen = await inspectBookFolder(chosenPath);
    const starts: string[] = [];
    const backend = { bookRoot: fallback.root, setRoot(root: string) { this.bookRoot = root; }, async start() { starts.push(this.bookRoot); }, async stop() {} };
    const preferences = { async activateBook() {}, async rememberBook() {} };
    const view = {
      async prepareBookSwitch() {
        await rename(chosenPath, join(directory, 'original-chosen'));
        await mkdir(chosenPath);
        return false;
      },
      async loadApp() {}, async showRecovery() {},
    };
    const books = new BookSwitcher(backend, preferences, view, { async drain() {}, start() {} });
    await assert.rejects(books.openInitial(chosen), /selected book folder changed/);
    assert.equal(backend.bookRoot, fallback.root);
    await assert.rejects(async () => {
      await verifyConfirmedBookRoot(backend.bookRoot, chosen);
      await backend.start();
    }, /selected folder changed/);
    assert.deepEqual(starts, []);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('an initial retry refuses a replacement directory at the confirmed path', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-first-identity-'));
  try {
    const chosenPath = join(directory, 'chosen');
    await mkdir(chosenPath);
    const chosen = await inspectBookFolder(chosenPath);
    let started = false;
    const backend = { bookRoot: chosen.root, setRoot(root: string) { this.bookRoot = root; }, async start() { started = true; }, async stop() {} };
    const preferences = { async activateBook() {}, async rememberBook() {} };
    const view = {
      async prepareBookSwitch() {
        await rename(chosenPath, join(directory, 'original-chosen'));
        await mkdir(chosenPath);
        return false;
      },
      async loadApp() {}, async showRecovery() {},
    };
    const books = new BookSwitcher(backend, preferences, view, { async drain() {}, start() {} });
    await assert.rejects(books.openInitial(chosen), /selected book folder changed/);
    assert.equal(books.initialRetryFolder, null);
    await assert.rejects(async () => {
      await verifyConfirmedBookRoot(backend.bookRoot, chosen);
      await backend.start();
    }, /selected folder changed/);
    assert.equal(started, false);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('an initial retry accepts the same confirmed directory after its database is created', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-first-created-'));
  try {
    const chosen = await inspectBookFolder(directory);
    assert.equal(chosen.existing, false);
    await mkdir(join(directory, 'data'));
    await writeFile(join(directory, 'data/onw.db'), 'synthetic startup database');
    await verifyConfirmedBookRoot(chosen.root, chosen);
  } finally { await rm(directory, { recursive: true, force: true }); }
});
