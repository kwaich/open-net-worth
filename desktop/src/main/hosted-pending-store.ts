import { mkdir, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';
import type { StoredSignIn } from './hosted-auth';

// What a pending sign-in or move carries across a restart. `move` only says a move was asked for;
// the book itself is packed after sign-in, so nothing large or sensitive waits on disk. `fresh` is a
// forced re-sign-in of the window already open, which returns to `returnPath` when it lands.
export interface PendingContext { move: boolean; fresh?: boolean; returnPath?: string }

// Electron's safeStorage in production; a plain pair in tests.
export interface SecretBox { available(): boolean; encrypt(text: string): Buffer; decrypt(data: Buffer): string }

// The PKCE verifier is a credential until the browser hand-off completes, so it is written only
// encrypted by the operating system's keychain, never as plain text. Without a keychain (some
// Linux sessions) a pending sign-in lives in memory and does not survive a restart.
export class PendingStore {
  constructor(private readonly path: string, private readonly box: SecretBox) {}

  get persistent(): boolean { return this.box.available(); }

  async save(entries: StoredSignIn<PendingContext>[]): Promise<void> {
    if (!this.persistent) return;
    if (!entries.length) { await this.clear(); return; }
    await mkdir(dirname(this.path), { recursive: true, mode: 0o700 });
    await writeFile(`${this.path}.tmp`, this.box.encrypt(JSON.stringify(entries)), { mode: 0o600 });
    await rename(`${this.path}.tmp`, this.path);
  }

  async load(): Promise<StoredSignIn<PendingContext>[]> {
    if (!this.persistent) return [];
    try {
      const parsed: unknown = JSON.parse(this.box.decrypt(await readFile(this.path)));
      if (!Array.isArray(parsed)) return [];
      return parsed.filter((entry): entry is StoredSignIn<PendingContext> => Boolean(entry) && typeof entry.state === 'string' && typeof entry.verifier === 'string' && typeof entry.expires === 'number' && typeof entry.context?.move === 'boolean');
    } catch { await this.clear(); return []; }
  }

  clear(): Promise<void> { return rm(this.path, { force: true }); }
}
