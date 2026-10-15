// plan/budgetCardPayment.tsx — a credit card's payment envelope, in the desktop side panel
// (owner, 2026-09-25, after YNAB's payment inspector): clicking a card's row used to jump to
// Accounts — and not even to that card, since the row carries no account id. Every figure is
// the row's own server field (`owed`, `available_for_payment`, `underfunded`) or the SAME
// activity breakdown the row's Activity popover fetches; nothing is re-derived here beyond the
// display-only "balance not yet set aside" gap. Without the side panel (phone, narrow window) the
// same detail opens as the target editor's modal — a bottom sheet on a phone (owner, 2026-09-28).
import { useEffect, useState } from 'react'
import * as PD from './planData'
import { authFetch } from '../../../api/client'
import { Button, Skeleton } from '../../ui'
import { Arrow, X } from './icons'
import { PlanningModal } from './PlanningModal'
import { ColorIcon } from './budgetParts'
import { NotesSection, TgtCard } from './budgetTarget'
import { PaymentBreakdownBox, buildActivityQuery, type PaymentBreakdown } from './budgetActivity'
import type { LocalCat } from './useBudgetModel'
import { cardPaymentStatus } from './cardPaymentStatus'

const fMoney = (n: number) => PD.fBaseCents(n)

export function CardPaymentPanel({
  c,
  month,
  note,
  onSaveNote,
  onBack,
  onOpenAccounts,
  onAssign,
  inline = true,
}: {
  c: LocalCat
  month: string
  note: string
  onSaveNote: (text: string) => void
  onBack: () => void
  onOpenAccounts: () => void
  /** Set this month's assigned figure for the card's payment envelope. */
  onAssign: (value: number) => void
  /** In the side panel; false floats it as a modal instead. */
  inline?: boolean
}) {
  // undefined = loading, null = couldn't load
  const [bd, setBd] = useState<PaymentBreakdown | null | undefined>(undefined)
  const query = buildActivityQuery(c, month)
  useEffect(() => {
    let cancelled = false
    authFetch(`/api/planning/activity-transactions?${query}`)
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null)
      .then((d) => {
        if (!cancelled) setBd((d?.breakdown as PaymentBreakdown | undefined) ?? null)
      })
    return () => {
      cancelled = true
    }
  }, [query])
  const s = cardPaymentStatus(c)
  const owed = c.paymentOwed ?? 0
  const avail = c.availableForPayment ?? 0
  const body = (
    <>
      <TgtCard title="Payment">
        <div className={'pl-pay-status ' + s.tone}>
          <div className="pl-pay-status-title">{s.title}</div>
          <div className="pl-pay-status-txt">{s.text}</div>
        </div>
        {s.toAssign > 0.005 && (
          <div className="pl-pay-fix">
            {/* One click covers the whole balance (owner, 2026-09-28: "I don't see any button to
                assign underfunded") — from Ready to Assign, on top of what's already assigned. */}
            <Button
              variant="primary"
              size="dense"
              onClick={() => onAssign(Math.round((c.assigned + s.toAssign) * 100) / 100)}
            >
              Assign <span className="mask">{fMoney(s.toAssign)}</span>
            </Button>
            {s.clearRed !== undefined && s.clearRed < s.toAssign - 0.005 && (
              <Button
                variant="secondary"
                size="dense"
                onClick={() => onAssign(Math.round((c.assigned + (s.clearRed ?? 0)) * 100) / 100)}
              >
                Just clear the red · <span className="mask">{fMoney(s.clearRed)}</span>
              </Button>
            )}
            {s.why && (
              <details className="pl-pay-why">
                <summary>Why is this?</summary>
                <p className="pl-pay-status-txt">{s.why}</p>
              </details>
            )}
          </div>
        )}
        <div className="pl-tgt-rows">
          <div className="pl-tgt-row">
            <span>Available for payment</span>
            <b className="mask">{fMoney(avail)}</b>
          </div>
          <div className="pl-tgt-row">
            <span>Current balance</span>
            <b className="mask">{fMoney(owed)}</b>
          </div>
        </div>
      </TgtCard>
      <TgtCard title="This month">
        {bd === undefined ? (
          <div role="status" aria-label="Loading this month's activity">
            <Skeleton height={12} width="80%" />
            <Skeleton height={12} width="60%" style={{ marginTop: 8 }} />
          </div>
        ) : bd === null ? (
          <div className="pl-pay-status-txt">Couldn't load this month's activity.</div>
        ) : (
          <PaymentBreakdownBox b={bd} />
        )}
      </TgtCard>
      <NotesSection note={note} onSave={onSaveNote} />
      <Button variant="secondary" size="dense" onClick={onOpenAccounts}>
        Open in Accounts
      </Button>
    </>
  )
  if (!inline)
    return (
      <PlanningModal onClose={onBack} label={'Payment for ' + c.label} className="pl-tgt-modal">
        <div className="pl-modal-head">
          <h3>
            <span className="pl-cdot" style={{ background: c.color, marginRight: 8 }} />
            {c.label} • Payment
          </h3>
          <button className="pl-modal-x" onClick={onBack} aria-label="Close">
            <X size={16} />
          </button>
        </div>
        <div className="pl-modal-body">{body}</div>
      </PlanningModal>
    )
  return (
    <div className="pl-budget-rail-target">
      <div className="pl-tgt-rail-head">
        <span className="pl-tgt-rail-title">
          <ColorIcon icon="envelope" color={c.color} />
          <span className="pl-tgt-rail-name">{c.label}</span>
        </span>
        <Button variant="secondary" size="dense" onClick={onBack}>
          <Arrow dir="left" size={12} /> Back
        </Button>
      </div>
      {body}
    </div>
  )
}
