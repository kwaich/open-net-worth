export type PartitionPolicy = Pick<Electron.Session, 'setPermissionRequestHandler' | 'setPermissionCheckHandler'> & { webRequest: Pick<Electron.WebRequest, 'onBeforeRequest' | 'onBeforeSendHeaders' | 'onHeadersReceived'> };

// The partition is shared with the window that last showed this Private Cloud. When that window
// switched back to a local book it cancelled every request here (WindowSession.detachSessionPolicy),
// so a move would fail before reaching the agent. Claim it: only the agent's own origin, no
// permissions. A window opened on the agent later installs its own policy over this one.
export function claimPartition(partition: PartitionPolicy, origin: string): void {
  partition.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  partition.setPermissionCheckHandler(() => false);
  partition.webRequest.onBeforeRequest((details, callback) => {
    let allowed = false;
    try { allowed = new URL(details.url).origin === origin; } catch { allowed = false; }
    callback({ cancel: !allowed });
  });
  partition.webRequest.onBeforeSendHeaders(null);
  partition.webRequest.onHeadersReceived(null);
}
