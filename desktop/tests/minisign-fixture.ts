import { createHash, generateKeyPairSync, sign, randomBytes, type KeyObject } from 'node:crypto';

// Builds minisign-format keys and signatures with node:crypto so the verifier is tested
// without the minisign binary. Interop with the real tool is pinned separately by a fixture
// the actual `minisign -S` produced (tests/update-signature.test.ts).
export interface TestSigner { publicKey: string; sign(message: Buffer, trustedComment?: string, algorithm?: 'ED' | 'Ed'): string }

export function testSigner(): TestSigner {
  const { privateKey, publicKey } = generateKeyPairSync('ed25519');
  const raw = (publicKey as KeyObject).export({ format: 'der', type: 'spki' }).subarray(-32);
  const keyId = randomBytes(8);
  return {
    publicKey: Buffer.concat([Buffer.from('Ed'), keyId, raw]).toString('base64'),
    sign(message, trustedComment = 'timestamp:1 file:SHA256SUMS', algorithm = 'ED') {
      const signed = algorithm === 'ED' ? createHash('blake2b512').update(message).digest() : message;
      const signature = sign(null, signed, privateKey);
      const global = sign(null, Buffer.concat([signature, Buffer.from(trustedComment)]), privateKey);
      return [
        'untrusted comment: signature from minisign secret key',
        Buffer.concat([Buffer.from(algorithm), keyId, signature]).toString('base64'),
        `trusted comment: ${trustedComment}`,
        global.toString('base64'),
        '',
      ].join('\n');
    },
  };
}
