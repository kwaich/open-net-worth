import { isStandalonePwa } from './pwa'

/** Install a .shortcut file without stranding the app.
 *
 *  In a browser tab the file opens in place and iOS hands it to Shortcuts. The home-screen app
 *  can't do that itself (all seen on device, 2026-10-13): navigating to the file strands the app
 *  on iOS's file preview, the in-app browser sheet renders it blank, a frame fetches it and shows
 *  nothing, Shortcuts' import link takes only iCloud URLs, and the share sheet lists no Shortcuts.
 *  So there it opens in Safari proper (`x-safari-https:`), which does the same as a tab; the
 *  status bar's "◀ app" link returns. */
export function openShortcutInstall(href: string): void {
  if (!isStandalonePwa()) {
    window.location.assign(href)
    return
  }
  const url = new URL(href, window.location.origin)
  window.location.href = `x-safari-${url.toString()}`
}
