import { useCallback } from 'react'
import { authFetch } from '../api/client'
import { setCachedQueryData, useQueryCache } from '../state/queryCache'

const INBOX_CACHE_KEYS = ['inbox:unread', 'inbox:all'] as const

/** One normalized attention item. ALL kinds — intel / signal — come from ONE
 * backend read aggregator (GET /api/inbox → services/inbox.py), which folds every source
 * into this shape + orders them. */
export interface InboxItem {
  id: string
  kind: 'intel' | 'signal'
  title: string
  summary: string
  /** Full markdown report body, when the source has one; rendered with a markdown
   * renderer (not as raw text). Other kinds leave this undefined. */
  detail?: string
  status: string
  created_at: string
  source: string
  proposed_change: Record<string, unknown> | null
  ref: { id: string; tab: string }
  read: boolean
}

// The endpoint that clears a given item, by kind. Every inbox row is dismissible:
//   • signal       → mark the in-app notification read   (POST .../dismiss)
//   • intel        → delete the story arc                (DELETE /api/intel/signals/{id})
function dismissRequest(item: InboxItem): Promise<Response> {
  const id = encodeURIComponent(item.ref.id)
  switch (item.kind) {
    case 'intel':
      return authFetch(`/api/intel/signals/${id}`, { method: 'DELETE' })
    default:
      return authFetch(`/api/inbox/signals/${id}/dismiss`, { method: 'POST' })
  }
}

/**
 * useInbox — the unified attention feed, backed by ONE backend aggregator
 * (GET /api/inbox → services/inbox.py), which folds every source (intel + signals)
 * into one ordered, normalized list — no more client-side fetch+merge. Dismissing
 * is OPTIMISTIC (the row vanishes on tap) then reconciled against its per-kind ACTION
 * endpoint; a failed call triggers a reload so the UI never lies. Re-fetches when
 * `includeRead` flips (Unread ↔ All filter).
 */
export function useInbox(includeRead: boolean) {
  const cacheKey = `inbox:${includeRead ? 'all' : 'unread'}`
  const fetchInbox = useCallback(async () => {
    // ONE read: the aggregator orders the whole feed (actionable first). A failing
    // source degrades to empty server-side, never 500.
    const res = await authFetch(`/api/inbox?include_read=${includeRead}`)
    if (!res.ok) throw new Error(`Failed to load inbox (${res.status})`)
    const data = await res.json()
    return (data.items ?? []) as InboxItem[]
  }, [includeRead])
  const query = useQueryCache(cacheKey, fetchInbox, { staleTime: 0 })
  const items = query.data ?? null
  const load = query.refresh
  const patchItems = useCallback((update: (current: InboxItem[]) => InboxItem[]) => {
    for (const key of INBOX_CACHE_KEYS) {
      setCachedQueryData<InboxItem[]>(key, (current) => update(current ?? []))
    }
  }, [])

  // Dismiss ONE item. Optimistically drop the row (by its prefixed inbox id), then
  // hit the kind-appropriate endpoint; on failure, reload to resync the truth.
  const dismiss = useCallback(
    async (item: InboxItem) => {
      patchItems((current) => current.filter((it) => it.id !== item.id))
      try {
        const r = await dismissRequest(item)
        if (!r.ok) throw new Error(`dismiss failed (${r.status})`)
      } catch {
        await load()
      }
    },
    [load, patchItems],
  )

  // Clear every item currently shown. Optimistically empty the list, fire all the
  // dismiss calls, then reload to land on the server truth (e.g. a re-armed alert).
  const dismissAll = useCallback(async () => {
    const current = items ?? []
    if (current.length === 0) return
    patchItems(() => [])
    await Promise.allSettled(current.map((it) => dismissRequest(it)))
    await load()
  }, [items, load, patchItems])

  // Mark all unread items as read without deleting them.
  // For signal items, one batch call marks all signals read. For all other kinds,
  // "read" is modelled as dismiss (intel has a dismiss endpoint but no separate
  // mark-read endpoint).
  const markAllRead = useCallback(async () => {
    const current = items ?? []
    const unread = current.filter((it) => !it.read)
    if (unread.length === 0) return
    // Optimistically mark all items as read in local state.
    patchItems((currentItems) => currentItems.map((it) => (it.read ? it : { ...it, read: true })))
    const signals = unread.filter((it) => it.kind === 'signal')
    const others = unread.filter((it) => it.kind !== 'signal')
    const calls: Promise<unknown>[] = []
    if (signals.length > 0) {
      // Batch endpoint: marks every in-app signal read at once.
      calls.push(authFetch('/api/inbox/signals/dismiss-all', { method: 'POST' }))
    }
    for (const it of others) {
      calls.push(dismissRequest(it))
    }
    await Promise.allSettled(calls)
    await load()
  }, [items, load, patchItems])

  const error =
    query.data === undefined && query.error
      ? query.error instanceof Error
        ? query.error.message
        : 'Failed to load inbox'
      : null
  return {
    items,
    loading: query.initialLoading,
    error,
    reload: load,
    dismiss,
    dismissAll,
    markAllRead,
  }
}
