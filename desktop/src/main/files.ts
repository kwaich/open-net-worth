import { constants } from 'node:fs';
import { open, lstat } from 'node:fs/promises';
import { basename, extname } from 'node:path';
import type { NativeFile } from '../contracts';

export const MAX_FILE_BYTES = 25 * 1024 * 1024;
export const FILE_EXTENSIONS = ['csv', 'pdf', 'xlsx', 'xls', 'txt', 'ofx', 'qfx', 'qbo', 'qif', 'mt940', 'sta', 'camt', 'xml', 'png', 'jpg', 'jpeg', 'webp', 'heic', 'heif'];
const MIME: Record<string, string> = { csv: 'text/csv', pdf: 'application/pdf', xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', xls: 'application/vnd.ms-excel', txt: 'text/plain', ofx: 'application/x-ofx', qfx: 'application/vnd.intu.qfx', qbo: 'application/vnd.intu.qbo', qif: 'application/x-qif', mt940: 'application/x-mt940', sta: 'application/x-mt940', camt: 'application/xml', xml: 'application/xml', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp', heic: 'image/heic', heif: 'image/heif' };

export function supportedFile(path: string): boolean { return FILE_EXTENSIONS.includes(extname(path).slice(1).toLowerCase()); }
export function fileMime(path: string): string { return MIME[extname(path).slice(1).toLowerCase()] ?? 'application/octet-stream'; }
export interface FileIdentity { device: number; inode: number; size: number; modifiedAt: number }

function sameIdentity(stat: { dev: number; ino: number; size: number; mtimeMs: number }, expected: FileIdentity): boolean {
  return stat.dev === expected.device && stat.ino === expected.inode && stat.size === expected.size && stat.mtimeMs === expected.modifiedAt;
}

export async function readSelectedFile(path: string, expected?: FileIdentity): Promise<NativeFile> {
  const before = await lstat(path);
  if (!before.isFile() || before.isSymbolicLink() || before.size > MAX_FILE_BYTES) throw new Error('Choose a regular file smaller than 25 MB.');
  const identity = expected ?? { device: before.dev, inode: before.ino, size: before.size, modifiedAt: before.mtimeMs };
  if (!sameIdentity(before, identity)) throw new Error('The file changed after it was selected.');
  const handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const stat = await handle.stat();
    if (!stat.isFile() || !sameIdentity(stat, identity) || stat.size > MAX_FILE_BYTES) throw new Error('The file changed while being opened.');
    const bytes = Buffer.alloc(stat.size + 1);
    let bytesRead = 0;
    while (bytesRead < bytes.length) {
      const chunk = await handle.read(bytes, bytesRead, bytes.length - bytesRead, bytesRead);
      if (!chunk.bytesRead) break;
      bytesRead += chunk.bytesRead;
    }
    if (bytesRead !== stat.size || !sameIdentity(await handle.stat(), identity)) throw new Error('The file changed while being read.');
    return { name: basename(path), type: fileMime(path), bytes: new Uint8Array(bytes.subarray(0, bytesRead)) };
  } finally { await handle.close(); }
}

export async function readSelectedFiles(paths: string[]): Promise<NativeFile[]> {
  if (paths.length > 8) throw new Error('Choose at most 8 files at a time.');
  const files: NativeFile[] = [];
  let total = 0;
  for (const path of paths) {
    const file = await readSelectedFile(path);
    total += file.bytes.byteLength;
    if (total > 100 * 1024 * 1024) throw new Error('Choose less than 100 MB at a time.');
    files.push(file);
  }
  return files;
}
