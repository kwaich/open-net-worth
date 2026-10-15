import assert from 'node:assert/strict';
import { test } from 'node:test';
import { savedServerOrigin, serverAddress, validateServer, ConnectionSwitcher, type ConnectionTarget } from '../src/main/connections';

test('server addresses accept only origins with explicit secure or private transport', () => {
  assert.deepEqual(serverAddress(' https://wealth.example/ '), { origin: 'https://wealth.example', insecure: false });
  for (const address of ['http://localhost:8098', 'http://127.0.0.1:8098', 'http://[::1]:8098']) assert.equal(serverAddress(address).insecure, false);
  for (const address of ['http://192.168.1.2:8098', 'http://10.1.2.3', 'http://172.16.1.2', 'http://wealth.local', 'http://[fd00::1]']) assert.equal(serverAddress(address).insecure, true);
  for (const address of ['http://wealth.example', 'http://172.32.1.2', 'http://169.254.1.2', 'http://0.0.0.0', 'https://user:pass@wealth.example', 'https://wealth.example/app', 'https://wealth.example/?token=a', 'https://wealth.example/#a', 'file:///book', 'javascript:1', 'https://wealth.example/?', 'https://wealth.example/#']) assert.throws(() => serverAddress(address), address);
});

test('server compatibility probes use no local credential, cookies or redirects', async () => {
  const requests: { url: string; options: RequestInit }[] = [];
  const fetcher = async (url: string | URL | Request, options?: RequestInit) => {
    requests.push({ url: String(url), options: options! });
    return Response.json(String(url).endsWith('/health') ? { status: 'ok', version: '1.2.3' } : { required: true, authenticated: false });
  };
  await validateServer('https://wealth.example', new AbortController().signal, fetcher);
  assert.equal(requests.length, 2);
  for (const { url, options } of requests) {
    assert.equal(new URL(url).origin, 'https://wealth.example');
    assert.equal(options.redirect, 'error');
    assert.equal(options.credentials, 'omit');
    assert.equal(new Headers(options.headers).has('X-ONW-Desktop-Session'), false);
  }
  await assert.rejects(validateServer('https://wealth.example', new AbortController().signal, async () => Response.json({ status: 'ok' })));
});

test('connection changes commit only after load and roll back without losing the local book', async () => {
  const events: string[] = [];
  let fail = false;
  const local: ConnectionTarget = { mode: 'local', serverUrl: null };
  const server: ConnectionTarget = { mode: 'server', serverUrl: 'https://wealth.example' };
  const switcher = new ConnectionSwitcher(local, {
    retire: async () => { events.push('retire'); },
    open: async target => { events.push(`open:${target.mode}`); if (target.mode === 'server' && fail) throw new Error('offline'); },
    persist: async target => { events.push(`persist:${target.mode}`); },
    recover: async () => { events.push('recover'); },
  });
  fail = true;
  await assert.rejects(switcher.open(server), /offline.*previous connection/);
  assert.deepEqual(switcher.current, local);
  assert.deepEqual(events, ['retire', 'open:server', 'retire', 'open:local']);
  events.length = 0; fail = false;
  await switcher.open(server);
  assert.deepEqual(events, ['retire', 'open:server', 'persist:server']);
  assert.deepEqual(switcher.current, server);
});

test('a server typed without a scheme gets http on this machine or the local network, https elsewhere', () => {
  assert.deepEqual(serverAddress('localhost:8080'), { origin: 'http://localhost:8080', insecure: false });
  assert.deepEqual(serverAddress('127.0.0.1:8080/'), { origin: 'http://127.0.0.1:8080', insecure: false });
  assert.deepEqual(serverAddress('192.168.1.20:8080'), { origin: 'http://192.168.1.20:8080', insecure: true });
  assert.deepEqual(serverAddress('wealth.example.com'), { origin: 'https://wealth.example.com', insecure: false });
  assert.throws(() => serverAddress('wealth.example.com/path'), /server origin only/);
});

test('a saved server address that no longer validates is dropped instead of blocking startup', () => {
  assert.equal(savedServerOrigin(' https://wealth.example/ '), 'https://wealth.example');
  for (const saved of ['http://wealth.example', 'file:///book', 'https://wealth.example/app', '']) assert.equal(savedServerOrigin(saved), null, saved);
});
