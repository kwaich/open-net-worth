// budgetLens/proportional/rowClick.ts — what a click on a 50/30/20 row (desktop) or card (phone)
// does: open the envelope's editor, as a Zero-Based row opens its panel (owner, 2026-09-26).
import type { MouseEvent } from 'react'
import type { RowHandlers } from './propCells'
import type { PropEnvelope } from './propApi'

/** A row or card click opens the envelope's editor — unless it landed on one of the row's own
 *  controls (cap, activity link, chip, move, quick-sort), which do their own thing. */
export function openOnRowClick(e: PropEnvelope, h: RowHandlers) {
  return (ev: MouseEvent<HTMLElement>) => {
    if ((ev.target as HTMLElement).closest('button, a, input, select, [role="menu"]')) return
    h.onEdit(e.id)
  }
}
