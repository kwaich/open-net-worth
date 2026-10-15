// Opt-in, for a run from source only: ONW_DESKTOP_PROFILE=dev (`make desktop-dev`) is a second app
// beside an installed one, with its own name, settings folder and default book, so a test run never
// repoints, locks out or opens the real app's book. Without it a source run is the normal app.
export interface DesktopProfile {
  name: string;
  // The settings folder's name under the OS app-data folder; null keeps Electron's default.
  settingsFolder: string | null;
  // The default book folder's name under the home folder.
  bookFolder: string;
}

export function desktopProfile(isPackaged: boolean, env: NodeJS.ProcessEnv): DesktopProfile {
  if (!isPackaged && env.ONW_DESKTOP_PROFILE === 'dev') {
    return { name: 'Open Net Worth Dev', settingsFolder: 'Open Net Worth Dev', bookFolder: '.open-net-worth-dev' };
  }
  return { name: 'Open Net Worth', settingsFolder: null, bookFolder: '.open-net-worth' };
}
