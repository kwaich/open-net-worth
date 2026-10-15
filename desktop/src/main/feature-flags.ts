// Open Net Worth Private Cloud is not offered yet. Every desktop entry point (first-launch chooser,
// File menu, Connection dialog, sign-in IPC, onw:// deep links, a saved cloud session) reads this one
// switch; ONW_DESKTOP_PRIVATE_CLOUD=1 restores them all.
export const PRIVATE_CLOUD_ENABLED = process.env.ONW_DESKTOP_PRIVATE_CLOUD === '1';

export function assertPrivateCloudEnabled(enabled: boolean = PRIVATE_CLOUD_ENABLED): void {
  if (!enabled) throw new Error('Open Net Worth Private Cloud is not available yet.');
}

// Shown next to the version wherever the app names it (About panel). Not part of the version
// itself: update feeds compare plain numbers. Set to '' when the beta ends.
export const RELEASE_LABEL = 'Beta';
export const displayVersion = (version: string): string => (RELEASE_LABEL ? `${version} (${RELEASE_LABEL})` : version);
