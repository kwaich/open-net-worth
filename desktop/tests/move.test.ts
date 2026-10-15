import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { MoveCancelled, MoveError, type MoveCall, type MoveEndpoint, type MoveResult } from '../src/main/move-endpoint';
import { packToFile, type PackedBook } from '../src/main/move-pack';
import { runMove } from '../src/main/move-flow';
import { megabytes, uploadBook, type UploadUi } from '../src/main/move-upload';
import { applySecrets, finishMove, readSecrets } from '../src/main/move-secrets';

const PART = 10;
const DATA = Buffer.from('0123456789'.repeat(3) + 'abc');
const sha = (bytes: Uint8Array) => createHash('sha256').update(bytes).digest('hex');
const timers = { sleep: async () => {} };
const MOVE = 'mv_' + 'a'.repeat(32);

async function book(data = DATA): Promise<PackedBook & { dir: string }> {
  const dir = await mkdtemp(join(tmpdir(), 'onw-move-test-'));
  const path = join(dir, 'book.zip');
  await writeFile(path, data);
  return { path, size: data.length, sha256: sha(data), dir, cleanup: () => rm(dir, { recursive: true, force: true }) };
}

function ui(overrides: Partial<UploadUi> = {}): UploadUi & { log: string[] } {
  const log: string[] = [];
  return { log, progress: (percent, text) => { log.push(`${percent}:${text}`); }, confirmReplace: async () => true, cancelled: () => false, ...overrides };
}

// A destination that behaves like the agent: parts by number, commit, then an import that finishes on the second poll.
function agent(options: { commit?: MoveResult[]; drop?: (call: MoveCall, attempt: number) => boolean; max?: number; importFails?: string } = {}) {
  const parts = new Map<number, Uint8Array>();
  const calls: MoveCall[] = [];
  const commits = [...(options.commit ?? [])];
  let polls = 0;
  let attempts = 0;
  const endpoint: MoveEndpoint = {
    async call(call) {
      calls.push(call);
      if (options.drop?.(call, attempts++)) throw new Error('connection lost');
      if (call.path === '/api/move/begin') return options.max !== undefined ? { status: 413, body: { detail: { max_size: options.max } } } : { status: 200, body: { move_id: MOVE, part_size: PART, max_size: 1e12 } };
      const part = /\/parts\/(\d+)$/.exec(call.path);
      if (part) {
        assert.equal(call.headers!['X-Part-Sha256'], sha(call.bytes!));
        parts.set(Number(part[1]), call.bytes!);
        return { status: 204, body: null };
      }
      if (call.path.endsWith('/commit')) return commits.shift() ?? { status: 200, body: { status: 'importing' } };
      if (call.method === 'GET') {
        if (parts.size === Math.ceil(DATA.length / PART) && calls.some(c => c.path.endsWith('/commit'))) {
          polls++;
          if (options.importFails) return { status: 200, body: { status: 'failed', progress: 0, error: options.importFails } };
          return { status: 200, body: polls < 2 ? { status: 'importing', progress: 40, received: [...parts.keys()] } : { status: 'done', progress: 100, received: [...parts.keys()] } };
        }
        return { status: 200, body: { status: 'uploading', received: [...parts.keys()] } };
      }
      return { status: 200, body: {} };
    },
  };
  return { endpoint, parts, calls };
}

test('a book uploads in numbered parts, each with its checksum, and reassembles identically', async () => {
  const packed = await book();
  const { endpoint, parts } = agent();
  const screen = ui();
  assert.equal(await uploadBook(endpoint, packed, screen, timers), MOVE);
  assert.deepEqual([...parts.keys()], [0, 1, 2, 3]);
  assert.equal(Buffer.concat([...parts.values()].map(part => Buffer.from(part))).toString(), DATA.toString());
  assert.ok(screen.log.some(line => /^100:Uploading/.test(line)));
  assert.ok(screen.log.some(line => /^40:Importing/.test(line)));
  await packed.cleanup();
});

