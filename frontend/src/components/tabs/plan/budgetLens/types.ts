// budgetLens/types.ts — TS mirror of backend.services.budget_engines.types (RFC
// .claude/rfcs/2026-08-22-pluggable-budget-engines.md, Decision C). Field names match the
// dataclasses verbatim (dataclasses.asdict output over the wire) — never renamed here, so a
// backend field can be traced to its frontend counterpart by grep alone.

export type MeasureTone = 'positive' | 'negative' | 'neutral' | 'warning'
export type HeadlineUnit = 'money' | 'percent' | 'count'
export type RowsShape = 'rows' | 'proportion-bars' | 'breakdown' | 'gauges'
export type HealthVerdict = 'good' | 'attention' | 'bad' | 'unknown'
export type EngineKind = 'derived' | 'configured'
export type RenderMode = 'generic' | 'custom'

export interface Measure {
  key: string
  label: string
  amount_base: number | null
  percent: number | null
  tone: MeasureTone
}

export interface RowDrilldown {
  category_ids?: string[] | null
  envelope_id?: string | null
}

export interface BudgetRow {
  id: string
  label: string
  color: string | null
  measures: Measure[]
  status: string
  drilldown: RowDrilldown | null
}

export interface BudgetGroup {
  id: string
  label: string
  rows: BudgetRow[]
  subtotal: Measure[]
  status: string
}

export interface Headline {
  label: string
  value: number
  unit: HeadlineUnit
  status: string
  help: string | null
}

export interface Health {
  verdict: HealthVerdict
  reasons: string[]
}

export interface Unmapped {
  label: string
  amount_base: number
  tone: MeasureTone
  uncategorized_amount_base: number
  uncategorized_count: number
  skipped_amount_base: number
  skipped_count: number
}

export interface BudgetViewModel {
  engine_id: string
  period: string
  base_currency: string
  headline: Headline
  groups: BudgetGroup[]
  rows_shape: RowsShape
  health: Health
  unmapped: Unmapped | null
}

export interface BudgetEngineVerb {
  id: string
  label: string
  params: Record<string, string>
}

export interface BudgetEngineDescriptor {
  id: string
  name: string
  description: string
  question: string
  kind: EngineKind
  render: RenderMode
  is_home: boolean
  configured: boolean
  verbs: BudgetEngineVerb[]
}

export interface BudgetEnginesResponse {
  engines: BudgetEngineDescriptor[]
  home_engine_id: string
  /** Has this book dismissed the Plan -> Budget first-run introduction? Per-book, server-
   *  persisted (never localStorage — see BudgetLensIntroStore). Note: `model_note`
   *  (Lucius-only disambiguation guidance) is deliberately NOT part of this shape — it
   *  never rides the REST wire, only the `list_budget_engines` AI tool's own output. */
  intro_dismissed: boolean
}

// ── Generic engine draft (GenericEnginePane's setup flow, any "generic"-render engine) ──

export interface GenericDraftRow {
  category_id: string
  label: string
  low_confidence: boolean
}

/** One row of zero-based's own draft — an envelope and the monthly target suggested for it.
 *  `low_confidence` means "we cannot size this confidently"; every envelope is proposed. */
export interface EnvelopeDraftRow {
  envelope_id: string
  label: string
  low_confidence: boolean
  suggested_amount: number
  category_ids: string[]
}

export interface GenericDraftResponse {
  engine_id: string
  has_proposal: boolean
  // Engine-shaped (e.g. zero-based's {targets, rows}) — never interpreted field-by-field
  // here; genericRows.ts reads only `rows` plus whichever key the engine's OWN
  // declared verb param names.
  proposed_state: Record<string, unknown> | null
}

// ── 50/30/20 (the month read and its verbs live in proportional/propApi.ts) ──────────

/** 50/30/20's setup result: each group's share of income, in percent, by group id. */
export type ProportionalTargets = Record<string, number>
