// budgetLens/setup/amountRows.ts — pure helpers behind the setup flow that asks "which
// envelopes, and how much each" — Zero-Based's monthly targets. No fetch, no React, so the logic
// is testable without mounting anything.
//
// It can't use the GENERIC pane, which drives a verb taking ONE list-of-ids param and cannot
// carry an amount at all.
import type { EnvelopeDraftRow } from '../types'

/** One row's editable state: is it included, and at what amount? */
export interface AmountEdit {
  included: boolean
  /** Kept as the raw STRING the user typed, not a number — a controlled numeric input that
   *  round-trips through Number() cannot hold "" or "12." while someone is mid-type, and
   *  fights the caret on every keystroke. Parsed once, at submit. */
  amount: string
}

export type AmountEdits = Record<string, AmountEdit>

/** The engine's own draft rows. Returns [] for anything unexpected rather than throwing — a
 *  setup screen must not white-screen because a proposal came back in a shape it didn't
 *  expect. */
export function proposedAmountRows(state: Record<string, unknown> | null): EnvelopeDraftRow[] {
  const rows = (state ?? {})['rows']
  if (!Array.isArray(rows)) return []
  return rows.filter(
    (r): r is EnvelopeDraftRow =>
      !!r && typeof r === 'object' && typeof (r as EnvelopeDraftRow).envelope_id === 'string',
  )
}

/** Initial edits from a draft: every row the engine proposed starts included, at its suggested
 *  amount. `startBlank` passes `included: false` instead — same rows, nothing pre-picked, so
 *  "set up manually" is a real starting point rather than a different screen. */
export function initialAmountEdits(rows: EnvelopeDraftRow[], included: boolean): AmountEdits {
  const out: AmountEdits = {}
  for (const row of rows) {
    out[row.envelope_id] = {
      included,
      // A suggestion of 0 means "no history to size this from" — show it empty rather than a
      // literal 0 the user has to clear before typing.
      amount: row.suggested_amount > 0 ? String(row.suggested_amount) : '',
    }
  }
  return out
}

/** Parse one amount field. Blank/garbage reads as 0 — never NaN, which would reach the API. */
export function parseAmount(raw: string): number {
  const n = Number((raw ?? '').trim())
  return Number.isFinite(n) && n > 0 ? Math.round(n * 100) / 100 : 0
}

/** The payload: only included rows, amounts parsed. Order follows `rows`, so what the user saw
 *  top-to-bottom is what gets sent. */
export function amountsPayload(
  rows: EnvelopeDraftRow[],
  edits: AmountEdits,
): { envelope_id: string; amount: number }[] {
  return rows
    .filter((r) => edits[r.envelope_id]?.included)
    .map((r) => ({
      envelope_id: r.envelope_id,
      amount: parseAmount(edits[r.envelope_id]?.amount ?? ''),
    }))
}

export interface AmountTotals {
  count: number
  total: number
  /** Included but left at zero. Worth surfacing: saving a target of nothing clears the
   *  envelope's target, which is rarely what someone who ticked it meant. */
  unfunded: number
}

export function amountTotals(rows: EnvelopeDraftRow[], edits: AmountEdits): AmountTotals {
  const picked = amountsPayload(rows, edits)
  return {
    count: picked.length,
    total: Math.round(picked.reduce((sum, p) => sum + p.amount, 0) * 100) / 100,
    unfunded: picked.filter((p) => p.amount <= 0).length,
  }
}