test('a dropped connection resumes: the part is retried, and not resent if it had already landed', async () => {
  const packed = await book();
  let failed = 0;
  const { endpoint, calls } = agent({ drop: call => /\/parts\/1$/.test(call.path) && failed++ < 2 });
  await uploadBook(endpoint, packed, ui(), timers);
  assert.equal(calls.filter(call => /\/parts\/1$/.test(call.path)).length, 3);
  const landed = await book();
  const flaky = agent();
  const original = flaky.endpoint.call.bind(flaky.endpoint);
  let dropReply = true;
  flaky.endpoint.call = async call => {
    const result = await original(call);
    if (dropReply && /\/parts\/2$/.test(call.path)) { dropReply = false; throw new Error('reply lost'); }
    return result;
  };
  await uploadBook(flaky.endpoint, landed, ui(), timers);
  assert.equal(flaky.calls.filter(call => /\/parts\/2$/.test(call.path)).length, 1);
  await packed.cleanup(); await landed.cleanup();
});

test('a connection that never comes back ends the move with a plain message and aborts it', async () => {
  const packed = await book();
  const { endpoint, calls } = agent({ drop: call => /\/parts\/0$/.test(call.path) });
  await assert.rejects(uploadBook(endpoint, packed, ui(), timers), /connection to your Private Cloud was lost/);
  assert.ok(calls.some(call => call.method === 'DELETE'));
  await packed.cleanup();
});

test('a book too large for the destination says by how much, before sending anything', async () => {
  const packed = await book();
  const { endpoint, calls } = agent({ max: 5 * 1024 * 1024 });
  await assert.rejects(uploadBook(endpoint, packed, ui(), timers), /room for 5 MB/);
  assert.equal(calls.length, 1);
  await packed.cleanup();
  assert.equal(megabytes(3 * 1024 ** 3), '3.0 GB');
});

test('a destination with a book asks first; declining leaves it untouched', async () => {
  const notEmpty = { status: 409, body: { detail: { code: 'not_empty', message: 'This server already has a book.' } } };
  const declined = await book();
  const first = agent({ commit: [notEmpty] });
  assert.equal(await uploadBook(first.endpoint, declined, ui({ confirmReplace: async () => false }), timers), null);
  assert.equal(first.calls.filter(call => call.path.endsWith('/commit')).length, 1);
  assert.ok(first.calls.some(call => call.method === 'DELETE'));
  const accepted = await book();
  const second = agent({ commit: [notEmpty] });
  assert.equal(await uploadBook(second.endpoint, accepted, ui(), timers), MOVE);
  assert.deepEqual(second.calls.filter(call => call.path.endsWith('/commit')).map(call => call.json), [{ replace: false }, { replace: true }]);
  await declined.cleanup(); await accepted.cleanup();
});

test('an import that fails on the destination reports its reason', async () => {
  const packed = await book();
  await assert.rejects(uploadBook(agent({ importFails: 'The uploaded book did not match its checksum.' }).endpoint, packed, ui(), timers), /did not match its checksum/);
  await packed.cleanup();
});

test('cancelling stops between parts and tells the destination to discard what it has', async () => {
  const packed = await book();
  let sent = 0;
  const { endpoint, calls } = agent();
  const cancelling = ui({ cancelled: () => calls.filter(call => call.method === 'PUT').length >= 2 });
  await assert.rejects(uploadBook(endpoint, packed, cancelling, timers), MoveCancelled);
  assert.equal(calls.filter(call => call.method === 'PUT').length, 2 + sent);
  assert.ok(calls.some(call => call.method === 'DELETE'));
  await packed.cleanup();
});

