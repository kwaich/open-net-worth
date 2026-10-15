// plan/usePlanSubTab.ts — the active Plan sub-tab, mirrored into the URL (?sub=) and remembered
// for the visit (sessionStorage): within a visit Plan returns to the tab you were on, a fresh
// visit starts on Budget, Plan's home (owner, 2026-10-10: Outlook kept reopening forever).
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'

type PlanTab = 'budget' | 'cashflow' | 'outlook'
const isPlanTab = (s: unknown): s is PlanTab =>
  s === 'budget' || s === 'cashflow' || s === 'outlook'

const KEY = 'pl-subtab'
function remembered(): string | null {
  try {
    return sessionStorage.getItem(KEY)
  } catch {
    return null
  }
}
function remember(tab: PlanTab): void {
  try {
    sessionStorage.setItem(KEY, tab)
    localStorage.removeItem(KEY) // the old forever-memory, dropped on first use
  } catch {
    /* storage blocked: the tab simply isn't remembered */
  }
}

export function usePlanSubTab(
  subTabRequest: string | undefined,
): [PlanTab, (tab: PlanTab) => void] {
  const navigate = useNavigate()
  // A ?sub= link wins from the FIRST render. Starting on the remembered tab instead made the
  // mirror effect below write that tab over the link while the deep-link effect switched away
  // from it: each navigation then echoed back as a "new" request for the other tab, and the
  // two effects ping-ponged until React gave up ("Maximum update depth exceeded") — it
  // needed a full reload whose remembered tab differed from the link's.
  const [tab, setTab] = useState<PlanTab>(() => {
    const s = isPlanTab(subTabRequest) ? subTabRequest : remembered()
    return isPlanTab(s) ? s : 'budget'
  })

  // Mirror the active sub-tab into the URL (?sub=) on every change, not just localStorage —
  // otherwise the URL and the visible tab can silently disagree (owner incident: a "Manage in
  // Budget" link landed on Outlook because only localStorage remembered the last tab). replace:
  // true so casual tab-clicking doesn't spam browser history; the visit's memory is the fallback
  // for arriving at a bare /plan with no ?sub= (e.g. the sidebar nav icon).
  useEffect(() => {
    remember(tab)
    void navigate({
      to: '/plan',
      search: ((prev: Record<string, unknown>) => ({ ...prev, sub: tab })) as never,
      replace: true,
    } as never)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab])

  // Chip deep-link (?sub=outlook) — apply once per distinct request value so the
  // user can still switch sub-tabs manually afterwards.
  const lastSubReq = useRef<string | null>(null)
  useEffect(() => {
    if (!subTabRequest || lastSubReq.current === subTabRequest) return
    lastSubReq.current = subTabRequest
    // eslint-disable-next-line react-hooks/set-state-in-effect -- applying an external URL request
    if (isPlanTab(subTabRequest)) setTab(subTabRequest)
  }, [subTabRequest])

  return [tab, setTab]
}
