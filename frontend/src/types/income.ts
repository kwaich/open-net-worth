// Dividend calendar types (ADR 0020). Kept in a dedicated module (not portfolio.ts, which is
// under concurrent edit) — the existing IncomeSummary/IncomeForecast still live in portfolio.ts
// and are imported from there.

/** One projected upcoming dividend payment for a holding. */
export interface IncomeCalendarRow {
  position_id: string
  instrument_id: string
  symbol: string
  name: string
  ex_date: string // ISO YYYY-MM-DD
  pay_date: string // ISO YYYY-MM-DD
  per_share: number
  currency: string
  amount_base: number
  // "scheduled" = a Yahoo-declared ex-date or bond coupon; "estimated" = projected from history.
  status: 'scheduled' | 'estimated'
  // Payment type: dividends from equities/funds; coupon from bond periodic payments;
  // principal from bond maturity redemption.
  kind: 'dividend' | 'coupon' | 'principal'
  // True on the final coupon row when a principal redemption also falls within the horizon.
  principal_at_maturity: boolean
}

/** GET /api/income/calendar response — upcoming payments within the horizon. */
export interface IncomeCalendar {
  base_currency: string
  horizon_days: number
  payments: IncomeCalendarRow[]
  count: number
  /** Income total (coupons + dividends) — excludes principal redemptions (return-of-capital). */
  total_base: number
  /** Principal redemption total within the horizon (separate from income). */
  principal_base?: number
  next_payment: IncomeCalendarRow | null
}
