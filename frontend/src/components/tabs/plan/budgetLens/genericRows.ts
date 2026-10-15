// budgetLens/genericRows.ts — pure helpers behind GenericEnginePane's setup/review flow (the
// fallback for any budget engine with no bespoke frontend). No fetch, no React — kept separate
// so the verb/row logic is testable without mounting anything. A generic engine's
// setup verb takes a plain include/exclude category-id list (its ONE list-shaped param),
// never a per-category enum — that's what makes a toggle-per-row review possible with zero
// engine-specific code.
import type { CategoryLike } from '../../../../hooks/serverState'
import type { BudgetEngineVerb, GenericDraftRow } from './types'

// Categories excluded from a generic engine's setup picker — mirrors
// services.categories.STRUCTURAL_KEYS ({"split"}), the one id every real engine's own
// draft() already filters out for the same reason (a structural placeholder, never a real
// spending category).
const STRUCTURAL_CATEGORY_IDS = new Set(['split'])

/** The engine's setup verb — a declared verb whose params are a single LIST-shaped field
 *  (e.g. `set_tracked({category_ids: "list of ..."})`). Discovered from the
 *  engine's own declared verbs rather than hardcoding a verb id: any future engine whose
 *  setup verb has this same one-list-param shape gets a working generic pane with no
 *  frontend change. `undefined` when no such verb exists — the pane then has nothing it can
 *  drive generically. */
export function setupVerb(verbs: BudgetEngineVerb[]): BudgetEngineVerb | undefined {
  return verbs.find((v) => {
    const entries = Object.entries(v.params)
    if (entries.length !== 1) return false
    const [, hint] = entries[0] ?? []
    return typeof hint === 'string' && /list/i.test(hint)
  })
}

/** The setup verb's one list param name (e.g. `"category_ids"`) — read off the verb
 *  itself, never hardcoded. */
export function listParamName(verb: BudgetEngineVerb | undefined): string | null {
  if (!verb) return null
  const [name] = Object.keys(verb.params)
  return name ?? null
}

/** The ids a fresh draft proposes as included — read off the draft response's OWN copy of
 *  the verb's list param (e.g. `proposed_state.category_ids`), so no per-engine row field
 *  is ever read here. */
export function proposedSelection(
  proposedState: Record<string, unknown>,
  paramName: string,
): ReadonlySet<string> {
  const value = proposedState[paramName]
  return new Set(
    Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [],
  )
}

/** The draft response's per-category rows — every engine's draft() ships `category_id`,
 *  `label`, `low_confidence` on each row (envelope's `is_pot`/proportional's `bucket` are
 *  extra, engine-specific fields this generic reader never touches). */
export function proposedRows(proposedState: Record<string, unknown>): GenericDraftRow[] {
  const rows = proposedState.rows
  return Array.isArray(rows) ? (rows as GenericDraftRow[]) : []
}

/** "Start blank instead" — every expense category available, none pre-selected. */
export function blankGenericRows(categories: CategoryLike[]): GenericDraftRow[] {
  return categories
    .filter((c) => c.kind === 'expense' && !STRUCTURAL_CATEGORY_IDS.has(c.id))
    .map((c) => ({ category_id: c.id, label: c.label ?? c.id, low_confidence: false }))
    .sort((a, b) => a.label.localeCompare(b.label))
}
