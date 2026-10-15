import { stateUrl } from './hosted-config';
import { isNewer } from './updates';

export type AgentState = 'running' | 'starting' | 'updating' | 'restarting' | 'stopped' | 'failed' | 'deleting' | 'unknown';
const STATES: readonly string[] = ['running', 'starting', 'updating', 'restarting', 'stopped', 'failed', 'deleting', 'unknown'];

export type AgentCheck =
  | { kind: 'open' }
  | { kind: 'update'; required: string }
  | { kind: 'state'; state: Exclude<AgentState, 'running'> }
  | { kind: 'unreachable' };

// Asks the gateway (C5, no sign-in needed) what state this Private Cloud is in before the app opens it, and
// whether this version of the app is still new enough to talk to it. An answer that is not the gateway's
// (an older gateway, a proxy page) means "go ahead and try": only a definite answer blocks the window.
export async function checkAgent(origin: string, appVersion: string, fetcher: typeof fetch = fetch): Promise<AgentCheck> {
  let response: Response;
  try {
    response = await fetcher(stateUrl(origin), { credentials: 'omit', redirect: 'error', cache: 'no-store', headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(8_000) });
  } catch { return { kind: 'unreachable' }; }
  const body = await response.json().catch(() => null) as { state?: unknown; min_desktop_version?: unknown } | null;
  if (typeof body?.state !== 'string' || !STATES.includes(body.state)) return { kind: 'open' };
  if (typeof body.min_desktop_version === 'string' && isNewer(body.min_desktop_version, appVersion)) return { kind: 'update', required: body.min_desktop_version };
  return body.state === 'running' ? { kind: 'open' } : { kind: 'state', state: body.state as Exclude<AgentState, 'running'> };
}
