// URL slug ↔ tab id mapping — the single source of truth for which URL segment
// corresponds to which tab id. Tab ids (used throughout the codebase) are kept
// stable; only this module translates them to/from URL slugs.
//
// SLUG_MAP: tab id → URL slug (what appears in the hash URL)
// SLUG_ALIASES: old slug → canonical slug (for backward-compat redirects)

import type { TabId } from './tabs'

// Every tab id now equals its own URL slug (2026-09-06 id/label cleanup — 'cash', 'overview',
// 'transactions', 'integrations' and 'builder' were renamed to match what they'd always shown:
// 'accounts', 'dashboard', 'plan', 'connectors', 'build'). Kept as a real (if currently empty)
// map, not deleted, so a future tab needing a slug that diverges from its id has somewhere to
// go without re-inventing this module.
export const SLUG_MAP: Partial<Record<TabId, string>> = {}

// Inverts SLUG_MAP at module load — slug → tab id (for tab ids that have a
// non-default mapping). Tabs not in SLUG_MAP use their id as the slug.
const REVERSE_SLUG_MAP: Record<string, TabId> = Object.fromEntries(
  Object.entries(SLUG_MAP).map(([tabId, slug]) => [slug, tabId as TabId]),
)

// Old URL PATH SEGMENTS that are now served at a different canonical slug — e.g. someone's
// browser bookmark is literally the path `/cash`. These keys are frozen historical URLs, NOT
// tab ids, so they are UNAFFECTED by the 2026-09-06 id rename above: `/cash` must keep
// redirecting to `/accounts` whatever the internal id is now called. Used in router.tsx to
// register redirect routes.
export const SLUG_ALIASES: Record<string, string> = {
  cash: 'accounts',
  transactions: 'plan',
  integrations: 'connectors',
}

/** Convert a tab id to its URL slug (e.g. 'accounts' → 'accounts'; identity today — see
 *  SLUG_MAP's own comment). */
export function tabToSlug(tabId: TabId): string {
  return SLUG_MAP[tabId] ?? tabId
}

/**
 * Convert a URL slug to a tab id.
 * Returns the slug itself if there is no mapping (every tab maps 1:1 today).
 */
export function slugToTab(slug: string): string {
  return REVERSE_SLUG_MAP[slug] ?? slug
}
