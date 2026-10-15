import { app, dialog } from 'electron';
import { mkdtemp } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import type { BackendSupervisor } from './backend';
import { AgentSession } from './agent-session';
import { FlowDialog, type FlowScreen } from './flow-dialog';
import { progressPage } from './flow-pages';
import { HostedPageClient, trackPageClient } from './hosted-page-client';
import { movedDetail, whenText } from './hosted-move';
import { inspectBookFolder, verifyBookFolder } from './book-folder';
import { LocalEndpoint, MoveCancelled, type MoveEndpoint } from './move-endpoint';
import { runMove, type MoveOutcome } from './move-flow';
import { packToFile } from './move-pack';
import { switchCopy } from './move-secrets';
import type { Preferences } from './preferences';
import type { ConnectionTarget } from './connections';
import type { DesktopWindow } from './window';

interface MoveDeps {
  main(): DesktopWindow;
  backend(): BackendSupervisor;
  preferences(): Preferences;
  flow(): FlowDialog;
  open(target: ConnectionTarget): Promise<void>;
  // Runs `work` with this computer's book open in a background backend, then closes it.
  withLocalBackend<T>(root: string, work: (local: MoveEndpoint) => Promise<T>): Promise<T>;
}

const EXPORT_WINDOW_MS = 2 * 60 * 60_000;

class MoveProgress implements FlowScreen {
  cancel = false;
  readonly title: string;
  constructor(private readonly heading: string, private readonly flow: FlowDialog) { this.title = heading; }
  html(theme: 'dark' | 'light'): string { return progressPage(theme, this.heading); }
  async onAction(name: string): Promise<void> { if (name === 'cancel') { this.cancel = true; await this.flow.call('setProgress', null, 'Cancelling…'); } }
  onClose(): void { this.cancel = true; }
}

// Moving a book between this computer and a Private Cloud (contract C8), and the "old copy" marker that keeps the
// two from drifting apart unnoticed.
export class MoveActions {
  private noticed = new Set<string>();
  constructor(private readonly deps: MoveDeps) {}

  private async working<T>(heading: string, work: (progress: MoveProgress) => Promise<T>): Promise<T | null> {
    const flow = this.deps.flow();
    const progress = new MoveProgress(heading, flow);
    await flow.show(progress);
    try { return await work(progress); }
    catch (error) {
      flow.close();
      if (error instanceof MoveCancelled || progress.cancel) { await this.say('info', 'The move was cancelled.', 'Nothing was changed.'); return null; }
      throw error;
    } finally { flow.close(); }
  }

  private ui(progress: MoveProgress, replace: () => Promise<boolean>) {
    const flow = this.deps.flow();
    return { progress: (percent: number | null, text: string) => { void flow.call('setProgress', percent, text); }, confirmReplace: replace, cancelled: () => progress.cancel };
  }

  private async say(type: 'info' | 'warning' | 'error', message: string, detail: string, buttons = ['Done']): Promise<number> {
    const main = this.deps.main();
    return (await dialog.showMessageBox(main.window, { type, title: 'Open Net Worth', message, detail, buttons, defaultId: 0, cancelId: buttons.length - 1 })).response;
  }

  private async tempDirectory(): Promise<string> { return mkdtemp(join(app.getPath('temp') || tmpdir(), 'onw-move-')); }

  // Forward: this computer's book goes to the Private Cloud the person just signed in to.
  async moveToCloud(origin: string, entryUrl: string): Promise<void> {
    const backend = this.deps.backend();
    const main = this.deps.main();
    if (main.serverOrigin || !backend.origin) { await this.say('error', 'Your book could not be moved.', 'Open the book on this computer first, then choose Move my book to Private Cloud again.'); return; }
    const held: { agent?: AgentSession } = {};
    try {
      const outcome = await this.working('Moving your book', async progress => {
        void this.deps.flow().call('setProgress', null, 'Signing in to your Private Cloud…');
        const agent = held.agent = await AgentSession.open(origin, entryUrl);
        const local = new LocalEndpoint(backend);
        const result = await runMove({
          from: local, to: agent.client,
          pack: async () => packToFile(await backend.fetchRaw('/api/data/export', { signal: AbortSignal.timeout(EXPORT_WINDOW_MS) }), await this.tempDirectory()),
        }, this.ui(progress, async () => (await this.say('info', 'Replace the book in your Private Cloud?', 'Your Private Cloud already has data. Moving your book replaces all of it, and the two cannot be merged. Your book on this computer is not changed.', ['Replace Private Cloud book', 'Cancel'])) === 0));
        if (result) await this.markMoved(origin, local, result);
        return result;
      });
      if (!outcome) return;
      await this.deps.open({ mode: 'server', serverUrl: origin, hosted: { entryUrl: null } });
      await this.say('info', 'Your book is now in your Private Cloud.', movedDetail(outcome.checklist, 'to-cloud'));
    } catch (error) {
      await this.say('error', 'Your book could not be moved.', `${error instanceof Error ? error.message : 'The move could not finish.'}\n\nYour book on this computer was not changed.`);
    } finally { held.agent?.close(); }
  }

