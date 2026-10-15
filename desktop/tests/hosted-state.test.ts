import assert from 'node:assert/strict';
import { test } from 'node:test';
import { checkAgent } from '../src/main/hosted-state';
import { hostedStatePage } from '../src/main/hosted-state-page';

const origin = 'https://acme1.opennetworth.cloud';
const answer = (body: unknown, status = 200): typeof fetch => (async (url: unknown) => { urls.push(String(url)); return new Response(typeof body === 'string' ? body : JSON.stringify(body), { status }); }) as typeof fetch;
const urls: string[] = [];

test('a running Private Cloud opens, and the check needs no sign-in', async () => {
  urls.length = 0;
  assert.deepEqual(await checkAgent(origin, '1.2.0', answer({ state: 'running', portal_url: 'https://cloud.opennetworth.ai', min_desktop_version: '1.0.0' })), { kind: 'open' });
  assert.equal(urls[0], `${origin}/_gateway/state`);
});

test('every other state is reported with its name', async () => {
  for (const state of ['starting', 'updating', 'restarting', 'stopped', 'failed', 'deleting', 'unknown']) {
    assert.deepEqual(await checkAgent(origin, '1.2.0', answer({ state })), { kind: 'state', state }, state);
  }
  assert.deepEqual(await checkAgent(origin, '1.2.0', answer({ state: 'unknown' }, 404)), { kind: 'state', state: 'unknown' });
});

test('an app older than the minimum is asked to update, even while the cloud runs', async () => {
  assert.deepEqual(await checkAgent(origin, '1.2.0', answer({ state: 'running', min_desktop_version: '1.3.0' })), { kind: 'update', required: '1.3.0' });
  assert.deepEqual(await checkAgent(origin, '1.3.0', answer({ state: 'running', min_desktop_version: '1.3.0' })), { kind: 'open' });
  assert.deepEqual(await checkAgent(origin, '2.0.0', answer({ state: 'running', min_desktop_version: '1.3.0' })), { kind: 'open' });
  assert.deepEqual(await checkAgent(origin, '1.2.0', answer({ state: 'running', min_desktop_version: 'soon' })), { kind: 'open' });
});

test('an answer that is not the gateway\'s lets the app try, and a network failure is reported', async () => {
  assert.deepEqual(await checkAgent(origin, '1.2.0', answer('<html>bad gateway</html>', 502)), { kind: 'open' });
  assert.deepEqual(await checkAgent(origin, '1.2.0', answer({ state: 'exploded' })), { kind: 'open' });
  assert.deepEqual(await checkAgent(origin, '1.2.0', (async () => { throw new TypeError('offline'); }) as typeof fetch), { kind: 'unreachable' });
});

test('each state page names its fix and offers the right button', () => {
  const page = (check: Parameters<typeof hostedStatePage>[0]) => hostedStatePage(check, 'dark');
  assert.match(page({ kind: 'state', state: 'stopped' }), /Reactivate in your account/);
  assert.match(page({ kind: 'state', state: 'failed' }), /Open your account/);
  assert.match(page({ kind: 'state', state: 'unknown' }), /No Private Cloud found/);
  assert.match(page({ kind: 'unreachable' }), /Check your internet connection/);
  const update = page({ kind: 'update', required: '1.3.0' });
  assert.match(update, /Update the desktop app/);
  assert.match(update, /1\.3\.0/);
  assert.match(update, /Download the update/);
  assert.doesNotMatch(update, /id="retry"/);
  const starting = page({ kind: 'state', state: 'starting' });
  assert.match(starting, /setTimeout/);
  assert.doesNotMatch(page({ kind: 'state', state: 'stopped' }), /setTimeout/);
  for (const text of [starting, update, page({ kind: 'state', state: 'updating' })]) assert.doesNotMatch(text, /instance/i);
});
