import { isIP } from 'node:net';

export const DEFAULT_PORTAL = 'https://cloud.opennetworth.ai';
export const DEFAULT_INSTANCE_DOMAIN = 'opennetworth.cloud';
export const DEEP_LINK_SCHEME = 'onw';

export interface HostedConfig { portalOrigin: string; instanceScheme: 'http' | 'https'; instanceDomain: string }

const SLUG = /^[a-z0-9]{1,32}$/;
const DOMAIN = /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?::\d{1,5})?$/;

function loopbackHost(host: string): boolean {
  const name = host.toLowerCase().replace(/^\[|\]$/g, '').replace(/:\d+$/, '');
  return name === 'localhost' || name.endsWith('.localhost') || name === '::1' || isIP(name) === 4 && name.startsWith('127.');
}

function portal(value: string): string {
  let url: URL;
  try { url = new URL(value.trim()); } catch { throw new Error('The hosted sign-in address is not a valid URL.'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error('The hosted sign-in address must be an origin without a path or credentials.');
  if (url.protocol === 'http:' && !loopbackHost(url.host)) throw new Error('The hosted sign-in address must use HTTPS.');
  return url.origin;
}

function instance(value: string): Pick<HostedConfig, 'instanceScheme' | 'instanceDomain'> {
  const match = /^(?:(https?):\/\/)?([^/?#@\s]+)\/?$/i.exec(value.trim());
  const domain = match?.[2]?.toLowerCase();
  if (!match || !domain || !DOMAIN.test(domain)) throw new Error('The Private Cloud domain is not valid.');
  const scheme = (match[1]?.toLowerCase() ?? 'https') as 'http' | 'https';
  if (scheme === 'http' && !loopbackHost(domain)) throw new Error('The Private Cloud domain must use HTTPS.');
  return { instanceScheme: scheme, instanceDomain: domain };
}

// Both values are overridable so the flow can run against a local test stack, e.g.
// ONW_HOSTED_PORTAL_URL=http://localhost:8100 ONW_HOSTED_INSTANCE_DOMAIN=http://localhost:8180.
export function hostedConfig(env: NodeJS.ProcessEnv = process.env): HostedConfig {
  return { portalOrigin: portal(env.ONW_HOSTED_PORTAL_URL?.trim() || DEFAULT_PORTAL), ...instance(env.ONW_HOSTED_INSTANCE_DOMAIN?.trim() || DEFAULT_INSTANCE_DOMAIN) };
}

export function validSlug(value: unknown): value is string { return typeof value === 'string' && SLUG.test(value); }

export function instanceOrigin(config: HostedConfig, slug: string): string {
  if (!validSlug(slug)) throw new Error('The Private Cloud name is not valid.');
  return `${config.instanceScheme}://${slug}.${config.instanceDomain}`;
}

// `fresh` asks the portal for a forced sign-in before it hands off: the agent's Terminal needs a session
// minted from one in the last 15 minutes, and the window's own cookie only changes through a hand-off.
export function signInUrl(config: HostedConfig, challenge: string, state: string, move = false, fresh = false): string {
  const url = new URL('/connect', config.portalOrigin);
  url.searchParams.set('client', 'desktop');
  url.searchParams.set('code_challenge', challenge);
  url.searchParams.set('app_state', state);
  if (move) url.searchParams.set('move', '1');
  if (fresh) url.searchParams.set('fresh', '1');
  return url.href;
}

export function callbackUrl(config: HostedConfig, slug: string, code: string, verifier: string): string {
  const url = new URL('/_gateway/callback', instanceOrigin(config, slug));
  url.searchParams.set('code', code);
  url.searchParams.set('code_verifier', verifier);
  return url.href;
}

export function stateUrl(origin: string): string { return new URL('/_gateway/state', origin).href; }

export function redeemUrl(config: Pick<HostedConfig, 'portalOrigin'>): string { return new URL('/api/desktop/redeem', config.portalOrigin).href; }

// A saved URL is trusted only if it is `<slug>.<instance domain>` for the configured domain.
export function hostedOrigin(config: HostedConfig, value: string): string | null {
  try {
    const url = new URL(value);
    const suffix = `.${config.instanceDomain}`;
    if (url.origin !== `${config.instanceScheme}://${url.host}` || !url.host.endsWith(suffix) || url.pathname !== '/' || url.search || url.hash || url.username || url.password) return null;
    return validSlug(url.host.slice(0, -suffix.length)) ? url.origin : null;
  } catch { return null; }
}

// The gateway sends a visitor with no session to the portal's /connect; the app never renders that page.
// A page on the agent's own origin, nothing else: never a way to send the window somewhere new.
export function safeReturnPath(value: unknown): string | null {
  return typeof value === 'string' && value.length <= 2048 && /^\/(?![/\\])[\x21-\x7e]*$/.test(value) ? value : null;
}

// The gateway's `/_gateway/refresh` redirects to the portal's /connect with `fresh=1` and the page to come back to.
export function freshReauth(config: Pick<HostedConfig, 'portalOrigin'>, value: string): { returnPath: string | null } | null {
  try {
    const url = new URL(value);
    if (!portalConnectUrl(config, value) || url.searchParams.get('fresh') !== '1') return null;
    return { returnPath: safeReturnPath(url.searchParams.get('return')) };
  } catch { return null; }
}

export function portalConnectUrl(config: Pick<HostedConfig, 'portalOrigin'>, value: string): boolean {
  try { const url = new URL(value); return url.origin === config.portalOrigin && (url.pathname === '/connect' || url.pathname.startsWith('/connect/')); } catch { return false; }
}
