import { BrowserWindow, ipcMain, nativeTheme, session, type IpcMainInvokeEvent } from 'electron';
import { join } from 'node:path';
import type { DesktopWindow } from './window';
import { Preferences, settingsPatch } from './preferences';
import { appDocument } from './security';
import { activityPage } from './activity-page';

interface ActivityActions {
  parent(): DesktopWindow;
  origin(): string | null;
  available(): boolean;
  save(patch: { background?: boolean; launchAtLogin?: boolean }): Promise<void>;
}

export class ActivityDialog {
  private window: BrowserWindow | null = null;
  private url = '';
  private checking = false;
  private recheck = false;
  private applying = false;
  private closing = false;
  constructor(private readonly preferences: Preferences, private readonly actions: ActivityActions) {
    ipcMain.handle('desktop-activity:save', (event, value: unknown) => { this.authorize(event); return this.save(value); });
    ipcMain.handle('desktop-activity:dismiss', event => { this.authorize(event); return this.dismiss(); });
  }

  async offer(): Promise<void> {
    // A navigation during a check (e.g. "/" → "/lucius") would otherwise be lost: the running check
    // sees the URL change and gives up, and this call was skipped. Check again once it finishes.
    if (this.checking) { this.recheck = true; return; }
    if (this.window || this.preferences.current.activitySetupComplete || !this.actions.available()) return;
    const parent = this.actions.parent();
    const contents = parent.window.webContents;
    const url = contents.getURL();
    if (!this.actions.origin() || !appDocument(this.actions.origin()!, url) || new URL(url).pathname.startsWith('/setup')) return;
    this.checking = true;
    try {
      const complete = await contents.executeJavaScript(`(async()=>{try{const response=await fetch('/api/setup-status');return response.ok&&(await response.json()).is_fresh===false}catch{return false}})()`);
      if (complete && this.actions.available() && this.actions.parent() === parent && !contents.isDestroyed() && contents.getURL() === url && !this.preferences.current.activitySetupComplete) await this.show(true);
    } catch { /* A failed check does not interrupt the book or login. */ }
    finally {
      this.checking = false;
      if (this.recheck) { this.recheck = false; void this.offer(); }
    }
  }

  async show(automatic = false): Promise<void> {
    if (this.window && !this.window.isDestroyed()) { this.window.focus(); return; }
    const isolated = session.fromPartition('onw-activity-settings');
    isolated.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    isolated.setPermissionCheckHandler(() => false);
    const owned = new BrowserWindow({ width: 720, height: 610, minWidth: 560, minHeight: 560, show: false, parent: this.actions.parent().window, modal: true, title: 'Background and startup — Open Net Worth', backgroundColor: nativeTheme.shouldUseDarkColors ? '#0e1014' : '#ffffff', webPreferences: { preload: join(__dirname, '..', 'activity-preload.js'), session: isolated, nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, devTools: false } });
    this.window = owned;
    owned.webContents.on('will-navigate', event => event.preventDefault());
    owned.webContents.on('will-attach-webview', event => event.preventDefault());
    owned.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    owned.on('close', event => { event.preventDefault(); if (!this.applying) void this.dismiss(); });
    owned.once('closed', () => { if (this.window === owned) this.window = null; });
    this.url = `data:text/html;charset=utf-8,${encodeURIComponent(activityPage(this.preferences.current, Boolean(this.actions.parent().serverOrigin), automatic, nativeTheme.shouldUseDarkColors ? 'dark' : 'light'))}`;
    await owned.loadURL(this.url);
    owned.show();
    await new Promise<void>(resolve => owned.once('closed', resolve));
  }

  destroy(): void { this.window?.destroy(); }
  get isOpen(): boolean { return Boolean(this.window && !this.window.isDestroyed()); }
  private authorize(event: IpcMainInvokeEvent): void {
    if (!this.window || event.sender !== this.window.webContents || event.senderFrame !== this.window.webContents.mainFrame || event.senderFrame.url !== this.url) throw new Error('Untrusted desktop settings caller.');
  }

  private async dismiss(): Promise<void> {
    if (this.applying || this.closing) return;
    this.closing = true;
    try { await this.preferences.save({ activitySetupComplete: true }); this.window?.destroy(); }
    catch { await this.window?.webContents.executeJavaScript("document.getElementById('error').textContent='Could not save these choices. Try again.'").catch(() => {}); }
    finally { this.closing = false; }
  }

  private async save(value: unknown): Promise<void> {
    if (this.applying || this.closing) throw new Error('Wait for the current save to finish.');
    const patch = settingsPatch(value);
    if (typeof patch.background !== 'boolean' || typeof patch.launchAtLogin !== 'boolean' || Object.keys(patch).length !== 2) throw new Error('Choose background and startup settings.');
    this.applying = true;
    try { await this.actions.save(patch); await this.preferences.save({ activitySetupComplete: true }); this.window?.destroy(); }
    finally { this.applying = false; }
  }
}
