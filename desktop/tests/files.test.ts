import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, writeFile, symlink, rm, lstat } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { readSelectedFile, MAX_FILE_BYTES, supportedFile } from '../src/main/files';

test('chosen imports return bounded bytes and never follow a symlink', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-desktop-files-'));
  try {
    const file = join(directory, 'statement.csv');
    await writeFile(file, 'date,amount\n2026-01-01,12');
    const chosen = await readSelectedFile(file);
    assert.equal(chosen.name, 'statement.csv');
    assert.equal(chosen.type, 'text/csv');
    assert.equal(Buffer.from(chosen.bytes).toString(), 'date,amount\n2026-01-01,12');
    await symlink(file, join(directory, 'link.csv'));
    await assert.rejects(readSelectedFile(join(directory, 'link.csv')));
    await assert.rejects(readSelectedFile(directory));
    const oversized = join(directory, 'large.csv');
    await writeFile(oversized, Buffer.alloc(MAX_FILE_BYTES + 1));
    await assert.rejects(readSelectedFile(oversized));
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('native open and watched intake support existing structured statement formats', () => {
  for (const extension of ['OFX', 'qfx', 'qbo', 'qif', 'mt940', 'sta', 'camt', 'xml']) assert.equal(supportedFile(`statement.${extension}`), true);
  assert.equal(supportedFile('program.exe'), false);
});

test('watch intake refuses a file changed since its stable scan', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-desktop-changed-'));
  try {
    const path = join(directory, 'statement.csv');
    await writeFile(path, 'original');
    const original = await lstat(path);
    const expected = { device: original.dev, inode: original.ino, size: original.size, modifiedAt: original.mtimeMs };
    await writeFile(path, 'a replacement with different bytes');
    await assert.rejects(readSelectedFile(path, expected), /changed/);
  } finally { await rm(directory, { recursive: true, force: true }); }
});
