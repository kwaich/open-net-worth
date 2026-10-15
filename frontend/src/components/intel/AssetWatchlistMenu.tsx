import { useEffect, useRef, useState } from 'react'
import { Button } from '../ui'
import { authFetch } from '../../api/client'
import { Check, ChevronDown, Plus, Star } from '../../ui/icons'
import type { Watchlist, WatchlistItem } from './WatchlistsSection'

interface Props {
  ticker: string
  assetClass: string
  /** GET-only refetch of the asset detail after membership changes (so the alerts card
   *  picks up the new watchlist item) — never re-runs the news/LLM brief. Fired on close
   *  if anything changed. Mirrors the old toggleFollow refetch. */
  onChanged: () => void
}

interface ListState {
  id: string
  name: string
  count: number
  member: boolean
  is_auto_sync: boolean
}

// Replaces the single ★ Follow toggle. The app has many named watchlists and a ticker can
// belong to several at once, so this adds/removes the asset PER LIST — add appends one list
// to the item's watchlist_ids; remove drops just that list. When removing from the last list
// the watchlist ITEM is deleted, but price alerts live independently in the alert store
// (alerts.json) and are never touched by membership changes.
// Membership snapshot is fetched once on mount for the count badge.
export function AssetWatchlistMenu({ ticker, assetClass, onChanged }: Props) {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [busy, setBusy] = useState(false)
  const [lists, setLists] = useState<ListState[]>([])
  const itemIdRef = useRef<string | null>(null)
  const changedRef = useRef(false)

  // Fetch the membership snapshot once per ticker (drives the count badge + the menu rows).
  // State is set only in the async continuation — never synchronously in the effect body.
  useEffect(() => {
    let alive = true
    authFetch('/api/watchlist')
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
      .then((wl: { watchlists: Watchlist[]; items: WatchlistItem[] }) => {
        if (!alive) return
        const mine = (wl.items ?? []).find(
          (i) => i.type === 'ticker' && i.value.toUpperCase() === ticker.toUpperCase(),
        )
        itemIdRef.current = mine?.id ?? null
        const memberIds = new Set(mine?.watchlist_ids ?? [])
        const count = (id: string) =>
          (wl.items ?? []).filter((i) => (i.watchlist_ids ?? []).includes(id)).length
        setLists(
          [...(wl.watchlists ?? [])]
            .sort((a, b) => a.order - b.order)
            .map((w) => ({
              id: w.id,
              name: w.name,
              count: count(w.id),
              member: memberIds.has(w.id),
              is_auto_sync: !!w.is_auto_sync,
            })),
        )
        setLoading(false)
      })
      .catch(() => {
        if (alive) {
          setError(true)
          setLoading(false)
        }
      })
    return () => {
      alive = false
    }
  }, [ticker])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && close()
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  function close() {
    setOpen(false)
    if (changedRef.current) {
      changedRef.current = false
      onChanged()
    }
  }

  // Optimistic: flip the row, fire the request, revert on failure. itemId is the single
  // watchlist item for this ticker; a fresh add (or re-add after the last list was removed)
  // returns the canonical item id, so we capture it from the POST response.
  async function toggle(list: ListState) {
    if (busy) return
    setBusy(true)
    const adding = !list.member
    setLists((ls) =>
      ls.map((l) =>
        l.id === list.id ? { ...l, member: adding, count: l.count + (adding ? 1 : -1) } : l,
      ),
    )
    try {
      if (adding) {
        const res = await authFetch('/api/watchlist/items', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            type: 'ticker',
            value: ticker,
            asset_class: assetClass,
            watchlist: list.id,
          }),
        })
        if (!res.ok && res.status !== 409) throw new Error('add failed')
        const item = await res.json().catch(() => null)
        if (item?.id) itemIdRef.current = item.id
      } else {
        const id = itemIdRef.current
        if (!id) throw new Error('no item id')
        const res = await authFetch(
          `/api/watchlist/items/${id}?watchlist_id=${encodeURIComponent(list.id)}`,
          { method: 'DELETE' },
        )
        if (!res.ok) throw new Error('remove failed')
      }
      changedRef.current = true
    } catch {
      // revert the optimistic flip
      setLists((ls) =>
        ls.map((l) =>
          l.id === list.id ? { ...l, member: !adding, count: l.count + (adding ? -1 : 1) } : l,
        ),
      )
    } finally {
      setBusy(false)
    }
  }

  async function createAndAdd() {
    if (busy) return
    setBusy(true)
    try {
      const res = await authFetch('/api/watchlists', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: 'New watchlist' }),
      })
      if (!res.ok) return
      const wl = await res.json()
      if (!wl?.id) return
      const addRes = await authFetch('/api/watchlist/items', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          type: 'ticker',
          value: ticker,
          asset_class: assetClass,
          watchlist: wl.id,
        }),
      })
      const item = await addRes.json().catch(() => null)
      if (item?.id) itemIdRef.current = item.id
      setLists((ls) => [
        ...ls,
        {
          id: wl.id,
          name: wl.name || 'New watchlist',
          count: 1,
          member: true,
          is_auto_sync: false,
        },
      ])
      changedRef.current = true
    } finally {
      setBusy(false)
    }
  }

  const inCount = lists.filter((l) => l.member).length

  return (
    <div className="asd-wl">
      <Button
        variant={inCount > 0 ? 'secondary' : 'ghost'}
        size="sm"
        onClick={() => (open ? close() : setOpen(true))}
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label="Watchlists"
        // Filled once the asset is actually saved somewhere — the common star convention
        // (owner ask 2026-08-31: "'save' [Bookmark] is not a proper icon for watch").
        iconLeft={<Star size={14} fill={inCount > 0 ? 'currentColor' : 'none'} />}
      >
        {/* Text AND the count badge collapse at narrow widths (bare icon + chevron stay) —
            same treatment as the Refresh button (owner ask 2026-08-31: "let's make
            watchlist become an icon too on mobile" / "on mobile ... let's not show 'n'
            for watchlist, just show the icon"). */}
        <span className="asd-wl-label">Watchlists</span>
        {inCount > 0 && <span className="asd-wl-badge">{inCount}</span>}
        <ChevronDown size={13} className="asd-wl-caret" />
      </Button>
      {open && (
        <>
          <div className="asd-wl-overlay" onClick={close} />
          <div className="asd-wl-menu" role="menu">
            <div className="asd-wl-head">Add to watchlists</div>
            {loading && <div className="asd-wl-msg">Loading…</div>}
            {error && !loading && <div className="asd-wl-msg">Couldn’t load watchlists.</div>}
            {!loading && !error && lists.length === 0 && (
              <div className="asd-wl-msg">No watchlists yet.</div>
            )}
            {!loading &&
              !error &&
              lists.map((l) => (
                <button
                  key={l.id}
                  type="button"
                  className="asd-wl-row"
                  role="menuitemcheckbox"
                  aria-checked={l.member}
                  disabled={busy || l.is_auto_sync}
                  title={l.is_auto_sync ? 'Auto-managed — mirrors your holdings' : undefined}
                  onClick={() => {
                    if (!l.is_auto_sync) void toggle(l)
                  }}
                >
                  <span className={`asd-wl-check${l.member ? ' asd-wl-check--on' : ''}`}>
                    {l.member && <Check size={11} />}
                  </span>
                  <span className="asd-wl-nm">{l.name}</span>
                  <span className="asd-wl-ct">{l.count}</span>
                </button>
              ))}
            {!loading && !error && (
              <>
                <div className="asd-wl-sep" />
                <button
                  type="button"
                  className="asd-wl-new"
                  disabled={busy}
                  onClick={() => void createAndAdd()}
                >
                  <Plus size={13} /> New watchlist
                </button>
              </>
            )}
          </div>
        </>
      )}
    </div>
  )
}
