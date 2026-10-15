import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, writeFile, readFile, readdir, rm, symlink } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { atomicExport } from '../src/main/exports';

test('failed export preserves the chosen previous file and removes its temporary file', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-desktop-export-'));
  try {
    const file = join(directory, 'book.zip');
    await writeFile(file, 'previous');
    async function* broken() { yield Buffer.from('incomplete'); throw new Error('network disconnected'); }
    await assert.rejects(atomicExport(file, broken()), /network disconnected/);
    assert.equal(await readFile(file, 'utf8'), 'previous');
    assert.deepEqual(await readdir(directory), ['book.zip']);
    await atomicExport(file, (async function* () { yield Buffer.from('complete'); })());
    assert.equal(await readFile(file, 'utf8'), 'complete');
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('export refuses paths inside the running book, including symlinked parents', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-desktop-export-book-'));
  try {
    const file = join(directory, 'onw.db');
    await writeFile(file, 'database');
    const source = () => (async function* () { yield Buffer.from('zip'); })();
    await assert.rejects(atomicExport(file, source(), directory), /book/);
    await assert.rejects(atomicExport(join(directory, '..hidden.zip'), source(), directory), /book/);
    const alias = `${directory}-alias`;
    await symlink(directory, alias);
    try { await assert.rejects(atomicExport(join(alias, 'data.zip'), source(), directory), /book/); }
    finally { await rm(alias); }
    assert.equal(await readFile(file, 'utf8'), 'database');
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('export refuses an existing symlink destination', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-desktop-export-link-'));
  try {
    const original = join(directory, 'original');
    await writeFile(original, 'keep');
    const link = join(directory, 'book.zip');
    await symlink(original, link);
    await assert.rejects(atomicExport(link, (async function* () { yield Buffer.from('overwrite'); })()));
    assert.equal(await readFile(original, 'utf8'), 'keep');
  } finally { await rm(directory, { recursive: true, force: true }); }
});
