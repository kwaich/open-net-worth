// Detects the installed home-screen app (standalone display mode), as opposed to a regular
// browser tab — `display-mode: standalone` is the modern, cross-browser signal;
// `navigator.standalone` is iOS Safari's own older/more reliable flag for the same thing,
// checked as a fallback since not every iOS version reports `display-mode` accurately for
// home-screen web apps.
export function isStandalonePwa(): boolean {
  if (typeof window === 'undefined') return false
  const mediaMatch = window.matchMedia?.('(display-mode: standalone)').matches ?? false
  const iosFlag = (navigator as Navigator & { standalone?: boolean }).standalone === true
  return mediaMatch || iosFlag
}

export function stampPwaDisplayMode(): void {
  if (typeof document === 'undefined') return
  document.documentElement.dataset.onwDisplayMode = isStandalonePwa() ? 'standalone' : 'browser'
}
