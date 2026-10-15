import { BrowserWindow, ipcMain, nativeTheme, screen, session, type IpcMainInvokeEvent } from 'electron';
import { join } from 'node:path';
import { assertPrivateCloudEnabled } from './feature-flags';

export interface FlowScreen {
  html(theme: 'dark' | 'light'): string;
  title: string;
  // Called for each button the page offers; a rejection is shown under the buttons.
  onAction(name: string, argument: string): Promise<void>;
  // The window was closed with the title-bar button.
  onClose(): void;
}

// One modal screen at a time over the main window: the "Continue in your browser" wait and the move progress.
export class FlowDialog {
  private window: BrowserWindow | null = null;
  private screen: FlowScreen | null = null;
  private url = '';
  private closing = false;

  constructor(private readonly parent: () => BrowserWindow) {
    ipcMain.handle('desktop-flow:action', (event, name: unknown, argument: unknown) => {
      assertPrivateCloudEnabled();
      this.authorize(event);
      if (typeof name !== 'string' || name.length > 32 || typeof argument !== 'undefined' && (typeof argument !== 'string' || argument.length > 256)) throw new Error('Invalid action.');
      return this.screen!.onAction(name, argument ?? '');
    });
  }

  get isOpen(): boolean { return Boolean(this.window && !this.window.isDestroyed()); }

  async show(next: FlowScreen): Promise<void> {
    this.screen = next;
    this.url = `data:text/html;charset=utf-8,${encodeURIComponent(next.html(nativeTheme.shouldUseDarkColors ? 'dark' : 'light'))}`;
    if (this.window && !this.window.isDestroyed()) { await this.window.loadURL(this.url); this.window.focus(); return; }
    const isolated = session.fromPartition('onw-flow-dialog');
    isolated.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    isolated.setPermissionCheckHandler(() => false);
    const owned = this.window = new BrowserWindow({ width: 700, height: 420, minWidth: 520, show: false, parent: this.parent(), modal: true, title: `${next.title} — Open Net Worth`, backgroundColor: nativeTheme.shouldUseDarkColors ? '#0e1014' : '#ffffff', webPreferences: { preload: join(__dirname, '..', 'client-preload.js'), session: isolated, nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, devTools: false, enablePreferredSizeMode: true } });
    owned.webContents.on('preferred-size-changed', (_event, size) => {
      const max = screen.getDisplayMatching(owned.getBounds()).workArea.height - 80;
      owned.setContentSize(owned.getContentSize()[0], Math.min(Math.max(size.height, 260), max));
      if (!owned.isVisible()) owned.show();
    });
    owned.once('ready-to-show', () => setTimeout(() => { if (!owned.isDestroyed() && !owned.isVisible()) owned.show(); }, 300));
    owned.webContents.on('will-navigate', event => event.preventDefault());
    owned.webContents.on('will-attach-webview', event => event.preventDefault());
    owned.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    owned.once('closed', () => { const wasProgrammatic = this.closing; this.closing = false; if (this.window === owned) this.window = null; if (!wasProgrammatic) this.screen?.onClose(); });
    await owned.loadURL(this.url);
  }

  // Runs a call defined by the page (for example `setProgress`) with JSON-safe arguments.
  async call(name: string, ...args: unknown[]): Promise<void> {
    if (!this.window || this.window.isDestroyed()) return;
    await this.window.webContents.executeJavaScript(`window.${name}(${args.map(value => JSON.stringify(value)).join(',')})`).catch(() => {});
  }

  close(): void {
    if (!this.window || this.window.isDestroyed()) return;
    this.closing = true;
    this.window.destroy();
  }

  private authorize(event: IpcMainInvokeEvent): void {
    if (!this.window || !this.screen || event.sender !== this.window.webContents || event.senderFrame !== this.window.webContents.mainFrame || event.senderFrame.url !== this.url) throw new Error('Untrusted caller.');
  }
}
