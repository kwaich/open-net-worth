// Platform-correct glyphs for the modifier shown beside a shortcut ("⌘ B" on a Mac, "Ctrl B"
// elsewhere). `Mod` is the platform's primary modifier.
export function isMacPlatform(): boolean {
  return typeof navigator !== 'undefined' && /Mac|iPhone|iPad/i.test(navigator.platform || '')
}

export function shortcutKeys(keys: readonly string[]): string[] {
  const mac = isMacPlatform()
  const glyphs: Record<string, string> = mac ? { Mod: '⌘', Shift: '⇧', Alt: '⌥' } : { Mod: 'Ctrl' }
  return keys.map((key) => glyphs[key] ?? key)
}

/** Cmd/Ctrl+Shift+/ (Cmd+? on a US layout) opens the keyboard-shortcuts panel. Cmd+/ alone is
 *  taken: it toggles the Lucius panel. */
export function isShortcutsKey(e: KeyboardEvent): boolean {
  return (e.metaKey || e.ctrlKey) && !e.altKey && e.shiftKey && (e.key === '/' || e.key === '?')
}

/** Cmd/Ctrl+B toggles the sidebar, except inside rich text where it means bold. */
export function isSidebarToggleKey(e: KeyboardEvent): boolean {
  return (
    (e.metaKey || e.ctrlKey) &&
    !e.shiftKey &&
    !e.altKey &&
    e.key.toLowerCase() === 'b' &&
    !(e.target as HTMLElement | null)?.isContentEditable
  )
}
