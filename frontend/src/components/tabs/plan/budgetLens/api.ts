// budgetLens/api.ts — thin fetch wrappers over the pluggable-budget-engines REST surface
// (backend/api/planning.py, "Pluggable budget engines" section). One function per route; no
// caller-chosen fields/include param on any of them (CLAUDE.md — money-bearing reads never
// get a projection knob).
import { authFetch } from '../../../../api/client'
import { fetchJson } from '../../../../hooks/serverState'
import { invalidateQueryCache } from '../../../../state/queryCache'
import { emitDataChanged } from '../../../../lucius/bus'
import type { BudgetEnginesResponse, BudgetViewModel, GenericDraftResponse } from './types'

export const ENGINES_KEY = '/api/planning/engines'

export function viewKey(engineId: string, ym: string): string {
  return `/api/planning/view?engine=${encodeURIComponent(engineId)}&ym=${encodeURIComponent(ym)}`
}

export function fetchBudgetEngines(): Promise<BudgetEnginesResponse> {
  return fetchJson<BudgetEnginesResponse>(ENGINES_KEY)
}

export function fetchBudgetView(engineId: string, ym: string): Promise<BudgetViewModel> {
  return fetchJson<BudgetViewModel>(viewKey(engineId, ym))
}

/** Any engine's own draft() proposal — the setup-flow data source for Zero-Based's targets
 *  and for GenericEnginePane. */
export function fetchEngineDraft(engineId: string, ym: string): Promise<GenericDraftResponse> {
  return fetchJson<GenericDraftResponse>(
    `/api/planning/engines/${encodeURIComponent(engineId)}/draft?ym=${encodeURIComponent(ym)}`,
  )
}

/** POST /api/planning/verb — execute one declared write verb on any engine. The one write
 *  call GenericEnginePane needs: {engine_id, verb_id, params}, where verb_id/params are
 *  discovered from the engine's own descriptor (genericRows.ts), never hardcoded. */
export async function applyBudgetVerb(
  engineId: string,
  verbId: string,
  params: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const res = await authFetch('/api/planning/verb', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ engine_id: engineId, verb_id: verbId, params }),
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => null)
    throw new Error(
      typeof detail?.detail === 'string' ? detail.detail : `POST verb → HTTP ${res.status}`,
    )
  }
  return (await res.json()) as Record<string, unknown>
}

/** Promote a lens to the book's real budget. Non-destructive: RFC Decision A guarantees every
 *  other engine's state survives untouched, so this is reversible by switching back. */
export async function setHomeEngine(engineId: string): Promise<BudgetEnginesResponse> {
  const res = await authFetch('/api/planning/home-engine', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ engine_id: engineId }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

/** Persist that this book has seen the Plan -> Budget first-run introduction — it never
 *  reappears once dismissed (server-side, per-book; see BudgetLensIntroStore). */
export async function dismissBudgetLensIntro(): Promise<{ intro_dismissed: boolean }> {
  const res = await authFetch('/api/planning/budget-lens-intro/dismiss', { method: 'POST' })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export const PROFILE_KEY = '/api/profile'

/** The Profile — `/api/profile` is a shared cache key holding the WHOLE object (Settings and the
 *  chat panel write it back whole), so it is only ever read whole here too. 50/30/20 measures
 *  the current month against its `monthly_net_income`; Outlook and FIRE read the same field. */
export function fetchProfile(): Promise<{ monthly_net_income?: number | null }> {
  return fetchJson<{ monthly_net_income?: number | null }>(PROFILE_KEY)
}

/** Save the monthly net income to the Profile (a partial PUT — every other field untouched),
 *  then tell every Profile reader it changed. */
export async function saveMonthlyNetIncome(amount: number): Promise<void> {
  const res = await authFetch(PROFILE_KEY, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ monthly_net_income: amount }),
  })
  if (!res.ok) throw new Error('Could not save your income — try again.')
  invalidateQueryCache(PROFILE_KEY)
  emitDataChanged(['profile'])
}
