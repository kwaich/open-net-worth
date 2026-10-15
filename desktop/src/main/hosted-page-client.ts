import type { MoveCall, MoveEndpoint, MoveResult } from './move-endpoint';

interface Sender { send(channel: string, payload: unknown): void }
interface Frame { url: string }
interface Waiter { resolve(result: MoveResult): void; reject(error: Error): void; timer: NodeJS.Timeout }

export const PAGE_REQUEST_CHANNEL = 'desktop-hosted:request';
export const PAGE_RESPONSE_CHANNEL = 'desktop-hosted:response';

// Asks a signed-in Private Cloud page (through its preload) to make a request, so the call carries the page's
// own origin: the gateway accepts changes only from there. The preload refuses any path outside the move and
// sign-out surface, and only this process can drive it.
export class HostedPageClient implements MoveEndpoint {
  private readonly waiting = new Map<number, Waiter>();
  private next = 1;
  constructor(private readonly target: Sender, private readonly origin: string, private readonly timeoutMs = 5 * 60_000) {}

  // Only the page this client drives, on the origin it expects, may answer it.
  accepts(sender: unknown, frame: Frame | null | undefined): boolean {
    try { return sender === this.target && Boolean(frame) && new URL(frame!.url).origin === this.origin; } catch { return false; }
  }

  call(request: MoveCall): Promise<MoveResult> {
    const id = this.next++;
    return new Promise<MoveResult>((resolve, reject) => {
      const timer = setTimeout(() => { this.waiting.delete(id); reject(new Error('Your Private Cloud did not respond.')); }, this.timeoutMs);
      this.waiting.set(id, { resolve, reject, timer });
      try { this.target.send(PAGE_REQUEST_CHANNEL, { id, request }); }
      catch { clearTimeout(timer); this.waiting.delete(id); reject(new Error('The Private Cloud page is not available.')); }
    });
  }

  deliver(message: unknown): void {
    const { id, status, body } = (message ?? {}) as { id?: unknown; status?: unknown; body?: unknown };
    const waiter = typeof id === 'number' ? this.waiting.get(id) : undefined;
    if (!waiter || typeof status !== 'number') return;
    clearTimeout(waiter.timer);
    this.waiting.delete(id as number);
    if (status === 0) waiter.reject(new Error('Your Private Cloud could not be reached.'));
    else waiter.resolve({ status, body });
  }

  abandon(): void {
    for (const [id, waiter] of this.waiting) { clearTimeout(waiter.timer); waiter.reject(new Error('The Private Cloud window closed.')); this.waiting.delete(id); }
  }
}

const clients = new Set<HostedPageClient>();

export function trackPageClient(client: HostedPageClient): () => void {
  clients.add(client);
  return () => { clients.delete(client); };
}

// The one listener for replies from any tracked page; each reply goes only to the client that owns the sender.
export function routePageResponse(sender: unknown, frame: Frame | null | undefined, message: unknown): void {
  for (const client of clients) if (client.accepts(sender, frame)) client.deliver(message);
}
