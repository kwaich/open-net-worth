import { existsSync, readFileSync } from 'node:fs';
import { mkdir, rename, rm, writeFile } from 'node:fs/promises';
import { posix } from 'node:path';

// Linux-only module: always POSIX paths, whatever platform the code (or its tests) runs on.
const { dirname, isAbsolute, join } = posix;

export const AUTOSTART_FILE = 'open-net-worth.desktop';

export function autostartPath(env: NodeJS.ProcessEnv, home: string): string {
  const configured = env.XDG_CONFIG_HOME;
  return join(configured && isAbsolute(configured) ? configured : join(home, '.config'), 'autostart', AUTOSTART_FILE);
}

// Desktop Entry spec: quote an argument that has anything special, backslash-escape `"`, `` ` ``, `$`
// and `\` inside the quotes, then double every backslash again for the file's own string escaping.
export function execArgument(value: string): string {
  if (/[\n\r\0]/.test(value)) throw new Error('Invalid launcher argument.');
  const escaped = value.replaceAll('%', '%%');
  if (/^[A-Za-z0-9_\-./:@%+=,]+$/.test(escaped)) return escaped;
  return `"${escaped.replace(/(["`$\\])/g, '\\$1')}"`.replaceAll('\\', '\\\\');
}

export function autostartEntry(execPath: string, args: string[]): string {
  return ['[Desktop Entry]', 'Type=Application', 'Name=Open Net Worth', 'Comment=Start Open Net Worth quietly when you sign in',
    `Exec=${[execPath, ...args].map(execArgument).join(' ')}`, 'Icon=open-net-worth', 'Terminal=false', 'X-GNOME-Autostart-enabled=true', ''].join('\n');
}

// Electron's setLoginItemSettings is a no-op on Linux; the XDG autostart file is what desktop
// environments read at sign-in.
export class LinuxAutostart {
  constructor(private readonly file: string, private readonly execPath: string, private readonly args: string[]) {}

  enabled(): boolean {
    if (!existsSync(this.file)) return false;
    try { return !/^\s*(Hidden\s*=\s*true|X-GNOME-Autostart-enabled\s*=\s*false)\s*$/m.test(readFileSync(this.file, 'utf8')); } catch { return false; }
  }

  async set(enabled: boolean): Promise<void> {
    if (!enabled) { await rm(this.file, { force: true }); return; }
    await mkdir(dirname(this.file), { recursive: true });
    const staged = `${this.file}.tmp-${process.pid}`;
    await writeFile(staged, autostartEntry(this.execPath, this.args), { mode: 0o644 });
    await rename(staged, this.file);
  }
}
