import { MoveError, detailOf, type MoveEndpoint } from './move-endpoint';

export interface SecretsSkipped { kind: string; id: string; reason: string }

// The keys and connections of a book, read from the side that has them. Held in memory only until the other
// side has them: they never reach a file, a log or the portal.
export async function readSecrets(from: MoveEndpoint): Promise<unknown> {
  // A one-time token first: the export is refused without it, so it cannot be a single replayed request.
  const issued = await from.call({ method: 'POST', path: '/api/move/export-nonce' });
  const nonce = (issued.body as { nonce?: unknown } | null)?.nonce;
  if (issued.status !== 200 || typeof nonce !== 'string') throw new MoveError(detailOf(issued, 'Your keys and connections could not be read'));
  const result = await from.call({ method: 'POST', path: '/api/move/secrets/export', headers: { 'X-Move-Nonce': nonce } });
  if (result.status !== 200 || !result.body || typeof result.body !== 'object') throw new MoveError(detailOf(result, 'Your keys and connections could not be read'));
  return result.body;
}

export async function applySecrets(to: MoveEndpoint, moveId: string, payload: unknown): Promise<void> {
  const result = await to.call({ method: 'POST', path: `/api/move/${moveId}/secrets`, json: payload });
  if (result.status !== 200) throw new MoveError(detailOf(result, 'Your keys and connections could not be moved'));
}

export interface ChecklistItem { id: string; title: string; detail: string; fix: string }
export interface FinishResult { enabled: string[]; checklist: ChecklistItem[] }

export async function finishMove(to: MoveEndpoint, moveId: string): Promise<FinishResult> {
  const result = await to.call({ method: 'POST', path: `/api/move/${moveId}/finish` });
  const body = result.body as Partial<FinishResult> | null;
  if (result.status !== 200 || !body) throw new MoveError(detailOf(result, 'The move could not be finished'));
  return { enabled: Array.isArray(body.enabled) ? body.enabled : [], checklist: Array.isArray(body.checklist) ? body.checklist : [] };
}

export async function switchCopy(endpoint: MoveEndpoint, action: 'pause' | 'resume', body: { connectors: string[]; automations?: string[] }): Promise<{ connectors: string[]; automations: string[] }> {
  const result = await endpoint.call({ method: 'POST', path: `/api/move/${action}-copy`, json: body });
  const parsed = result.body as { connectors?: string[]; automations?: string[] } | null;
  if (result.status !== 200 || !parsed) throw new MoveError(detailOf(result, `The ${action === 'pause' ? 'pause' : 'resume'} did not finish`));
  return { connectors: parsed.connectors ?? [], automations: parsed.automations ?? [] };
}
