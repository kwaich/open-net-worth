// budgetLens/setup/AmountReview.tsx — the review step for a setup that asks "which envelopes,
// and how much each": Zero-Based's monthly targets. The wording is passed in; nothing
// engine-specific is decided here.
import { useState } from 'react'
import { Button, NumberField } from '../../../../ui'
import { fBase } from '../../../../../utils/format'
import { amountTotals, amountsPayload, initialAmountEdits, parseAmount } from './amountRows'
import type { AmountEdits } from './amountRows'
import type { EnvelopeDraftRow } from '../types'

export interface AmountReviewCopy {
  /** The question this list answers, above the rows. */
  prompt: string
  /** Accessible name for one row's include toggle, e.g. "Give Rent a monthly target". */
  toggleLabel: (label: string) => string
  /** Accessible name for one row's amount field. */
  amountLabel: (label: string) => string
  /** Noun for the recap line: "12 of 16 targets". */
  unit: string
  /** Shown when the draft was generated rather than started blank. */
  honesty: string
  /** Marker on a row we could not size confidently. */
  lowConfidence: string
}

export function AmountReview({
  rows,
  copy,
  preselected,
  busy,
  error,
  onApply,
  onCancel,
}: {
  rows: EnvelopeDraftRow[]
  copy: AmountReviewCopy
  /** True for a generated draft (everything pre-picked at its suggestion), false for "set up
   *  manually" — the same rows with nothing chosen, never a different screen. */
  preselected: boolean
  busy: boolean
  error: string | null
  onApply: (picked: { envelope_id: string; amount: number }[]) => void
  onCancel: () => void
}) {
  const [edits, setEdits] = useState<AmountEdits>(() => initialAmountEdits(rows, preselected))

  function toggle(envelopeId: string) {
    setEdits((prev) => ({
      ...prev,
      [envelopeId]: {
        amount: prev[envelopeId]?.amount ?? '',
        included: !prev[envelopeId]?.included,
      },
    }))
  }

  function setAmount(envelopeId: string, amount: string) {
    setEdits((prev) => ({
      ...prev,
      // Typing an amount into a row you had not picked is unambiguous intent to pick it —
      // making the user tick it afterwards would be a second step for the same decision.
      [envelopeId]: { included: prev[envelopeId]?.included || parseAmount(amount) > 0, amount },
    }))
  }

  const totals = amountTotals(rows, edits)

  return (
    <div className="bgl-review bgl-review--modal">
      {preselected && <p className="bgl-review__honesty">{copy.honesty}</p>}

      <div className="bgl-review__bulk">
        <span className="bgl-review__bulk-label">{copy.prompt}</span>
        <span className="bgl-review__bulk-actions">
          <Button
            variant="ghost"
            size="dense"
            disabled={busy || totals.count === rows.length}
            onClick={() => setEdits(initialAmountEdits(rows, true))}
          >
            Select all
          </Button>
          <Button
            variant="ghost"
            size="dense"
            disabled={busy || totals.count === 0}
            onClick={() => setEdits(initialAmountEdits(rows, false))}
          >
            Clear
          </Button>
        </span>
      </div>

      <div className="bgl-review-list">
        {rows.length === 0 ? (
          <p className="bgl-review-list__empty">
            No envelopes yet — create one in your budget first, then come back.
          </p>
        ) : (
          rows.map((row) => {
            const edit = edits[row.envelope_id]
            const on = !!edit?.included
            return (
              <div
                key={row.envelope_id}
                className={`bgl-amountrow${on ? ' bgl-amountrow--on' : ''}`}
              >
                <button
                  type="button"
                  role="switch"
                  aria-checked={on}
                  aria-label={copy.toggleLabel(row.label)}
                  className="bgl-amountrow__pick"
                  onClick={() => toggle(row.envelope_id)}
                >
                  <span className="bgl-amountrow__name">{row.label}</span>
                  {row.low_confidence && (
                    <span
                      className="bgl-review-row__lowconf"
                      title="Not enough steady history to size this — check the amount"
                    >
                      {copy.lowConfidence}
                    </span>
                  )}
                </button>
                <NumberField
                  min={0}
                  step={10}
                  aria-label={copy.amountLabel(row.label)}
                  className="bgl-amountrow__amount"
                  value={edit?.amount ?? ''}
                  placeholder="0"
                  disabled={busy}
                  onChange={(e) => setAmount(row.envelope_id, e.target.value)}
                />
              </div>
            )
          })
        )}
      </div>

      <div className="bgl-review__footer">
        <span className="bgl-review__recap">
          {totals.count} of {rows.length} {copy.unit} • {fBase(totals.total)} a month
          {totals.unfunded > 0 && (
            <span className="bgl-review__warn"> • {totals.unfunded} with no amount yet</span>
          )}
        </span>
        <span className="bgl-review__actions">
          <Button variant="secondary" size="dense" disabled={busy} onClick={onCancel}>
            Cancel
          </Button>
          <Button
            variant="primary"
            size="dense"
            disabled={busy || totals.count === 0}
            onClick={() => onApply(amountsPayload(rows, edits))}
          >
            {busy ? 'Applying…' : 'Apply'}
          </Button>
        </span>
      </div>
      {error && <p className="bgl-review__error">{error}</p>}
    </div>
  )
}
