import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { basename } from 'node:path';
import type { NativeFile } from '../contracts';
import { MAX_FILE_BYTES, fileMime, type FileIdentity } from './files';
import type { FolderLocator } from './preferences';
import { childEnvironment, type RuntimeConfig } from './runtime';

export class WatchedFileReader {
  private helpers = new Set<ChildProcessWithoutNullStreams>();
  constructor(private readonly runtime: RuntimeConfig) {}

  read(folder: FolderLocator, name: string, file: FileIdentity): Promise<NativeFile> {
    if (!name || name !== basename(name) || name.includes('/') || name.includes('\\') || name.includes('\0') || file.size < 1 || file.size > MAX_FILE_BYTES) return Promise.reject(new Error('Invalid watched file.'));
    const helper = spawn(this.runtime.python, ['-m', 'backend.cli.desktop_file'], { cwd: this.runtime.cwd, env: childEnvironment(process.env), stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true, detached: process.platform !== 'win32' });
    this.helpers.add(helper);
    return this.collect(helper, name, file.size, { directory: folder.path, device: folder.device, inode: folder.inode, name, file });
  }

  stop(): void { for (const helper of this.helpers) this.kill(helper); }

  private collect(helper: ChildProcessWithoutNullStreams, name: string, expectedSize: number, config: unknown): Promise<NativeFile> {
    return new Promise((resolve, reject) => {
      const chunks: Buffer[] = [];
      let length = 0;
      let settled = false;
      const finish = (error?: Error, value?: NativeFile) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        this.helpers.delete(helper);
        helper.stdin.end();
        if (error) { this.kill(helper); reject(error); } else resolve(value!);
      };
      const timer = setTimeout(() => finish(new Error('The watched file could not be read in time.')), 10_000);
      helper.stdout.on('data', (chunk: Buffer) => {
        length += chunk.length;
        if (length > MAX_FILE_BYTES + 8_192) finish(new Error('The watched file exceeded the import limit.'));
        else chunks.push(chunk);
      });
      helper.stderr.on('data', () => {});
      helper.stdin.on('error', () => finish(new Error('The watched file could not be opened.')));
      helper.on('error', () => finish(new Error('The bundled file reader could not start.')));
      helper.on('close', (code) => {
        if (settled) return;
        try {
          const output = Buffer.concat(chunks);
          const separator = output.indexOf(10);
          if (code !== 0 || separator < 1 || separator > 8_192) throw new Error('Invalid file response.');
          const header = JSON.parse(output.subarray(0, separator).toString('utf8')) as { type: string; size: number; name: string };
          const bytes = output.subarray(separator + 1);
          if (header.type !== 'file' || header.name !== name || header.size !== expectedSize || bytes.length !== expectedSize) throw new Error('Invalid file response.');
          finish(undefined, { name, type: fileMime(name), bytes: new Uint8Array(bytes) });
        } catch { finish(new Error('The watched folder or file changed while being read.')); }
      });
      helper.stdin.write(`${JSON.stringify(config)}\n`);
    });
  }

  private kill(helper: ChildProcessWithoutNullStreams): void {
    if (!helper.pid || helper.exitCode !== null || helper.signalCode !== null) return;
    try { process.platform === 'win32' ? helper.kill('SIGKILL') : process.kill(-helper.pid, 'SIGKILL'); }
    catch { /* The trusted helper already exited; it never starts descendants. */ }
  }
}
