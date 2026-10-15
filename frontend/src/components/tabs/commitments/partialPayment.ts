// Pure helper for the Schedule tab's "mark paid" flow (PayBox, detail3.jsx) — there is no
// dedicated backend "split an installment" endpoint (see docs/adr/2026-07-22-commitments-tab-
// redesign-faithful-port.md), so a PARTIAL payment is built client-side: the target
// installment is replaced by a PAID row for the amount actually paid, plus a fresh "— balance"
// row for what's still owed, then the whole array is written back via
// `PUT /api/commitments/{id}/installments`. A full payment (amountPaid >= the installment's
// full amount) doesn't need this at all — the caller should PATCH `{status:'paid'}` on the one
// row instead (see detailMutations.ts's `payInstallment`); this function is only for the
// genuine split case.
import type { Installment } from '../../../types/commitments'

/** Replaces `targetId` in `installments` with a paid row (for `amountPaid`) + a new
 *  "<label> — balance" row (for the remainder, still due, unlinked). Every other row passes
 *  through unchanged. `amountPaid` is assumed < the target row's full amount — the caller
 *  decides between this and a plain full-payment PATCH. */
export function buildPartialPaymentInstallments(
  installments: Installment[],
  targetId: string,
  amountPaid: number,
): Installment[] {
  return installments.flatMap((inst) => {
    if (inst.id !== targetId) return [inst]
    const paidRow: Installment = { ...inst, amount: round2(amountPaid), status: 'paid' }
    const balanceRow: Installment = {
      ...inst,
      id: `${inst.id}-bal-${Date.now()}`,
      label: `${inst.label} — balance`,
      amount: round2(inst.amount - amountPaid),
      status: inst.status,
      paid_txn_id: null,
    }
    return [paidRow, balanceRow]
  })
}

function round2(n: number): number {
  return Math.round(n * 100) / 100
}
