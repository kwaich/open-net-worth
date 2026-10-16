// Widget data access — each widget asks for the resource IT renders.
//
// This replaced `OverviewDataContext`, a provider that wrapped the whole grid and eagerly
// fetched the portfolio, the net-worth series and commitments before knowing which widgets were
// mounted. Two things were wrong with that on a user-composable dashboard:
//
//   1. A dashboard holding only the coffee widget still paid for a live-priced portfolio
//      projection — data nothing on screen rendered.
//   2. It made widgets non-standalone. A widget that needs a provider cannot be dropped onto
//      another view, and the picker exists precisely so users can drop widgets anywhere.
//
// The cache is what makes per-widget fetching cheap: `state/queryCache` dedups by URL
// (`revalidate()` returns the in-flight promise), so twenty widgets sharing one resource still
// cost ONE request — and a dashboard that mounts none of them costs nothing.
//
// These wrappers exist only to give widgets a stable, well-named seam and to keep the loading/
// error conventions identical across the group. They add no fetching of their own.

import { useMemo } from 'react'
import {
  useCommitmentsCoverageRead,
  useCommitmentsSummary,
  useNetWorth,
  useNetWorthStats,
  usePortfolioAnalytics,
} from '../../../hooks/serverState'
import { usePortfolioContext } from '../../../state/portfolio'
import type { ComputedRow } from '../../../types/portfolio'
import type {
  CardBill,
  ExpectedIncome,
  NetWorthStats,
  PortfolioAnalytics,
} from '../../../types/aggregates'
import type { PortfolioCoverage, UpcomingEvent } from '../../../types/commitments'

export interface AnalyticsAccess {
  /** Views derived from the portfolio. `null` until the first fetch resolves. */
  analytics: PortfolioAnalytics | null
  loading: boolean
  error: boolean
  /** Net worth — from its ONE endpoint, `GET /api/net-worth`. Deliberately NOT taken off the
   *  analytics payload: a second path to the same number is a second chance to render a
   *  different one, and it did — the Overview read a live-priced figure while the Accounts
   *  header read a last-known one, so the two tabs disagreed on screen. */
  netWorth: number | null
}

/** The derived portfolio views: allocation, reach, venues, KPIs, risk flags, treemap, drift,
 *  the invested book. Widgets that also need row identity call `useValuedRows()` alongside;
 *  both are cache-deduped, so this is two requests for the tab, not two per widget. */
export function useAnalytics(): AnalyticsAccess {
  const query = usePortfolioAnalytics()
  const netWorthQuery = useNetWorth()
  const analytics = query.data ?? null
  return {
    analytics,
    loading: query.initialLoading || netWorthQuery.initialLoading,
    error: !!query.error && analytics == null,
    netWorth: netWorthQuery.data?.net_worth ?? null,
  }
}

/** The valued holdings themselves, for widgets that render a LIST and need row identity
 *  (movers, unrealized gains, the treemap). Money on these rows is server-computed; sort,
 *  filter and label them, never do arithmetic on them.
 *
 *  Reads the shell-wide `PortfolioProvider` rather than re-fetching: rows are needed on every
 *  tab (Intel, Lucius, the palette), so the shell already holds them. */
export function useValuedRows(): { rows: ComputedRow[]; byKey: Map<string, ComputedRow> } {
  const { rows } = usePortfolioContext()
  const byKey = useMemo(() => {
    const map = new Map<string, ComputedRow>()
    for (const r of rows) {
      const key = r.position_id || r.instrument_id || r.label || ''
      if (key && !map.has(key)) map.set(key, r)
    }
    return map
  }, [rows])
  return { rows, byKey }
}

export interface NetWorthSeriesAccess {
  stats: NetWorthStats | null
  history: NetWorthStats['history']
  loading: boolean
}

/** The monthly net-worth series plus its server-computed range deltas and month-to-date
 *  change. Only the chart/headline widgets pay for this. */
export function useNetWorthSeries(): NetWorthSeriesAccess {
  const query = useNetWorthStats()
  const stats = query.data ?? null
  return { stats, history: stats?.history ?? [], loading: query.initialLoading }
}

export interface CommitmentsAccess {
  /** True until the first read resolves — an empty `upcoming` before then means "not yet". */
  loading: boolean
  upcoming: UpcomingEvent[]
  /** Total going out within the next 30 days — summed server-side over the same feed. */
  stripTotal: number
  /** Past-due outgoing items and their total — server-summed, funding inflows excluded. */
  overdueCount: number
  overdueTotal: number
  /** Recurring income expected inside the same 30 days (a declared salary, say). */
  expectedIncome: ExpectedIncome[]
  expectedIncomeTotal: number
  /** Recurring outflows and card statements due in the same 30 days. */
  bills: ExpectedIncome[]
  cardBills: CardBill[]
  /** Unpaid card statements due within a week or overdue (Needs attention). */
  cardsDueSoon: CardBill[]
  /** Commitments due + bills + card bills — server-summed. */
  goingOutTotal: number
}

/** The commitments feed + its window totals. */
export function useCommitments(): CommitmentsAccess {
  const query = useCommitmentsSummary()
  return {
    loading: query.initialLoading,
    upcoming: query.data?.events ?? [],
    stripTotal: query.data?.strip_total_base ?? 0,
    overdueCount: query.data?.overdue_count ?? 0,
    overdueTotal: query.data?.overdue_total_base ?? 0,
    expectedIncome: query.data?.expected_income ?? [],
    expectedIncomeTotal: query.data?.expected_income_total_base ?? 0,
    bills: query.data?.bills ?? [],
    cardBills: query.data?.card_bills ?? [],
    cardsDueSoon: query.data?.cards_due_soon ?? [],
    goingOutTotal: query.data?.going_out_total_base ?? query.data?.strip_total_base ?? 0,
  }
}

export interface FundingCoverageAccess {
  loading: boolean
  coverage: PortfolioCoverage | null
}

/** The portfolio funding verdict the risk flags read — the same cache entry (and so the same
 *  object, the same shortfall) the Commitments masthead reads. */
export function useFundingCoverage(): FundingCoverageAccess {
  const query = useCommitmentsCoverageRead()
  return { loading: query.initialLoading, coverage: query.data ?? null }
}
