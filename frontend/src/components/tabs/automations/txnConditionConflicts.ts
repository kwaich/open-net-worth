// Conditions that can never all hold: the trigger would sit there and never fire. The editor
// warns under the condition that closes the trap, and the trigger can't be saved until it's fixed.
import { conditionIsComplete, conditionValues, type TxnCondition } from './txnConditions'

/** Not a field to add but conditions to fix: they can never all be true, so it would never fire. */
export const CONFLICTING_CONDITIONS = 'conditions that can all be true'

export interface ConditionConflict {
  /** The condition the warning sits under — the last one involved. */
  index: number
  message: string
}

/** "investment" is either a buy or a sale — the ledger gives an investment one cash leg. */
const KIND_ATOMS: Record<string, string[]> = {
  investment: ['investment_buy', 'investment_sale'],
}

type Indexed = { condition: TxnCondition; index: number }

export function conditionConflicts(conditions: TxnCondition[]): ConditionConflict[] {
  const rows = conditions
    .map((condition, index) => ({ condition, index }))
    .filter(({ condition }) => conditionIsComplete(condition))
  return [
    oneValueConflict(rows, 'payee', (v) => [v]),
    oneValueConflict(rows, 'kind', (v) => KIND_ATOMS[v] ?? [v]),
    accountConflict(rows),
    amountConflict(rows),
  ].filter((c): c is ConditionConflict => c !== null)
}

/** A transaction has ONE payee and ONE kind: two "is" with no value in common, or an "is" whose
 *  every value an "is not" rules out, can't both be true. */
function oneValueConflict(
  rows: Indexed[],
  field: 'payee' | 'kind',
  atoms: (value: string) => string[],
): ConditionConflict | null {
  const of = (op: string) =>
    rows.filter((r) => r.condition.field === field && r.condition.op === op)
  const expand = (r: Indexed) => new Set(conditionValues(r.condition.value).flatMap(atoms))
  const is = of('is')
  if (!is.length) return null
  let allowed = expand(is[0]!)
  for (const row of is.slice(1)) {
    const next = expand(row)
    allowed = new Set([...allowed].filter((v) => next.has(v)))
    if (!allowed.size) {
      return {
        index: row.index,
        message: `A transaction has one ${field}, so these can’t both be true — pick both in one condition instead.`,
      }
    }
  }
  const not = of('is_not')
  const excluded = new Set(not.flatMap((r) => [...expand(r)]))
  if (not.length && [...allowed].every((v) => excluded.has(v))) {
    return {
      index: Math.max(...[...is, ...not].map((r) => r.index)),
      message: `“Is” and “is not” rule out every ${field} here, so this can never match.`,
    }
  }
  return null
}

/** Two "Account is" can both hold (a transfer between them); an "is" whose every account an
 *  "is not" excludes can't. */
function accountConflict(rows: Indexed[]): ConditionConflict | null {
  const accounts = rows.filter((r) => r.condition.field === 'account')
  const not = accounts.filter((r) => r.condition.op === 'is_not')
  const excluded = new Set(not.flatMap((r) => conditionValues(r.condition.value)))
  const trapped = accounts.find(
    (r) =>
      r.condition.op === 'is' &&
      conditionValues(r.condition.value).every((account) => excluded.has(account)),
  )
  if (!trapped) return null
  return {
    index: Math.max(trapped.index, ...not.map((r) => r.index)),
    message: '“Is” and “is not” rule out every account here, so this can never match.',
  }
}

function amountConflict(rows: Indexed[]): ConditionConflict | null {
  const amounts = rows.filter((r) => r.condition.field === 'amount')
  const figures = (op: string) =>
    amounts.filter((r) => r.condition.op === op).map((r) => Number(r.condition.value))
  const over = figures('over')
  const under = figures('under')
  if (!over.length || !under.length) return null
  const floor = Math.max(...over)
  const ceiling = Math.min(...under)
  if (floor < ceiling) return null
  return {
    index: Math.max(...amounts.map((r) => r.index)),
    message: `An amount can’t be over ${floor.toLocaleString()} and under ${ceiling.toLocaleString()} at once, so this can never match.`,
  }
}
