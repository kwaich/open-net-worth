// budgetLens/GenericEngineReview.tsx — the setup review list for any budget engine with no
// bespoke frontend (RFC Decision D). A generic engine's setup verb is a plain include/exclude
// category-id list (see genericRows.ts), so each row is a toggle and the footer recap is a
// plain count.
import { useState } from 'react'
import { Button } from '../../../ui'
import type { GenericDraftRow } from './types'

export type GenericReviewSource = 'draft' | 'blank'

export interface GenericEngineReviewProps {
  rows: GenericDraftRow[]
  initialSelected: ReadonlySet<string>
  /** 'draft' shows an honesty banner — the list was drafted from names, not reviewed by Lucius
   *  — nothing was drafted to be honest about when the user started blank (mirrors C7/C6). */
  source: GenericReviewSource
  busy: boolean
  error: string | null
  onApply: (selected: ReadonlySet<string>) => void
  onCancel: () => void
}

const HONESTY_COPY =
  'Drafted from category names and recent spending — a quick, name-based best guess, not ' +
  'something Lucius reviewed. Check anything that looks off before applying.'

export function GenericEngineReview({
  rows,
  initialSelected,
  source,
  busy,
  error,
  onApply,
  onCancel,
}: GenericEngineReviewProps) {
  const [selected, setSelected] = useState<ReadonlySet<string>>(initialSelected)

  function toggle(categoryId: string, checked: boolean) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (checked) next.add(categoryId)
      else next.delete(categoryId)
      return next
    })
  }

  return (
    <div className="bgl-review">
      {source === 'draft' && <p className="bgl-review__honesty">{HONESTY_COPY}</p>}

      {/* Bulk actions: the real book this was built against has ~25 categories, and "start from
          none" / "start from all" then correcting a handful is far less work than flipping 25
          switches one at a time. */}
      {rows.length > 0 && (
        <div className="bgl-review__bulk">
          <span className="bgl-review__bulk-label">Include which categories?</span>
          <span className="bgl-review__bulk-actions">
            <Button
              variant="ghost"
              size="dense"
              disabled={busy || selected.size === rows.length}
              onClick={() => setSelected(new Set(rows.map((r) => r.category_id)))}
            >
              Select all
            </Button>
            <Button
              variant="ghost"
              size="dense"
              disabled={busy || selected.size === 0}
              onClick={() => setSelected(new Set())}
            >
              Clear
            </Button>
          </span>
        </div>
      )}

      <div className="bgl-review-list">
        {rows.length === 0 ? (
          <p className="bgl-review-list__empty">No categories available to choose from yet.</p>
        ) : (
          rows.map((row) => {
            const on = selected.has(row.category_id)
            return (
              // The WHOLE row is the control, not a 34px switch at the end of it — clicking a
              // small target 25 times was the interaction (owner: "too hard to interact with").
              // One <button role="switch"> rather than a row wrapping <Switch>: <Switch> is
              // itself a <label>, and nesting labels is invalid, so the row could not have been
              // made clickable around it.
              <button
                key={row.category_id}
                type="button"
                role="switch"
                aria-checked={on}
                // Names the ACTION, not just the category — "Include Groceries" tells a screen
                // reader what flipping this does, which the visible label alone does not.
                aria-label={`Include ${row.label}`}
                className={`bgl-pickrow${on ? ' bgl-pickrow--on' : ''}`}
                onClick={() => toggle(row.category_id, !on)}
              >
                {/* The marker lives INSIDE the label, not as its own grid column. As a column it
                    was conditional, so a row without one put the toggle in column 2 instead of
                    column 3 — and the still-applied gap for the empty track left those toggles
                    ~8px left of the rest. */}
                <span className="bgl-pickrow__label">
                  <span className="bgl-pickrow__name">{row.label}</span>
                  {row.low_confidence && (
                    <span
                      className="bgl-review-row__lowconf"
                      title="Ambiguous label — double-check this one"
                    >
                      low confidence
                    </span>
                  )}
                </span>
                <span className="bgl-pickrow__track" aria-hidden="true">
                  <span className="bgl-pickrow__thumb" />
                </span>
              </button>
            )
          })
        )}
      </div>

      <div className="bgl-review__footer">
        <span className="bgl-review__recap">
          {selected.size} of {rows.length} selected
        </span>
        <span className="bgl-review__actions">
          <Button variant="secondary" size="dense" disabled={busy} onClick={onCancel}>
            Cancel
          </Button>
          <Button variant="primary" size="dense" disabled={busy} onClick={() => onApply(selected)}>
            {busy ? 'Applying…' : 'Apply'}
          </Button>
        </span>
      </div>
      {error && <p className="bgl-review__error">{error}</p>}
    </div>
  )
}
