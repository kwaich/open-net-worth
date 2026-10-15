import { mkdir, rmdir } from 'node:fs/promises';
import { join } from 'node:path';
import type { BookFolder } from './book-folder';
import { FolderNotEmptyError, inspectBookFolder } from './book-folder';

// `nested`: the book lives in an "Open Net Worth" subfolder of the folder the person picked.
export interface PickerDialogs { choose(): Promise<string | null>; confirm(folder: BookFolder, nested: boolean): Promise<boolean> }

export const NESTED_BOOK_FOLDER = 'Open Net Worth';

// A folder holding other files gets the book in a subfolder, so the book never mixes with them.
// An existing "Open Net Worth" that is a book or empty is reused; one holding other files is
// skipped for "Open Net Worth 2", 3, …
async function bookFolderAt(selected: string): Promise<{ folder: BookFolder; created: boolean; nested: boolean }> {
  try { return { folder: await inspectBookFolder(selected), created: false, nested: false }; }
  catch (error) { if (!(error instanceof FolderNotEmptyError)) throw error; }
  for (let n = 1; n <= 100; n++) {
    const nested = join(selected, n === 1 ? NESTED_BOOK_FOLDER : `${NESTED_BOOK_FOLDER} ${n}`);
    const created = await mkdir(nested, { mode: 0o700 }).then(() => true, (e: NodeJS.ErrnoException) => { if (e.code === 'EEXIST') return false; throw e; });
    try { return { folder: await inspectBookFolder(nested), created, nested: true }; }
    catch (error) { if (!(error instanceof FolderNotEmptyError)) throw error; }
  }
  throw new Error('Choose an empty folder for your book.');
}

export async function chooseBookFolder(dialogs: PickerDialogs, open: (folder: BookFolder) => Promise<boolean>, qaRoot: string | undefined): Promise<{ cancelled: boolean; opened: boolean }> {
  if (qaRoot) throw new Error('Book switching is disabled while an explicit QA book folder is enforced.');
  const selected = await dialogs.choose();
  if (!selected) return { cancelled: true, opened: false };
  const { folder, created, nested } = await bookFolderAt(selected);
  if (!await dialogs.confirm(folder, nested)) {
    if (created) await rmdir(folder.root).catch(() => {});
    return { cancelled: true, opened: false };
  }
  return { cancelled: false, opened: await open(folder) };
}
