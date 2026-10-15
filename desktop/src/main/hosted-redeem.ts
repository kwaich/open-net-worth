import { redeemUrl, validSlug, type HostedConfig } from './hosted-config';
import type { DeepLink } from './hosted-auth';

const STATE = /^[A-Za-z0-9_-]{16,128}$/;
const CODE = /^[\x21-\x7e]{1,512}$/;
// Crockford base32 without I, L, O, U, in two groups of four.
const SHORT_CODE = /^[0-9A-HJKMNP-TV-Z]{8}$/;

export class RedeemError extends Error {}

// People type the code from a screen: spaces, dashes and case do not matter, and Crockford reads O as 0 and I/L as 1.
export function normalizeShortCode(input: string): string | null {
  const text = input.toUpperCase().replace(/[\s-]/g, '').replace(/O/g, '0').replace(/[IL]/g, '1');
  return SHORT_CODE.test(text) ? `${text.slice(0, 4)}-${text.slice(4)}` : null;
}

// The portal answers once per code. What it returns is still checked as hard as a deep link: it is only
// useful together with this app's PKCE verifier, but a wrong shape must never reach the gateway URL.
export async function redeemShortCode(config: Pick<HostedConfig, 'portalOrigin'>, input: string, fetcher: typeof fetch = fetch): Promise<DeepLink> {
  const shortCode = normalizeShortCode(input);
  if (!shortCode) throw new RedeemError('Enter the code exactly as the sign-in page shows it, like ABCD-2345.');
  let response: Response;
  try {
    response = await fetcher(redeemUrl(config), { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify({ short_code: shortCode }), credentials: 'omit', redirect: 'error', signal: AbortSignal.timeout(15_000) });
  } catch { throw new RedeemError('Could not reach the sign-in service. Check your connection and try again.'); }
  if (response.status === 429) throw new RedeemError('Too many attempts. Wait a minute and try again.');
  if (!response.ok) throw new RedeemError('That code is not valid or has expired. Start again from the sign-in page.');
  const body = await response.json().catch(() => null) as { slug?: unknown; code?: unknown; state?: unknown } | null;
  if (!validSlug(body?.slug) || typeof body?.code !== 'string' || !CODE.test(body.code) || typeof body.state !== 'string' || !STATE.test(body.state)) throw new RedeemError('The sign-in service returned something unexpected. Start again from the sign-in page.');
  return { slug: body.slug, code: body.code, state: body.state };
}
