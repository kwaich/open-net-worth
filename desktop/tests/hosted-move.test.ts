import assert from 'node:assert/strict';
import { test } from 'node:test';
import { movedDetail, whenText } from '../src/main/hosted-move';
import { HostedPageClient, PAGE_REQUEST_CHANNEL, routePageResponse, trackPageClient } from '../src/main/hosted-page-client';

const item = { id: 'local_model', title: 'A model on your computer', detail: 'ollama', fix: 'Add an API key under Settings → AI.' };

test('the finish message lists only what could not move, each with its fix', () => {
  const detail = movedDetail([item], 'to-cloud');
  assert.match(detail, /A model on your computer \(ollama\): Add an API key/);
  assert.match(detail, /old copy/);
  assert.match(detail, /Move my book back to this computer/);
  assert.match(movedDetail([], 'to-cloud'), /keys, connections and automations moved/);
  assert.doesNotMatch(movedDetail([], 'to-computer'), /Move my book back/);
  assert.match(movedDetail([], 'to-computer'), /Private Cloud is untouched/);
});

test('moved dates read as dates and a bad value never throws', () => {
  assert.match(whenText('2026-10-03T12:00:00Z', 'en-US'), /Oct/);
  assert.equal(whenText('nonsense'), 'earlier');
});

function sender() {
  const sent: { channel: string; payload: { id: number; request: { path: string } } }[] = [];
  return { sent, send: (channel: string, payload: unknown) => { sent.push({ channel, payload: payload as never }); } };
}

test('page requests are matched to answers, time out and abandon cleanly', async () => {
  const target = sender();
  const client = new HostedPageClient(target, 'https://acme1.opennetworth.cloud', 20);
  const pending = client.call({ method: 'GET', path: '/api/move/mv_x' });
  assert.equal(target.sent[0]!.channel, PAGE_REQUEST_CHANNEL);
  client.deliver({ id: 999, status: 200, body: {} });
  client.deliver({ id: target.sent[0]!.payload.id, status: 200, body: { received: [0] } });
  assert.deepEqual(await pending, { status: 200, body: { received: [0] } });
  await assert.rejects(client.call({ method: 'GET', path: '/x' }), /did not respond/);
  const dead = client.call({ method: 'GET', path: '/x' });
  client.deliver({ id: target.sent[2]!.payload.id, status: 0, body: null });
  await assert.rejects(dead, /could not be reached/);
  const abandoned = new HostedPageClient(sender(), 'https://acme1.opennetworth.cloud', 10_000);
  const waiting = abandoned.call({ method: 'GET', path: '/x' });
  abandoned.abandon();
  await assert.rejects(waiting, /closed/);
});

test('only the page a client drives, on its own origin, can answer it', async () => {
  const target = sender();
  const other = sender();
  const client = new HostedPageClient(target, 'https://acme1.opennetworth.cloud', 10_000);
  const untrack = trackPageClient(client);
  const pending = client.call({ method: 'GET', path: '/api/move/mv_x' });
  const id = target.sent[0]!.payload.id;
  routePageResponse(other, { url: 'https://acme1.opennetworth.cloud/' }, { id, status: 200, body: 'from the wrong window' });
  routePageResponse(target, { url: 'https://evil.example/' }, { id, status: 200, body: 'from the wrong origin' });
  routePageResponse(target, null, { id, status: 200, body: 'no frame' });
  routePageResponse(target, { url: 'https://acme1.opennetworth.cloud/settings' }, { id, status: 200, body: 'ok' });
  assert.deepEqual(await pending, { status: 200, body: 'ok' });
  untrack();
  const after = client.call({ method: 'GET', path: '/x' });
  routePageResponse(target, { url: 'https://acme1.opennetworth.cloud/' }, { id: target.sent[1]!.payload.id, status: 200, body: 'untracked' });
  client.abandon();
  await assert.rejects(after, /closed/);
});
