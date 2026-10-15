// plan/outlookApi.ts — the Outlook's ONE source of numbers: the backend retirement engine.
//
// The Monte Carlo no longer runs in the browser. Every figure on this screen (the fan, the odds,
// the safe-to-retire age, the "+X/mo" hint, the shelf odds) comes from POST /api/plan/outlook ·
// /insights · GET /api/plan/scenario-odds — the same engine, the same random paths and the same
// saved-plan defaults `simulate_retirement` uses, so the screen and Lucius cannot disagree
// (ADR 2026-10-14-one-retirement-engine-saving-is-a-fact). A lever left out is the SAVED plan's
// value, which is how `{}` is the baseline "My FIRE plan".

import { authFetch } from '../../../api/client'
import type { LifeResult, OutcomeRow } from './planData'
import type { IncomeStream, LifeEvent, ShockCfg } from './usePlanState'

/** The Outlook's levers, in the vocabulary plan_state stores and Lucius's plan tools take. */
export interface OutlookLevers {
  saving?: number
  ret?: number
  vol?: number
  monthlyRetirementSpending?: number
  swr?: number
  lifeExp?: number
  retireAge?: number
  streams?: IncomeStream[]
  events?: LifeEvent[]
  crash?: boolean
  weakSeq?: boolean
  lostDec?: boolean
  highInfl?: boolean
  shockCfg?: ShockCfg
}

export interface OutlookInsights {
  earliest: { age: number; odds: number } | null
  extraSave: number | null
  gapSave: number | null
  numberAge: number | null
}

interface RawLifeResult {
  bands: LifeResult['bands']
  retire_year: number
  horizon_years: number
  n_paths: number
  success_probability: number
  depletion_probability: number
  nest_egg_at_retire: number
  nest_egg_band: LifeResult['nestEggBand']
  median_ending: number
  p10_ending: number
  p90_ending: number
  outcomes: OutcomeRow[] | null
}

interface RawInsights {
  earliest: { age: number; odds: number } | null
  extra_save: number | null
  gap_save: number | null
  number_age: number | null
}

async function post<T>(url: string, body: unknown): Promise<T> {
  const res = await authFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`POST ${url} → HTTP ${res.status}`)
  return (await res.json()) as T
}

/** The fan for these levers. `quick` runs fewer paths (the first rows of the same ones) while a
 *  slider is being dragged; `outcomes` adds the Rich/Broke/Dead split. */
export async function fetchOutlook(
  levers: OutlookLevers,
  opts: { quick?: boolean; outcomes?: boolean } = {},
): Promise<{ result: LifeResult; outcomes: OutcomeRow[] | null }> {
  const { result: raw } = await post<{ result: RawLifeResult }>('/api/plan/outlook', {
    levers,
    quick: opts.quick ?? false,
    outcomes: opts.outcomes ?? false,
  })
  return {
    result: {
      bands: raw.bands,
      retireYear: raw.retire_year,
      horizonYears: raw.horizon_years,
      nPaths: raw.n_paths,
      successProbability: raw.success_probability,
      depletionProbability: raw.depletion_probability,
      nestEggAtRetire: raw.nest_egg_at_retire,
      nestEggBand: raw.nest_egg_band,
      medianEnding: raw.median_ending,
      p10Ending: raw.p10_ending,
      p90Ending: raw.p90_ending,
    },
    outcomes: raw.outcomes,
  }
}

export async function fetchInsights(levers: OutlookLevers): Promise<OutlookInsights> {
  const raw = await post<RawInsights>('/api/plan/outlook/insights', { levers })
  return {
    earliest: raw.earliest,
    extraSave: raw.extra_save,
    gapSave: raw.gap_save,
    numberAge: raw.number_age,
  }
}

/** Whole-percent odds per saved scenario id, live (a stored figure is a save-time snapshot). */
export async function fetchScenarioOdds(): Promise<Record<string, number>> {
  const res = await authFetch('/api/plan/scenario-odds')
  if (!res.ok) throw new Error(`GET /api/plan/scenario-odds → HTTP ${res.status}`)
  return ((await res.json()) as { odds: Record<string, number> }).odds
}
