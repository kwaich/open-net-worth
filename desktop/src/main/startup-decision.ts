import { lstat } from 'node:fs/promises';
import type { BookInUseError } from './runtime';

export type StartupRecoveryKind = 'server-unreachable' | 'missing-book' | 'book-in-use' | 'book-failed';

export async function bookFolderMissing(root: string): Promise<boolean> {
  try { await lstat(root); return false; }
  catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    return code === 'ENOENT' || code === 'ENOTDIR';
  }
}

export function startupRecoveryKind(input: { serverOrigin: string | null; failure: BookInUseError | null; missing: boolean }): StartupRecoveryKind {
  if (input.serverOrigin) return 'server-unreachable';
  if (input.failure) return 'book-in-use';
  return input.missing ? 'missing-book' : 'book-failed';
}
