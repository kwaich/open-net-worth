import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { PendingStore, type SecretBox } from '../src/main/hosted-pending-store';

const reverse = (text: string) => Buffer.from([...Buffer.from(text)].reverse());
const box = (available = true): SecretBox => ({ available: () => available, encrypt: text => reverse(text), decrypt: data => Buffer.from([...data].reverse()).toString() });
const entry = { state: 'S'.repeat(20), verifier: 'secret-verifier-value', expires: 9_999, context: { move: true } };

async function directory() { return mkdtemp(join(tmpdir(), 'onw-pending-')); }

test('a pending sign-in is stored encrypted and round-trips', async () => {
  const dir = await directory();
  try {
    const store = new PendingStore(join(dir, 'pending.bin'), box());
    await store.save([entry]);
    const raw = await readFile(join(dir, 'pending.bin'));
    assert.equal(raw.includes('secret-verifier-value'), false);
    if (process.platform !== 'win32') assert.equal((await stat(join(dir, 'pending.bin'))).mode & 0o077, 0);
    assert.deepEqual(await store.load(), [entry]);
    await store.save([]);
    assert.deepEqual(await store.load(), []);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('without an operating-system keychain nothing is written', async () => {
  const dir = await directory();
  try {
    const store = new PendingStore(join(dir, 'pending.bin'), box(false));
    assert.equal(store.persistent, false);
    await store.save([entry]);
    await assert.rejects(stat(join(dir, 'pending.bin')));
    assert.deepEqual(await store.load(), []);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('a damaged or foreign file is discarded rather than trusted', async () => {
  const dir = await directory();
  try {
    const path = join(dir, 'pending.bin');
    const store = new PendingStore(path, box());
    await writeFile(path, 'not encrypted at all');
    assert.deepEqual(await store.load(), []);
    await assert.rejects(stat(path));
    await writeFile(path, reverse(JSON.stringify([{ state: 1 }, entry, { ...entry, context: {} }])));
    assert.deepEqual(await store.load(), [entry]);
  } finally { await rm(dir, { recursive: true, force: true }); }
});
