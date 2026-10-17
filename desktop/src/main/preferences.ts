import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';
import { desktopEvent } from './desktop-events';
import type { DesktopSettings } from '../contracts';

// A book that now lives in a Private Cloud: where, when, and what this copy paused (to switch back on if it is used again).
export interface MovedMarker { to: string; at: string; connectors: string[]; automations: string[] }
export interface FolderLocator { id: string; label: string; path: string; device: number; inode: number }
export interface DevicePreferences {
  background: boolean;
  launchAtLogin: boolean;
  notifications: boolean;
  keepAwake: boolean;
  activitySetupComplete: boolean;
  folders: FolderLocator[];
  deliveredNotifications: string[];
  moved: MovedMarker | null;
  selectedBookFolder: string | null;
  selectedServerUrl: string | null;
  skippedUpdateVersion: string | null;
  lastRunVersion: string | null;
  selectedHostedUrl: string | null;
}
interface BookPreferences { folders: FolderLocator[]; deliveredNotifications: string[]; moved: MovedMarker | null }
interface StoredPreferences extends Omit<DevicePreferences, keyof BookPreferences> { books: Record<string, Omit<BookPreferences, 'moved'> & { moved?: MovedMarker | null }> }

class InvalidPreferences extends Error { constructor() { super('Desktop preferences are invalid.'); } }

export class Preferences {
  private value: StoredPreferences = { background: false, launchAtLogin: false, notifications: true, keepAwake: false, activitySetupComplete: false, selectedBookFolder: null, selectedServerUrl: null, skippedUpdateVersion: null, lastRunVersion: null, selectedHostedUrl: null, books: {} };
  private activeBook = '';
  private writes = Promise.resolve();
  constructor(private readonly path: string) {}
  get current(): Readonly<DevicePreferences> { const { books: _books, ...global } = this.value; return { ...global, ...this.scope(this.activeBook) }; }

  async load(defaultBook = ''): Promise<void> {
    this.activeBook = defaultBook;
    let upgrade = false;
    try {
      let saved = JSON.parse(await readFile(this.path, 'utf8')) as StoredPreferences;
      if (!('books' in saved)) {
        const old = saved as unknown as DevicePreferences;
        if (!Array.isArray(old.folders) || !Array.isArray(old.deliveredNotifications) || typeof old.background !== 'boolean' || typeof old.launchAtLogin !== 'boolean' || typeof old.notifications !== 'boolean') throw new InvalidPreferences();
        saved = { background: old.background, launchAtLogin: old.launchAtLogin, notifications: old.notifications, keepAwake: false, activitySetupComplete: false, selectedBookFolder: null, selectedServerUrl: null, skippedUpdateVersion: null, lastRunVersion: null, selectedHostedUrl: null, books: { [defaultBook]: { folders: old.folders, deliveredNotifications: old.deliveredNotifications } } };
        upgrade = true;
      }
      if (!Object.hasOwn(saved, 'selectedServerUrl')) { saved = { ...saved, selectedServerUrl: null }; upgrade = true; }
      saved = { ...saved, keepAwake: saved.keepAwake ?? false, activitySetupComplete: saved.activitySetupComplete ?? false, skippedUpdateVersion: saved.skippedUpdateVersion ?? null, lastRunVersion: saved.lastRunVersion ?? null, selectedHostedUrl: saved.selectedHostedUrl ?? null };
      if (saved.skippedUpdateVersion !== null && typeof saved.skippedUpdateVersion !== 'string') throw new InvalidPreferences();
      if (saved.selectedHostedUrl !== null && typeof saved.selectedHostedUrl !== 'string') throw new InvalidPreferences();
      if (saved.lastRunVersion !== null && typeof saved.lastRunVersion !== 'string') throw new InvalidPreferences();
      if (typeof saved.activitySetupComplete !== 'boolean' || typeof saved.keepAwake !== 'boolean') throw new InvalidPreferences();
      if (saved.selectedServerUrl !== null && typeof saved.selectedServerUrl !== 'string') throw new InvalidPreferences();
      if (typeof saved.background !== 'boolean' || typeof saved.launchAtLogin !== 'boolean' || typeof saved.notifications !== 'boolean' || !saved.books || typeof saved.books !== 'object' || Array.isArray(saved.books) || saved.selectedBookFolder !== null && typeof saved.selectedBookFolder !== 'string' || Object.values(saved.books).some(book => !Array.isArray(book.folders) || !Array.isArray(book.deliveredNotifications))) throw new InvalidPreferences();
      this.value = saved;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') await this.setAside(error);
      return;
    }
    if (upgrade) await this.write(this.value);
  }

  // A damaged settings file must never stop the app opening: keep it for inspection and start from defaults.
  private async setAside(error: unknown): Promise<void> {
    const aside = `${this.path}.corrupt-${Date.now()}`;
    await rename(this.path, aside).catch(() => {});
    desktopEvent({ event: 'desktop.preferences_reset', error: error instanceof Error ? error.message : 'Unreadable', keptAs: aside });
  }

  save(changes: Partial<DevicePreferences>): Promise<void> {
    return this.saveForBook(this.activeBook, changes);
  }

  saveForBook(book: string, changes: Partial<DevicePreferences>): Promise<void> {
    const nextWrite = this.writes.then(() => this.persist(book, changes));
    this.writes = nextWrite.catch(() => {});
    return nextWrite;
  }

  async activateBook(root: string): Promise<void> { await this.writes; this.activeBook = root; }
  rememberBook(root: string): Promise<void> { return this.save({ selectedBookFolder: root }); }
  private scope(root: string): BookPreferences { const book = this.value.books[root]; return { folders: book?.folders ?? [], deliveredNotifications: book?.deliveredNotifications ?? [], moved: book?.moved ?? null }; }

  private async persist(book: string, changes: Partial<DevicePreferences>): Promise<void> {
    const { folders, deliveredNotifications, moved, ...global } = changes;
    const scope = { ...this.scope(book), ...(folders === undefined ? {} : { folders }), ...(deliveredNotifications === undefined ? {} : { deliveredNotifications }), ...(moved === undefined ? {} : { moved }) };
    const next = { ...this.value, ...global, books: { ...this.value.books, [book]: scope } };
    await this.write(next);
    this.value = next;
  }

  private async write(next: StoredPreferences): Promise<void> {
    await mkdir(dirname(this.path), { recursive: true, mode: 0o700 });
    await writeFile(`${this.path}.tmp`, `${JSON.stringify(next)}\n`, { mode: 0o600 });
    await rename(`${this.path}.tmp`, this.path);
  }
}

export type SettingsPatch = Partial<Pick<DesktopSettings, 'background' | 'launchAtLogin' | 'notifications' | 'keepAwake'>>;

export function settingsPatch(value: unknown): SettingsPatch {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid desktop settings.');
  const patch: Record<string, boolean> = {};
  for (const [name, enabled] of Object.entries(value)) {
    if (!['background', 'launchAtLogin', 'notifications', 'keepAwake'].includes(name) || typeof enabled !== 'boolean') throw new Error('Invalid desktop settings.');
    patch[name] = enabled;
  }
  return patch;
}
