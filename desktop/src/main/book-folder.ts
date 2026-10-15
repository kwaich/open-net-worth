import { lstat, readdir, realpath } from 'node:fs/promises';
import { basename, dirname, join, resolve } from 'node:path';

// Files the OS drops into any folder it shows; they don't make a folder "not empty".
const OS_CLUTTER = new Set(['.DS_Store', '.localized', 'Thumbs.db', 'desktop.ini']);

export class FolderNotEmptyError extends Error {}

export interface BookFolder { root: string; existing: boolean; device: number; inode: number }

async function regular(path: string, directory: boolean): Promise<boolean> {
  try {
    const stat = await lstat(path);
    if (stat.isSymbolicLink() || (directory ? !stat.isDirectory() : !stat.isFile())) throw new Error('The book data folder and database must be regular files inside the selected folder.');
    return true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return false;
    throw error;
  }
}

export async function inspectBookFolder(selected: string): Promise<BookFolder> {
  let root = await realpath(selected);
  if (basename(resolve(selected)) === 'data' && basename(root) === 'data' && await regular(join(root, 'onw.db'), false)) {
    if ((await lstat(resolve(selected))).isSymbolicLink()) throw new Error('Choose the complete book folder, not a linked data folder.');
    root = dirname(root);
  }
  const stat = await lstat(root);
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error('Choose a regular book folder.');
  const data = join(root, 'data');
  const existing = await regular(data, true) && await regular(join(data, 'onw.db'), false);
  if (!existing && (await readdir(root)).some(name => !OS_CLUTTER.has(name))) throw new FolderNotEmptyError('This folder already has other files in it. Choose an empty folder for a new book, or a folder that already holds a book.');
  return { root, existing, device: stat.dev, inode: stat.ino };
}

export async function verifyBookFolder(chosen: BookFolder): Promise<void> {
  const current = await inspectBookFolder(chosen.root);
  if (current.root !== chosen.root || current.existing !== chosen.existing || current.device !== chosen.device || current.inode !== chosen.inode) throw new Error('The selected book folder changed. Choose it again.');
}

export async function verifyRememberedBookRoot(root: string): Promise<void> {
  if ((await inspectBookFolder(root)).root !== root) throw new Error('The remembered book folder now points somewhere else. Choose its folder again.');
}

export async function verifyConfirmedBookRoot(root: string, confirmed: BookFolder): Promise<void> {
  const current = await inspectBookFolder(root);
  if (root !== confirmed.root || current.root !== confirmed.root || current.device !== confirmed.device || current.inode !== confirmed.inode) throw new Error('The selected folder changed. Choose its folder again.');
}

export function initialBookRoot(qaRoot: string | undefined, remembered: string | null, fallback: string): string {
  return qaRoot ? resolve(qaRoot) : remembered ?? fallback;
}
