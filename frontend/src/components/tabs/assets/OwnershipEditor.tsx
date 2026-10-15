// Ownership — your share, who with, and the loan against it. It IS the create wizard's step 3
// (OwnershipStep) in a dialog, and saves exactly {share_pct, share_note, loan_account_id}
// (RFC §3.4). A loan staged with "Add a loan" is created in Accounts only when Save changes is
// pressed, just before the asset is saved; if the asset save is then refused, the loan stays
// selected by its real id, so a retry links it instead of creating a second (RFC §3.10).
import { useCallback, useRef } from 'react'
import type { AssetLoan, OwnedAsset } from '../../../types/assets'
import { getBaseCurrency } from '../../../utils/format'
import { Modal, Stack } from '../../ui'
import * as api from './api'
import { AssetsApiError } from './api'
import {
  ownershipErrors,
  ownershipFromAsset,
  sharePctOf,
  errorKeysOf,
  stagedLoanErrors,
  withOwnership,
  type OwnershipErrors,
  type OwnershipValue,
} from './assetForms'
import { EditorFooter, SaveError, useEditorSave } from './editorKit'
import type { LoanFormGate } from './InlineAddLoan'
import { OwnershipStep } from './OwnershipStep'
import { LoanRefused, linkedTo, useStagedLoan } from './useStagedLoan'
import { useSyncedDraft } from './useSyncedDraft'

const FIELDS = ['share_pct', 'loan_account_id', 'loan_name', 'loan_owed'] as const

/** A refused loan creation, re-addressed to the staged loan's own field (as the wizard does). */
function stagedLoanRefusal(err: unknown): AssetsApiError {
  const [field, message] = Object.entries(stagedLoanErrors(err))[0] ?? ['loan_account_id', '']
  const status = err instanceof AssetsApiError ? err.status : 0
  return new AssetsApiError(message, status, null, null, field, null)
}

export interface OwnershipEditorProps {
  a: OwnedAsset
  loans: AssetLoan[]
  onClose(): void
  onSaved(message: string): void
}

export function OwnershipEditor({ a, loans, onClose, onSaved }: OwnershipEditorProps) {
  // Every change merges into the newest value (`latest`), never a copy from the last render.
  const [value, update, latest] = useSyncedDraft<OwnershipValue>(() => ownershipFromAsset(a))
  const staged = useStagedLoan((loan) => update(linkedTo(latest.current, loan)))
  const { saving, save, check, errorFor, edited, unreadable, dialogError } = useEditorSave()
  // The open "Add a loan" form, asked before saving: it holds Save changes while it has
  // anything not yet added (and closes if untouched).
  const loanForm = useRef<LoanFormGate | null>(null)
  const onLoanForm = useCallback((gate: LoanFormGate | null) => {
    loanForm.current = gate
  }, [])

  async function onSave() {
    if (loanForm.current?.holds()) return
    const current = latest.current
    if (!check(ownershipErrors(current))) return
    const saved = await save(async () => {
      const loanId = await staged.resolveLoanId(current).catch((err: unknown) => {
        throw err instanceof LoanRefused ? stagedLoanRefusal(err.refusal) : err
      })
      await api.updateAsset(a.id, {
        share_pct: sharePctOf(current),
        share_note: current.shared ? current.shareNote.trim() : '',
        loan_account_id: loanId,
      })
    })
    if (saved) onSaved('Ownership updated.')
  }

  const errors: OwnershipErrors = {
    share_pct: errorFor('share_pct'),
    loan_account_id: errorFor('loan_account_id'),
    loan_name: errorFor('loan_name'),
    loan_owed: errorFor('loan_owed'),
  }

  return (
    <Modal
      className="as-modal"
      open
      onClose={onClose}
      title="Ownership"
      subtitle={a.name}
      size="md"
      footer={
        <EditorFooter saving={saving} label="Save changes" onCancel={onClose} onSave={onSave} />
      }
    >
      <Stack gap="--space-6">
        {/* The loan account is created in the book's currency, so its amount is typed in it. */}
        <OwnershipStep
          category={a.category}
          value={value}
          onChange={(patch) => {
            update(withOwnership(latest.current, patch))
            edited(...errorKeysOf(Object.keys(patch)))
          }}
          loans={staged.withCreated(loans)}
          assetId={a.id}
          currency={getBaseCurrency()}
          errors={errors}
          onUnreadable={unreadable}
          onLoanForm={onLoanForm}
        />
        <SaveError message={dialogError(FIELDS)} />
      </Stack>
    </Modal>
  )
}