test('packing streams to disk with the right size and checksum, and refuses an empty export', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'onw-pack-'));
  try {
    const seen: number[] = [];
    const packed = await packToFile(new Response(DATA), dir, total => seen.push(total));
    assert.equal(packed.size, DATA.length);
    assert.equal(packed.sha256, sha(DATA));
    assert.deepEqual(await readFile(packed.path), DATA);
    assert.equal(seen.at(-1), DATA.length);
    if (process.platform !== 'win32') assert.equal((await stat(packed.path)).mode & 0o077, 0);
    await packed.cleanup();
    await assert.rejects(stat(dir));
    const empty = await mkdtemp(join(tmpdir(), 'onw-pack-'));
    await assert.rejects(packToFile(new Response(new Uint8Array(0)), empty), MoveError);
    await assert.rejects(stat(empty));
    await assert.rejects(packToFile(new Response('no', { status: 500 }), dir), /could not be exported/);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('keys and connections go from one side to the other and are never part of the file', async () => {
  const payload = { ai_providers: { anthropic: { api_key: 'sk-SECRET' } }, connectors: [] };
  const asked: string[] = [];
  const from: MoveEndpoint = { call: async call => {
    asked.push(call.path);
    if (call.path === '/api/move/export-nonce') return { status: 200, body: { nonce: 'n-1' } };
    assert.equal(call.path, '/api/move/secrets/export');
    assert.equal(call.headers!['X-Move-Nonce'], 'n-1');
    return { status: 200, body: payload };
  } };
  const sent: MoveCall[] = [];
  const to: MoveEndpoint = { call: async call => { sent.push(call); return { status: 200, body: { applied: [], skipped: [] } }; } };
  await applySecrets(to, MOVE, await readSecrets(from));
  assert.deepEqual(sent[0]!.json, payload);
  assert.equal(sent[0]!.path, `/api/move/${MOVE}/secrets`);
  assert.deepEqual(asked, ['/api/move/export-nonce', '/api/move/secrets/export']);
  await assert.rejects(readSecrets({ call: async () => ({ status: 500, body: null }) }), MoveError);
  const finish = await finishMove({ call: async () => ({ status: 200, body: { enabled: ['telegram'], checklist: [{ id: 'local_model', title: 'A model', detail: '', fix: 'x' }] } }) }, MOVE);
  assert.deepEqual(finish.enabled, ['telegram']);
});

test('the whole move runs in order, survives a failed secrets step, and always removes the temp file', async () => {
  const packed = await book();
  const order: string[] = [];
  const target = agent();
  const to: MoveEndpoint = { call: async call => { order.push(`${call.method} ${call.path.replace(MOVE, ':id').replace(/\/parts\/\d+/, '/parts/n')}`); return target.endpoint.call(call).then(result => call.path.endsWith('/finish') ? { status: 200, body: { enabled: ['telegram'], checklist: [] } } : call.path.endsWith('/secrets') ? { status: 500, body: null } : result); } };
  const outcome = await runMove({ from: { call: async call => ({ status: 200, body: call.path.endsWith('nonce') ? { nonce: 'n' } : { ai_providers: {}, connectors: [] } }) }, to, pack: async () => packed }, ui(), timers);
  assert.deepEqual(outcome, { enabled: ['telegram'], checklist: [], secretsMoved: false });
  assert.ok(order.indexOf('POST /api/move/:id/secrets') > order.indexOf('POST /api/move/:id/commit'));
  assert.equal(order.at(-1), 'POST /api/move/:id/finish');
  await assert.rejects(stat(packed.dir));
});

test('a declined replacement or a failure leaves no temp file behind', async () => {
  const declined = await book();
  const notEmpty = { status: 409, body: { detail: { code: 'not_empty' } } };
  assert.equal(await runMove({ from: agent().endpoint, to: agent({ commit: [notEmpty] }).endpoint, pack: async () => declined }, ui({ confirmReplace: async () => false })), null);
  await assert.rejects(stat(declined.dir));
  const failed = await book();
  await assert.rejects(runMove({ from: agent().endpoint, to: agent({ max: 1 }).endpoint, pack: async () => failed }, ui()), MoveError);
  await assert.rejects(stat(failed.dir));
});
