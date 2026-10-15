import assert from 'node:assert/strict';
import { test } from 'node:test';
import { openLocalConnection } from '../src/main/local-connection-action';

test('a failed remembered book reports its error even after retiring the chooser and preserves the connection when declined', async () => {
  const selected: boolean[] = [];
  let chooserAlive = true;
  let shown = false;
  const opened = await openLocalConnection({
    select: async alternate => { selected.push(alternate); chooserAlive = false; throw new Error('This book is already open on port 8098.'); },
    showFailure: async (message, offerAlternate) => {
      shown = true;
      assert.equal(chooserAlive, false);
      assert.match(message, /already open on port 8098/);
      assert.equal(offerAlternate, true);
      return false;
    },
  });
  assert.equal(opened, false);
  assert.equal(shown, true);
  assert.deepEqual(selected, [false]);
});

test('only an explicit alternate choice opens another local picker after a remembered-book failure', async () => {
  const selected: boolean[] = [];
  const opened = await openLocalConnection({
    select: async alternate => { selected.push(alternate); if (!alternate) throw new Error('The remembered folder is missing.'); return true; },
    showFailure: async () => true,
  });
  assert.equal(opened, true);
  assert.deepEqual(selected, [false, true]);
});

test('cancelling an alternate picker keeps the connection and a second failure does not retry automatically', async () => {
  for (const fail of [false, true]) {
    const selected: boolean[] = [];
    const offers: boolean[] = [];
    const opened = await openLocalConnection({
      select: async alternate => { selected.push(alternate); if (!alternate || fail) throw new Error('Book unavailable.'); return false; },
      showFailure: async (_message, offerAlternate) => { offers.push(offerAlternate); return true; },
    });
    assert.equal(opened, false);
    assert.deepEqual(selected, [false, true]);
    assert.deepEqual(offers, fail ? [true, false] : [true]);
  }
});
