import assert from 'node:assert/strict';
import { test } from 'node:test';
import { WindowLifetime } from '../src/main/window-lifetime';

test('retired callbacks never read destroyed BrowserWindow.webContents or access credentials', () => {
  let destroyed = false;
  let reads = 0;
  let credentialReads = 0;
  const contents = { isDestroyed: () => false };
  const lifetime = new WindowLifetime({
    isDestroyed: () => destroyed,
    get webContents() { reads++; if (destroyed) throw new Error('Object has been destroyed'); return contents; },
  });
  const request = () => {
    if (!lifetime.current()) return { cancel: true };
    credentialReads++;
    return { cancel: false };
  };
  assert.deepEqual(request(), { cancel: false });
  lifetime.retire();
  assert.deepEqual(request(), { cancel: true });
  destroyed = true;
  assert.deepEqual(request(), { cancel: true });
  assert.equal(reads, 1);
  assert.equal(credentialReads, 1);
});

test('unexpected window or webContents destruction fails closed before native property access', () => {
  let reads = 0;
  const destroyedWindow = new WindowLifetime({ isDestroyed: () => true, get webContents() { reads++; throw new Error('Object has been destroyed'); } });
  assert.equal(destroyedWindow.current(), null);
  assert.equal(reads, 0);
  const contents = new WindowLifetime({ isDestroyed: () => false, webContents: { isDestroyed: () => true } });
  assert.equal(contents.current(), null);
});
