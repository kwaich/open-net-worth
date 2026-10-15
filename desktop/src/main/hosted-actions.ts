import { dialog, shell } from 'electron';
import { deepLinkArgument } from './hosted-auth';
import { hostedConfig } from './hosted-config';
import { HostedSignIn } from './hosted';
import { HostedPageClient, trackPageClient } from './hosted-page-client';
import { redeemShortCode, RedeemError } from './hosted-redeem';
import { FlowDialog, type FlowScreen } from './flow-dialog';
import { waitingPage } from './flow-pages';
import { RELEASES_PAGE } from './updates';
import type { PendingContext, PendingStore } from './hosted-pending-store';
import type { MoveActions } from './move-actions';
import type { ConnectionTarget } from './connections';
import type { BackendSupervisor } from './backend';
import type { Preferences } from './preferences';
import type { DesktopWindow } from './window';

interface HostedActionDeps {
  main(): DesktopWindow;
  backend(): BackendSupervisor;
  preferences(): Preferences;
  open(target: ConnectionTarget): Promise<void>;
  quitting(): boolean;
  flow(): FlowDialog;
  store: PendingStore;
  moves(): MoveActions;
  // The signed-in account changed: menus and the connection dialog should say who it is.
  identityChanged(): void;
}

// The app-level half of Private Cloud sign-in: the browser hand-off with its waiting screen, the signed-in
// account, switching and signing out. Moving a book is `MoveActions`.
export class HostedActions {
  private readonly signInFlow: HostedSignIn;
  private email_: string | null = null;

  constructor(private readonly deps: HostedActionDeps) {
    this.signInFlow = new HostedSignIn({ config: hostedConfig, store: deps.store, openBrowser: url => shell.openExternal(url), complete: (origin, entry, context) => this.complete(origin, entry, context) });
  }

  get email(): string | null { return this.email_; }

  // Picks up a sign-in that was waiting when the app last quit. A link that arrives with no matching
  // sign-in is still answered, so the person hears why nothing happened.
  async start(early: string | null): Promise<void> {
    await this.signInFlow.restore();
    if (early) this.handleLink(early);
    else if (this.signInFlow.waiting) await this.showWaiting(this.signInFlow.movePending);
  }

  async signIn(automatic = false): Promise<void> {
    await this.signInFlow.start(false, automatic);
    await this.showWaiting(false);
  }

  // The window is already open on the agent and needs a session from a forced sign-in (its Terminal): the same
  // browser hand-off, with `fresh`, replaces the window's gateway cookie and brings it back to the page it was on.
  async signInFresh(returnPath: string | null): Promise<void> {
    await this.signInFlow.start(false, false, { returnPath });
    await this.showWaiting(false);
  }

  async moveBook(): Promise<void> {
    const main = this.deps.main();
    if (main.serverOrigin || !this.deps.backend().origin || this.deps.quitting()) throw new Error('Open your book on this computer first.');
    await this.signInFlow.start(true);
    await this.showWaiting(true);
  }

  handleArgv(argv: readonly string[]): void { const link = deepLinkArgument(argv); if (link) this.handleLink(link); }

  handleLink(url: string): void {
    this.signInFlow.handleLink(url).catch(error => this.fail(error instanceof Error ? error.message : 'Signing in to your Private Cloud did not finish.'));
  }

  private showWaiting(moving: boolean): Promise<void> {
    const screen: FlowScreen = {
      title: 'Sign in to Private Cloud',
      html: theme => waitingPage(theme, moving),
      onAction: async (name, argument) => {
        if (name === 'again') await this.signInFlow.reopen();
        else if (name === 'cancel') await this.cancelSignIn();
        else if (name === 'redeem') await this.redeem(argument);
      },
      onClose: () => { void this.signInFlow.cancel(); },
    };
    return this.deps.flow().show(screen);
  }

  private async cancelSignIn(): Promise<void> { await this.signInFlow.cancel(); this.deps.flow().close(); }

  // The fallback to the browser handing the app a link: the code the sign-in page shows.
  private async redeem(code: string): Promise<void> {
    try { await this.signInFlow.complete(await redeemShortCode(hostedConfig(), code)); }
    catch (error) {
      if (error instanceof RedeemError) throw error;
      throw new Error(error instanceof Error && error.message ? error.message : 'That code did not work. Start again from the sign-in page.');
    }
  }

  async openPortal(): Promise<void> { await shell.openExternal(hostedConfig().portalOrigin); }
  async openUpdate(): Promise<void> { await shell.openExternal(RELEASES_PAGE); }

  // Switch = end this session everywhere, then a new hand-off; the portal asks which account to use.
  async switchAccount(): Promise<void> { await this.signOut(); await this.signIn(); }

  async signOut(): Promise<void> {
    const main = this.deps.main();
    if (!main.hosted || !main.serverOrigin) return;
    await this.signInFlow.cancel();
    const contents = main.contents;
    if (contents) {
      const client = new HostedPageClient(contents, main.serverOrigin, 10_000);
      const untrack = trackPageClient(client);
      try { await client.call({ method: 'POST', path: '/_gateway/logout', form: { scope: 'everywhere' } }); } catch { /* The device's own session is dropped below either way. */ }
      finally { client.abandon(); untrack(); }
    }
    await main.endHostedSession();
    await this.deps.preferences().save({ selectedHostedUrl: null });
    this.email_ = null;
    this.deps.identityChanged();
  }

  // Who the agent says is signed in (the gateway's email header, surfaced by /api/auth/status).
  async refreshIdentity(): Promise<void> {
    const main = this.deps.main();
    let email: string | null = null;
    if (main.hosted) {
      try {
        const body = await (await main.hostedFetch('/api/auth/status')).json() as { gateway?: { email?: unknown } };
        email = typeof body.gateway?.email === 'string' && body.gateway.email ? body.gateway.email : null;
      } catch { /* The menu falls back to "Signed in to Private Cloud". */ }
    }
    if (email !== this.email_) { this.email_ = email; this.deps.identityChanged(); }
  }

  private async complete(origin: string, entryUrl: string, context: PendingContext): Promise<void> {
    if (this.deps.quitting()) return;
    if (context.move) { await this.deps.moves().moveToCloud(origin, entryUrl); return; }
    this.deps.flow().close();
    const main = this.deps.main();
    if (main.hosted && main.serverOrigin === origin) {
      await main.loadHosted(entryUrl);
      if (context.fresh && context.returnPath) await main.loadHosted(`${origin}${context.returnPath}`);
    } else await this.deps.open({ mode: 'server', serverUrl: origin, hosted: { entryUrl } });
    await this.deps.preferences().save({ selectedServerUrl: null, selectedHostedUrl: origin });
    this.deps.main().show();
    void this.refreshIdentity();
  }

  private fail(message: string): void {
    this.deps.flow().close();
    void dialog.showMessageBox(this.deps.main().window, { type: 'error', title: 'Open Net Worth', message: 'Sign-in did not finish', detail: message }).catch(() => {});
  }
}
