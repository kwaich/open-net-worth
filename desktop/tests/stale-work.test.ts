import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { FolderWatcher } from '../src/main/folders';
import { NativeNotifications } from '../src/main/notifications';
import { Preferences } from '../src/main/preferences';
import type { BackendSupervisor } from '../src/main/backend';
import type { NativeFile } from '../src/contracts';

test('draining a stabilized watcher cancels its in-flight read before any upload to another book', async context => {
  context.mock.timers.enable({ apis: ['Date'], now: 1_000 });
  const directory = await mkdtemp(join(tmpdir(), 'onw-stale-watch-'));
  try {
    const folder = join(directory, 'Statements');
    await mkdir(folder);
    await writeFile(join(folder, 'statement.csv'), 'amount\n17');
    const preferences = new Preferences(join(directory, 'settings.json'));
    await preferences.load('/book/A');
    const grants: { id: string; label: string; enabled: boolean }[] = [];
    let uploads = 0;
    let readStarted!: () => void;
    const started = new Promise<void>(resolve => { readStarted = resolve; });
    let finishRead!: (file: NativeFile) => void;
    const bytes = new Promise<NativeFile>(resolve => { finishRead = resolve; });
    const backend = { bookRoot: '/book/A', origin: 'http://127.0.0.1:12345', json: async (path: string, init?: RequestInit) => {
      if (path === '/api/desktop/folders' && init?.method === 'POST') grants.push({ ...JSON.parse(init.body as string), enabled: true });
      if (path === '/api/desktop/folders') return { folders: grants };
      uploads++; return { duplicate: false, job: { status: 'ready' } };
    }, readWatchedFile: async () => { readStarted(); return bytes; } } as unknown as BackendSupervisor;
    const watcher = new FolderWatcher(backend, preferences, () => { throw new Error('Stale notification'); });
    await watcher.add(folder);
    await watcher.poll();
    context.mock.timers.setTime(4_000);
    const poll = watcher.poll();
    await started;
    let drained = false;
    const drain = watcher.drain().then(() => { drained = true; });
    assert.equal(drained, false);
    finishRead({ name: 'statement.csv', type: 'text/csv', bytes: new Uint8Array(Buffer.from('amount\n17')) });
    await Promise.all([poll, drain]);
    await preferences.activateBook('/book/B');
    assert.equal(uploads, 0);
    assert.deepEqual(preferences.current.folders, []);
    await preferences.activateBook('/book/A');
    assert.equal(preferences.current.folders.length, 1);
  } finally { context.mock.timers.reset(); await rm(directory, { recursive: true, force: true }); }
});

test('draining a notification request discards old results before cursor persistence', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-stale-notification-'));
  try {
    const preferences = new Preferences(join(directory, 'settings.json'));
    await preferences.load('/book/A');
    await preferences.save({ notifications: false });
    let finish!: (rows: { id: string; read: boolean }[]) => void;
    const response = new Promise<{ id: string; read: boolean }[]>(resolve => { finish = resolve; });
    const backend = { bookRoot: '/book/A', origin: 'http://127.0.0.1:12345', json: async () => response } as unknown as BackendSupervisor;
    const notifications = new NativeNotifications(backend, preferences, () => {}, () => {});
    const poll = (notifications as unknown as { poll(): Promise<void> }).poll();
    const drain = notifications.drain();
    finish([{ id: 'activity-A', read: false }]);
    await Promise.all([poll, drain]);
    assert.deepEqual(preferences.current.deliveredNotifications, []);
    await preferences.activateBook('/book/B');
    assert.deepEqual(preferences.current.deliveredNotifications, []);
  } finally { await rm(directory, { recursive: true, force: true }); }
});
