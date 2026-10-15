// Domain types for the investment activity feed (Activity sub-tab).
// ActivityRow is a computed/derived shape only — never persisted.

export type ActivityType =
  | 'buy'
  | 'sell'
  | 'dividend'
  | 'coupon'
  | 'interest'
  | 'deposit'
  | 'withdrawal'
  // Shares moved to another custodian (a non-taxable disposal) — never a sale.
  | 'transfer'
  | 'vest'
  | 'fee'

export interface ActivityRow {
  date: string // ISO date YYYY-MM-DD
  type: ActivityType
  symbol: string | null
  name: string | null
  detail: string | null
  account_id: string
  account_name: string
  amount_base: number
  // False for vest and transfer rows (no cash movement; a vest's figure is the FMV).
  signed: boolean
  source_id: string
  source_kind: string
}

export interface ActivitySummary {
  count_30d: number
  net_invested_30d: number
  income_30d: number
  fees_30d: number
}

export interface ActivityFeed {
  events: ActivityRow[]
  summary: ActivitySummary
  base: string
}
