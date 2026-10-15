// scenarioOdds.ts — LIVE shelf odds for saved scenarios.
//
// A scenario's stored odds/successPct is a snapshot from save time — it bakes in the net worth
// and age of THAT day and silently goes stale as both move. The shelf obeys this tab's
// one-engine rule: GET /api/plan/scenario-odds runs every saved scenario through the SAME
// backend engine and paths as the headline (a recalled scenario's shelf badge IS its headline),
// and the stored value is only the bootstrap shown until the first live number lands (and what
// headless/AI readers of plan_state see).

import { useEffect, useState } from 'react'
import { fetchScenarioOdds } from './outlookApi'
import type { SavedScenario } from './usePlanState'

/** Live odds per scenario id, re-read whenever the scenarios or `contextKey` (the saved plan
 *  and the book the server resolves them against) change. */
export function useScenarioOdds(
  scenarios: SavedScenario[],
  contextKey: string,
  enabled: boolean,
): Record<string, number> {
  const [odds, setOdds] = useState<Record<string, number>>({})
  const key = JSON.stringify([
    scenarios.map((s) => [
      s.id,
      s.saving,
      s.ret,
      s.vol,
      s.monthlyRetirementSpending,
      s.swr,
      s.lifeExp,
      s.retireAge,
      s.atNumber,
      s.streams,
      s.events,
      s.crash,
      s.weakSeq,
      s.lostDec,
      s.highInfl,
      s.shockCfg,
    ]),
    contextKey,
  ])
  const hasScenarios = scenarios.length > 0

  useEffect(() => {
    if (!enabled || !hasScenarios) return
    let cancelled = false
    // Debounce: batch bursts (mount + fire-config + refetch land together) and let the
    // interactive fan go first.
    const timer = setTimeout(() => {
      fetchScenarioOdds()
        .then((next) => {
          if (!cancelled) setOdds(next)
        })
        .catch(() => undefined)
    }, 800)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [enabled, hasScenarios, key])

  return odds
}
