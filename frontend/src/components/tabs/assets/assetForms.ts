// Values and validation behind the create wizard's steps — and behind the asset page's section
// editors, which reuse the same step components verbatim. Pure: no React, no requests.
import type { OwnedAsset } from '../../../types/assets'
import { AssetsApiError, type CreateAssetBody, type NewLoan } from './api'

/** Is it all yours, and is a loan secured against it. `loanId` is '' for no loan, a loan
 *  account's id, or NEW_LOAN while `newLoan` is staged — a staged loan is created in Accounts
 *  only when the form it belongs to is saved. */
export interface OwnershipValue {
  shared: boolean
  sharePct: number | null
  shareNote: string
  loanId: string
  newLoan: NewLoan | null
}

/** What was paid and when (for a policy: premiums paid so far, and its start). `costDate` is
 *  '' when not set. */
export interface PurchaseValue {
  cost: number | null
  costDate: string
}

export type OwnershipErrors = Partial<
  Record<'share_pct' | 'loan_account_id' | 'loan_name' | 'loan_owed', string>
>
export type PurchaseErrors = Partial<Record<'cost' | 'cost_date', string>>
export type NameValueErrors = Partial<Record<'name' | 'value', string>>
export type NewLoanErrors = Partial<Record<'name' | 'owed', string>>

/** The loan select's value for a staged loan that has no account yet — never an account id. */
export const NEW_LOAN = '__new_loan__'

export const EMPTY_OWNERSHIP: OwnershipValue = {
  shared: false,
  sharePct: null,
  shareNote: '',
  loanId: '',
  newLoan: null,
}

export const EMPTY_PURCHASE: PurchaseValue = { cost: null, costDate: '' }

/** "Your share" starts here the moment Shared is picked — never blank, never 100. */
export const DEFAULT_SHARED_PCT = 50

export function nameValueErrors(name: string, value: number | null): NameValueErrors {
  const errors: NameValueErrors = {}
  if (!name.trim()) errors.name = 'Enter a name.'
  if (value == null) errors.value = "Enter what it's worth today."
  else if (value <= 0) errors.value = 'Enter a value greater than 0.'
  return errors
}

export function ownershipErrors(v: OwnershipValue): OwnershipErrors {
  if (!v.shared) return {}
  const pct = v.sharePct
  return pct == null || pct < 0 || pct > 100 ? { share_pct: 'Enter a share from 0 to 100.' } : {}
}

/** `today` is the local ISO date — a purchase can't be dated after it. */
export function purchaseErrors(v: PurchaseValue, today: string): PurchaseErrors {
  const errors: PurchaseErrors = {}
  if (v.cost != null && v.cost <= 0)
    errors.cost = 'Enter a value greater than 0, or leave it blank.'
  if (v.costDate && v.costDate > today) errors.cost_date = "Pick a date that isn't in the future."
  return errors
}

export function newLoanErrors(name: string, owed: number | null): NewLoanErrors {
  const errors: NewLoanErrors = {}
  if (!name.trim()) errors.name = 'Enter a name for this loan.'
  if (owed == null || owed <= 0) errors.owed = 'Enter an amount greater than 0.'
  return errors
}

/** A step reports each change as a patch; its owner merges it into its NEWEST value with
 *  these — never a copy from the step's last render. A browser's autofill (or a script's
 *  fill()) fires a field's blur commit and the next field's change in one task, before React
 *  renders in between, so a step that rebuilt its whole value from its props dropped the first
 *  change. Picking Shared starts the share at DEFAULT_SHARED_PCT — never blank, never 100. */
export function withOwnership(v: OwnershipValue, patch: Partial<OwnershipValue>): OwnershipValue {
  const next = { ...v, ...patch }
  return next.shared && next.sharePct == null ? { ...next, sharePct: DEFAULT_SHARED_PCT } : next
}

export function withPurchase(v: PurchaseValue, patch: Partial<PurchaseValue>): PurchaseValue {
  return { ...v, ...patch }
}

// The errors each draft field answers: editing a field clears the error shown beside it.
const ERRORS_OF: Record<string, readonly string[]> = {
  name: ['name'],
  value: ['value'],
  shared: ['share_pct'],
  sharePct: ['share_pct'],
  loanId: ['loan_account_id'],
  newLoan: ['loan_account_id', 'loan_name', 'loan_owed'],
  cost: ['cost'],
  costDate: ['cost_date'],
}

/** The error keys the changed draft `fields` answer. */
export function errorKeysOf(fields: readonly string[]): string[] {
  return fields.flatMap((f) => ERRORS_OF[f] ?? [])
}

