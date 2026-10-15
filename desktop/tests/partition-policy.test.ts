import assert from 'node:assert/strict';
import { test } from 'node:test';
import { claimPartition, type PartitionPolicy } from '../src/main/partition-policy';

type Listener = ((details: { url: string }, callback: (response: { cancel?: boolean }) => void) => void) | null;

function detachedPartition() {
  // What a window leaves behind when it switches back to a local book: every request cancelled.
  const cancelAll: Listener = (_details, callback) => callback({ cancel: true });
  const state = { beforeRequest: cancelAll as Listener, beforeSendHeaders: cancelAll as Listener | null, headersReceived: cancelAll as Listener | null, permission: null as unknown, check: null as unknown };
  const partition = {
    setPermissionRequestHandler: (handler: unknown) => { state.permission = handler; },
    setPermissionCheckHandler: (handler: unknown) => { state.check = handler; },
    webRequest: {
      onBeforeRequest: (listener: Listener) => { state.beforeRequest = listener; },
      onBeforeSendHeaders: (listener: Listener) => { state.beforeSendHeaders = listener; },
      onHeadersReceived: (listener: Listener) => { state.headersReceived = listener; },
    },
  } as unknown as PartitionPolicy;
  return { partition, state };
}

function decide(listener: Listener, url: string): boolean | undefined {
  let cancelled: boolean | undefined;
  listener!({ url }, response => { cancelled = response.cancel; });
  return cancelled;
}

test('a move reaches its agent even after the window that showed it detached the shared session', () => {
  const { partition, state } = detachedPartition();
  claimPartition(partition, 'https://w7dvrf.opennetworth.cloud');
  assert.equal(decide(state.beforeRequest, 'https://w7dvrf.opennetworth.cloud/_gateway/callback?code=x'), false);
  assert.equal(state.beforeSendHeaders, null, 'the cancel-everything header hook is gone');
  assert.equal(state.headersReceived, null);
});

test('the claimed session still refuses every other origin and every permission', () => {
  const { partition, state } = detachedPartition();
  claimPartition(partition, 'https://w7dvrf.opennetworth.cloud');
  assert.equal(decide(state.beforeRequest, 'https://cloud.opennetworth.ai/connect'), true);
  assert.equal(decide(state.beforeRequest, 'https://other.opennetworth.cloud/'), true);
  assert.equal(decide(state.beforeRequest, 'not a url'), true);
  let granted: boolean | undefined;
  (state.permission as (c: unknown, p: string, cb: (ok: boolean) => void) => void)(null, 'media', ok => { granted = ok; });
  assert.equal(granted, false);
  assert.equal((state.check as () => boolean)(), false);
});
