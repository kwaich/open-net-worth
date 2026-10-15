// The create-once rule for a loan typed into "Add a loan" (RFC assets-create-edit-redesign
// §3.10): nothing is written until the form it belongs to is saved; then the loan is created in
// Accounts just before the asset, and from that moment it is the loan the form links — by its
// real id — so a retry after the asset itself is refused never creates a second one. Shared by
// the create wizard and the asset page's Ownership editor.
import { useState } from 'react'
import type { AssetLoan } from '../../../types/assets'
import * as api from './api'
import { NEW_LOAN, type OwnershipValue } from './assetForms'

/** The staged loan itself was refused — its reason belongs beside the loan's own fields. */
export class LoanRefused extends Error {
  readonly refusal: unknown

  constructor(refusal: unknown) {
    super(refusal instanceof Error ? refusal.message : 'The loan was not added.')
    this.name = 'LoanRefused'
    this.refusal = refusal
  }
}

/** The ownership once its staged loan exists: that loan, selected by its real id. */
export function linkedTo(ownership: OwnershipValue, loan: AssetLoan): OwnershipValue {
  return { ...ownership, loanId: loan.id, newLoan: null }
}

export interface StagedLoan {
  /** The loan created by this form, if any — it stays in Accounts whatever happens next. */
  createdLoan: AssetLoan | null
  /** `loans`, plus the created loan until a refetch lists it. */
  withCreated(loans: AssetLoan[]): AssetLoan[]
  /** The loan to link. A staged loan is created now and handed to the hook's `onCreated`, so
   *  the form keeps it by its real id. Throws LoanRefused when the loan itself is refused. */
  resolveLoanId(ownership: OwnershipValue): Promise<string | null>
}

export function useStagedLoan(onCreated: (loan: AssetLoan) => void): StagedLoan {
  const [createdLoan, setCreatedLoan] = useState<AssetLoan | null>(null)
  return {
    createdLoan,
    withCreated: (loans) =>
      createdLoan && !loans.some((l) => l.id === createdLoan.id) ? [...loans, createdLoan] : loans,
    async resolveLoanId(ownership) {
      if (ownership.loanId !== NEW_LOAN) return ownership.loanId || null
      if (!ownership.newLoan) return null
      let loan: AssetLoan
      try {
        loan = await api.createLoanAccount(ownership.newLoan)
      } catch (err) {
        throw new LoanRefused(err)
      }
      setCreatedLoan(loan)
      onCreated(loan)
      return loan.id
    },
  }
}
