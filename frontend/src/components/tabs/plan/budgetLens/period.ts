// budgetLens/period.ts — the current YYYY-MM, matching the backend's own `_this_month()`
// default (backend/api/planning.py). Phase 1 is peek-only with no month navigation of its own
// (out of scope per the acceptance file — B/C/D/E never exercise a non-current month), so every
// lens reads exactly the month the backend would have defaulted to anyway.
export function currentYm(): string {
  const d = new Date()
  return `${d.getFullYear()}`.padStart(4, '0') + '-' + `${d.getMonth() + 1}`.padStart(2, '0')
}
