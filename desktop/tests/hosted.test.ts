import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { test } from 'node:test';
import { HostedLinkError, PendingSignIns, SIGN_IN_TTL_MS, createPkce, createState, deepLinkArgument, parseDeepLink } from '../src/main/hosted-auth';
import { callbackUrl, freshReauth, hostedConfig, hostedOrigin, instanceOrigin, portalConnectUrl, redeemUrl, safeReturnPath, signInUrl, stateUrl, validSlug } from '../src/main/hosted-config';
import { HostedSignIn } from '../src/main/hosted';
import type { PendingContext } from '../src/main/hosted-pending-store';
import type { StoredSignIn } from '../src/main/hosted-auth';

const config = hostedConfig({});
const local = hostedConfig({ ONW_HOSTED_PORTAL_URL: 'http://localhost:8100', ONW_HOSTED_INSTANCE_DOMAIN: 'http://localhost:8180' });
const link = (params: Record<string, string>) => `onw://auth?${new URLSearchParams(params)}`;

test('PKCE verifier is url-safe, 43-128 chars, and the challenge is its unpadded base64url SHA-256', () => {
  for (let i = 0; i < 20; i++) {
    const { verifier, challenge } = createPkce();
    assert.match(verifier, /^[A-Za-z0-9_-]{43,128}$/);
    assert.equal(challenge, createHash('sha256').update(verifier).digest('base64url'));
    assert.doesNotMatch(challenge, /=/);
  }
  assert.notEqual(createPkce().verifier, createPkce().verifier);
});

test('app state is 16-128 url-safe characters and unique', () => {
  const states = new Set(Array.from({ length: 50 }, () => createState()));
  assert.equal(states.size, 50);
  for (const state of states) assert.match(state, /^[A-Za-z0-9_-]{16,128}$/);
});

test('defaults and local-stack overrides are read from the environment', () => {
  assert.deepEqual(config, { portalOrigin: 'https://cloud.opennetworth.ai', instanceScheme: 'https', instanceDomain: 'opennetworth.cloud' });
  assert.deepEqual(local, { portalOrigin: 'http://localhost:8100', instanceScheme: 'http', instanceDomain: 'localhost:8180' });
  assert.equal(hostedConfig({ ONW_HOSTED_INSTANCE_DOMAIN: 'example.test' }).instanceDomain, 'example.test');
});

test('plain HTTP is accepted only for loopback and malformed values are refused', () => {
  assert.throws(() => hostedConfig({ ONW_HOSTED_PORTAL_URL: 'http://portal.example' }));
  assert.throws(() => hostedConfig({ ONW_HOSTED_INSTANCE_DOMAIN: 'http://instances.example' }));
  assert.throws(() => hostedConfig({ ONW_HOSTED_PORTAL_URL: 'https://user:pw@portal.example' }));
  assert.throws(() => hostedConfig({ ONW_HOSTED_PORTAL_URL: 'https://portal.example/app' }));
  assert.throws(() => hostedConfig({ ONW_HOSTED_INSTANCE_DOMAIN: 'bad domain/x' }));
  assert.throws(() => hostedConfig({ ONW_HOSTED_INSTANCE_DOMAIN: 'evil.example@x.test' }));
});

test('slugs are lowercase alphanumerics of 1-32 characters', () => {
  for (const slug of ['a', 'abc123', 'x'.repeat(32)]) assert.equal(validSlug(slug), true, slug);
  for (const slug of ['', 'x'.repeat(33), 'ABC', 'a-b', 'a.b', 'a/b', 'a@b', 'a b', '../x', undefined, 7]) assert.equal(validSlug(slug), false, String(slug));
  assert.throws(() => instanceOrigin(config, 'evil.example/#'));
});

