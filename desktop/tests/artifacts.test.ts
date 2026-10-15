import assert from 'node:assert/strict';
import { test } from 'node:test';
import { artifactFrameAllowed, artifactRequest, artifactPolicy } from '../src/main/artifact-policy';

test('artifact protocol accepts only bounded stored artifact references', () => {
  assert.deepEqual(artifactRequest('onw-artifact://preview/art_abcdef123456?type=html'), { id: 'art_abcdef123456', type: 'html' });
  for (const url of ['onw-artifact://preview/../secrets?type=html', 'onw-artifact://preview/art_a?type=html&path=/secret', 'onw-artifact://other/art_a?type=html', 'http://preview/art_a?type=html', 'onw-artifact://preview/art_a?type=javascript']) assert.equal(artifactRequest(url), null);
});

test('interactive artifact policy keeps an opaque sandbox and prohibits network egress', () => {
  const origin = 'http://127.0.0.1:12345';
  const policy = artifactPolicy('html', origin);
  assert.match(policy, /sandbox allow-scripts/);
  assert.match(policy, /default-src 'none'/);
  assert.match(policy, /script-src 'unsafe-inline'/);
  assert.doesNotMatch(policy, /allow-same-origin|connect-src/);
  assert.match(policy, /frame-ancestors http:\/\/127\.0\.0\.1:12345;/);
  assert.match(artifactPolicy('svg', origin), /script-src 'none'/);
});

test('artifact previews require a direct child of the trusted app main frame', () => {
  const origin = 'http://127.0.0.1:12345';
  const mainFrame = { url: `${origin}/accounts`, routingId: 7 };
  const context = { ownedContentsId: 1, requestContentsId: 1, resourceType: 'subFrame', mainFrame, frame: { parent: mainFrame } };
  assert.equal(artifactFrameAllowed(origin, context), true);
  assert.equal(artifactFrameAllowed(origin, { ...context, frame: { parent: { url: 'https://untrusted.example', routingId: 8 } } }), false);
  assert.equal(artifactFrameAllowed(origin, { ...context, requestContentsId: 2 }), false);
  assert.equal(artifactFrameAllowed(origin, { ...context, resourceType: 'mainFrame' }), false);
  assert.equal(artifactFrameAllowed(origin, { ...context, mainFrame: { ...mainFrame, url: `${origin}/api/uploads/raw/file` } }), false);
});
