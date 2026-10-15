import assert from 'node:assert/strict';
import { mkdtemp, readFile, readdir, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { AUTOSTART_FILE, LinuxAutostart, autostartEntry, autostartPath, execArgument } from '../src/main/linux-autostart';

test('the autostart file follows XDG_CONFIG_HOME only when it is absolute', () => {
  assert.equal(autostartPath({ XDG_CONFIG_HOME: '/cfg' }, '/home/me'), `/cfg/autostart/${AUTOSTART_FILE}`);
  assert.equal(autostartPath({ XDG_CONFIG_HOME: 'relative' }, '/home/me'), `/home/me/.config/autostart/${AUTOSTART_FILE}`);
  assert.equal(autostartPath({}, '/home/me'), `/home/me/.config/autostart/${AUTOSTART_FILE}`);
});

test('launcher arguments are quoted per the Desktop Entry spec', () => {
  assert.equal(execArgument('/home/me/.local/bin/Open-Net-Worth.AppImage'), '/home/me/.local/bin/Open-Net-Worth.AppImage');
  assert.equal(execArgument('/home/me/My Apps/app'), '"/home/me/My Apps/app"');
  assert.equal(execArgument('/a/$b'), '"/a/\\\\$b"');
  assert.equal(execArgument('100%'), '100%%');
  assert.throws(() => execArgument('a\nExec=evil'));
});

test('the entry starts the app quietly with the login flag', () => {
  const entry = autostartEntry('/opt/Open Net Worth/open-net-worth', ['--onw-login']);
  assert.match(entry, /^\[Desktop Entry\]\nType=Application\n/);
  assert.match(entry, /^Exec="\/opt\/Open Net Worth\/open-net-worth" --onw-login$/m);
  assert.match(entry, /^Terminal=false$/m);
});

test('enabling writes the file atomically, disabling removes it, and both are idempotent', { skip: process.platform === 'win32' && 'POSIX file modes; Linux-only behaviour' }, async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-autostart-'));
  try {
    const file = join(directory, 'config', 'autostart', AUTOSTART_FILE);
    const login = new LinuxAutostart(file, '/usr/bin/open-net-worth', ['--onw-login']);
    assert.equal(login.enabled(), false);
    await login.set(false);
    await login.set(true);
    await login.set(true);
    assert.equal(login.enabled(), true);
    assert.equal((await stat(file)).mode & 0o777, 0o644);
    assert.deepEqual(await readdir(join(directory, 'config', 'autostart')), [AUTOSTART_FILE]);
    assert.match(await readFile(file, 'utf8'), /^Exec=\/usr\/bin\/open-net-worth --onw-login$/m);
    await login.set(false);
    assert.equal(login.enabled(), false);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('an entry the user disabled in their desktop settings reads as off', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-autostart-'));
  try {
    const file = join(directory, AUTOSTART_FILE);
    const login = new LinuxAutostart(file, '/usr/bin/open-net-worth', []);
    await login.set(true);
    await writeFile(file, `${await readFile(file, 'utf8')}Hidden=true\n`);
    assert.equal(login.enabled(), false);
  } finally { await rm(directory, { recursive: true, force: true }); }
});
