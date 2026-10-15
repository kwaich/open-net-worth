import assert from 'node:assert/strict';
import { test } from 'node:test';
import { RedeemError, normalizeShortCode, redeemShortCode } from '../src/main/hosted-redeem';

const config = { portalOrigin: 'https://cloud.opennetworth.ai' };
const state = 'S'.repeat(20);

function reply(status: number, body: unknown): typeof fetch {
  return (async (_url: unknown, init?: RequestInit) => { calls.push({ url: String(_url), init }); return new Response(JSON.stringify(body), { status }); }) as typeof fetch;
}
const calls: { url: string; init?: RequestInit }[] = [];

test('a typed code is normalized the way Crockford base32 is read', () => {
  assert.equal(normalizeShortCode('abcd-2345'), 'ABCD-2345');
  assert.equal(normalizeShortCode(' abcd 2345 '), 'ABCD-2345');
  assert.equal(normalizeShortCode('ABCD2345'), 'ABCD-2345');
  assert.equal(normalizeShortCode('OBCD-I345'), '0BCD-1345');
  for (const bad of ['', 'ABC-123', 'ABCD-23456', 'ABCU-2345', 'ABCD_2345', '../etc']) assert.equal(normalizeShortCode(bad), null, bad);
});

test('redeeming posts the normalized code to the portal and returns a validated hand-off', async () => {
  calls.length = 0;
  const link = await redeemShortCode(config, 'abcd 2345', reply(200, { slug: 'acme1', code: 'one-time.code_1', state }));
  assert.deepEqual(link, { slug: 'acme1', code: 'one-time.code_1', state });
  assert.equal(calls[0]!.url, 'https://cloud.opennetworth.ai/api/desktop/redeem');
  assert.equal(calls[0]!.init!.method, 'POST');
  assert.deepEqual(JSON.parse(String(calls[0]!.init!.body)), { short_code: 'ABCD-2345' });
  assert.equal(calls[0]!.init!.credentials, 'omit');
});

test('a bad, used or rate-limited code reads as a plain sentence', async () => {
  await assert.rejects(redeemShortCode(config, 'nope', reply(200, {})), RedeemError);
  await assert.rejects(redeemShortCode(config, 'ABCD-2345', reply(404, {})), /not valid or has expired/);
  await assert.rejects(redeemShortCode(config, 'ABCD-2345', reply(429, {})), /Too many attempts/);
  const offline = (async () => { throw new TypeError('fetch failed'); }) as typeof fetch;
  await assert.rejects(redeemShortCode(config, 'ABCD-2345', offline), /Could not reach/);
});

test('a reply of the wrong shape never reaches the gateway URL', async () => {
  for (const body of [{ slug: 'Evil/x', code: 'c', state }, { slug: 'acme1', code: 'has space', state }, { slug: 'acme1', code: 'c', state: 'short' }, { slug: 'acme1', state }, null]) {
    await assert.rejects(redeemShortCode(config, 'ABCD-2345', reply(200, body)), /unexpected/, JSON.stringify(body));
  }
});
