import type { BrowserWindowConstructorOptions } from 'electron';

// The macOS window draws no title bar: the app's own top strip carries the traffic lights and
// is the drag handle. The preload reads this argument and stamps `data-desktop-chrome="mac"` on
// <html>, which is how the web UI (local or a remote server's) learns to reserve room for them.
export const MAC_CHROME_ARGUMENT = '--onw-chrome=mac';
export const FULLSCREEN_CHANNEL = 'desktop-chrome:fullscreen';

// Centres the 12px buttons in the app's 48px top strip (`.sidebar-header` / `.topbar`).
const TRAFFIC_LIGHTS = { x: 16, y: 18 };

export interface NativeChrome { options: BrowserWindowConstructorOptions; arguments: string[] }

export function nativeChrome(platform: NodeJS.Platform): NativeChrome {
  if (platform !== 'darwin') return { options: {}, arguments: [] };
  return { options: { titleBarStyle: 'hidden', trafficLightPosition: TRAFFIC_LIGHTS }, arguments: [MAC_CHROME_ARGUMENT] };
}

// Dev-only QA mode for running the harness on a machine someone is using: windows are invisible,
// click-through and never take focus; the app is an accessory (no Dock, no activation); and
// notifications stay off. Never true in a packaged build.
export function qaInactiveMode(isPackaged: boolean, env: NodeJS.ProcessEnv): boolean {
  return !isPackaged && env.ONW_QA_MODE === '1' && env.ONW_QA_INACTIVE === '1';
}