test('sign-in, callback, state and redeem URLs follow the contracts', () => {
  const sign = new URL(signInUrl(config, 'chal', 'state_state_state_1'));
  assert.equal(sign.origin, 'https://cloud.opennetworth.ai');
  assert.equal(sign.pathname, '/connect');
  assert.deepEqual([...sign.searchParams.keys()].sort(), ['app_state', 'client', 'code_challenge']);
  assert.equal(sign.searchParams.get('client'), 'desktop');
  const callback = new URL(callbackUrl(local, 'acme1', 'a+b/c=', 'verifier'));
  assert.equal(callback.origin, 'http://acme1.localhost:8180');
  assert.equal(callback.pathname, '/_gateway/callback');
  assert.equal(callback.searchParams.get('code'), 'a+b/c=');
  assert.equal(callback.searchParams.get('code_verifier'), 'verifier');
  assert.equal(stateUrl('https://acme1.opennetworth.cloud'), 'https://acme1.opennetworth.cloud/_gateway/state');
  assert.equal(redeemUrl(config), 'https://cloud.opennetworth.ai/api/desktop/redeem');
  assert.equal(new URL(signInUrl(config, 'c', 'state_state_state_1', true)).searchParams.get('move'), '1');
  assert.equal(new URL(signInUrl(config, 'c', 'state_state_state_1')).searchParams.has('move'), false);
  assert.throws(() => callbackUrl(config, 'Bad Slug', 'c', 'v'));
});

test('a saved instance URL is trusted only for the configured domain', () => {
  assert.equal(hostedOrigin(config, 'https://acme1.opennetworth.cloud'), 'https://acme1.opennetworth.cloud');
  assert.equal(hostedOrigin(local, 'http://acme1.localhost:8180'), 'http://acme1.localhost:8180');
  for (const value of ['https://opennetworth.cloud', 'https://evil.example', 'http://acme1.opennetworth.cloud', 'https://a.b.opennetworth.cloud', 'https://acme1.opennetworth.cloud/app', 'https://u@acme1.opennetworth.cloud', 'nonsense']) assert.equal(hostedOrigin(config, value), null, value);
});

test('only the portal /connect page is handed to the system browser', () => {
  assert.equal(portalConnectUrl(config, 'https://cloud.opennetworth.ai/connect?slug=a&client=desktop'), true);
  assert.equal(portalConnectUrl(config, 'https://cloud.opennetworth.ai/billing'), false);
  assert.equal(portalConnectUrl(config, 'https://acme1.opennetworth.cloud/connect'), false);
  assert.equal(portalConnectUrl(config, 'not a url'), false);
});

test('deep links parse to slug, code and state', () => {
  const state = 'S'.repeat(20);
  assert.deepEqual(parseDeepLink(link({ slug: 'acme1', code: 'one-time.code_1', state })), { slug: 'acme1', code: 'one-time.code_1', state });
  assert.equal(deepLinkArgument(['/app', '--flag', `onw://auth?slug=a`]), 'onw://auth?slug=a');
  assert.equal(deepLinkArgument(['/app', 'file.csv']), null);
});

test('malformed deep links are refused without echoing the code', () => {
  const state = 'S'.repeat(20);
  const bad = [
    link({ slug: 'Evil.Example', code: 'secretcode', state }),
    link({ slug: '', code: 'secretcode', state }),
    link({ code: 'secretcode', state }),
    link({ slug: 'acme1', state }),
    link({ slug: 'acme1', code: 'secretcode', state: 'short' }),
    link({ slug: 'acme1', code: 'secretcode', state: `${'a'.repeat(20)}!` }),
    link({ slug: 'acme1', code: 'secret code', state }),
    `https://auth?${new URLSearchParams({ slug: 'acme1', code: 'secretcode', state })}`,
    `onw://other?${new URLSearchParams({ slug: 'acme1', code: 'secretcode', state })}`,
    'onw://',
    'not a url',
  ];
  for (const value of bad) {
    assert.throws(() => parseDeepLink(value), (error: unknown) => error instanceof HostedLinkError && error.reason === 'malformed' && !error.message.includes('secretcode'), value);
  }
});

test('a state is accepted once, only while fresh, and only if this app created it', () => {
  let now = 1_000;
  const pending = new PendingSignIns<string>(() => now);
  const { state } = pending.begin('ctx');
  assert.equal(pending.consume(state).context, 'ctx');
  assert.throws(() => pending.consume(state), (error: unknown) => error instanceof HostedLinkError && error.reason === 'unknown');
  assert.throws(() => pending.consume('never-created-state-1'), HostedLinkError);
  const old = pending.begin('old');
  now += SIGN_IN_TTL_MS - 1;
  const fresh = pending.begin('fresh');
  now += 2;
  assert.throws(() => pending.consume(old.state), HostedLinkError);
  assert.equal(pending.consume(fresh.state).context, 'fresh');
  assert.equal(pending.size, 0);
});

