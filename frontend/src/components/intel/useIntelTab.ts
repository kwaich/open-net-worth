import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { INTEL_SUBTAB_KEY } from '../../hooks/useIntelStoryIndex'
import type { FilterId } from './IntelFeedPanel'
import { listIdOf, resolveTabKey, type IntelTabKey } from './intelTabs'
import type { Watchlist } from './WatchlistsSection'

// The last list tab you opened — where a legacy ?sub=watching link lands.
const WATCHLIST_PREF_KEY = 'intel-watchlist'

function load(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function save(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* per-viewer convenience only */
  }
}

// The open tab is remembered for the VISIT (sessionStorage): a fresh visit opens on Holdings
// (owner, 2026-10-10, the same rule as Plan, Investments and the Dashboard).
function loadVisitTab(): string | null {
  try {
    return sessionStorage.getItem(INTEL_SUBTAB_KEY)
  } catch {
    return null
  }
}

function rememberVisitTab(tab: string): void {
  try {
    sessionStorage.setItem(INTEL_SUBTAB_KEY, tab)
    localStorage.removeItem(INTEL_SUBTAB_KEY) // the old forever-memory, dropped on first use
  } catch {
    /* per-viewer convenience only */
  }
}

/** Intel's current tab, and the per-tab view state that resets with it (the type filter, the
 *  archive toggle). Mirrors Investments: per-visit fallback + ?sub= in the URL (replace, not
 *  push). The raw key is checked against the lists that exist once they load
 *  (`intelTabs.resolveTabKey`); `watchlists` is null until then. */
export function useIntelTab({
  watchlists,
  subTabRequest,
  monitorsOpen,
  backdrop,
}: {
  watchlists: Watchlist[] | null
  subTabRequest?: string
  monitorsOpen?: boolean
  /** Intel is only rendered behind Settings or Help, so the URL belongs to them. */
  backdrop?: boolean
}) {
  const navigate = useNavigate()
  const [tabRaw, setTabRaw] = useState<string>(() => loadVisitTab() || 'holdings')
  const [lastList, setLastList] = useState<string | null>(() => load(WATCHLIST_PREF_KEY))
  const [filter, setFilter] = useState<FilterId>('all')
  const [showArchive, setShowArchive] = useState(false)

  const show = useCallback((next: string, archive = false) => {
    setTabRaw(next)
    setFilter('all')
    setShowArchive(archive)
  }, [])
  // A tab switch starts that tab's feed clean — its own filters, never the archive view. A list
  // tab is also remembered as the last list you opened.
  const setTab = useCallback(
    (next: IntelTabKey) => {
      show(next)
      const id = listIdOf(next)
      if (!id) return
      setLastList(id)
      save(WATCHLIST_PREF_KEY, id)
    },
    [show],
  )

  // Deep link (?sub=list:…, or the legacy ?sub=watching) — applied once per distinct request
  // (adjusted during render, not in an effect), so manual tab clicks win after. Our own ?sub=
  // mirror re-requests the tab already shown, which is a no-op.
  const [lastSubReq, setLastSubReq] = useState<string | undefined>(undefined)
  if (subTabRequest && subTabRequest !== lastSubReq) {
    setLastSubReq(subTabRequest)
    if (resolveTabKey(subTabRequest, [], null) !== null && subTabRequest !== tabRaw) {
      show(subTabRequest)
    }
  }

  const tab: IntelTabKey = resolveTabKey(tabRaw, watchlists ?? [], lastList) ?? 'holdings'

  // Mirror the tab into ?sub= and the visit's memory once the lists have loaded (before that a list
  // tab can't be told from a deleted one). Never while /intel/monitors is open — navigating to
  // /intel would close the workspace — but again once it closes, so a list deleted there
  // doesn't leave a stale ?sub= behind. Never while Intel is only the backdrop behind Settings
  // or Help either: leaving /intel/monitors FOR Settings would otherwise bounce straight back.
  const listsLoaded = watchlists !== null
  useEffect(() => {
    if (!listsLoaded) return
    rememberVisitTab(tab)
    if (monitorsOpen || backdrop) return
    void navigate({
      to: '/intel',
      search: ((prev: Record<string, unknown>) => ({ ...prev, sub: tab })) as never,
      replace: true,
    } as never)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, listsLoaded, monitorsOpen, backdrop])

  return {
    tab,
    setTab,
    /** Open a tab (and its archive, for an archived story) without remembering it as a list. */
    jumpTo: show,
    filter,
    setFilter,
    showArchive,
    toggleArchive: useCallback(() => setShowArchive((v) => !v), []),
  }
}
