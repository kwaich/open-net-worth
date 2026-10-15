import type { BookFolder } from './book-folder';
import { verifyBookFolder, verifyRememberedBookRoot } from './book-folder';
import { BookInUseError } from './runtime';

interface SwitchBackend { readonly bookRoot: string; setRoot(root: string): void; start(): Promise<void>; stop(): Promise<void> }
interface SwitchPreferences { activateBook(root: string): Promise<void>; rememberBook(root: string): Promise<void> }
export type BookEntry = 'folder' | 'restore';
export type BookStartupStage = 'opening' | 'creating' | 'restore' | 'choosing';
interface SwitchView { prepareBookSwitch(stage?: BookStartupStage): Promise<boolean>; loadApp(entry?: BookEntry): Promise<void>; showRecovery(starting: boolean): Promise<void> }
interface SwitchWorkers { drain(): Promise<void>; start(): void }

export class BookSwitcher {
  private queue = Promise.resolve();
  initialRetryFolder: BookFolder | null = null;
  constructor(private readonly backend: SwitchBackend, private readonly preferences: SwitchPreferences, private readonly view: SwitchView, private readonly workers: SwitchWorkers) {}

  exclusive<T>(action: () => Promise<T>): Promise<T> {
    const operation = this.queue.then(action);
    this.queue = operation.then(() => {}, () => {});
    return operation;
  }

  open(chosen: BookFolder): Promise<boolean> {
    return this.exclusive(() => this.switchBook(chosen));
  }

  openInitial(chosen: BookFolder, entry: BookEntry = 'folder'): Promise<boolean> {
    return this.exclusive(async () => {
      this.initialRetryFolder = null;
      await verifyBookFolder(chosen);
      if (entry === 'restore' && chosen.existing) throw new Error('Choose an empty folder to restore a backup.');
      await this.view.prepareBookSwitch(entry === 'restore' ? 'restore' : chosen.existing ? 'opening' : 'creating');
      await this.workers.drain();
      await this.backend.stop();
      try {
        await verifyBookFolder(chosen);
        this.initialRetryFolder = chosen;
        await this.preferences.activateBook(chosen.root);
        this.backend.setRoot(chosen.root);
        await this.backend.start();
        await this.view.loadApp(entry);
        await this.preferences.rememberBook(chosen.root);
        this.workers.start();
        return true;
      } catch (error) {
        await this.backend.stop();
        await this.view.showRecovery(false);
        throw error;
      }
    });
  }

  private async switchBook(chosen: BookFolder): Promise<boolean> {
    const previous = this.backend.bookRoot;
    if (chosen.root === previous) return false;
    await verifyBookFolder(chosen);
    await this.view.prepareBookSwitch(chosen.existing ? 'opening' : 'creating');
    await this.workers.drain();
    await this.backend.stop();
    try {
      await verifyBookFolder(chosen);
      await this.preferences.activateBook(chosen.root);
      this.backend.setRoot(chosen.root);
      await this.backend.start();
      await this.view.loadApp('folder');
      await this.preferences.rememberBook(chosen.root);
      this.workers.start();
      return true;
    } catch (error) {
      const reason = error instanceof BookInUseError ? `${error.message} ` : '';
      await this.view.prepareBookSwitch('opening');
      await this.workers.drain();
      await this.backend.stop();
      await this.preferences.activateBook(previous);
      this.backend.setRoot(previous);
      try {
        await verifyRememberedBookRoot(previous);
        await this.backend.start();
        await this.view.loadApp('folder');
        this.workers.start();
      } catch { await this.backend.stop(); await this.view.showRecovery(false); }
      throw new Error(`${reason}The selected book could not open. Your previous book has been kept.`);
    }
  }
}