test('pending sign-ins stay bounded', () => {
  const pending = new PendingSignIns<undefined>();
  const first = pending.begin(undefined);
  for (let i = 0; i < 20; i++) pending.begin(undefined);
  assert.ok(pending.size <= 8);
  assert.throws(() => pending.consume(first.state), HostedLinkError);
});

function memoryStore(initial: StoredSignIn<PendingContext>[] = []) {
  const state = { saved: initial, clears: 0 };
  return { state, load: async () => state.saved, save: async (entries: StoredSignIn<PendingContext>[]) => { state.saved = entries; }, clear: async () => { state.saved = []; state.clears++; } };
}

function flow(clock = { now: 0 }, store = memoryStore()) {
  const opened: string[] = [];
  const completed: { origin: string; entry: string; move: boolean; context: PendingContext }[] = [];
  const signIn = new HostedSignIn({ config: () => local, store, openBrowser: async url => { opened.push(url); }, complete: async (origin, entry, context) => { completed.push({ origin, entry, move: context.move, context }); }, now: () => clock.now });
  return { signIn, opened, completed, clock, store };
}

test('a sign-in opens the portal with a challenge and completes with the matching verifier', async () => {
  const { signIn, opened, completed } = flow();
  await signIn.start();
  const url = new URL(opened[0]!);
  assert.equal(url.origin, 'http://localhost:8100');
  const state = url.searchParams.get('app_state')!;
  const challenge = url.searchParams.get('code_challenge')!;
  await signIn.handleLink(link({ slug: 'acme1', code: 'abc123', state }));
  assert.equal(completed.length, 1);
  const entry = new URL(completed[0]!.entry);
  assert.equal(completed[0]!.origin, 'http://acme1.localhost:8180');
  assert.equal(entry.searchParams.get('code'), 'abc123');
  assert.equal(createHash('sha256').update(entry.searchParams.get('code_verifier')!).digest('base64url'), challenge);
});

test('a replayed, unknown, expired or bad-slug link never completes', async () => {
  const { signIn, opened, completed, clock } = flow();
  await signIn.start();
  const state = new URL(opened[0]!).searchParams.get('app_state')!;
  await assert.rejects(signIn.handleLink(link({ slug: 'bad/slug', code: 'abc', state })), HostedLinkError);
  await signIn.handleLink(link({ slug: 'acme1', code: 'abc', state }));
  await assert.rejects(signIn.handleLink(link({ slug: 'acme1', code: 'abc', state })), HostedLinkError);
  await assert.rejects(signIn.handleLink(link({ slug: 'acme1', code: 'abc', state: 'x'.repeat(24) })), HostedLinkError);
  await signIn.start();
  clock.now += SIGN_IN_TTL_MS + 1;
  await assert.rejects(signIn.handleLink(link({ slug: 'acme1', code: 'abc', state: new URL(opened[1]!).searchParams.get('app_state')! })), HostedLinkError);
  assert.equal(completed.length, 1);
});

test('a sign-in stays valid for a day, long enough to close the tab, sign up and pay', () => {
  assert.equal(SIGN_IN_TTL_MS, 24 * 60 * 60 * 1000);
});

test('an automatic restart is throttled but an explicit one always opens the browser', async () => {
  const { signIn, opened, clock } = flow();
  await signIn.start(false, true);
  await signIn.start(false, true);
  assert.equal(opened.length, 1);
  await signIn.start();
  assert.equal(opened.length, 2);
  clock.now += 6_000;
  await signIn.start(false, true);
  assert.equal(opened.length, 3);
});

test('"move" rides the sign-in that requested it and no other', async () => {
  const { signIn, opened, completed } = flow();
  await signIn.start(true);
  await signIn.start();
  await signIn.handleLink(link({ slug: 'acme1', code: 'c1', state: new URL(opened[1]!).searchParams.get('app_state')! }));
  await signIn.handleLink(link({ slug: 'acme1', code: 'c2', state: new URL(opened[0]!).searchParams.get('app_state')! }));
  assert.deepEqual(completed.map(entry => entry.move), [false, true]);
});

