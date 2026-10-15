import { constants } from 'node:fs';
import { lstat, open, realpath, rename, rm } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { basename, dirname, join, relative, isAbsolute, sep } from 'node:path';

export async function atomicExport(destination: string, content: AsyncIterable<Uint8Array>, bookRoot?: string): Promise<void> {
  if (bookRoot) {
    const canonicalDestination = join(await realpath(dirname(destination)), basename(destination));
    const offset = relative(await realpath(bookRoot), canonicalDestination);
    const outside = offset === '..' || offset.startsWith(`..${sep}`) || isAbsolute(offset);
    if (!outside) throw new Error('Choose an export destination outside your active book folder.');
  }
  try {
    const stat = await lstat(destination);
    if (!stat.isFile() || stat.isSymbolicLink()) throw new Error('Choose a regular export destination.');
  } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
  const temporary = join(dirname(destination), `.${basename(destination)}.${randomUUID()}.tmp`);
  const handle = await open(temporary, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW ?? 0), 0o600);
  let total = 0;
  try {
    for await (const chunk of content) {
      total += chunk.byteLength;
      if (total > 1024 * 1024 * 1024) throw new Error('The export is too large to save here.');
      await handle.writeFile(chunk);
    }
    await handle.sync();
    await handle.close();
    await rename(temporary, destination);
  } finally {
    await handle.close();
    await rm(temporary, { force: true });
  }
}
