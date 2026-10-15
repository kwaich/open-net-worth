// plan/FreshStartDialog.tsx — the confirm step of "Start fresh from <month>…".
//
// It fetches `fresh-start-preview` (a pure read — the server runs the REAL Ready-to-Assign fold
// with the carry floor overridden AND the month's assignments zeroed, writing nothing), so the
// figure this dialog promises is the figure the budget shows once confirmed. The promised figure
// then shows its OWN arithmetic as a small ledger — literally the SAME rows the RTA popover
// renders, from the same builder (`rtaLines.buildRtaLines`; ADR
// 2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category), no residual term, ever:
// cash on hand minus what the envelopes hold minus this month's uncovered card spending minus
// baseline (minus assigned in future months, when a later month's own assignment clamps this
// one's figure). "Held in envelopes" is normally 0 right after a clear but need not be, and can
// even be negative — a same-month overspend the envelope holds until the roll. Reversed when the
// floor already IS this month: the same dialog offers "Resume earlier history", which needs no
// figure at all — and which restores the FLOOR ONLY, so it says so.
// ADR 2026-09-08-budget-fresh-start-moves-the-floor-not-the-data.
import { useEffect, useState } from 'react'
import { ConfirmDialog, ErrorState, Skeleton } from '../../ui'
import { monthLabel } from '../../../hooks/useBudget'
import { fBaseCents } from './planData'
import { RtaRow } from './budgetRta'
import {
  fetchFreshStartPreview,
  freshStartConsequencesLabel,
  freshStartLedger,
  resumeHistoryLabel,
  type FreshStartPreview,
} from './freshStart'

export function FreshStartDialog({
  month,
  /** The CURRENT stored floor. Equal to `month` → this dialog offers the inverse. */
  rolloverStart,
  onConfirm,
  onCancel,
}: {
  month: string
  rolloverStart: string | null
  onConfirm: () => void
  onCancel: () => void
}) {
  const resuming = rolloverStart === month
  const [preview, setPreview] = useState<FreshStartPreview | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Mounted only while the dialog is open, for one fixed month — the fetch runs exactly once
  // and the null/null starting state IS the loading state. Resuming never pays for it.
  useEffect(() => {
    if (resuming) return
    let live = true
    fetchFreshStartPreview(month)
      .then((p) => live && setPreview(p))
      .catch((err: unknown) => live && setError(err instanceof Error ? err.message : String(err)))
    return () => {
      live = false
    }
  }, [month, resuming])

  const label = monthLabel(month)

  if (resuming)
    return (
      <ConfirmDialog
        data={{
          title: 'Resume earlier history?',
          confirmLabel: 'Resume earlier history',
          body: (
            <div className="pl-fresh-body">
              <p>{resumeHistoryLabel(month)}</p>
            </div>
          ),
        }}
        onConfirm={onConfirm}
        onCancel={onCancel}
      />
    )

  return (
    <ConfirmDialog
      data={{
        title: `Start fresh from ${label}?`,
        confirmLabel: 'Start fresh',
        confirmDisabled: !preview,
        body: error ? (
          <ErrorState title="Couldn't work out the new Ready to Assign" message={error} />
        ) : (
          <div className="pl-fresh-body">
            <p>
              Ready to Assign becomes{' '}
              {preview ? (
                <b className="mask">{fBaseCents(preview.ready_to_assign_after)}</b>
              ) : (
                <Skeleton width={84} height={13} radius={4} />
              )}
              :
            </p>
            {preview && (
              <div className="pl-fresh-ledger">
                {freshStartLedger(preview).map((line) => (
                  <RtaRow key={line.key} line={line} />
                ))}
              </div>
            )}
            <p>{freshStartConsequencesLabel(month)}</p>
          </div>
        ),
      }}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  )
}
