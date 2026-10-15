import { dialog, type BrowserWindow } from 'electron';
import type { BackendSupervisor } from './backend';
import { verifyBookFolder, verifyRememberedBookRoot } from './book-folder';
import { chooseBookFolder } from './book-picker';
import { validateServer, type ConnectionTarget } from './connections';
import type { Preferences } from './preferences';
import type { DesktopWindow } from './window';

interface ConnectionContext {
  backend: BackendSupervisor;
  preferences: Preferences;
  window(): DesktopWindow;
  dialogParent(): BrowserWindow;
  replaceWindow(origin: string | null, hosted?: boolean): DesktopWindow;
  setStarting(value: boolean): void;
  startWorkers(): void;
  suggestedRoot(): string;
  missingRoot?(): string | null;
  switch(target: ConnectionTarget): Promise<void>;
  // The page to show instead of a Private Cloud that can't be opened yet (null: go ahead and load it).
  hostedGate(origin: string, afterFailure: boolean): Promise<string | null>;
  qaRoot?: string;
}

export class ConnectionLoader {
  constructor(private readonly context: ConnectionContext) {}

  async open(target: ConnectionTarget): Promise<void> {
    const { backend, preferences } = this.context;
    const main = this.context.replaceWindow(target.serverUrl, Boolean(target.mode === 'server' && target.hosted));
    if (target.mode === 'server' && target.hosted) {
      await main.showServerRecovery(true);
      await this.openHosted(main, target.serverUrl, target.hosted.entryUrl);
    } else if (target.mode === 'server') {
      await main.showServerRecovery(true);
      await validateServer(target.serverUrl, new AbortController().signal);
      await main.loadServer();
    } else if (target.welcome) {
      const missing = this.context.missingRoot?.() ?? null;
      await main.showWelcome(this.context.suggestedRoot(), missing ? 'missing' : null, missing ?? '');
    }
    else {
      const root = target.bookFolder!;
      if (target.folder) await verifyBookFolder(target.folder); else await verifyRememberedBookRoot(root);
      await preferences.activateBook(root);
      backend.setRoot(root);
      await main.showRecovery(true);
      await backend.start();
      await main.loadApp('folder');
      if (preferences.current.selectedBookFolder === root) this.context.startWorkers();
    }
    this.context.setStarting(false);
  }

  // Asks the gateway what state the Private Cloud is in before opening it, and again if opening it fails,
  // so a paused, updating or outdated-app Private Cloud shows its fix instead of a hang or a generic error.
  private async openHosted(main: DesktopWindow, origin: string, entry: string | null): Promise<void> {
    const blocked = await this.context.hostedGate(origin, false);
    if (blocked) { await main.showHostedState(blocked); return; }
    try { await main.loadHosted(entry); }
    catch (error) {
      const page = await this.context.hostedGate(origin, true);
      if (!page) throw error;
      await main.showHostedState(page);
    }
  }

  async selectLocal(alternate = false): Promise<boolean> {
    const remembered = this.context.preferences.current.selectedBookFolder;
    const fromServer = Boolean(this.context.window().serverOrigin);
    const pickFolder = async () => {
      const selected = await dialog.showOpenDialog(this.context.dialogParent(), { title: 'Choose a folder for your book', properties: ['openDirectory', 'createDirectory'] });
      return selected.canceled ? null : selected.filePaths[0] ?? null;
    };
    const result = await chooseBookFolder({
      choose: async () => {
        // Already on this computer: reopening the same book is pointless, so go straight to the
        // picker. From a server: offer the last book, or another folder (an empty one = new book).
        if (!remembered || alternate || !fromServer) return pickFolder();
        const choice = await dialog.showMessageBox(this.context.dialogParent(), { type: 'question', title: 'Use on this computer', message: 'Which book?', detail: `Your last book on this computer:\n${remembered}\n\nOr choose another folder.`, buttons: ['Open last book', 'Choose another folder…', 'Cancel'], defaultId: 0, cancelId: 2 });
        if (choice.response === 0) return remembered;
        return choice.response === 1 ? pickFolder() : null;
      },
      confirm: async (folder, nested) => {
        const inside = nested ? 'The folder you chose has other files, so your book lives in its own "Open Net Worth" folder inside it.\n\n' : '';
        const away = fromServer ? ' Your server book stays on its server.' : '';
        const choice = await dialog.showMessageBox(this.context.dialogParent(), { type: 'question', title: 'Use on this computer', message: folder.existing ? 'Open this book?' : 'Start a new book here?', detail: `${folder.root}\n\n${inside}${folder.existing || nested ? '' : 'A new book will be created in this empty folder.\n\n'}The app will reload using this folder.${away} Anything you haven’t saved will be closed.`, buttons: [folder.existing ? 'Open book' : 'Create book', 'Cancel'], defaultId: 0, cancelId: 1 });
        return choice.response === 0;
      },
    }, async folder => { await this.context.switch({ mode: 'local', serverUrl: null, bookFolder: folder.root, folder }); return true; }, this.context.qaRoot);
    return result.opened;
  }

  async retry(): Promise<void> {
    const main = this.context.window();
    if (!main.serverOrigin) return;
    this.context.setStarting(true);
    await main.showServerRecovery(true);
    if (main.hosted) {
      try { await this.openHosted(main, main.serverOrigin, null); } catch { await main.showServerRecovery().catch(() => {}); } finally { this.context.setStarting(false); }
      return;
    }
    try { await validateServer(main.serverOrigin, new AbortController().signal); await main.loadServer(); }
    catch { await main.showServerRecovery(); }
    finally { this.context.setStarting(false); }
  }
}
