import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { randomBytes, randomUUID } from 'node:crypto';
import { createInterface } from 'node:readline';
import { desktopEvent } from './desktop-events';
import { BookInUseError, childEnvironment, failureReason, parseReady, parseStartupError, type RuntimeConfig } from './runtime';
import { SESSION_HEADER } from './security';
import { WatchedFileReader } from './watched-files';
import type { FolderLocator } from './preferences';
import type { FileIdentity } from './files';
import type { NativeFile } from '../contracts';

const STDERR_TAIL = 8192;

export class BackendSupervisor {
  private child: ChildProcessWithoutNullStreams | null = null;
  private token = '';
  private stopping = false;
  private instanceId = '';
  private stderrTail = '';
  private fileReader: WatchedFileReader | null = null;
  private runtime: RuntimeConfig;
  origin: string | null = null;
  startupFailure: BookInUseError | null = null;
  private startError = '';

  constructor(private readonly runtimeSource: RuntimeConfig | (() => Promise<RuntimeConfig>), private root: string, private readonly qaMode: boolean, private readonly onCrash: () => void) {
    this.runtime = typeof runtimeSource === 'function' ? { python: '', cwd: '', version: '' } : runtimeSource;
  }

  // The one sentence worth showing for a failed start: the backend's last stderr line, else the start error.
  get failureReason(): string { return failureReason(this.stderrTail, this.startError); }
  get credential(): string { return this.token; }
  get bookRoot(): string { return this.root; }
  setRoot(root: string): void {
    if (this.child || this.origin) throw new Error('Stop the current book before selecting another.');
    this.root = root;
  }
  readWatchedFile(folder: FolderLocator, name: string, file: FileIdentity): Promise<NativeFile> {
    if (!this.fileReader || !this.origin) return Promise.reject(new Error('The local backend is unavailable.'));
    return this.fileReader.read(folder, name, file);
  }

  async start(): Promise<void> {
    if (this.child) throw new Error('The backend is already running.');
    if (typeof this.runtimeSource === 'function' && !this.runtime.python) this.runtime = await this.runtimeSource();
    this.fileReader ??= new WatchedFileReader(this.runtime);
    this.stopping = false;
    this.startupFailure = null;
    this.stderrTail = '';
    this.startError = '';
    this.token = randomBytes(32).toString('hex');
    this.instanceId = randomUUID();
    const child = spawn(this.runtime.python, ['-m', 'backend.cli.desktop'], {
      cwd: this.runtime.cwd, env: childEnvironment(process.env), stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true, detached: process.platform !== 'win32',
    });
    this.child = child;
    child.stdout.on('data', () => {});
    // Drained, never forwarded: the backend owns its persistent log. Only the tail is kept, to explain a failed start.
    child.stderr.on('data', (bytes: Buffer) => { this.stderrTail = (this.stderrTail + bytes.toString()).slice(-STDERR_TAIL); });
    child.stdin.on('error', () => {});
    child.on('exit', () => {
      this.fileReader?.stop();
      this.signalTree(child, 'SIGTERM');
      this.origin = null;
      if (!this.stopping) this.onCrash();
    });
    child.on('close', () => { if (this.child === child) this.child = null; });
    const bootstrap = { token: this.token, instance_id: this.instanceId, root: this.root, qa_mode: this.qaMode };
    child.stdin.write(`${JSON.stringify(bootstrap)}\n`);
    try {
      await this.awaitReady(child);
      const status = await this.json<{ protocol: number; instance_id: string; version: string }>('/api/desktop/status');
      if (status.protocol !== 1 || status.instance_id !== this.instanceId || status.version !== this.runtime.version) throw new Error('The backend readiness check failed.');
    } catch (error) {
      desktopEvent({ event: 'desktop.backend_start_failed', error: error instanceof Error ? error.message : 'Startup failed', exitCode: child.exitCode, signal: child.signalCode, backendStderr: this.stderrTail });
      this.startError = error instanceof Error ? error.message : '';
      await this.stop();
      throw error;
    }
  }

  private awaitReady(child: ChildProcessWithoutNullStreams): Promise<void> {
    return new Promise((resolve, reject) => {
      const lines = createInterface({ input: child.stdout });
      const timer = setTimeout(() => finish(new Error('Startup timed out. Your book is preserved.')), 60_000);
      const onError = () => finish(new Error('The bundled backend could not start.'));
      const onClose = () => finish(new Error('The backend stopped during startup. Your book is preserved.'));
      const finish = (error?: Error) => {
        clearTimeout(timer);
        lines.close();
        child.removeListener('error', onError);
        child.removeListener('close', onClose);
        error ? reject(error) : resolve();
      };
      child.once('error', onError);
      child.once('close', onClose);
      lines.on('line', (line) => {
        try {
          const failure = parseStartupError(line, this.instanceId, this.runtime.version);
          if (failure) { this.startupFailure = failure; finish(failure); return; }
          const ready = parseReady(line, this.instanceId, this.runtime.version);
          this.origin = `http://127.0.0.1:${ready.port}`;
          finish();
        } catch { finish(new Error('The backend returned an incompatible startup response.')); }
      });
    });
  }

  // The raw reply, whatever its status; `request` is this plus "refuse anything that is not ok".
  async fetchRaw(path: string, init: RequestInit = {}): Promise<Response> {
    if (!this.origin || !path.startsWith('/') || path.startsWith('//')) throw new Error('The backend is unavailable.');
    const headers = new Headers(init.headers);
    headers.set(SESSION_HEADER, this.token);
    headers.set('X-Requested-With', 'XMLHttpRequest');
    return fetch(`${this.origin}${path}`, { ...init, headers, redirect: 'error', signal: init.signal ?? AbortSignal.timeout(30_000) });
  }

  async request(path: string, init: RequestInit = {}): Promise<Response> {
    const response = await this.fetchRaw(path, init);
    if (!response.ok) throw new Error(`The operation was refused (${response.status}).`);
    return response;
  }

  async json<T>(path: string, init: RequestInit = {}): Promise<T> {
    return (await this.request(path, init)).json() as Promise<T>;
  }

  async stop(): Promise<void> {
    this.stopping = true;
    this.fileReader?.stop();
    const child = this.child;
    if (!child) { this.origin = null; this.token = ''; return; }
    if (!child.pid) { this.child = null; this.origin = null; this.token = ''; return; }
    child.stdin.end(`${JSON.stringify({ type: 'shutdown' })}\n`);
    await new Promise<void>((resolve) => {
      const soft = setTimeout(() => this.signalTree(child, 'SIGTERM'), 18_000);
      const hard = setTimeout(() => this.signalTree(child, 'SIGKILL'), 22_000);
      child.once('close', () => { clearTimeout(soft); clearTimeout(hard); this.signalTree(child, 'SIGTERM'); resolve(); });
    });
    this.child = null;
    this.origin = null;
    this.token = '';
  }

  private signalTree(child: ChildProcessWithoutNullStreams, signal: NodeJS.Signals): void {
    if (!child.pid) return;
    try {
      if (process.platform === 'win32') {
        const args = ['/PID', String(child.pid), '/T'];
        if (signal === 'SIGKILL') args.push('/F');
        const killer = spawn('taskkill', args, { windowsHide: true, stdio: 'ignore' });
        killer.on('error', () => child.kill());
      } else process.kill(-child.pid, signal);
    } catch { /* Already stopped. */ }
  }
}
