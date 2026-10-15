export interface MoveCall {
  method: 'GET' | 'POST' | 'PUT' | 'DELETE';
  path: string;
  json?: unknown;
  form?: Record<string, string>;
  bytes?: Uint8Array;
  headers?: Record<string, string>;
}
export interface MoveResult { status: number; body: unknown }

// One side of a move: this computer's own backend, or a Private Cloud reached through its signed-in page.
export interface MoveEndpoint { call(request: MoveCall): Promise<MoveResult> }

export class MoveError extends Error {}
export class MoveCancelled extends Error { constructor() { super('The move was cancelled.'); } }

export function detailOf(result: MoveResult, fallback: string): string {
  const detail = (result.body as { detail?: unknown } | null)?.detail;
  if (typeof detail === 'string' && detail) return detail;
  const message = (detail as { message?: unknown } | null)?.message;
  return typeof message === 'string' && message ? message : `${fallback} (HTTP ${result.status}).`;
}

interface RawBackend { fetchRaw(path: string, init?: RequestInit): Promise<Response> }

// This computer's own backend, reached with the desktop's launch credential.
export class LocalEndpoint implements MoveEndpoint {
  constructor(private readonly backend: RawBackend, private readonly timeoutMs = 10 * 60_000) {}

  async call(request: MoveCall): Promise<MoveResult> {
    const headers: Record<string, string> = { ...request.headers };
    let body: BodyInit | undefined;
    if (request.json !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(request.json); }
    else if (request.bytes) { headers['Content-Type'] = 'application/octet-stream'; body = request.bytes as unknown as BodyInit; }
    const response = await this.backend.fetchRaw(request.path, { method: request.method, headers, body, signal: AbortSignal.timeout(this.timeoutMs) });
    return { status: response.status, body: await response.json().catch(() => null) };
  }
}
