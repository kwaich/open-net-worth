// Types for financial commitments — dated obligations + funding plans.
// Mirrors backend/db/commitments_store.py. Coverage fields (Phase 2) are optional
// so the type stays valid before the funding engine ships.

export type CommitmentKind = 'purchase' | 'loan_repayment' | 'auto_pay' | 'goal'
export type CommitmentStatus = 'active' | 'completed' | 'cancelled'
export type InstallmentStatus = 'paid' | 'due' | 'upcoming'
export type FundingType = 'cash' | 'income' | 'asset_sale' | 'loan' | 'windfall'
export type ActionStatus = 'pending' | 'done' | 'missed'
export type FundingStatus = 'planned' | 'done'

export interface Installment {
  id: string
  label: string
  description: string
  due_date: string
  amount: number // native currency (commitment.currency)
  status: InstallmentStatus
  paid_txn_id: string | null
  // Phase 2 (optional):
  covered?: boolean
  shortfall_base?: number
}

export interface FundingSource {
  id: string
  type: FundingType
  label: string
  amount: number | null // fixed sources only; asset_sale is valued live
  currency: string
  ref_position_id: string | null
  sell_quantity: number | null
  available_date: string | null
  ref_account_id: string | null
  repayment_commitment_id: string | null
  note: string
  /** Set when the action that would have unlocked this source is marked "not happening" —
   *  excluded from the funding projection/reconciliation/waterfall until re-enabled. */
  disabled: boolean
  /** Informational display hint for type=income recurring set-asides ("monthly"). */
  cadence: string | null
  /** Execution state — did this money actually arrive? Mirrors what installments have carried
   *  from day one (`status` + `paid_txn_id`). "missed" is NOT a value here: it is derived from
   *  `available_date` (backend `services.commitments.funding_source_overdue`), because a stored
   *  "missed" is only true if something sweeps the table daily. */
  status: 'planned' | 'done'
  realized_txn_id: string | null
  // Phase 2 (optional): live base value resolved by the API
  value_base?: number
}

// Per-commitment verdict attached to each row in GET /api/commitments.
// A dated NON-payment task/event on a commitment (apply for loan, sign contract,
// confirm transfer, stock-sale window, review, handover…).
export interface CommitmentAction {
  id: string
  label: string
  category: string
  due_date: string
  start_by: string | null
  amount: number | null
  currency: string
  status: ActionStatus
  counterparty: string
  note: string
  /** Ties this action to the funding_plan[] source it unlocks — "not happening" impact
   *  analysis reads this to know which source to disable. */
  funding_id: string | null
  /** FK to a one-shot Lucius Automation — set/cleared by the Actions tab's "remind me"
   *  bell. Null when no reminder is armed for this action. */
  reminder_automation_id: string | null
}

export interface CommitmentLoan {
  principal: number
  apr: number
  term_months: number
  payment: number
}

// One row in the cross-commitment upcoming feed (GET /api/commitments/upcoming).
export interface UpcomingEvent {
  commitment_id: string
  commitment_name: string
  // Tracker kinds (goal/auto_pay) get a bare mark-done control; projected kinds
  // (purchase/loan_repayment) need a ledger-linked payment instead.
  commitment_kind?: CommitmentKind
  kind: 'installment' | 'action' | 'funding'
  // Addressable row ids (per kind) so the UI can patch the row directly.
  installment_id?: string
  action_id?: string
  label: string
  category: string
  date: string
  amount_base: number | null
  status: string
  counterparty?: string
}

// One row from GET /api/commitments/unmarked-payments — a ledger transaction that
// looks like an unpaid installment payment, awaiting user confirmation to link.
export interface UnmarkedPaymentSuggestion {
  commitment_id: string
  commitment_name: string
  installment_id: string
  installment_label: string
  txn_id: string
  txn_date: string
  amount_base: number
}

export interface CommitmentCoverage {
  // False for tracker kinds (goal/auto_pay) the funding engine never evaluates —
  // consumers must not render a covered/at-risk verdict when false.
  assessed: boolean
  at_risk: boolean
  shortfall_base: number
  earliest_shortfall_date: string | null
}

/** One month of the portfolio cushion projection (GET /api/commitments/coverage's `timeline`) —
 *  the series the funding-outlook chart plots. */
export interface CoverageTimelineMonth {
  year_month: string // "YYYY-MM"
  balance: number
  total_in: number
  total_out: number
  is_tight: boolean
}

// Portfolio-wide verdict from GET /api/commitments/coverage (drives the Overview flag + masthead).
export interface PortfolioCoverage {
  base_currency: string
  at_risk: boolean
  earliest_shortfall_date: string | null
  /** The DEEPEST gap to the floor within `horizon_months` — reached in `lowest_balance_month`,
   *  not at `earliest_shortfall_date` (the first dip). Quote them together. */
  total_shortfall_base: number
  min_balance_base: number
  lowest_balance_month: string | null // "YYYY-MM"
  horizon_months: number
  low_balance_threshold: number
  over_committed: boolean
  /** Something in the verdict is converted at an exchange rate, so its cents move with FX. */
  spans_currencies: boolean
  reconciliation: {
    earmarked_base: number
    liquid_base: number
    over_committed: boolean
    excess_base: number
  }
  commitments: Array<{
    id: string
    assessed: boolean
    at_risk: boolean
    shortfall_base: number
    earliest_shortfall_date: string | null
  }>
  timeline: CoverageTimelineMonth[]
}

export interface Commitment {
  id: string
  name: string
  kind: CommitmentKind
  currency: string
  status: CommitmentStatus
  linked_position_id: string | null
  linked_account_id: string | null
  /** FK to the synced Planning envelope (planning.sync_commitment_envelopes is its only writer).
   *  Lets a budget row resolve back to its commitment. Null/absent when not synced. */
  envelope_id?: string | null
  note: string
  created_at: string
  installments: Installment[]
  funding_plan: FundingSource[]
  actions?: CommitmentAction[] // events/checklist layer (optional for older records)
  coverage?: CommitmentCoverage // Phase 2
  /** loan_repayment only — set at creation from the amortization terms. Drives the detail
   *  screen's loan-balance stat + early-payoff insight (pure client math, see loanIntel()). */
  loan?: CommitmentLoan | null
}
