import type { BrowserWindow, MessageBoxOptions } from 'electron';
import { serverAddress, validateServer } from './connections';

export interface ServerSubmitContext {
  // The window prompts attach to; null once it is gone, which ends the attempt quietly.
  surface(): BrowserWindow | null;
  localRunning(): boolean;
  ask(parent: BrowserWindow, options: MessageBoxOptions): Promise<number>;
  connect(origin: string): Promise<void>;
  // The surface vanished while connecting, so the failure has nowhere inline to show.
  lost(error: unknown): Promise<void>;
  validate?: typeof validateServer;
}

const insecurePrompt = (origin: string): MessageBoxOptions => ({ type: 'warning', title: 'Connection not encrypted', message: 'This connection is not encrypted.', detail: `${origin}\n\nYour password and book data travel without encryption. Continue only on a network you trust.`, buttons: ['Cancel', 'Connect anyway'], defaultId: 0, cancelId: 0 });
const switchPrompt = (origin: string): MessageBoxOptions => ({ type: 'question', title: 'Change connection', message: 'Switch to this server?', detail: `${origin}\n\nThe app will show the book on this server. The book on this computer isn’t changed — you can switch back any time.\n\nAnything you haven’t saved will be closed, and any browser tab you opened from this app will stop working.`, buttons: ['Cancel', 'Connect'], defaultId: 0, cancelId: 0 });

// The one path from a typed server address to an open connection, shared by the Connection
// dialog and the first-launch page: validate, confirm what needs confirming, then connect.
export class ServerSubmission {
  private validation: AbortController | null = null;
  private generation = 0;
  private applying = false;

  get validating(): boolean { return this.validation !== null; }
  get connecting(): boolean { return this.applying; }
  get busy(): boolean { return this.applying || this.validation !== null; }

  abort(): void { this.generation++; this.validation?.abort(); this.validation = null; }

  // Resolves true once connected, false when the attempt was abandoned (Back, a declined prompt, a closed window).
  async submit(value: unknown, context: ServerSubmitContext): Promise<boolean> {
    if (typeof value !== 'string') throw new Error('Enter a server URL.');
    if (this.busy) throw new Error('Wait for the current connection operation to finish.');
    const address = serverAddress(value);
    const controller = new AbortController();
    this.validation = controller;
    const generation = ++this.generation;
    const declined = async (options: MessageBoxOptions, accept: number): Promise<boolean> => {
      const surface = context.surface();
      if (!surface) return true;
      const response = await context.ask(surface, options);
      return response !== accept || generation !== this.generation;
    };
    try {
      await (context.validate ?? validateServer)(address.origin, controller.signal);
      if (generation !== this.generation || !context.surface()) return false;
      if (address.insecure && await declined(insecurePrompt(address.origin), 1)) return false;
      if (context.localRunning() && await declined(switchPrompt(address.origin), 1)) return false;
      this.applying = true;
      await context.connect(address.origin);
      return true;
    } catch (error) {
      if (this.applying && !context.surface()) await context.lost(error);
      throw error;
    } finally {
      if (this.validation === controller) this.validation = null;
      this.applying = false;
    }
  }
}
