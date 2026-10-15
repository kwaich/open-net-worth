import { readFile, realpath, stat } from 'node:fs/promises';
import { isAbsolute, posix, resolve, sep } from 'node:path';

export interface BackendReady { type: 'ready'; protocol: 1; port: number; instance_id: string; version: string }
export interface RuntimeConfig { python: string; cwd: string; version: string }

export class BookInUseError extends Error {
  constructor(readonly port: number | null) {
    super(`This book is already open in another Open Net Worth server${port === null ? '' : ` on port ${port}`}. Stop that server and retry, or choose another folder.`);
  }
}

export function failureReason(stderrTail: string, startError = ''): string {
  if (/NEWER code/.test(stderrTail)) return 'This book was last opened by a newer version of Open Net Worth. Update the app to open it.';
  const last = stderrTail.split('\n').map(line => line.trim()).filter(Boolean).at(-1) ?? startError;
  return last.length > 240 ? `${last.slice(0, 240)}…` : last;
}

export function parseStartupError(line: string, instanceId: string, version: string): BookInUseError | null {
  const value = JSON.parse(line) as { type: string; protocol: number; instance_id: string; version: string; code: string; port: number | null };
  if (value.type !== 'startup_error') return null;
  if (value.protocol !== 1 || value.instance_id !== instanceId || value.version !== version || value.code !== 'book_in_use' || value.port !== null && (!Number.isInteger(value.port) || value.port < 1 || value.port > 65535)) throw new Error('The bundled backend returned an incompatible startup error.');
  return new BookInUseError(value.port);
}

export function parseReady(line: string, instanceId: string, version: string): BackendReady {
  const value = JSON.parse(line) as BackendReady;
  if (value.type !== 'ready' || value.protocol !== 1 || value.instance_id !== instanceId || value.version !== version || !Number.isInteger(value.port) || value.port < 1 || value.port > 65535) throw new Error('The bundled backend returned an incompatible identity.');
  return value;
}

export function childEnvironment(ambient: NodeJS.ProcessEnv, platform: NodeJS.Platform = process.platform): NodeJS.ProcessEnv {
  const allowed = ['HOME', 'USER', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'PATH', 'SystemRoot', 'WINDIR', 'TMP', 'TEMP', 'TMPDIR', 'LANG', 'LC_ALL', 'TZ'];
  const clean = Object.fromEntries(allowed.flatMap(name => ambient[name] === undefined ? [] : [[name, ambient[name]]]));
  if (platform === 'darwin' || platform === 'linux') {
    // A POSIX PATH entry, so build it with POSIX rules even when this runs (in tests) on Windows.
    const userBin = clean.HOME && posix.isAbsolute(clean.HOME) ? posix.resolve(clean.HOME, '.local', 'bin') : '';
    const inherited = (clean.PATH || '').split(':').filter(Boolean);
    clean.PATH = [...new Set([...inherited, userBin, ...(platform === 'darwin' ? ['/opt/homebrew/bin'] : []), '/usr/local/bin', '/usr/bin', '/bin', '/usr/sbin', '/sbin'].filter(Boolean))].join(':');
  }
  return { ...clean, PYTHONNOUSERSITE: '1', PYTHONUNBUFFERED: '1', PYTHONUTF8: '1' };
}

export function runtimePath(root: string, relative: string): string {
  const path = resolve(root, relative);
  if (isAbsolute(relative) || path === resolve(root) || !path.startsWith(`${resolve(root)}${sep}`)) throw new Error('Invalid bundled runtime path.');
  return path;
}

export async function bundledRuntime(root: string, expectedVersion: string): Promise<RuntimeConfig> {
  const canonicalRoot = await realpath(root);
  const manifest = JSON.parse(await readFile(resolve(root, 'manifest.json'), 'utf8')) as { protocol: number; version: string; python: string; cwd: string; platform: string; arch: string };
  if (manifest.protocol !== 1 || manifest.version !== expectedVersion) throw new Error('The bundled Python runtime does not match this application.');
  const platform = process.platform === 'win32' ? 'windows' : process.platform;
  const architecture = (value: string) => ['x64', 'x86_64', 'amd64'].includes(value.toLowerCase()) ? 'x64' : ['arm64', 'aarch64'].includes(value.toLowerCase()) ? 'arm64' : value.toLowerCase();
  if (manifest.platform !== platform || typeof manifest.arch !== 'string' || architecture(manifest.arch) !== architecture(process.arch)) throw new Error('The bundled Python runtime is for another platform or architecture.');
  const python = await realpath(runtimePath(canonicalRoot, manifest.python));
  const cwd = manifest.cwd === '.' ? canonicalRoot : await realpath(runtimePath(canonicalRoot, manifest.cwd));
  if (!python.startsWith(`${canonicalRoot}${sep}`) || cwd !== canonicalRoot && !cwd.startsWith(`${canonicalRoot}${sep}`) || !(await stat(python)).isFile() || !(await stat(cwd)).isDirectory()) throw new Error('The bundled runtime path is invalid.');
  return { python, cwd, version: manifest.version };
}
