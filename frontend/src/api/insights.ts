// Insights / self-improvement read API (P3-SI1).
//
// fetchForecastScorecard hits GET /api/forecast-scorecard?window= — the
// forecast-vs-actuals scorecard (did our cash-flow + net-worth projections
// match reality?). ALL math (the replayed projection, per-month deltas, the
// accuracy/MAPE/bias stats, FX normalization, the data-quality gate) lives in
// services.forecast_scorecard; this wrapper just shapes the request + response.

import { authFetch } from './client'

async function asJson<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const detail = await r.text().catch(() => '')
    throw new Error(detail || `HTTP ${r.status}`)
  }
  return r.json() as Promise<T>
}

export type ScorecardWindow = '1M' | '3M' | '6M' | '1Y'

export interface ScorecardPeriod {
  year_month: string
  label: string
  cashflow_projected: number
  cashflow_actual: number
  cashflow_delta: number
  net_worth_actual: number
  net_worth_projected: number | null
  net_worth_delta: number | null
}

export interface ScorecardAccuracy {
  mean_abs_error: number
  mean_abs_pct_error: number | null
  bias: number
  periods_scored: number
}

export interface ForecastScorecard {
  window: ScorecardWindow
  base_currency: string
  forecastable: boolean
  projected_monthly_net: number
  projected_monthly_income: number | null
  projected_monthly_expenses: number | null
  periods: ScorecardPeriod[]
  cashflow_accuracy: ScorecardAccuracy
  net_worth_accuracy: ScorecardAccuracy
  data_quality: {
    has_baseline: boolean
    periods_available: number
    net_worth_anchored: boolean
  }
}

/** Forecast-vs-actuals scorecard for the trailing `window` (default 3M). */
export async function fetchForecastScorecard(
  window: ScorecardWindow = '3M',
): Promise<ForecastScorecard> {
  return asJson<ForecastScorecard>(
    await authFetch(`/api/forecast-scorecard?window=${encodeURIComponent(window)}`),
  )
}