/** The share to store: the typed one while shared, else all of it. */
export function sharePctOf(v: OwnershipValue): number {
  return v.shared ? (v.sharePct ?? DEFAULT_SHARED_PCT) : 100
}

export function ownershipFromAsset(a: OwnedAsset): OwnershipValue {
  const shared = a.share_pct < 100
  return {
    shared,
    sharePct: shared ? a.share_pct : null,
    shareNote: a.share_note,
    loanId: a.loan_account_id ?? '',
    newLoan: null,
  }
}

export function purchaseFromAsset(a: OwnedAsset): PurchaseValue {
  return { cost: a.cost, costDate: a.cost_date ?? '' }
}

/** Shown when a save fails without a reason the server put into words. */
export const CREATE_FAILED = "We couldn't add this asset. Check the details and try again."

/** A refused staged loan (POST /api/accounts), beside the loan field it names — its name, its
 *  amount, or else the loan picker itself. */
export function stagedLoanErrors(err: unknown): OwnershipErrors {
  const message = err instanceof AssetsApiError ? err.message : CREATE_FAILED
  const field = err instanceof AssetsApiError ? err.field : null
  if (field === 'name') return { loan_name: message }
  if (field === 'initial_balance') return { loan_owed: message }
  return { loan_account_id: message }
}

// ── the create wizard ────────────────────────────────────────

export type WizardStep = 'cat' | 'nameValue' | 'ownership' | 'purchase'

export const WIZARD_STEPS: WizardStep[] = ['cat', 'nameValue', 'ownership', 'purchase']

export interface WizardDraft {
  cat: string | null
  /** Step 1's half-typed "Create a category" label — it counts as input. */
  newCatLabel: string
  name: string
  value: number | null
  ownership: OwnershipValue
  purchase: PurchaseValue
}

/** Every step's field errors share one map — the keys are the request fields they belong to. */
export type WizardErrors = NameValueErrors & OwnershipErrors & PurchaseErrors

export function emptyDraft(cat: string | null): WizardDraft {
  return {
    cat,
    newCatLabel: '',
    name: '',
    value: null,
    ownership: EMPTY_OWNERSHIP,
    purchase: EMPTY_PURCHASE,
  }
}

export function stepErrors(step: WizardStep, d: WizardDraft, today: string): WizardErrors {
  if (step === 'nameValue') return nameValueErrors(d.name, d.value)
  if (step === 'ownership') return ownershipErrors(d.ownership)
  if (step === 'purchase') return purchaseErrors(d.purchase, today)
  return {}
}

/** The first step whose input is invalid, with its errors — null when all of them pass. */
export function firstInvalidStep(
  d: WizardDraft,
  today: string,
): { step: WizardStep; errors: WizardErrors } | null {
  for (const step of WIZARD_STEPS) {
    const errors = stepErrors(step, d, today)
    if (Object.keys(errors).length > 0) return { step, errors }
  }
  return null
}

/** Whether closing now would lose anything typed. Picking a category alone is not input. */
export function isDirty(d: WizardDraft): boolean {
  const o = d.ownership
  return (
    d.name.trim() !== '' ||
    d.value != null ||
    (o.shared && o.sharePct !== 100) ||
    o.shareNote.trim() !== '' ||
    o.loanId !== '' ||
    o.newLoan != null ||
    d.purchase.cost != null ||
    d.purchase.costDate !== '' ||
    d.newCatLabel.trim() !== ''
  )
}

const FIELD_STEP: Record<string, WizardStep> = {
  name: 'nameValue',
  value: 'nameValue',
  share_pct: 'ownership',
  loan_account_id: 'ownership',
  cost: 'purchase',
  cost_date: 'purchase',
}

/** The step that shows a request field — null for a message that belongs to the dialog. */
export function stepOfField(field: string | null): WizardStep | null {
  return field ? (FIELD_STEP[field] ?? null) : null
}

/** POST /api/assets's body: all of it unless shared, and only what was given. */
export function createBody(d: WizardDraft, loanId: string | null): CreateAssetBody {
  const body: CreateAssetBody = {
    name: d.name.trim(),
    category: d.cat ?? 'other',
    value: d.value ?? 0,
    share_pct: sharePctOf(d.ownership),
  }
  const shareNote = d.ownership.shared ? d.ownership.shareNote.trim() : ''
  if (shareNote) body.share_note = shareNote
  if (loanId) body.loan_account_id = loanId
  if (d.purchase.cost != null) body.cost = d.purchase.cost
  if (d.purchase.costDate) body.cost_date = d.purchase.costDate
  return body
}
