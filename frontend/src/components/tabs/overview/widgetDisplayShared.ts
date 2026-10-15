// Overview v2 — small constants shared between the two per-widget
// "Appearance" surfaces: the kebab-triggered `WidgetDisplayPanel` modal and
// the fullscreen detail view's Appearance rail (`WidgetFullscreen`, item 7).
// Split into its own module (rather than exported from WidgetDisplayPanel.tsx)
// so neither component file exports anything but its own component — mixing
// component + non-component exports from one file breaks React Fast Refresh.

export const CURATED_ACCENTS: Array<{ key: string; label: string; cssVar: string }> = [
  { key: 'blue', label: 'Blue', cssVar: 'var(--accent)' },
  { key: 'green', label: 'Green', cssVar: 'var(--good)' },
  { key: 'amber', label: 'Amber', cssVar: 'var(--warn)' },
  { key: 'violet', label: 'Violet', cssVar: 'var(--violet)' },
]

export const DEFAULT_CUSTOM_HEX = '#7c6cf5'

/** `instance.limit` value meaning "show every row". */
export const ALL_ROWS = 0

/** How many rows a list widget shows: the user's pick, else the def's default; null = no cap. */
export function rowCap(
  instance: { limit?: number | null },
  def: { rows?: { default: number | 'all' } } | undefined,
): number | null {
  const limit = instance.limit
  if (limit === ALL_ROWS) return null
  if (limit != null && limit > 0) return limit
  const d = def?.rows?.default
  return typeof d === 'number' ? d : null
}
