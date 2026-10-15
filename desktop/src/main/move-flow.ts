import { MoveCancelled, type MoveEndpoint } from './move-endpoint';
import type { PackedBook } from './move-pack';
import { applySecrets, finishMove, readSecrets, type FinishResult } from './move-secrets';
import { uploadBook, type UploadTimers, type UploadUi } from './move-upload';

export interface MoveParties {
  // The side that has the book and its keys, and the side that receives them.
  from: MoveEndpoint;
  to: MoveEndpoint;
  pack(): Promise<PackedBook>;
}
export interface MoveOutcome extends FinishResult { secretsMoved: boolean }

// The whole move: pack to disk, upload and import, then the keys and connections, then switch the new copy on.
// The book is what matters, so a failure to move the keys is reported (the checklist says to reconnect) instead
// of failing a move that has already landed. Returns null when the person declined to replace the destination.
export async function runMove(parties: MoveParties, ui: UploadUi, timers?: UploadTimers): Promise<MoveOutcome | null> {
  // Read first: a gateway session is fresh for only 15 minutes, and packing or uploading a large book can outlast that.
  const secrets = await readSecrets(parties.from).then(payload => ({ payload }), () => null);
  ui.progress(null, 'Packing your book…');
  const book = await parties.pack();
  try {
    const moveId = await uploadBook(parties.to, book, ui, timers);
    if (!moveId) return null;
    if (ui.cancelled()) throw new MoveCancelled();
    ui.progress(null, 'Moving your keys and connections…');
    let secretsMoved = true;
    try { if (!secrets) throw new Error(); await applySecrets(parties.to, moveId, secrets.payload); } catch { secretsMoved = false; }
    ui.progress(null, 'Switching it on…');
    return { ...await finishMove(parties.to, moveId), secretsMoved };
  } finally { await book.cleanup(); }
}
