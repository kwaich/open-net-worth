import assert from 'node:assert/strict';
import { test } from 'node:test';
import { verifyMinisign } from '../src/main/update-signature';
import { testSigner } from './minisign-fixture';

// Produced by the real `minisign -S` (v0.12, a throwaway key whose secret was discarded), so the
// verifier is pinned against the actual tool and not only against our own idea of the format.
const REAL_KEY = 'RWSwTr8YazfyBRwNJ0BU0u2ay6a+Xju6IlQwIWFJ/YNhp2Vqfbsxbf9b';
const REAL_SUMS = `2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881  Open-Net-Worth-linux-x86_64.AppImage\n`;
const REAL_SIGNATURE = `untrusted comment: signature from minisign secret key
RUSwTr8YazfyBSILGOHJ0yMp+f1Fj6POv7suyrlRXh2HDCZ9ZOtId5UBcOMyr5rihX+FpRKmStYZn3hFehJElwHfZ1qDRyRMzgI=
trusted comment: onw test v0.2.0
9x8TNvh+FObBJ4EknpMrE6lV9u2ur6lA4n4hI80LxdT2CWEumL8GDpy0QBxMAB6uU0Vac+1MrfeR/nhu2w/jAQ==
`;

const sums = Buffer.from(`${'a'.repeat(64)}  Open-Net-Worth-linux-x86_64.AppImage\n`);

test('a signature written by the real minisign tool verifies', () => {
  verifyMinisign(Buffer.from(REAL_SUMS), REAL_SIGNATURE, REAL_KEY);
});

test('the real tool\'s signature rejects a changed message and a different key', () => {
  assert.throws(() => verifyMinisign(Buffer.from(`${REAL_SUMS}x`), REAL_SIGNATURE, REAL_KEY), /not valid/);
  assert.throws(() => verifyMinisign(Buffer.from(REAL_SUMS), REAL_SIGNATURE, testSigner().publicKey), /different key/);
});

test('both minisign algorithms verify (prehashed ED and legacy Ed)', () => {
  const signer = testSigner();
  verifyMinisign(sums, signer.sign(sums), signer.publicKey);
  verifyMinisign(sums, signer.sign(sums, 'legacy', 'Ed'), signer.publicKey);
});

test('a tampered checksum file is rejected', () => {
  const signer = testSigner();
  const signature = signer.sign(sums);
  assert.throws(() => verifyMinisign(Buffer.from(sums.toString().replace('a', 'b')), signature, signer.publicKey), /not valid/);
});

test('a signature from another key is rejected, even with a matching key id', () => {
  const trusted = testSigner();
  const attacker = testSigner();
  assert.throws(() => verifyMinisign(sums, attacker.sign(sums), trusted.publicKey), /different key/);
  const forged = attacker.sign(sums);
  const lines = forged.split('\n');
  const body = Buffer.from(lines[1], 'base64');
  Buffer.from(trusted.publicKey, 'base64').copy(body, 2, 2, 10);
  lines[1] = body.toString('base64');
  assert.throws(() => verifyMinisign(sums, lines.join('\n'), trusted.publicKey), /not valid/);
});

test('an altered trusted comment is rejected', () => {
  const signer = testSigner();
  const signature = signer.sign(sums, 'file:SHA256SUMS').replace('file:SHA256SUMS', 'file:evil');
  assert.throws(() => verifyMinisign(sums, signature, signer.publicKey), /not valid/);
});

test('malformed keys and signature files are refused, never treated as valid', () => {
  const signer = testSigner();
  const signature = signer.sign(sums);
  assert.throws(() => verifyMinisign(sums, signature, ''), /malformed/);
  assert.throws(() => verifyMinisign(sums, signature, 'RWAAAA'), /malformed/);
  assert.throws(() => verifyMinisign(sums, '', signer.publicKey), /malformed/);
  assert.throws(() => verifyMinisign(sums, 'untrusted comment: x\nnot-base64!!\ntrusted comment: y\nzzz\n', signer.publicKey), /malformed/);
});
