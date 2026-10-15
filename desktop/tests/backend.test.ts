import assert from 'node:assert/strict';
import { mock, test } from 'node:test';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { BackendSupervisor } from '../src/main/backend';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';

test('creating and stopping a server-mode supervisor never initializes the local runtime', async () => {
  let initialized = 0;
  const backend = new BackendSupervisor(async () => { initialized++; throw new Error('No local runtime expected'); }, '/unused/book', true, () => {});
  await backend.stop();
  assert.equal(initialized, 0);
  assert.equal(backend.origin, null);
  assert.equal(backend.credential, '');
});

test('a lock failure buffered after process exit survives until stdout closes', async () => {
  const backend = new BackendSupervisor({ python: '/unused/python3', cwd: process.cwd(), version: '0.1.0' }, '/book', true, () => {});
  Reflect.set(backend, 'instanceId', 'owned');
  const child = Object.assign(new EventEmitter(), { stdout: new PassThrough() });
  const ready = Reflect.apply(Reflect.get(backend, 'awaitReady'), backend, [child]);
  child.emit('exit', 1, null);
  child.stdout.write(`${JSON.stringify({ type: 'startup_error', protocol: 1, instance_id: 'owned', version: '0.1.0', code: 'book_in_use', port: 8097 })}\n`);
  child.stdout.end();
  child.emit('close', 1, null);
  await assert.rejects(ready, /port 8097/);
  assert.equal(backend.startupFailure?.port, 8097);
});

test('stopping a closed child clears late readiness before another book is selected', async () => {
  const backend = new BackendSupervisor({ python: '/unused/python3', cwd: process.cwd(), version: '0.1.0' }, '/book', true, () => {});
  Reflect.set(backend, 'instanceId', 'owned');
  Reflect.set(backend, 'token', 'synthetic');
  const child = Object.assign(new EventEmitter(), { stdout: new PassThrough() });
  const ready = Reflect.apply(Reflect.get(backend, 'awaitReady'), backend, [child]);
  child.emit('exit', 1, null);
  child.stdout.write(`${JSON.stringify({ type: 'ready', protocol: 1, instance_id: 'owned', version: '0.1.0', port: 49123 })}\n`);
  child.stdout.end();
  child.emit('close', 1, null);
  await ready;
  await backend.stop();
  assert.equal(backend.origin, null);
  assert.equal(backend.credential, '');
  backend.setRoot('/other-book');
});

test('missing bundled interpreter returns a startup error without hanging recovery', async () => {
  const backend = new BackendSupervisor({ python: '/nonexistent/onw/python3', cwd: process.cwd(), version: '0.1.0' }, '/nonexistent/book', true, () => {});
  await assert.rejects(backend.start(), /could not start/);
  assert.equal(backend.origin, null);
  await backend.stop();
});

test('the supervisor changes root only after the owned backend is stopped', () => {
  const backend = new BackendSupervisor({ python: '/unused/python3', cwd: process.cwd(), version: '0.1.0' }, '/book/A', true, () => {});
  backend.origin = 'http://127.0.0.1:12345';
  assert.throws(() => backend.setRoot('/book/B'), /Stop the current book/);
  assert.equal(backend.bookRoot, '/book/A');
  backend.origin = null;
  backend.setRoot('/book/B');
  assert.equal(backend.bookRoot, '/book/B');
});

test('a backend that dies during startup has its stderr reported, not discarded', { skip: process.platform === 'win32' }, async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-backend-stderr-'));
  const python = join(directory, 'python3');
  await writeFile(python, '#!/bin/sh\necho "synthetic import failure" >&2\nexit 3\n', { mode: 0o755 });
  const written: string[] = [];
  const write = mock.method(process.stderr, 'write', (chunk: string | Uint8Array) => { written.push(String(chunk)); return true; });
  try {
    const backend = new BackendSupervisor({ python, cwd: directory, version: '0.1.0' }, '/book', true, () => {});
    await assert.rejects(backend.start(), /stopped during startup/);
  } finally {
    write.mock.restore();
    await rm(directory, { recursive: true, force: true });
  }
  const report = JSON.parse(written.find(line => line.includes('desktop.backend_start_failed'))!);
  assert.equal(report.exitCode, 3);
  assert.match(report.backendStderr, /synthetic import failure/);
});
