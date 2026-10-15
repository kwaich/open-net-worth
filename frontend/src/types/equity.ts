// Domain types for the Equity Compensation feature.
// Shape mirrors the §14 integration contract in .claude/rfcs/equity-comp.md.

export type TaxEvent = 'at_vest' | 'deferred'
export type GrantType = 'rsu' | 'ltip'
export type TrancheKind = 'time' | 'performance'
export type TrancheStatus = 'vested' | 'pending_release' | 'upcoming' | 'performance_pending'
export type GrantStatus = 'active' | 'fully_vested' | 'cancelled' | 'forfeited'

export interface Tranche {
  id: string
  vest_date: string
  units: number
  kind: TrancheKind
  condition_note: string | null
  vested: boolean
  vest_event_id: string | null
  // Computed by the server for display
  status: TrancheStatus
}

export interface Grant {
  id: string
  type: GrantType
  instrument_id: string
  symbol: string
  grant_date: string
  total_units: number
  vested_units: number
  unvested_units: number
  next_vest_date: string | null
  currency: string
  /** Market price per unit on the grant date, in the grant's currency. Reference only — not used for tax. */
  grant_price_per_unit: number | null
  tax_event: TaxEvent
  status: GrantStatus
  /** True when this plan delivers the shares AFTER they vest (settlement lag, blackout,
   *  country delivery rules). Vested units then park in a contingent pending position —
   *  owned and in net worth, but not sellable — until the release is recorded. */
  release_required: boolean
  /** Vested but not yet delivered. A SUBSET of vested_units, never a third slice. */
  pending_release_units: number
  locked_position_id: string | null
  pending_position_id: string | null
  account_id: string | null
  tranches: Tranche[]
  note: string
  created_at: string
  updated_at: string
}

export interface GrantsResponse {
  grants: Grant[]
}

// ── Vest event (used in the vest report) ────────────────────────────────────

export interface VestEvent {
  id: string
  grant_id: string
  symbol: string
  grant_date: string
  vest_date: string
  price_paid_per_unit: number
  fmv_per_unit: number
  fmv_currency: string
  units: number
  gross_gain_base: number
  tax_event: TaxEvent
}

// ── Vest report ─────────────────────────────────────────────────────────────

export interface EquityVestReport {
  year: number
  years: number[]
  base_currency: string
  events: VestEvent[]
  total_gain_base: number
}

// ── Upcoming vests ──────────────────────────────────────────────────────────

export interface UpcomingVestItem {
  grant_id: string
  tranche_id: string
  symbol: string
  vest_date: string
  units: number
  indicative_value_base: number | null
  overdue: boolean
}

export interface UpcomingVestsResponse {
  items: UpcomingVestItem[]
}

// ── Pending releases (vested, not yet delivered) ────────────────────────────

export interface PendingReleaseItem {
  vest_event_id: string
  grant_id: string
  tranche_id: string
  symbol: string
  instrument_id: string
  vest_date: string
  units: number
  fmv_per_unit: number
  fmv_currency: string
  expected_release_date: string | null
  /** Only ever true when an expected date was recorded and has passed. A blank
   *  expected date means unknown, never late. */
  overdue: boolean
  indicative_value_base: number | null
  base_currency: string
  /** move_pending transfers a parked vest; record_only annotates a legacy existing lot. */
  release_mode?: 'move_pending' | 'record_only' | null
}

export interface PendingReleasesResponse {
  items: PendingReleaseItem[]
}

// ── Vest history (every recorded vest, including completed releases) ─────────

export interface VestHistoryItem extends PendingReleaseItem {
  release_status: 'released' | 'awaiting_release' | 'not_released'
  /** Actual recorded delivery date. Never inferred from vest date or lot existence. */
  released_on: string | null
}

export interface VestHistoryResponse {
  total: number
  items: VestHistoryItem[]
}

// ── FMV prefill ─────────────────────────────────────────────────────────────

export interface FmvPrefill {
  fmv_per_unit: number | null
  fmv_currency: string
  as_of: string | null
  source: 'price_history' | 'none'
}

// ── Grant create / update ───────────────────────────────────────────────────

export interface TrancheInput {
  vest_date: string
  units: number
  kind: TrancheKind
  condition_note?: string
}

export interface GrantCreate {
  instrument_id: string
  total_units: number
  grant_date: string
  type: GrantType
  tax_event: TaxEvent
  release_required?: boolean
  currency?: string
  /** Market price per unit on the grant date, in the grant's currency. Reference only — not used for tax. */
  grant_price_per_unit?: number
  account_id?: string
  tranches: TrancheInput[]
  note?: string
}

// Instrument, type, and total_units are immutable after create — omitted here.
export interface GrantUpdate {
  grant_date?: string
  grant_price_per_unit?: number | null
  tax_event?: TaxEvent
  release_required?: boolean
  note?: string
  account_id?: string
  account_name?: string
}

// ── Vest apply ──────────────────────────────────────────────────────────────

export interface VestApply {
  units: number
  fmv_per_unit: number
  fmv_currency: string
  vest_date: string
  /** Optional and never guessed — omit unless the user states a date. */
  expected_release_date?: string
  idempotency_key?: string
}

export interface ReleaseApply {
  release_date?: string
  note?: string
}

export interface ReleaseResult {
  vest_event_id: string
  released_on: string
  units: number
  lot_id: string
  position_id: string
  release_mode: 'move_pending' | 'record_only'
}

export interface VestResult {
  vest_event_id: string
  txn_id: string | null
  /** null when the vest is pending release — the lot is struck at delivery. */
  lot_id: string | null
  pending_release: boolean
  gross_gain_base: number
  base_currency: string
}