  private async markMoved(origin: string, local: MoveEndpoint, outcome: MoveOutcome): Promise<void> {
    const paused = await switchCopy(local, 'pause', { connectors: outcome.enabled });
    await this.deps.preferences().saveForBook(this.deps.backend().bookRoot, { moved: { to: origin, at: new Date().toISOString(), connectors: paused.connectors, automations: paused.automations } });
  }

  // Reverse: the Private Cloud's book replaces the book on this computer; the cloud copy becomes the old one.
  async moveBack(): Promise<void> {
    const main = this.deps.main();
    const origin = main.serverOrigin;
    const contents = main.contents;
    if (!main.hosted || !origin || !contents) throw new Error('Open your Private Cloud first.');
    const root = await this.localRoot(main);
    if (!root) return;
    const answer = await this.say('warning', 'Move your book back to this computer?', `This replaces the book in this folder with your Private Cloud's book:\n${root}\n\nYour Private Cloud copy is not deleted; its bots and automations are paused.`, ['Move back', 'Cancel']);
    if (answer !== 0) return;
    const agent = new HostedPageClient(contents, origin);
    const untrack = trackPageClient(agent);
    try {
      const outcome = await this.working('Moving your book back', progress => this.deps.withLocalBackend(root, async local => {
        const result = await runMove({
          from: agent, to: local,
          pack: async () => packToFile(await main.hostedFetch('/api/data/export', { signal: AbortSignal.timeout(EXPORT_WINDOW_MS), headers: { 'X-Requested-With': 'XMLHttpRequest' } }), await this.tempDirectory()),
        }, this.ui(progress, async () => true));
        if (result) {
          await switchCopy(agent, 'pause', { connectors: result.enabled });
          await this.deps.preferences().saveForBook(root, { moved: null });
        }
        return result;
      }));
      if (!outcome) return;
      const open = await this.say('info', 'Your book is back on this computer.', movedDetail(outcome.checklist, 'to-computer'), ['Open it now', 'Stay in Private Cloud']);
      if (open === 0) await this.deps.open({ mode: 'local', serverUrl: null, bookFolder: root });
    } catch (error) {
      await this.say('error', 'Your book could not be moved back.', `${error instanceof Error ? error.message : 'The move could not finish.'}\n\nNothing in your Private Cloud was changed.`);
    } finally { agent.abandon(); untrack(); }
  }

  private async localRoot(main: DesktopWindow): Promise<string | null> {
    const remembered = this.deps.preferences().current.selectedBookFolder;
    if (remembered) return remembered;
    const picked = await dialog.showOpenDialog(main.window, { title: 'Choose an empty folder for your book', properties: ['openDirectory', 'createDirectory'] });
    if (picked.canceled || !picked.filePaths[0]) return null;
    const folder = await inspectBookFolder(picked.filePaths[0]);
    await verifyBookFolder(folder);
    return folder.root;
  }

  // Shown once per launch when a book that now lives in a Private Cloud is opened here, and as a note on the page.
  async noticeIfMoved(open: (origin: string) => Promise<void>): Promise<void> {
    const main = this.deps.main();
    const moved = this.deps.preferences().current.moved;
    if (!moved || main.serverOrigin || !this.deps.backend().origin) return;
    main.contents?.send('desktop-moved:banner', { when: whenText(moved.at) });
    const root = this.deps.backend().bookRoot;
    if (this.noticed.has(root)) return;
    this.noticed.add(root);
    const choice = await this.say('info', 'This book now lives in your Private Cloud.', `You moved it on ${whenText(moved.at)}. This copy is paused. Changes you make here will not appear there, and the two will drift apart.`, ['Open Private Cloud', 'Keep this copy open']);
    if (choice === 0) await open(moved.to);
  }
}
