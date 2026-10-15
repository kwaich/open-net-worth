// useMentionables — local-first mentionable entity search for the @ picker.
//
// Returns candidates immediately from the live DOM harvest, then merges a debounced
// backend response on top. DOM rows may carry a data-entity-sublabel for an instant
// rich second line (payee · account · date · amount); the backend sublabel always
// wins when it arrives (base-currency, authoritative).
//
// NOT a client-side "recently used" cache — deliberately removed (owner decision
// 2026-08-11). It was already dead code in practice (nothing called
// pushRecentMention outside its own tests), but its localStorage entries from
// whenever it WAS wired up outlived the entities they pointed to forever, with no
// server-side connection to invalidate them: a picked mention survived the
// underlying entity being deleted/merged away server-side and kept resurfacing in
// every search matching its stale cached label — indefinitely, since nothing ever
// re-validated a browser's local cache against the server. Simplest fix that can't
// regress: there is no cache to go stale. DOM harvest is inherently always current,
// since it only ever reads what's actually rendered right now.
//
// Extraction-readiness (§14.8 R-EX7): the core logic lives in framework-free modules:
//   lucius/harvest.ts    — harvestDOM
//   lucius/mergeRows.ts  — dedupeRows, filterRows
//   lucius/adapters.ts   — DataSourceAdapter (the seam)
// This hook is a thin React wrapper that wires them together.

import { useEffect, useMemo, useRef, useState } from 'react'
import { getDataSource } from '../lucius/adapters'
import { harvestDOM } from '../lucius/harvest'
import { dedupeRows, filterRows } from '../lucius/mergeRows'

// MentionRow is declared in the core type module (lucius/mentionRow.ts) to break the
// core→hook import cycle. Re-export here for backward compat — callers importing
// MentionRow from this hook do not need to change.
export type { MentionRow } from '../lucius/mentionRow'
import type { MentionRow } from '../lucius/mentionRow'

// ── Hook ────────────────────────────────────────────────────────────────────

interface Options {
  surfaces?: string[]
  types?: string[]
}

export interface UseMentionablesResult {
  /** Merged local + backend rows (deduplicated, local pinned first). */
  items: MentionRow[]
  /** True while the debounced backend fetch is in flight. */
  loading: boolean
  /** True when the backend fetch failed (network error or non-ok status). */
  error: boolean
  /** Re-runs the last backend search immediately (resets the error state). */
  retry: () => void
  /** Backend-only rows that are NOT already represented in local (DOM harvest).
   *  These are the candidates for the "Everywhere" group in the picker. */
  remoteItems: MentionRow[]
}

export function useMentionables(query: string, opts: Options = {}): UseMentionablesResult {
  // Re-harvest the DOM on `lx-rescan` — the same event RouterShell dispatches on every
  // tab/route change (rAF + a 260ms settle) so ScreenContextBar's "Screen awareness"
  // label stays current. Without this, `localRows` was captured ONCE, at whatever
  // moment this hook's Composer instance first mounted, and never recomputed — so the
  // popover kept showing "on screen" rows from an earlier tab (owner report
  // 2026-08-11) even though the composer itself never unmounts across navigation and
  // the screen-awareness label right next to it updates correctly.
  const [rescanTick, setRescanTick] = useState(0)
  useEffect(() => {
    const bump = () => setRescanTick((t) => t + 1)
    window.addEventListener('lx-rescan', bump)
    return () => window.removeEventListener('lx-rescan', bump)
  }, [])

  // Local rows are synchronously derived — no state needed for them beyond the tick above.
  const localRows = useMemo(() => harvestDOM(), [rescanTick])

  // Filter local rows immediately on every query change; attach hint when present.
  const filteredLocal = useMemo(() => filterRows(localRows, query), [localRows, query])

  const [backendRows, setBackendRows] = useState<MentionRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Snapshot of the last query that was actually fetched — used by retry().
  const lastQueryRef = useRef<string>(query)

  const { surfaces, types } = opts
  const surfaceKey = surfaces?.join(',') ?? ''
  const typeKey = types?.join(',') ?? ''

  const runFetch = useRef<(term: string) => void>(() => {})

  // Stable fetch runner — called by the debounced effect and retry().
  runFetch.current = (term: string) => {
    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac
    lastQueryRef.current = term

    setLoading(true)
    setError(false)

    const dataSource = getDataSource()
    if (!dataSource) {
      setBackendRows([])
      setLoading(false)
      return
    }

    dataSource
      .search({
        q: term,
        surfaces: surfaceKey ? surfaceKey.split(',') : undefined,
        types: typeKey ? typeKey.split(',') : undefined,
        limit: 40,
        signal: ac.signal,
      })
      .then(({ items }) => {
        if (ac.signal.aborted) return
        setBackendRows(items)
        setLoading(false)
      })
      .catch((err: unknown) => {
        if ((err as { name?: string }).name === 'AbortError') return
        setBackendRows([])
        setError(true)
        setLoading(false)
      })
  }

  useEffect(() => {
    // Cancel any in-flight request and pending timer.
    if (timerRef.current) clearTimeout(timerRef.current)
    abortRef.current?.abort()

    setLoading(true)
    setError(false)

    timerRef.current = setTimeout(() => runFetch.current(query), 150)

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
      abortRef.current?.abort()
    }
  }, [query, surfaceKey, typeKey])

  const retry = useRef(() => {
    if (timerRef.current) clearTimeout(timerRef.current)
    runFetch.current(lastQueryRef.current)
  })

  // Merge: local rows pinned first, backend appended (deduped, sublabel-enriched).
  const { items, remoteItems } = useMemo(() => {
    const filtered = filterRows(backendRows, query)
    const merged = dedupeRows(filteredLocal, filtered)

    // Identify local keys so we can find which backend rows are remote-only.
    const localKeys = new Set(filteredLocal.map((r) => `${r.type}:${r.value}`))
    const remoteItems = filtered.filter((r) => !localKeys.has(`${r.type}:${r.value}`))

    return { items: merged, remoteItems }
  }, [filteredLocal, backendRows, query])

  return { items, loading, error, retry: retry.current, remoteItems }
}
