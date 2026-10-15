import { randomUUID } from 'node:crypto';
import { readdir, lstat, realpath } from 'node:fs/promises';
import { basename, extname, join } from 'node:path';
import type { WatchedFolder } from '../contracts';
import { BackendSupervisor } from './backend';
import { FILE_EXTENSIONS, MAX_FILE_BYTES } from './files';
import { Preferences, type FolderLocator } from './preferences';

interface Grant { id: string; label: string; enabled: boolean }
interface Candidate { signature: string; stableSince: number; processed: boolean }

export class FolderWatcher {
  private timer: NodeJS.Timeout | null = null;
  private scanning = false;
  private generation = 0;
  private work = new Set<Promise<void>>();
  private candidates = new Map<string, Candidate>();
  private offsets = new Map<string, number>();

  constructor(private readonly backend: BackendSupervisor, private readonly preferences: Preferences, private readonly onImported: () => void) {}

  start(): void {
    this.stop();
    this.timer = setInterval(() => { void this.poll().catch(() => {}); }, 5_000);
    void this.poll().catch(() => {});
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.generation++;
    this.candidates.clear();
    this.offsets.clear();
  }

  async drain(): Promise<void> { this.stop(); await Promise.allSettled([...this.work]); }

  private track(operation: Promise<void>): Promise<void> {
    this.work.add(operation);
    void operation.finally(() => this.work.delete(operation)).catch(() => {});
    return operation;
  }

  async list(): Promise<WatchedFolder[]> {
    const { folders } = await this.backend.json<{ folders: Grant[] }>('/api/desktop/folders');
    return Promise.all(this.preferences.current.folders.map(async locator => {
      const grant = folders.find(row => row.id === locator.id);
      return { id: locator.id, label: locator.label, location: locator.path, enabled: Boolean(grant?.enabled), available: await this.available(locator) };
    }));
  }

  add(path: string): Promise<void> { return this.track(this.addFolder(path, this.generation)); }

  private async addFolder(path: string, generation: number): Promise<void> {
    const root = this.backend.bookRoot;
    const canonical = await realpath(path);
    if (this.preferences.current.folders.some(folder => folder.path === canonical)) return;
    const stat = await lstat(canonical);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error('Choose a regular folder.');
    if (this.preferences.current.folders.length >= 8) throw new Error('Choose at most 8 watched folders.');
    const folder: FolderLocator = { id: `desktop_${randomUUID()}`, label: basename(canonical), path: canonical, device: stat.dev, inode: stat.ino };
    if (generation !== this.generation) throw new Error('The book changed. Choose the folder again.');
    await this.backend.json('/api/desktop/folders', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: folder.id, label: folder.label }) });
    await this.preferences.saveForBook(root, { folders: [...this.preferences.current.folders, folder] });
  }

  revoke(id: string): Promise<void> { return this.track(this.revokeFolder(id)); }

  private async revokeFolder(id: string): Promise<void> {
    const root = this.backend.bookRoot;
    if (!this.preferences.current.folders.some(folder => folder.id === id)) throw new Error('Unknown watched folder.');
    await this.backend.request(`/api/desktop/folders/${encodeURIComponent(id)}`, { method: 'DELETE' });
    await this.preferences.saveForBook(root, { folders: this.preferences.current.folders.filter(folder => folder.id !== id) });
  }

  private async available(folder: FolderLocator): Promise<boolean> {
    try {
      const stat = await lstat(folder.path);
      return stat.isDirectory() && !stat.isSymbolicLink() && stat.dev === folder.device && stat.ino === folder.inode && await realpath(folder.path) === folder.path;
    } catch { return false; }
  }

  poll(): Promise<void> {
    if (this.scanning || !this.backend.origin) return Promise.resolve();
    this.scanning = true;
    return this.track(this.pollBook(this.generation).finally(() => { this.scanning = false; }));
  }

  private async pollBook(generation: number): Promise<void> {
    const { folders } = await this.backend.json<{ folders: Grant[] }>('/api/desktop/folders');
    if (generation !== this.generation) return;
    for (const locator of this.preferences.current.folders) {
      if (generation !== this.generation) return;
      if (!folders.some(grant => grant.id === locator.id && grant.enabled) || !await this.available(locator)) continue;
      await this.scanFolder(locator, generation);
    }
  }

  private async scanFolder(folder: FolderLocator, generation: number): Promise<void> {
    const entries = await readdir(folder.path, { withFileTypes: true });
    const offset = (this.offsets.get(folder.id) ?? 0) % Math.max(1, entries.length);
    const batch = entries.slice(offset, offset + 500);
    this.offsets.set(folder.id, offset + batch.length >= entries.length ? 0 : offset + batch.length);
    for (const entry of batch) {
      if (generation !== this.generation) return;
      if (!entry.isFile() || !FILE_EXTENSIONS.includes(extname(entry.name).slice(1).toLowerCase())) continue;
      const path = join(folder.path, entry.name);
      try {
        const stat = await lstat(path);
        if (!stat.isFile() || stat.isSymbolicLink() || stat.size === 0 || stat.size > MAX_FILE_BYTES) continue;
        const key = `${folder.id}/${entry.name}`;
        const signature = `${stat.dev}:${stat.ino}:${stat.size}:${stat.mtimeMs}`;
        const candidate = this.candidates.get(key);
        if (!candidate || candidate.signature !== signature) {
          this.candidates.set(key, { signature, stableSince: Date.now(), processed: false });
          continue;
        }
        if (candidate.processed || Date.now() - candidate.stableSince < 2_000) continue;
        if (!await this.available(folder)) continue;
        if (generation !== this.generation) return;
        const file = await this.backend.readWatchedFile(folder, entry.name, { device: stat.dev, inode: stat.ino, size: stat.size, modifiedAt: stat.mtimeMs });
        if (!await this.available(folder)) continue;
        const after = await lstat(path);
        if (`${after.dev}:${after.ino}:${after.size}:${after.mtimeMs}` !== signature) continue;
        if (generation !== this.generation) return;
        const body = new FormData();
        body.append('grant_id', folder.id);
        body.append('file', new Blob([new Uint8Array(file.bytes)], { type: file.type }), file.name);
        const result = await this.backend.json<{ duplicate: boolean; job: { status: string } }>('/api/desktop/imports', { method: 'POST', body });
        candidate.processed = true;
        if (generation === this.generation && !result.duplicate && result.job.status === 'ready') this.onImported();
      } catch { /* Unavailable files wait for the next scan; job errors remain in the review queue. */ }
    }
    if (this.candidates.size > 4_000) this.candidates.clear();
  }
}
