// The Add asset wizard's state: which step is showing and what has been typed (useWizardFlow),
// and its one-save-at-a-time save — the staged loan first (once), then the asset
// (useWizardSave). Kept apart from AssetWizard.tsx, which only draws the frame and its steps.
import { useCallback, useRef, useState } from 'react'
import type { OwnedAsset } from '../../../types/assets'
import * as api from './api'
import { AssetsApiError } from './api'
import { canHaveLoan } from './assetCategoryRules'
import {
  CREATE_FAILED,
  WIZARD_STEPS,
  createBody,
  emptyDraft,
  errorKeysOf,
  firstInvalidStep,
  stagedLoanErrors,
  stepErrors,
  stepOfField,
  type WizardDraft,
  type WizardErrors,
  type WizardStep,
} from './assetForms'
import type { LoanFormGate } from './InlineAddLoan'
import { LoanRefused, type StagedLoan } from './useStagedLoan'
import { useSyncedDraft } from './useSyncedDraft'

/** Which step is showing, what has been typed, and each step's errors. */
export function useWizardFlow(initialCategory: string | null, today: string) {
  const [step, setStep] = useState<WizardStep>(initialCategory ? 'nameValue' : 'cat')
  const [draft, update, latest] = useSyncedDraft<WizardDraft>(() => emptyDraft(initialCategory))
  const [errors, setErrors] = useState<WizardErrors>({})
  const [saveErr, setSaveErr] = useState('')
  // Money fields holding text they can't read — read synchronously, since the blur that
  // reports one lands in the same gesture as the Continue click (or Enter) that follows.
  const unreadable = useRef(new Set<string>())

  /** A money field's report; while one can't be read, its step neither advances nor saves
   *  (the field says why itself) — the figure it had before is never used in its place. */
  function markUnreadable(field: string, bad: boolean) {
    if (bad) unreadable.current.add(field)
    else unreadable.current.delete(field)
  }
  const hasUnreadable = () => unreadable.current.size > 0
  // Step 3's "Add a loan" form while it is open: asked before the wizard moves on or saves,
  // it holds both while it has anything not yet added (and closes if untouched).
  const loanForm = useRef<LoanFormGate | null>(null)
  const setLoanForm = useCallback((gate: LoanFormGate | null) => {
    loanForm.current = gate
  }, [])
  const loanFormHolds = () => loanForm.current?.holds() ?? false

  /** The user changed these draft fields — the errors beside them go. */
  function clearErrors(fields: readonly string[]) {
    const keys = errorKeysOf(fields)
    setErrors((e) =>
      keys.some((k) => k in e)
        ? (Object.fromEntries(Object.entries(e).filter(([k]) => !keys.includes(k))) as WizardErrors)
        : e,
    )
  }
  function go(next: WizardStep, nextErrors: WizardErrors = {}) {
    setErrors(nextErrors)
    setSaveErr('')
    setStep(next)
  }
  function advance() {
    if (loanFormHolds() || hasUnreadable()) return
    const problems = stepErrors(step, latest.current, today)
    if (Object.keys(problems).length > 0) return setErrors(problems)
    go(WIZARD_STEPS[WIZARD_STEPS.indexOf(step) + 1]!)
  }
  function back() {
    go(WIZARD_STEPS[WIZARD_STEPS.indexOf(step) - 1]!)
  }
  function pick(key: string) {
    const { ownership } = latest.current
    // A loan belongs only to a category that asks about one — switching away drops it.
    update({
      cat: key,
      ownership: canHaveLoan(key) ? ownership : { ...ownership, loanId: '', newLoan: null },
    })
    go('nameValue')
  }
  return {
    step,
    draft,
    update,
    latest,
    errors,
    setErrors,
    clearErrors,
    markUnreadable,
    hasUnreadable,
    setLoanForm,
    loanFormHolds,
    saveErr,
    setSaveErr,
    go,
    advance,
    back,
    pick,
  }
}

export type WizardFlow = ReturnType<typeof useWizardFlow>

/** One save at a time: the staged loan (once), then the asset. A refusal goes back to the
 *  step that shows the field it names, or into the dialog. */
export function useWizardSave(
  flow: WizardFlow,
  today: string,
  staged: StagedLoan,
  onCreated: (asset: OwnedAsset) => void,
) {
  const [saving, setSaving] = useState(false)
  const savingRef = useRef(false)

  function showRefusal(err: unknown) {
    if (err instanceof LoanRefused) return flow.go('ownership', stagedLoanErrors(err.refusal))
    const field = err instanceof AssetsApiError ? err.field : null
    const owner = stepOfField(field)
    if (owner && field) return flow.go(owner, { [field]: (err as AssetsApiError).message })
    flow.setSaveErr(err instanceof AssetsApiError ? err.message : CREATE_FAILED)
  }

  async function save() {
    if (savingRef.current || flow.loanFormHolds() || flow.hasUnreadable()) return
    const invalid = firstInvalidStep(flow.latest.current, today)
    if (invalid) return flow.go(invalid.step, invalid.errors)
    savingRef.current = true
    setSaving(true)
    flow.setErrors({})
    flow.setSaveErr('')
    let created: OwnedAsset
    try {
      const loanId = await staged.resolveLoanId(flow.latest.current.ownership)
      created = await api.createAsset(createBody(flow.latest.current, loanId))
    } catch (err) {
      savingRef.current = false
      setSaving(false)
      showRefusal(err)
      return
    }
    // Saved — this dialog is done, and stays "saving" so nothing can post it a second time.
    onCreated(created)
  }

  return { saving, savingRef, save }
}
