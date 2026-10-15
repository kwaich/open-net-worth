import { createHash } from 'node:crypto';
import { createWriteStream } from 'node:fs';
import { rm } from 'node:fs/promises';
import { join } from 'node:path';
import { Readable, Transform } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import type { ReadableStream as WebStream } from 'node:stream/web';
import { MoveError } from './move-endpoint';

export interface PackedBook { path: string; size: number; sha256: string; cleanup(): Promise<void> }

// Writes a book export to disk as it arrives (a book with years of documents is gigabytes; it is never held
// in memory), with its size and checksum, which the destination verifies again after the upload.
export async function packToFile(response: Response, directory: string, onBytes: (total: number) => void = () => {}): Promise<PackedBook> {
  if (!response.ok || !response.body) throw new MoveError('The book could not be exported.');
  const path = join(directory, 'book.zip');
  const digest = createHash('sha256');
  let size = 0;
  const tap = new Transform({ transform(chunk: Buffer, _encoding, done) { digest.update(chunk); size += chunk.length; onBytes(size); done(null, chunk); } });
  try { await pipeline(Readable.fromWeb(response.body as WebStream), tap, createWriteStream(path, { mode: 0o600 })); }
  catch (error) { await rm(directory, { recursive: true, force: true }); throw error; }
  if (size === 0) { await rm(directory, { recursive: true, force: true }); throw new MoveError('The exported book was empty.'); }
  return { path, size, sha256: digest.digest('hex'), cleanup: () => rm(directory, { recursive: true, force: true }) };
}
