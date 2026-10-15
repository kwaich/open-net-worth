import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ArtifactProtocolOwner } from '../src/main/artifact-protocol';

test('artifact protocol ownership supports first launch and repeated local-server-local switches', () => {
  let registered = false;
  let removes = 0;
  const registry = {
    handle: () => { assert.equal(registered, false); registered = true; },
    unhandle: () => { assert.equal(registered, true); registered = false; removes++; },
  };
  const local = new ArtifactProtocolOwner(registry);
  assert.equal(removes, 0);
  local.register(() => new Response());
  local.retire();
  local.retire();
  const server = new ArtifactProtocolOwner(registry);
  server.retire();
  assert.equal(removes, 1);
  const nextLocal = new ArtifactProtocolOwner(registry);
  nextLocal.register(() => new Response());
  nextLocal.retire();
  assert.equal(removes, 2);
});
