import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, mkdir, writeFile, symlink, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
import { BookInUseError, parseStartupError, parseReady, childEnvironment, runtimePath, bundledRuntime } from '../src/main/runtime';

test('startup failure identifies an owned busy book and rejects foreign or invalid responses', () => {
  const error = { type: 'startup_error', protocol: 1, instance_id: 'owned', version: '0.1.0', code: 'book_in_use', port: 8097 };
  const parsed = parseStartupError(JSON.stringify(error), 'owned', '0.1.0');
  assert.ok(parsed instanceof BookInUseError);
  assert.match(parsed.message, /port 8097/);
  for (const patch of [{ instance_id: 'foreign' }, { version: 'other' }, { protocol: 2 }, { code: 'unknown' }, { port: '8097' }, { port: -1 }]) {
    assert.throws(() => parseStartupError(JSON.stringify({ ...error, ...patch }), 'owned', '0.1.0'));
  }
});

test('readiness rejects another backend identity and invalid ports', () => {
  const ready = { type: 'ready', protocol: 1, port: 49123, instance_id: 'owned', version: '0.1.0' };
  assert.equal(parseReady(JSON.stringify(ready), 'owned', '0.1.0').port, 49123);
  assert.throws(() => parseReady(JSON.stringify({ ...ready, instance_id: 'stranger' }), 'owned', '0.1.0'));
  assert.throws(() => parseReady(JSON.stringify({ ...ready, port: 8080.5 }), 'owned', '0.1.0'));
  assert.throws(() => parseReady(JSON.stringify({ ...ready, protocol: 2 }), 'owned', '0.1.0'));
  assert.throws(() => parseReady(JSON.stringify({ ...ready, version: '9.9.9' }), 'owned', '0.1.0'));
});

test('bundled runtime validates host platform, architecture and contained executable realpath', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-runtime-manifest-'));
  try {
    const runtime = join(directory, 'runtime');
    await mkdir(join(runtime, 'python', 'bin'), { recursive: true });
    await writeFile(join(runtime, 'python', 'bin', 'python3'), '', { mode: 0o700 });
    const valid = { version: '0.1.0', protocol: 1, python: 'python/bin/python3', cwd: '.', platform: process.platform === 'win32' ? 'windows' : process.platform, arch: process.arch === 'x64' ? 'x86_64' : 'aarch64' };
    const manifest = join(runtime, 'manifest.json');
    await writeFile(manifest, JSON.stringify(valid));
    assert.equal((await bundledRuntime(runtime, '0.1.0')).version, '0.1.0');
    for (const patch of [{ platform: 'wrong-platform' }, { arch: process.arch === 'arm64' ? 'x86_64' : 'arm64' }]) {
      await writeFile(manifest, JSON.stringify({ ...valid, ...patch }));
      await assert.rejects(bundledRuntime(runtime, '0.1.0'));
    }
    const outside = join(directory, 'external-python');
    await writeFile(outside, '', { mode: 0o700 });
    await symlink(outside, join(runtime, 'python-link'));
    await writeFile(manifest, JSON.stringify({ ...valid, python: 'python-link' }));
    await assert.rejects(bundledRuntime(runtime, '0.1.0'), /runtime/);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('child environment strips ambient Python, credentials and app configuration', () => {
  const environment = childEnvironment({ HOME: '/home/user', PATH: '/usr/bin', PYTHONPATH: '/evil', AUTH_PASSWORD: 'secret', ONW_ROOT: '/live', ANTHROPIC_API_KEY: 'key', LANG: 'en_US.UTF-8' });
  assert.equal(environment.HOME, '/home/user');
  assert.equal(environment.LANG, 'en_US.UTF-8');
  assert.equal(environment.PYTHONPATH, undefined);
  assert.equal(environment.AUTH_PASSWORD, undefined);
  assert.equal(environment.ONW_ROOT, undefined);
  assert.equal(environment.ANTHROPIC_API_KEY, undefined);
  assert.equal(environment.PYTHONNOUSERSITE, '1');
});

test('child environment preserves the account identity used by Claude login', () => {
  const environment = childEnvironment({ HOME: '/Users/test-user', USER: 'test-user', CLAUDE_CODE_OAUTH_TOKEN: 'secret', ANTHROPIC_AUTH_TOKEN: 'secret' }, 'darwin');
  assert.equal(environment.USER, 'test-user');
  assert.equal(environment.CLAUDE_CODE_OAUTH_TOKEN, undefined);
  assert.equal(environment.ANTHROPIC_AUTH_TOKEN, undefined);
});

test('a Finder-launched backend finds Claude in the native user install directory', { skip: process.platform !== 'darwin' }, async () => {
  const home = await mkdtemp(join(tmpdir(), 'onw-cli-home-'));
  try {
    const binary = join(home, '.local', 'bin', 'claude');
    await mkdir(join(home, '.local', 'bin'), { recursive: true });
    await writeFile(binary, '#!/bin/sh\nexit 0\n', { mode: 0o700 });
    const environment = childEnvironment({ HOME: home, PATH: '/usr/bin:/bin:/usr/sbin:/sbin' });
    const detected = execFileSync('/usr/bin/which', ['claude'], { env: environment, encoding: 'utf8' }).trim();
    assert.equal(detected, binary);
  } finally { await rm(home, { recursive: true, force: true }); }
});

test('macOS CLI search preserves inherited precedence and adds no relative home directory', () => {
  const environment = childEnvironment({ HOME: 'relative-home', PATH: '/custom/bin:/usr/bin:/custom/bin' }, 'darwin');
  assert.equal(environment.PATH, '/custom/bin:/usr/bin:/opt/homebrew/bin:/usr/local/bin:/bin:/usr/sbin:/sbin');
});

test('Linux CLI search keeps inherited precedence and adds the user install directory, not Homebrew', () => {
  const environment = childEnvironment({ HOME: '/home/me', PATH: '/custom/bin:/usr/bin' }, 'linux');
  assert.equal(environment.PATH, '/custom/bin:/usr/bin:/home/me/.local/bin:/usr/local/bin:/bin:/usr/sbin:/sbin');
});

test('Windows child environments preserve their existing PATH', () => {
  assert.equal(childEnvironment({ HOME: '/home/user', PATH: '/custom/bin' }, 'win32').PATH, '/custom/bin');
});

test('runtime manifest paths cannot escape bundled resources', () => {
  assert.equal(runtimePath('/runtime', 'python/bin/python3'), resolve('/runtime', 'python/bin/python3'));
  for (const path of ['../python', '/usr/bin/python3', '.', 'python/../../escape']) assert.throws(() => runtimePath('/runtime', path));
});
