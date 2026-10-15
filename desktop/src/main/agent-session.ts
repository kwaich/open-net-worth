import { BrowserWindow, session } from 'electron';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { HostedPageClient, trackPageClient } from './hosted-page-client';
import { claimPartition } from './partition-policy';

// The partition a Private Cloud window keeps its sign-in in; a hidden window using the same one is signed in too.
export function hostedPartition(origin: string): string { return `persist:onw-hosted-${createHash('sha256').update(origin).digest('hex')}`; }

// A hidden, signed-in page of the Private Cloud. A move runs through it while the person's window stays on their
// local book: the page carries the gateway's own-origin requirement, and nothing here is shown or interactive.
export class AgentSession {
  private constructor(private readonly window: BrowserWindow, readonly client: HostedPageClient, private readonly untrack: () => void) {}

  static async open(origin: string, entryUrl: string): Promise<AgentSession> {
    const partition = session.fromPartition(hostedPartition(origin));
    claimPartition(partition, origin);
    const window = new BrowserWindow({ show: false, width: 800, height: 600, webPreferences: { preload: join(__dirname, '..', 'client-preload.js'), session: partition, nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, devTools: false } });
    const contents = window.webContents;
    contents.on('will-attach-webview', event => event.preventDefault());
    contents.setWindowOpenHandler(() => ({ action: 'deny' }));
    const stay = (event: Electron.Event, url: string) => { try { if (new URL(url).origin !== origin) event.preventDefault(); } catch { event.preventDefault(); } };
    contents.on('will-navigate', stay);
    contents.on('will-redirect', stay);
    try {
      await window.loadURL(entryUrl);
      if (new URL(contents.getURL()).origin !== origin) throw new Error();
    } catch {
      window.destroy();
      throw new Error('Signing in to your Private Cloud did not finish. Start again from File → Move my book to Private Cloud.');
    }
    const client = new HostedPageClient(contents, origin);
    return new AgentSession(window, client, trackPageClient(client));
  }

  close(): void {
    this.client.abandon();
    this.untrack();
    if (!this.window.isDestroyed()) this.window.destroy();
  }
}
