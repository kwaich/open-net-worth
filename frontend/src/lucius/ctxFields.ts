/**
 * ctxFields.ts — Write-side twin of objectFields.ts.
 *
 * Problem: every emitter previously hand-wrote `data-ctx-fields={JSON.stringify({...})}`
 * with hand-picked keys and hand-applied formatters. This caused drift: holding rows
 * emitted `value` in FeedTable/FeedCards but `marketValue` in AssetDetailView; `price`
 * was missing from AssetDetailView; null values were inconsistently dropped.
 *
 * Solution: one canonical interface per entity type (the key schema) + one helper
 * (`buildCtxFields`) that drops null/undefined/empty and stringifies. Each emitter
 * maps its view-model to the typed canonical record once; the key names and the
 * null-drop rule are now enforced in one place.
 *
 * READ side: objectFields.ts / chipMeta.ts (unchanged — they are key-agnostic).
 * chipMeta.ts is updated to remove the `marketValue` alias now that all holding
 * emitters use the canonical `value` key.
 *
 * Canonical key sets (one interface per supported type):
 *   TransactionCtxRecord   — transaction rows (Cash.tsx, Tax.tsx)
 *   AccountCtxRecord       — account rows (Cash.tsx)
 *   HoldingCtxRecord       — holding rows (FeedTable, FeedCards, AssetDetailView)
 *   CategoryCtxRecord      — budget category rows (BudgetTable.tsx)
 *
 * Types not yet emitting data-ctx-fields (instrument, payee as stub) are not listed
 * here — add an interface and switch to buildCtxFields when the emitter grows.
 */

// ── Canonical record interfaces ────────────────────────────────────────────────

/** transaction — used by Cash.tsx (ledger rows) and Tax.tsx (contributor rows). */
export interface TransactionCtxRecord {
  id: string
  date: string
  payee?: string | null
  category?: string | null
  /** Signed amount string: "+14,200.00 SGD" or "−252.71 SGD" (U+2212 minus for expenses). */
  amount: string
  /** Single-leg account name (expense/income). Absent for transfers (use from_account/to_account). */
  account?: string | null
  from_account?: string | null
  to_account?: string | null
  description?: string | null
  status?: string | null
  /** Internal routing — included for the assistant, hidden from the popover by objectFields. */
  kind: string
}

/** account — used by Cash.tsx account sidebar. */
export interface AccountCtxRecord {
  id: string
  name: string
  /** Formatted balance in base currency or native currency string. */
  balance: string
  /** Human-readable account type label. */
  type: string
  /** Platform / custodian name (e.g. "IBKR"). Omit when absent. */
  custodian?: string | null
  /** Native currency code when different from base currency. Omit when same as base. */
  currency?: string | null
}

/** holding — used by FeedTable, FeedCards (Holdings sub-tab) and AssetDetailView. */
export interface HoldingCtxRecord {
  id: string
  symbol?: string | null
  /** Formatted per-share price in base currency. */
  price?: string | null
  /** Formatted total position value in base currency. CANONICAL key — never use "marketValue". */
  value: string
  account?: string | null
  /** Formatted weight percentage, e.g. "12.30%". */
  weight?: string | null
  /** Formatted day-change percentage, e.g. "+0.70%" or "−1.20%". */
  day?: string | null
  /** Quantity / number of units as a string. */
  qty?: string | number | null
  /** Formatted average cost in base currency. */
  avgCost?: string | null
  /** Formatted unrealized P&L in base currency. */
  uplBase?: string | null
  /** Formatted unrealized P&L percentage with sign, e.g. "+12.30%". */
  uplPct?: string | null
}

/** category — used by BudgetTable.tsx (planning rows). */
export interface CategoryCtxRecord {
  id: string
  label: string
  /** Formatted assigned amount in base currency. */
  assigned: string
  /** Formatted activity amount in base currency. */
  activity: string
  /** Formatted available amount in base currency. */
  available: string
  status?: string | null
}

// ── Helper ────────────────────────────────────────────────────────────────────

/**
 * Build the data-ctx-fields JSON string from a canonical record.
 *
 * Drops every key whose value is null, undefined, or an empty string — so
 * emitters can spread conditional fields without scattered ternaries.
 *
 * Example:
 *   buildCtxFields({ id: 'pos_1', symbol: 'AAPL', price: 'S$185.20', value: 'S$18,520', account: null })
 *   → '{"id":"pos_1","symbol":"AAPL","price":"S$185.20","value":"S$18,520"}'
 *
 * The value is passed directly to `data-ctx-fields={...}` — React handles the
 * serialization boundary; no double-encoding.
 */
export function buildCtxFields(
  record:
    | TransactionCtxRecord
    | AccountCtxRecord
    | HoldingCtxRecord
    | CategoryCtxRecord
    | Record<string, unknown>,
): string {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(record)) {
    if (v == null || v === '') continue
    out[k] = v
  }
  return JSON.stringify(out)
}