test('a fresh sign-in asks the portal for fresh=1 and comes back to the page it left', async () => {
  assert.equal(new URL(signInUrl(config, 'c', 'state_state_state_1', false, true)).searchParams.get('fresh'), '1');
  assert.equal(new URL(signInUrl(config, 'c', 'state_state_state_1')).searchParams.has('fresh'), false);
  const { signIn, opened, completed } = flow();
  await signIn.start(false, false, { returnPath: '/settings/terminal' });
  const url = new URL(opened[0]!);
  assert.equal(url.searchParams.get('fresh'), '1');
  assert.equal(url.searchParams.get('client'), 'desktop');
  await signIn.handleLink(link({ slug: 'acme1', code: 'c1', state: url.searchParams.get('app_state')! }));
  assert.deepEqual(completed[0]!.context, { move: false, fresh: true, returnPath: '/settings/terminal' });
  await signIn.start();
  assert.equal(new URL(opened[1]!).searchParams.has('fresh'), false);
});

test('reopening a fresh sign-in keeps asking for a fresh one', async () => {
  const { signIn, opened } = flow();
  await signIn.start(false, false, { returnPath: null });
  await signIn.reopen();
  assert.equal(opened.length, 2);
  assert.equal(opened[0], opened[1]);
});

test('a portal re-auth redirect is a fresh request only when it says fresh=1, and its return path is a same-site path', () => {
  const connect = (query: string) => `https://cloud.opennetworth.ai/connect?${query}`;
  assert.deepEqual(freshReauth(config, connect('slug=a&fresh=1&return=%2Fsettings%2Fterminal')), { returnPath: '/settings/terminal' });
  assert.deepEqual(freshReauth(config, connect('slug=a&fresh=1&return=%2F%2Fevil.example')), { returnPath: null });
  assert.deepEqual(freshReauth(config, connect('slug=a&fresh=1&return=https%3A%2F%2Fevil.example%2F')), { returnPath: null });
  assert.equal(freshReauth(config, connect('slug=a&return=%2F')), null);
  assert.equal(freshReauth(config, 'https://evil.example/connect?fresh=1'), null);
  assert.equal(freshReauth(config, 'nope'), null);
  assert.equal(safeReturnPath('/settings?tab=terminal'), '/settings?tab=terminal');
  assert.equal(safeReturnPath('/\\evil'), null);
  assert.equal(safeReturnPath(42), null);
});

test('a pending sign-in survives a restart and a cold-start link completes it', async () => {
  const first = flow();
  await first.signIn.start(true);
  const url = new URL(first.opened[0]!);
  assert.equal(url.searchParams.get('move'), '1');
  const restarted = flow({ now: 1_000 }, first.store);
  assert.equal(restarted.signIn.waiting, false);
  await restarted.signIn.restore();
  assert.equal(restarted.signIn.waiting, true);
  assert.equal(restarted.signIn.movePending, true);
  await restarted.signIn.handleLink(link({ slug: 'acme1', code: 'cold', state: url.searchParams.get('app_state')! }));
  assert.equal(restarted.completed.length, 1);
  assert.equal(restarted.completed[0]!.move, true);
  assert.deepEqual(first.store.state.saved, []);
});

test('a sign-in older than a day is dropped on restart', async () => {
  const first = flow();
  await first.signIn.start();
  const late = flow({ now: SIGN_IN_TTL_MS + 5 }, first.store);
  await late.signIn.restore();
  assert.equal(late.signIn.waiting, false);
});

test('cancel forgets the sign-in everywhere, so a late link is refused', async () => {
  const { signIn, opened, store, completed } = flow();
  await signIn.start();
  await signIn.cancel();
  assert.deepEqual(store.state.saved, []);
  await assert.rejects(signIn.handleLink(link({ slug: 'acme1', code: 'c', state: new URL(opened[0]!).searchParams.get('app_state')! })), HostedLinkError);
  assert.equal(completed.length, 0);
});

test('"open browser again" reuses the request in progress, and starts one if there is none', async () => {
  const { signIn, opened } = flow();
  await signIn.reopen();
  assert.equal(opened.length, 1);
  await signIn.reopen();
  assert.equal(opened[1], opened[0]);
  assert.equal(signIn.waiting, true);
});
