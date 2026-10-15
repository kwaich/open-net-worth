// mentionColors.ts — single-source color resolver for all mention surfaces.
//
// The ONLY place raw hex lives. SEMANTIC and TYPE_COLOR reference C.
// colorFor() is the universal read — never read item.dot directly at a call site.
//
// Precedence (immutable):
//   1. item.dot  — explicit per-item override (account color, transaction direction)
//   2. SEMANTIC[item.tone] — a semantic tone shared across types
//   3. TYPE_COLOR[item.type] — per-type default
//   4. SEMANTIC.neutral — last resort, never colorless

// Named palette — raw hex values and shared CSS token references live here.
export const C = {
  cash: '#34b08a',
  invest: '#3b86dd',
  retire: '#34b08a',
  credit: '#e0a032',
  crypto: '#9a7fd0',
  loan: '#e2554f',
  recv: '#d4537e',
  lucius: 'var(--lucius)',
  // Direction tints for transaction chips (pos = income/inflow, neg = expense/outflow).
  // Kept in the palette so callers can reference them symbolically.
  pos: 'var(--good)',
  neg: 'var(--bad)',
}

// Semantic tones — shared across types. An account and a category can both be 'cash'.
// Typed as a concrete object (not Record<string, string>) so noUncheckedIndexedAccess
// does not widen known property accesses (e.g. SEMANTIC.neutral) to string | undefined.
export const SEMANTIC = {
  cash: C.cash,
  invest: C.invest,
  retire: C.retire,
  credit: C.credit,
  crypto: C.crypto,
  loan: C.loan,
  recv: C.recv,
  lucius: C.lucius,
  neutral: '#7c8595',
  // rule_grey: the rule entity type has a slightly cooler grey than the shared
  // neutral (#646c7c vs #7c8595).  A dedicated tone key prevents the parity
  // guarantee from silently breaking if a caller ever passes tone to colorFor().
  rule_grey: '#646c7c',
} as const satisfies Record<string, string>

// Per-type defaults — every known type and future/plugin types that match a key get a color.
// Handoff type keys mapped to this app's real MentionType names:
//   budget → budget_month
//   scheduled → scheduled_task
//   time → returns (time-range type in the app)
// Extra app types not in the handoff get sensible defaults:
//   instrument → invest (a security is investment-toned)
//   net_worth  → lucius (the singleton net-worth view is Lucius-identity)
//   target     → retire (savings targets are retirement-toned)
//   allocation_slice → invest
export const TYPE_COLOR: Record<string, string> = {
  account: C.cash,
  holding: C.invest,
  instrument: C.invest,
  transaction: SEMANTIC.neutral, // direction unknown → neutral (a directioned txn gets red/green via item.dot)
  category: C.invest,
  budget_month: C.credit,
  returns: SEMANTIC.neutral, // handoff "time"
  payee: C.invest,
  rule: SEMANTIC.rule_grey,
  scheduled_task: C.credit, // handoff "scheduled"
  automation: C.credit,
  watchlist: C.crypto,
  skill: C.lucius,
  screen: C.lucius,
  source: C.lucius,
  net_worth: C.lucius,
  target: C.retire,
  allocation_slice: C.invest,
}

export interface ColorItem {
  type?: string
  dot?: string
  tone?: string
}

// Lookup-safe aliases — dynamic key access typed explicitly to avoid noUncheckedIndexedAccess widening.
const _SEMANTIC_MAP: Record<string, string> = SEMANTIC
const _TYPE_COLOR_MAP: Record<string, string> = TYPE_COLOR

/** Resolve the glyph/accent color for a mention item.
 *
 *  Precedence: item.dot → SEMANTIC[item.tone] → TYPE_COLOR[item.type] → neutral.
 *  Never returns undefined — falls back to neutral.
 */
export function colorFor(item: ColorItem | null | undefined): string {
  if (!item) return SEMANTIC.neutral
  if (item.dot) return item.dot
  const toneColor = item.tone ? _SEMANTIC_MAP[item.tone] : undefined
  if (toneColor) return toneColor
  const typeColor = item.type ? _TYPE_COLOR_MAP[item.type] : undefined
  if (typeColor) return typeColor
  return SEMANTIC.neutral
}
