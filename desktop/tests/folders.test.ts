import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, writeFile, symlink, rename, mkdir, realpath, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { FolderWatcher } from '../src/main/folders';
import { Preferences } from '../src/main/preferences';
import type { BackendSupervisor } from '../src/main/backend';
import { readSelectedFile } from '../src/main/files';

test('watched folders wait for stable files, skip symlinks and stop after grant revocation', async (context) => {
  context.mock.timers.enable({ apis: ['Date'], now: 1_000 });
  const directory = await mkdtemp(join(tmpdir(), 'onw-desktop-watched-'));
  try {
    const folder = join(directory, 'Statements');
    await mkdir(folder);
    const preferences = new Preferences(join(directory, 'settings.json'));
    const grants: { id: string; label: string; enabled: boolean }[] = [];
    const uploads: FormData[] = [];
    const backend = {
      bookRoot: '',
      origin: 'http://127.0.0.1:12345',
      json: async (path: string, init?: RequestInit) => {
        if (path === '/api/desktop/folders' && init?.method === 'POST') grants.push({ ...JSON.parse(init.body as string), enabled: true });
        if (path === '/api/desktop/folders') return { folders: grants };
        uploads.push(init!.body as FormData);
        return { duplicate: false, job: { status: 'ready' } };
      },
      request: async () => { grants[0].enabled = false; return new Response('{}'); },
      readWatchedFile: (folder: { path: string }, name: string) => readSelectedFile(join(folder.path, name)),
    } as unknown as BackendSupervisor;
    let notifications = 0;
    const watcher = new FolderWatcher(backend, preferences, () => notifications++);
    await watcher.add(folder);
    assert.equal((await watcher.list())[0].location, await realpath(folder));
    assert.deepEqual(Object.keys(grants[0]).sort(), ['enabled', 'id', 'label']);
    await writeFile(join(folder, 'statement.csv'), 'date,amount\n2026-01-01,42');
    await symlink(join(folder, 'statement.csv'), join(folder, 'symlink.csv'));
    await watcher.poll();
    assert.equal(uploads.length, 0);
    context.mock.timers.setTime(4_000);
    await watcher.poll();
    assert.equal(uploads.length, 1);
    assert.equal(uploads[0].get('grant_id'), grants[0].id);
    assert.equal(await (uploads[0].get('file') as Blob).text(), 'date,amount\n2026-01-01,42');
    assert.equal(notifications, 1);
    await watcher.poll();
    assert.equal(uploads.length, 1);
    await watcher.revoke(grants[0].id);
    await writeFile(join(folder, 'second.csv'), 'amount\n17');
    context.mock.timers.setTime(8_000);
    await watcher.poll();
    assert.equal(uploads.length, 1);
  } finally { context.mock.timers.reset(); await rm(directory, { recursive: true, force: true }); }
});

test('replacing a granted folder does not grant access to the replacement', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-desktop-folder-identity-'));
  try {
    const folder = join(directory, 'Statements');
    await mkdir(folder);
    const preferences = new Preferences(join(directory, 'settings.json'));
    const grants: unknown[] = [];
    const backend = { bookRoot: '', origin: 'http://127.0.0.1:12345', json: async (_path: string, init?: RequestInit) => {
      if (init?.method === 'POST') grants.push({ ...JSON.parse(init.body as string), enabled: true });
      return { folders: grants };
    } } as unknown as BackendSupervisor;
    const watcher = new FolderWatcher(backend, preferences, () => {});
    await watcher.add(folder);
    await rename(folder, join(directory, 'original'));
    await mkdir(folder);
    assert.equal((await watcher.list())[0].available, false);
  } finally { await rm(directory, { recursive: true, force: true }); }
});
