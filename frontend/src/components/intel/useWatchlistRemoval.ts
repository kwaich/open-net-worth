import { authFetch } from '../../api/client'
import type { ConfirmSpec } from './ConfirmDialog'
import { resolveWatchlistItemDeleteScope, watchlistDeleteFailed } from './watchlistDelete'
import type { Watchlist, WatchlistItem } from './WatchlistsSection'

// DELETE a watchlist item and read the response instead of assuming success — authFetch
// never throws on a non-2xx status, and a scoped removal can come back 200 `ok:false`
// (I7 Blocker 1 / SHOULD-FIX: both used to be silently swallowed by the caller).
async function performWatchlistDelete(url: string): Promise<{ ok: boolean; message: string }> {
  const res = await authFetch(url, { method: 'DELETE' })
  let json: { ok?: boolean; detail?: string } | null = null
  try {
    json = await res.json()
  } catch {
    /* no body / not JSON */
  }
  if (!watchlistDeleteFailed(res.ok, json)) return { ok: true, message: '' }
  const message =
    (json && typeof json.detail === 'string' && json.detail) || 'Could not remove — try again.'
  return { ok: false, message }
}

function removalBody(item: WatchlistItem, scopeName: string | null): string {
  const n = item.watchlist_ids.length
  if (scopeName)
    return `Removes ${item.value} from “${scopeName}”. It stays in your other ${n - 1} watchlist${n - 1 === 1 ? '' : 's'} with its alerts intact.`
  const where = n > 1 ? `all ${n} of your watchlists` : 'your watchlist'
  return `Removes ${item.value} from ${where}. Its price alerts are kept — they live independently of watchlist membership.`
}

/** The two confirm-then-DELETE flows: removing a ticker (from one list, or all of them) and
 *  unfollowing a topic. A failure replaces the dialog with the server's reason. */
export function useWatchlistRemoval({
  watchlists,
  activeListId,
  setConfirm,
  onRemoved,
}: {
  watchlists: Watchlist[]
  activeListId: string
  setConfirm: (spec: ConfirmSpec | null) => void
  onRemoved: () => void
}) {
  function confirmDelete(spec: Omit<ConfirmSpec, 'onConfirm'>, url: string, failTitle: string) {
    setConfirm({
      ...spec,
      onConfirm: async () => {
        const result = await performWatchlistDelete(url)
        if (!result.ok) {
          setConfirm({
            title: failTitle,
            body: result.message,
            confirmLabel: 'OK',
            onConfirm: () => setConfirm(null),
          })
          return
        }
        setConfirm(null)
        onRemoved()
      },
    })
  }

  function requestRemoveTicker(item: WatchlistItem, sourceListId?: string) {
    const scope = resolveWatchlistItemDeleteScope(item.watchlist_ids, sourceListId, activeListId)
    const scopeName = scope
      ? (watchlists.find((w) => w.id === scope)?.name ?? 'this watchlist')
      : null
    const url = scope
      ? `/api/watchlist/items/${item.id}?watchlist_id=${encodeURIComponent(scope)}`
      : `/api/watchlist/items/${item.id}`
    confirmDelete(
      {
        title: `Remove ${item.value}?`,
        body: removalBody(item, scopeName),
        confirmLabel: 'Remove',
      },
      url,
      'Could not remove',
    )
  }

  // A topic is followed, in no list (ADR 2026-09-27): removing it just stops following it.
  function requestUnfollowTopic(item: WatchlistItem) {
    confirmDelete(
      {
        title: 'Stop following this topic?',
        body: `Lucius will stop scanning news for “${item.label || item.value}”.`,
        confirmLabel: 'Stop following',
      },
      `/api/watchlist/items/${item.id}`,
      'Could not stop following',
    )
  }

  return { requestRemoveTicker, requestUnfollowTopic }
}
