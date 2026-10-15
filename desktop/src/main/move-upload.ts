import { createHash } from 'node:crypto';
import { open } from 'node:fs/promises';
import { MoveCancelled, MoveError, detailOf, type MoveEndpoint, type MoveResult } from './move-endpoint';
import type { PackedBook } from './move-pack';

export interface UploadUi {
  progress(percent: number | null, text: string): void;
  confirmReplace(): Promise<boolean>;
  cancelled(): boolean;
}
export interface UploadTimers { sleep(ms: number): Promise<void> }
const REAL_TIMERS: UploadTimers = { sleep: ms => new Promise(resolve => setTimeout(resolve, ms)) };
const ATTEMPTS = 6;

export function megabytes(bytes: number): string {
  return bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 1024 ** 2))} MB`;
}

function transient(result: MoveResult | null): boolean { return result === null || result.status >= 500 || result.status === 408 || result.status === 429; }

async function attempt(send: () => Promise<MoveResult>): Promise<MoveResult | null> {
  try { return await send(); } catch { return null; }
}

async function receivedParts(endpoint: MoveEndpoint, moveId: string): Promise<Set<number> | null> {
  const status = await attempt(() => endpoint.call({ method: 'GET', path: `/api/move/${moveId}` }));
  const received = (status?.body as { received?: unknown } | null)?.received;
  return status?.status === 200 && Array.isArray(received) ? new Set(received.filter((n): n is number => Number.isInteger(n))) : null;
}

async function uploadPart(endpoint: MoveEndpoint, moveId: string, n: number, bytes: Uint8Array, timers: UploadTimers): Promise<void> {
  const headers = { 'X-Part-Sha256': createHash('sha256').update(bytes).digest('hex') };
  for (let tries = 0; ; tries++) {
    const result = await attempt(() => endpoint.call({ method: 'PUT', path: `/api/move/${moveId}/parts/${n}`, bytes, headers }));
    if (result?.status === 204) return;
    if (!transient(result) || tries + 1 >= ATTEMPTS) throw new MoveError(result ? detailOf(result, 'The upload was refused') : 'The connection to your Private Cloud was lost. Check your internet connection and try again.');
    await timers.sleep(Math.min(30_000, 1_000 * 2 ** tries));
    // The reply may have been lost after the destination stored the part; ask before sending it again.
    if ((await receivedParts(endpoint, moveId))?.has(n)) return;
  }
}

async function begin(endpoint: MoveEndpoint, book: PackedBook): Promise<{ moveId: string; partSize: number }> {
  const result = await endpoint.call({ method: 'POST', path: '/api/move/begin', json: { size: book.size, sha256: book.sha256 } });
  const body = result.body as { move_id?: string; part_size?: number; detail?: { max_size?: number } } | null;
  if (result.status === 413) throw new MoveError(`Your book is ${megabytes(book.size)} but the destination has room for ${megabytes(body?.detail?.max_size ?? 0)}. Free up space there, or remove large documents, and try again.`);
  if (result.status !== 200 || !body?.move_id || !body.part_size) throw new MoveError(detailOf(result, 'The move could not start'));
  return { moveId: body.move_id, partSize: body.part_size };
}

async function waitForImport(endpoint: MoveEndpoint, moveId: string, ui: UploadUi, timers: UploadTimers): Promise<void> {
  for (let misses = 0; ;) {
    if (ui.cancelled()) throw new MoveCancelled();
    const result = await attempt(() => endpoint.call({ method: 'GET', path: `/api/move/${moveId}` }));
    if (!result || result.status !== 200) { if (++misses > ATTEMPTS * 5) throw new MoveError('Lost contact with your Private Cloud while it was importing your book.'); }
    else {
      misses = 0;
      const { status, progress, error } = result.body as { status: string; progress: number; error?: string | null };
      if (status === 'done') return;
      if (status === 'failed') throw new MoveError(error || 'The destination could not import the book.');
      ui.progress(Math.min(99, Math.max(0, progress)), 'Importing your book…');
    }
    await timers.sleep(1_000);
  }
}

// Sends the packed book in parts, resuming from whatever the destination already has after any drop in the
// connection, then commits and waits for the import. Resolves to the move's id, or null if the person declined
// to replace the destination's existing book.
export async function uploadBook(endpoint: MoveEndpoint, book: PackedBook, ui: UploadUi, timers: UploadTimers = REAL_TIMERS): Promise<string | null> {
  const { moveId, partSize } = await begin(endpoint, book);
  const abort = () => { void attempt(() => endpoint.call({ method: 'DELETE', path: `/api/move/${moveId}` })); };
  const file = await open(book.path, 'r');
  try {
    const parts = Math.ceil(book.size / partSize);
    for (let n = 0; n < parts; n++) {
      if (ui.cancelled()) { abort(); throw new MoveCancelled(); }
      const buffer = new Uint8Array(Math.min(partSize, book.size - n * partSize));
      await file.read(buffer, 0, buffer.length, n * partSize);
      await uploadPart(endpoint, moveId, n, buffer, timers);
      ui.progress(Math.round((n + 1) / parts * 100), `Uploading… ${megabytes(Math.min(book.size, (n + 1) * partSize))} of ${megabytes(book.size)}`);
    }
  } catch (error) { if (!(error instanceof MoveCancelled)) abort(); throw error; }
  finally { await file.close(); }
  ui.progress(null, 'Checking your book…');
  let commit = await endpoint.call({ method: 'POST', path: `/api/move/${moveId}/commit`, json: { replace: false } });
  if (commit.status === 409 && (commit.body as { detail?: { code?: string } } | null)?.detail?.code === 'not_empty') {
    if (!await ui.confirmReplace()) { abort(); return null; }
    commit = await endpoint.call({ method: 'POST', path: `/api/move/${moveId}/commit`, json: { replace: true } });
  }
  if (commit.status !== 200) { abort(); throw new MoveError(detailOf(commit, 'The destination refused your book')); }
  await waitForImport(endpoint, moveId, ui, timers);
  return moveId;
}
