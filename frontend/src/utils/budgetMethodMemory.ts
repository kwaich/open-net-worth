// The Plan tab can't know the book's budgeting method until /api/planning/engines answers, yet
// its first paint (route chunk, then Plan's own wait) needs a shape. Remembering the last method
// on this device lets that first skeleton already be the right one — a 50/30/20 book never sees
// a zero-based table swapped for a different layout mid-load.
const KEY = 'onw:plan:budget-method'

export type BudgetSkeletonVariant = 'budget' | 'proportional'

export function rememberBudgetMethod(engineId: string): void {
  try {
    localStorage.setItem(KEY, engineId)
  } catch {
    /* storage unavailable — the zero-based default still renders */
  }
}

export function budgetSkeletonVariant(): BudgetSkeletonVariant {
  try {
    return localStorage.getItem(KEY) === 'proportional' ? 'proportional' : 'budget'
  } catch {
    return 'budget'
  }
}
