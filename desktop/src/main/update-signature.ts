import { createHash, createPublicKey, verify } from 'node:crypto';

// Minisign (https://jedisct1.github.io/minisign/) verification, in-process: no binary to ship and
// nothing for an attacker who controls the release page to substitute. Key and signature are the
// documented base64 forms; the algorithm is pure Ed25519 ('Ed') or Ed25519 over the BLAKE2b-512
// of the message ('ED', what current minisign writes), plus the signature over the trusted comment.
const ED25519_SPKI_PREFIX = Buffer.from('302a300506032b6570032100', 'hex');
const SIGNATURE_BYTES = 64;
const KEY_ID_BYTES = 8;

export const SIGNATURE_ASSET = 'SHA256SUMS.minisig';

function ed25519Key(raw: Buffer) {
  return createPublicKey({ key: Buffer.concat([ED25519_SPKI_PREFIX, raw]), format: 'der', type: 'spki' });
}

function decode(base64: string, length: number, what: string): Buffer {
  const bytes = Buffer.from(base64.trim(), 'base64');
  if (bytes.length !== length) throw new Error(`The update ${what} is malformed.`);
  return bytes;
}

// `publicKey` is the second line of a minisign .pub file: base64('Ed' + keyId + ed25519 key).
export function verifyMinisign(message: Buffer, signatureFile: string, publicKey: string): void {
  const key = decode(publicKey, 2 + KEY_ID_BYTES + 32, 'public key');
  if (key.toString('latin1', 0, 2) !== 'Ed') throw new Error('The update public key is malformed.');
  const lines = signatureFile.split(/\r?\n/);
  const trusted = lines[2]?.startsWith('trusted comment: ') ? lines[2].slice('trusted comment: '.length) : null;
  if (lines[0]?.startsWith('untrusted comment:') !== true || !lines[1] || trusted === null || !lines[3]) throw new Error('The update signature is malformed.');
  const signature = decode(lines[1], 2 + KEY_ID_BYTES + SIGNATURE_BYTES, 'signature');
  const algorithm = signature.toString('latin1', 0, 2);
  if (algorithm !== 'Ed' && algorithm !== 'ED') throw new Error('The update signature uses an unsupported algorithm.');
  if (!signature.subarray(2, 2 + KEY_ID_BYTES).equals(key.subarray(2, 2 + KEY_ID_BYTES))) throw new Error('The update was signed with a different key than this app trusts.');
  const publicKeyObject = ed25519Key(key.subarray(2 + KEY_ID_BYTES));
  const raw = signature.subarray(2 + KEY_ID_BYTES);
  const signed = algorithm === 'ED' ? createHash('blake2b512').update(message).digest() : message;
  const globalSignature = decode(lines[3], SIGNATURE_BYTES, 'trusted-comment signature');
  const valid = verify(null, signed, publicKeyObject, raw)
    && verify(null, Buffer.concat([raw, Buffer.from(trusted, 'utf8')]), publicKeyObject, globalSignature);
  if (!valid) throw new Error('The update signature is not valid, so the update was not installed.');
}
