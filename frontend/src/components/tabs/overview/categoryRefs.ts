// A category setting stores ids (a rename never unhooks a widget), but a preset can only
// guess an id: the starter Subscriptions category's is stable, yet a book that deleted it,
// or made its own "Subscriptions" (an import, an older book), holds a different one. The
// widget and its settings editor both resolve through here, so they always agree.

export interface CategoryRef {
  id: string
  label?: string
}

/** The stored ids that still exist in this book; when none do, the category labelled
 * `fallbackLabel` (case-insensitive), if the setting names one and the book has it. */
export function resolveCategoryIds(
  stored: readonly string[],
  categories: readonly CategoryRef[],
  fallbackLabel?: string,
): string[] {
  const known = new Set(categories.map((c) => c.id))
  const kept = stored.filter((id) => known.has(id))
  if (kept.length || !fallbackLabel) return kept
  const want = fallbackLabel.trim().toLowerCase()
  const match = categories.find((c) => (c.label ?? '').trim().toLowerCase() === want)
  return match ? [match.id] : []
}
