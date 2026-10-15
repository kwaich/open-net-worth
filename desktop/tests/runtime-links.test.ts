import assert from 'node:assert/strict';
import { test } from 'node:test';
import { cp, mkdir, mkdtemp, readFile, readlink, rename, rm, symlink, writeFile } from 'node:fs/promises';
import { join, isAbsolute } from 'node:path';
import { tmpdir } from 'node:os';

const { runtimeResourceHooks, validateRuntimeLinks } = require('../resources/runtime-links.cjs') as {
  runtimeResourceHooks(source: string, appName: string): { beforeCopyExtraResources: (() => Promise<unknown>)[]; afterCopyExtraResources: ((options: { buildPath: string; platform: string }) => Promise<void>)[] };
  validateRuntimeLinks(directory: string, relativeOnly?: boolean): Promise<unknown>;
};

test('packaged Python links remain internal after packager copy and application relocation', { skip: process.platform === 'win32' }, async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-runtime-copy-'));
  try {
    const source = join(directory, 'source');
    await mkdir(join(source, 'python', 'bin'), { recursive: true });
    await writeFile(join(source, 'python', 'bin', 'python3.12'), 'bundled interpreter');
    await symlink('python3.12', join(source, 'python', 'bin', 'python3'));
    await symlink(join(source, 'python', 'bin', 'python3'), join(source, 'python', 'bin', 'python'));
    const buildPath = join(directory, 'packaged');
    const copied = join(buildPath, 'Open Net Worth.app', 'Contents', 'Resources', 'runtime');
    const hooks = runtimeResourceHooks(source, 'Open Net Worth');
    await hooks.beforeCopyExtraResources[0]();
    await cp(source, copied, { recursive: true });
    assert.equal(isAbsolute(await readlink(join(copied, 'python', 'bin', 'python3'))), true);
    await hooks.afterCopyExtraResources[0]({ buildPath, platform: 'darwin' });
    assert.equal(await readlink(join(copied, 'python', 'bin', 'python3')), 'python3.12');
    assert.equal(await readlink(join(copied, 'python', 'bin', 'python')), 'python3.12');
    const relocated = join(directory, 'relocated');
    await rename(copied, relocated);
    await rm(source, { recursive: true });
    await validateRuntimeLinks(relocated, true);
    assert.equal(await readFile(join(relocated, 'python', 'bin', 'python3'), 'utf8'), 'bundled interpreter');
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('packaging rejects external and broken runtime symlinks', { skip: process.platform === 'win32' }, async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-runtime-bad-links-'));
  try {
    const source = join(directory, 'runtime');
    await mkdir(source);
    const outside = join(directory, 'outside');
    await writeFile(outside, 'outside');
    await symlink(outside, join(source, 'escape'));
    await assert.rejects(validateRuntimeLinks(source), /nonportable/);
    await rm(join(source, 'escape'));
    await symlink('missing', join(source, 'broken'));
    await assert.rejects(validateRuntimeLinks(source));
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('packaging refuses an unexpected external link in copied resources', { skip: process.platform === 'win32' }, async () => {
  const directory = await mkdtemp(join(tmpdir(), 'onw-runtime-copy-escape-'));
  try {
    const source = join(directory, 'source');
    await mkdir(source);
    await writeFile(join(source, 'file'), 'runtime');
    const buildPath = join(directory, 'packaged');
    const copied = join(buildPath, 'resources', 'runtime');
    await cp(source, copied, { recursive: true });
    const outside = join(directory, 'outside');
    await writeFile(outside, 'outside');
    await symlink(outside, join(copied, 'escape'));
    await assert.rejects(runtimeResourceHooks(source, 'Open Net Worth').afterCopyExtraResources[0]({ buildPath, platform: 'linux' }), /nonportable/);
  } finally { await rm(directory, { recursive: true, force: true }); }
});
