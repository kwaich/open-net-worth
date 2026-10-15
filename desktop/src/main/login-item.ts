import { basename, dirname, resolve } from 'node:path';

export interface LoginItemOptions { path?: string; args?: string[] }

// Squirrel.Windows installs each version into its own app-<version> folder, so a login item that
// names the running executable dies with the next update. Update.exe, one level up, always
// starts whichever version is current.
export function squirrelUpdateExe(execPath: string): string {
  return resolve(dirname(execPath), '..', 'Update.exe');
}

export function loginItemOptions(platform: NodeJS.Platform, execPath: string, hasUpdateExe: (path: string) => boolean): LoginItemOptions {
  if (platform !== 'win32') return {};
  const updateExe = squirrelUpdateExe(execPath);
  if (!hasUpdateExe(updateExe)) return { args: ['--onw-login'] };
  return { path: updateExe, args: ['--processStart', `"${basename(execPath)}"`, '--process-start-args', '"--onw-login"'] };
}
