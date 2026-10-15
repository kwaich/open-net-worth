import { PendingSignIns, parseDeepLink, type DeepLink } from './hosted-auth';
import { callbackUrl, instanceOrigin, signInUrl, type HostedConfig } from './hosted-config';
import type { PendingContext, PendingStore } from './hosted-pending-store';

interface HostedDeps {
  config(): HostedConfig;
  openBrowser(url: string): Promise<void>;
  complete(origin: string, entryUrl: string, context: PendingContext): Promise<void>;
  store: Pick<PendingStore, 'load' | 'save' | 'clear'>;
  now?(): number;
}

// An expired session can bounce the window to the portal several times in a row; one browser tab is enough.
const AUTO_RESTART_GAP_MS = 5_000;

export class HostedSignIn {
  private readonly pending: PendingSignIns<PendingContext>;
  private lastStart = -Infinity;
  private lastUrl: string | null = null;
  constructor(private readonly deps: HostedDeps) { this.pending = new PendingSignIns(deps.now); }

  // Picks up sign-ins that were waiting when the app last quit. Every entry point waits for it, so a link
  // that arrives while the app is still starting is matched against them too.
  private restored: Promise<void> | null = null;
  restore(): Promise<void> { return this.restored ??= this.deps.store.load().then(saved => this.pending.restore(saved)); }

  get waiting(): boolean { return this.pending.size > 0; }
  get movePending(): boolean { return this.pending.snapshot().some(entry => entry.context.move); }
  private get freshPending(): { returnPath: string | null } | null {
    const entry = this.pending.snapshot().find(candidate => candidate.context.fresh);
    return entry ? { returnPath: entry.context.returnPath ?? null } : null;
  }

  async start(move = false, automatic = false, reauth: { returnPath: string | null } | null = null): Promise<void> {
    await this.restore();
    const now = (this.deps.now ?? Date.now)();
    if (automatic && now - this.lastStart < AUTO_RESTART_GAP_MS) return;
    this.lastStart = now;
    const { state, challenge } = this.pending.begin({ move, ...(reauth ? { fresh: true, ...(reauth.returnPath ? { returnPath: reauth.returnPath } : {}) } : {}) });
    await this.deps.store.save(this.pending.snapshot());
    this.lastUrl = signInUrl(this.deps.config(), challenge, state, move, Boolean(reauth));
    await this.deps.openBrowser(this.lastUrl);
  }

  // "Open browser again" reuses the request in progress, so the page the person already has still matches.
  async reopen(): Promise<void> {
    await this.restore();
    if (!this.lastUrl || !this.pending.size) return this.start(this.movePending, false, this.freshPending);
    await this.deps.openBrowser(this.lastUrl);
  }

  async handleLink(raw: string): Promise<void> { await this.complete(parseDeepLink(raw)); }

  async complete(link: DeepLink): Promise<void> {
    await this.restore();
    const config = this.deps.config();
    const { verifier, context } = this.pending.consume(link.state);
    await this.deps.store.save(this.pending.snapshot());
    await this.deps.complete(instanceOrigin(config, link.slug), callbackUrl(config, link.slug, link.code, verifier), context);
  }

  async cancel(): Promise<void> { this.pending.clear(); this.lastUrl = null; await this.deps.store.clear(); }
}
