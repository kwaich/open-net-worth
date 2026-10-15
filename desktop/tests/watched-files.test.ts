import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, mkdir, writeFile, lstat, rename, rm, symlink } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { existsSync } from 'node:fs';
import { WatchedFileReader } from '../src/main/watched-files';

test('watch helper reads only a file under the physically granted directory', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-watch-helper-'));
  const repo = resolve(__dirname, '..', '..');
  const venvPython = join(repo, '.venv', ...(process.platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python3']));
  const reader = new WatchedFileReader({ python: process.env.ONW_DESKTOP_PYTHON ?? (existsSync(venvPython) ? venvPython : 'python'), cwd: repo, version: '0.1.0' });
  try {
    const folder = join(directory, 'Statements');
    await mkdir(folder);
    const parent = await lstat(folder);
    const grant = { id: 'grant', label: 'Statements', path: folder, device: parent.dev, inode: parent.ino };
    await writeFile(join(folder, 'statement.ofx'), 'original statement');
    const stat = await lstat(join(folder, 'statement.ofx'));
    const expected = { device: stat.dev, inode: stat.ino, size: stat.size, modifiedAt: stat.mtimeMs };
    const file = await reader.read(grant, 'statement.ofx', expected);
    assert.equal(Buffer.from(file.bytes).toString(), 'original statement');
    assert.equal(file.type, 'application/x-ofx');
    // Creating a symlink needs a privilege an unprivileged Windows account may not hold.
    const linked = await symlink(join(folder, 'statement.ofx'), join(folder, 'link.ofx')).then(() => true, () => process.platform === 'win32' ? false : Promise.reject(new Error('symlink failed')));
    if (linked) await assert.rejects(reader.read(grant, 'link.ofx', expected));
    await rename(folder, join(directory, 'original'));
    await mkdir(folder);
    await writeFile(join(folder, 'statement.ofx'), 'outside bytes');
    const outside = await lstat(join(folder, 'statement.ofx'));
    await assert.rejects(reader.read(grant, 'statement.ofx', { device: outside.dev, inode: outside.ino, size: outside.size, modifiedAt: outside.mtimeMs }));
    await assert.rejects(reader.read(grant, '../original/statement.ofx', expected));
  } finally { reader.stop(); await rm(directory, { recursive: true, force: true }); }
});
