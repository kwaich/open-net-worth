import { createHash, randomBytes } from 'node:crypto';
import { DEEP_LINK_SCHEME, validSlug } from './hosted-config';

// The portal keeps a desktop request for 24 hours, so the app does too: a restart or a closed tab in between is fine.
export const SIGN_IN_TTL_MS = 24 * 60 * 60 * 1000;
const MAX_PENDING = 8;
const STATE = /^[A-Za-z0-9_-]{16,128}$/;
const CODE = /^[\x21-\x7e]{1,512}$/;

export interface Pkce { verifier: string; challenge: string }

export function createPkce(random: (size: number) => Buffer = randomBytes): Pkce {
  const verifier = random(48).toString('base64url');
  return { verifier, challenge: createHash('sha256').update(verifier).digest('base64url') };
}

export function createState(random: (size: number) => Buffer = randomBytes): string { return random(24).toString('base64url'); }

export type LinkFailure = 'malformed' | 'unknown';
export class HostedLinkError extends Error { constructor(readonly reason: LinkFailure, message: string) { super(message); } }

export interface DeepLink { slug: string; code: string; state: string }

// Errors never echo the link: its query holds a one-time code.
export function parseDeepLink(raw: string): DeepLink {
  let url: URL;
  try { url = new URL(raw); } catch { throw new HostedLinkError('malformed', 'The sign-in link is not valid.'); }
  if (url.protocol !== `${DEEP_LINK_SCHEME}:` || url.hostname !== 'auth' || url.username || url.password) throw new HostedLinkError('malformed', 'The sign-in link is not valid.');
  const slug = url.searchParams.get('slug');
  const code = url.searchParams.get('code');
  const state = url.searchParams.get('state');
  if (!validSlug(slug)) throw new HostedLinkError('malformed', 'The sign-in link names an invalid address.');
  if (!code || !CODE.test(code) || !state || !STATE.test(state)) throw new HostedLinkError('malformed', 'The sign-in link is not valid.');
  return { slug, code, state };
}

export function deepLinkArgument(argv: readonly string[]): string | null {
  return argv.find(value => value.startsWith(`${DEEP_LINK_SCHEME}://`)) ?? null;
}

interface Entry<T> { verifier: string; expires: number; context: T }
export interface StoredSignIn<T> extends Entry<T> { state: string }

// Sign-ins this app started. A state is accepted once and only while fresh, so a link the
// app didn't ask for, an old one, or a replay is refused. `snapshot`/`restore` let the store
// keep them across a restart.
export class PendingSignIns<T = undefined> {
  private readonly entries = new Map<string, Entry<T>>();
  constructor(private readonly now: () => number = Date.now, private readonly ttl = SIGN_IN_TTL_MS) {}

  begin(context: T, pkce: Pkce = createPkce(), state: string = createState()): { state: string; challenge: string } {
    this.sweep();
    if (this.entries.size >= MAX_PENDING) this.entries.delete(this.entries.keys().next().value!);
    this.entries.set(state, { verifier: pkce.verifier, expires: this.now() + this.ttl, context });
    return { state, challenge: pkce.challenge };
  }

  consume(state: string): { verifier: string; context: T } {
    this.sweep();
    const entry = this.entries.get(state);
    this.entries.delete(state);
    if (!entry) throw new HostedLinkError('unknown', 'This sign-in link has expired or was not started by this app.');
    return { verifier: entry.verifier, context: entry.context };
  }

  snapshot(): StoredSignIn<T>[] { this.sweep(); return [...this.entries].map(([state, entry]) => ({ state, ...entry })); }
  restore(saved: StoredSignIn<T>[]): void {
    for (const { state, ...entry } of saved) if (STATE.test(state) && entry.expires > this.now() && typeof entry.verifier === 'string') this.entries.set(state, entry);
    while (this.entries.size > MAX_PENDING) this.entries.delete(this.entries.keys().next().value!);
  }

  get size(): number { this.sweep(); return this.entries.size; }
  clear(): void { this.entries.clear(); }
  private sweep(): void { const now = this.now(); for (const [state, entry] of this.entries) if (entry.expires <= now) this.entries.delete(state); }
}
