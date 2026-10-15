import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { BrowserWindow } from 'electron';
import { ServerSubmission, type ServerSubmitContext } from '../src/main/server-submission';

const window = {} as BrowserWindow;
function context(overrides: Partial<ServerSubmitContext> = {}): { context: ServerSubmitContext; events: string[] } {
  const events: string[] = [];
  return {
    events,
    context: {
      surface: () => window,
      localRunning: () => false,
      ask: async (_parent, options) => { events.push(`ask:${options.title}`); return 1; },
      connect: async origin => { events.push(`connect:${origin}`); },
      lost: async () => { events.push('lost'); },
      validate: async origin => { events.push(`validate:${origin}`); },
      ...overrides,
    },
  };
}

test('an address that is not a URL is refused before any request or prompt, with the connection error text', async () => {
  const { context: ctx, events } = context();
  await assert.rejects(new ServerSubmission().submit('not a url at all/ x', ctx), /Enter a complete server URL|Use the server origin only/);
  await assert.rejects(new ServerSubmission().submit('https://wealth.example/app', ctx), /Use the server origin only, without credentials, a path, query or fragment\./);
  await assert.rejects(new ServerSubmission().submit('http://wealth.example', ctx), /Use HTTPS for this server/);
  await assert.rejects(new ServerSubmission().submit(42, ctx), /Enter a server URL\./);
  assert.deepEqual(events, []);
});

test('a secure server is validated and connected without a prompt', async () => {
  const submission = new ServerSubmission();
  const { context: ctx, events } = context();
  assert.equal(await submission.submit(' https://wealth.example/ ', ctx), true);
  assert.deepEqual(events, ['validate:https://wealth.example', 'connect:https://wealth.example']);
  assert.equal(submission.busy, false);
});

test('an unreachable server surfaces the validation error and never connects', async () => {
  const submission = new ServerSubmission();
  const { context: ctx, events } = context({ validate: async () => { throw new Error('Could not reach a compatible Open Net Worth server.'); } });
  await assert.rejects(submission.submit('https://wealth.example', ctx), /Could not reach/);
  assert.deepEqual(events, []);
  assert.equal(submission.busy, false);
});

test('a plain-HTTP LAN address asks "Connection not encrypted" and connects only on Connect anyway', async () => {
  const submission = new ServerSubmission();
  const declined = context({ ask: async (_parent, options) => { assert.deepEqual(options.buttons, ['Cancel', 'Connect anyway']); return 0; } });
  assert.equal(await submission.submit('http://192.168.1.20:8080', declined.context), false);
  assert.deepEqual(declined.events, ['validate:http://192.168.1.20:8080']);
  const accepted = context();
  assert.equal(await submission.submit('http://192.168.1.20:8080', accepted.context), true);
  assert.deepEqual(accepted.events, ['validate:http://192.168.1.20:8080', 'ask:Connection not encrypted', 'connect:http://192.168.1.20:8080']);
});

test('loopback HTTP is not warned about, and a running local book asks to switch first', async () => {
  const submission = new ServerSubmission();
  const plain = context();
  assert.equal(await submission.submit('http://127.0.0.1:8098', plain.context), true);
  assert.deepEqual(plain.events, ['validate:http://127.0.0.1:8098', 'connect:http://127.0.0.1:8098']);
  const running = context({ localRunning: () => true });
  assert.equal(await submission.submit('https://wealth.example', running.context), true);
  assert.deepEqual(running.events, ['validate:https://wealth.example', 'ask:Change connection', 'connect:https://wealth.example']);
  const declined = context({ localRunning: () => true, ask: async () => 0 });
  assert.equal(await submission.submit('https://wealth.example', declined.context), false);
  assert.deepEqual(declined.events, ['validate:https://wealth.example']);
});

test('Back during validation abandons the attempt; the stale submit connects nothing', async () => {
  const submission = new ServerSubmission();
  let release!: () => void;
  const { context: ctx, events } = context({ validate: () => new Promise<void>(resolve => { release = resolve; }) });
  const pending = submission.submit('https://wealth.example', ctx);
  assert.equal(submission.validating, true);
  await assert.rejects(submission.submit('https://other.example', ctx), /Wait for the current connection operation/);
  submission.abort();
  release();
  assert.equal(await pending, false);
  assert.deepEqual(events, []);
  assert.equal(submission.busy, false);
});

test('a window that disappears mid-validation ends quietly; one that disappears mid-connect reports the failure', async () => {
  const gone = context({ surface: () => null });
  assert.equal(await new ServerSubmission().submit('https://wealth.example', gone.context), false);
  assert.deepEqual(gone.events, ['validate:https://wealth.example']);
  let alive = true;
  const lost = context({ surface: () => alive ? window : null, connect: async () => { alive = false; throw new Error('Could not open.'); } });
  const submission = new ServerSubmission();
  await assert.rejects(submission.submit('https://wealth.example', lost.context), /Could not open/);
  assert.deepEqual(lost.events, ['validate:https://wealth.example', 'lost']);
  assert.equal(submission.connecting, false);
});

test('a failed connect with the window still showing rethrows inline without a separate dialog', async () => {
  const failing = context({ connect: async () => { throw new Error('The selected connection could not open.'); } });
  await assert.rejects(new ServerSubmission().submit('https://wealth.example', failing.context), /could not open/);
  assert.deepEqual(failing.events, ['validate:https://wealth.example']);
});
