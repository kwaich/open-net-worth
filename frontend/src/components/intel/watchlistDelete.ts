/**
 * Decides whether removing a watchlist item (topic or ticker) should be SCOPED to one
 * watchlist or a FULL delete everywhere.
 *
 * `sourceListId` is the watchlist section the user actually clicked/opened the item
 * under — e.g. MonitorsWorkspace's All-monitors "Topics" tab groups rows under each
 * watchlist's own section header, and ListDetailView / the sidebar cockpit each render
 * exactly one watchlist's own items. When a genuine source is known it takes precedence
 * over `activeListId`, because `activeListId` is unrelated sidebar state — it tracks
 * whichever watchlist the sidebar switcher happens to be on, not which watchlist(s) the
 * tapped item actually belongs to or was opened from (I7 Blocker 2: a topic opened from
 * a DIFFERENT list's section than the sidebar's activeListId used to get removed from
 * the wrong list — silent wrong-target mutation).
 *
 * Falls back to the `activeListId`-based rule when no source is known (contexts with no
 * per-item list grouping, e.g. the ungrouped Price-alerts tab), and to a FULL delete
 * (`null`) when neither is a genuine multi-membership. Scoping a DELETE to a watchlist
 * the item isn't actually in is a silent no-op (I7).
 */
export function resolveWatchlistItemDeleteScope(
  memberIds: string[],
  sourceListId: string | undefined,
  activeListId: string,
): string | null {
  if (sourceListId && memberIds.length > 1 && memberIds.includes(sourceListId)) return sourceListId
  if (memberIds.length > 1 && memberIds.includes(activeListId)) return activeListId
  return null
}

export interface WatchlistDeleteApiBody {
  ok?: boolean
  detail?: string
}

/**
 * True when a watchlist item DELETE response must be surfaced to the user as a failure,
 * not silently swallowed. Two distinct lies the pre-I7 code used to let through: an
 * HTTP-level failure (e.g. a 409 — the item is locked into My holdings) and a 200 whose
 * body reports `ok: false` (a scoped removal that was actually a no-op). `authFetch`
 * never throws on a non-2xx response (see `api/client.ts`), so callers MUST check this
 * explicitly instead of assuming success once the request resolves.
 */
export function watchlistDeleteFailed(
  resOk: boolean,
  body: WatchlistDeleteApiBody | null,
): boolean {
  return !resOk || body?.ok === false
}
