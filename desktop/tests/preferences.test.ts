import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, readdir, readFile, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { Preferences, settingsPatch } from '../src/main/preferences';

test('device preferences persist concurrent independent changes atomically', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-desktop-prefs-'));
  try {
    const path = join(directory, 'settings.json');
    const preferences = new Preferences(path);
    await preferences.load();
    await Promise.all([preferences.save({ background: true }), preferences.save({ notifications: false }), preferences.save({ deliveredNotifications: ['notification-1'] })]);
    const reopened = new Preferences(path);
    await reopened.load();
    assert.equal(reopened.current.background, true);
    assert.equal(reopened.current.notifications, false);
    assert.deepEqual(reopened.current.deliveredNotifications, ['notification-1']);
    assert.equal(JSON.parse(await readFile(path, 'utf8')).background, true);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('renderer settings cannot introduce filesystem paths or non-boolean switches', () => {
  assert.deepEqual(settingsPatch({ notifications: false }), { notifications: false });
  for (const value of [{ folders: ['/home'] }, { background: 'true' }, null, [], { token: 'secret' }]) assert.throws(() => settingsPatch(value));
});

test('a selected server persists separately from the remembered local book and its grants', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-server-prefs-'));
  try {
    const file = join(directory, 'settings.json');
    const preferences = new Preferences(file);
    await preferences.load('/book/A');
    await preferences.rememberBook('/book/A');
    await preferences.save({ deliveredNotifications: ['local-notification'], selectedServerUrl: 'https://wealth.example' });
    const reopened = new Preferences(file);
    await reopened.load('/book/A');
    assert.equal(reopened.current.selectedServerUrl, 'https://wealth.example');
    assert.equal(reopened.current.selectedBookFolder, '/book/A');
    assert.deepEqual(reopened.current.deliveredNotifications, ['local-notification']);
    await reopened.save({ selectedServerUrl: null });
    assert.equal(reopened.current.selectedBookFolder, '/book/A');
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('a signed-in hosted instance persists beside, not inside, the server and book choices', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-hosted-prefs-'));
  try {
    const file = join(directory, 'settings.json');
    await writeFile(file, JSON.stringify({ background: false, launchAtLogin: false, notifications: true, selectedBookFolder: '/book/A', selectedServerUrl: null, books: {} }));
    const preferences = new Preferences(file);
    await preferences.load('/book/A');
    assert.equal(preferences.current.selectedHostedUrl, null);
    await preferences.save({ selectedHostedUrl: 'https://acme1.opennetworth.cloud' });
    const reopened = new Preferences(file);
    await reopened.load('/book/A');
    assert.equal(reopened.current.selectedHostedUrl, 'https://acme1.opennetworth.cloud');
    assert.equal(reopened.current.selectedBookFolder, '/book/A');
    await reopened.save({ selectedHostedUrl: null });
    assert.equal(JSON.parse(await readFile(file, 'utf8')).selectedHostedUrl, null);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('the server preference migrates existing scoped preferences once without dropping books', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-server-prefs-migrate-'));
  try {
    const file = join(directory, 'settings.json');
    await writeFile(file, JSON.stringify({ background: false, launchAtLogin: false, notifications: true, selectedBookFolder: '/book/A', books: { '/book/A': { folders: [], deliveredNotifications: ['local'] } } }));
    const preferences = new Preferences(file);
    await preferences.load('/book/A');
    assert.equal(preferences.current.selectedServerUrl, null);
    assert.deepEqual(preferences.current.deliveredNotifications, ['local']);
    assert.equal(JSON.parse(await readFile(file, 'utf8')).selectedServerUrl, null);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('folder locators and notification history stay scoped across book selection and restart', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-scoped-prefs-'));
  try {
    const preferences = new Preferences(join(directory, 'settings.json'));
    await preferences.load('/book/A');
    const folder = { id: 'grant-A', label: 'Statements', path: '/chosen/Statements', device: 1, inode: 2 };
    const pending = preferences.save({ folders: [folder], deliveredNotifications: ['notification-A'] });
    await preferences.activateBook('/book/B');
    await pending;
    assert.deepEqual(preferences.current.folders, []);
    await preferences.save({ deliveredNotifications: ['notification-B'] });
    await preferences.rememberBook('/book/B');
    await preferences.saveForBook('/book/A', { deliveredNotifications: ['old-poll-A'] });
    assert.deepEqual(preferences.current.deliveredNotifications, ['notification-B']);
    await preferences.activateBook('/book/A');
    assert.deepEqual(preferences.current.folders, [folder]);
    assert.deepEqual(preferences.current.deliveredNotifications, ['old-poll-A']);
    const reopened = new Preferences(join(directory, 'settings.json'));
    await reopened.load('/book/A');
    assert.equal(reopened.current.selectedBookFolder, '/book/B');
    await reopened.activateBook('/book/B');
    assert.deepEqual(reopened.current.deliveredNotifications, ['notification-B']);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('previous device preferences migrate once to the canonical initial book scope', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-prefs-migrate-'));
  try {
    const file = join(directory, 'settings.json');
    await writeFile(file, JSON.stringify({ background: true, launchAtLogin: false, notifications: true, folders: [], deliveredNotifications: ['old'] }));
    const preferences = new Preferences(file);
    await preferences.load('/canonical/book');
    assert.deepEqual(preferences.current.deliveredNotifications, ['old']);
    const stored = JSON.parse(await readFile(file, 'utf8'));
    assert.deepEqual(stored.books['/canonical/book'].deliveredNotifications, ['old']);
    assert.equal('folders' in stored, false);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('a skipped update version is app-global, survives a restart and a book switch, and defaults to none', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-skip-prefs-'));
  try {
    const path = join(directory, 'settings.json');
    const preferences = new Preferences(path);
    await preferences.load('/book/A');
    assert.equal(preferences.current.skippedUpdateVersion, null);
    await preferences.save({ skippedUpdateVersion: '0.2.0' });
    const reopened = new Preferences(path);
    await reopened.load('/book/B');
    assert.equal(reopened.current.skippedUpdateVersion, '0.2.0');
    assert.equal(reopened.current.lastRunVersion, null);
    await reopened.save({ lastRunVersion: '0.1.7' });
    const again = new Preferences(path);
    await again.load();
    assert.equal(again.current.lastRunVersion, '0.1.7');
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('a damaged settings file is set aside and the app starts from defaults', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-corrupt-prefs-'));
  try {
    const path = join(directory, 'settings.json');
    for (const damaged of ['{not json', 'null', JSON.stringify({ books: {}, background: 'yes', launchAtLogin: false, notifications: true })]) {
      await writeFile(path, damaged);
      const preferences = new Preferences(path);
      await preferences.load('/book/A');
      assert.equal(preferences.current.background, false);
      assert.equal(preferences.current.selectedBookFolder, null);
      const kept = (await readdir(directory)).filter(name => name.startsWith('settings.json.corrupt-'));
      assert.equal(kept.length, 1);
      assert.equal(await readFile(join(directory, kept[0]!), 'utf8'), damaged);
      await rm(join(directory, kept[0]!));
      await preferences.save({ background: true });
      assert.equal(JSON.parse(await readFile(path, 'utf8')).background, true);
    }
  } finally { await rm(directory, { recursive: true, force: true }); }
});
