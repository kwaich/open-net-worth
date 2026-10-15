// "Is it all yours?" — the create wizard's step 3, reused verbatim by the asset page's
// Ownership editor. Controlled: the caller owns the value, the errors and the save.
import { useId, useState } from 'react'
import type { AssetLoan } from '../../../types/assets'
import {
  Button,
  FormField,
  MoneyField,
  SegmentedControl,
  Select,
  Stack,
  Text,
  TextField,
  type SelectOption,
} from '../../ui'
import { TEXT_CAPS } from './api'
import { canHaveLoan } from './assetCategoryRules'
import {
  DEFAULT_SHARED_PCT,
  NEW_LOAN,
  type OwnershipErrors,
  type OwnershipValue,
} from './assetForms'
import { fmt, loanOptionState } from './assetsData'
import { Icons } from './icons'
import { InlineAddLoan, type LoanFormGate } from './InlineAddLoan'

export type { OwnershipErrors, OwnershipValue } from './assetForms'

export interface OwnershipStepProps {
  category: string
  value: OwnershipValue
  /** One change as a patch — the owner merges it into its newest value with `withOwnership`
   *  (which also starts a share at 50 the moment Shared is picked). */
  onChange(patch: Partial<OwnershipValue>): void
  /** Every loan account; one held by another live asset is shown but can't be picked. */
  loans: AssetLoan[]
  /** The asset being edited, so its own loan stays selectable — null while creating. */
  assetId: string | null
  /** The currency the amount owed is typed in. */
  currency: string
  errors: OwnershipErrors
  /** A money field's text can't be read (true) or can again (false) — its owner must not save. */
  onUnreadable?(field: string, unreadable: boolean): void
  /** The open "Add a loan" form's gate (null once it closes) — the owner asks it before its
   *  primary goes, so an un-added loan is never applied or dropped silently. */
  onLoanForm?(gate: LoanFormGate | null): void
}

type Owner = 'mine' | 'shared'

function loanOptions(loans: AssetLoan[], assetId: string | null, value: OwnershipValue) {
  const options: SelectOption[] = [{ value: '', label: 'No loan' }]
  for (const loan of loans) {
    const state = loanOptionState(loan, assetId)
    const label = `${loan.name} · ${fmt(loan.balance)}`
    if (typeof state !== 'object') options.push({ value: loan.id, label })
    // A legacy double link (RFC Q7): the loan this asset already has stays its own, even when
    // an older asset holds it too — only a loan held by ANOTHER asset is refused.
    else if (loan.id === value.loanId)
      options.push({ value: loan.id, label: `${label} (also secures ${state.heldBy.name})` })
    else
      options.push({
        value: loan.id,
        label: `${label} (secures ${state.heldBy.name})`,
        disabled: true,
      })
  }
  // The linked loan is no longer an active account (archived) — show it, never "No loan".
  const current = value.loanId
  if (current && current !== NEW_LOAN && !loans.some((l) => l.id === current))
    options.push({ value: current, label: 'Linked loan (no longer active)' })
  if (value.newLoan) {
    const { name, owed } = value.newLoan
    options.push({ value: NEW_LOAN, label: `${name} · ${fmt(owed)} (new)` })
  }
  return options
}

interface SharePanelProps {
  id: string
  value: OwnershipValue
  onChange(patch: Partial<OwnershipValue>): void
  error: string | undefined
  /** A money field's text can't be read (true) or can again (false) — its owner must not save. */
  onUnreadable?(field: string, unreadable: boolean): void
}

function SharePanel({ id, value, onChange, error, onUnreadable }: SharePanelProps) {
  return (
    <div className="as-form">
      <FormField
        label="Your share"
        htmlFor={`${id}-share`}
        help="Net worth counts only your share."
        error={error}
      >
        <MoneyField
          id={`${id}-share`}
          suffix="%"
          invalid={!!error}
          value={value.sharePct ?? DEFAULT_SHARED_PCT}
          formattedValue={String(value.sharePct ?? DEFAULT_SHARED_PCT)}
          onValueChange={(sharePct) => onChange({ sharePct })}
          onInvalidChange={(bad) => onUnreadable?.('share_pct', bad)}
        />
      </FormField>
      <FormField label="Shared with (optional)" htmlFor={`${id}-with`}>
        <TextField
          id={`${id}-with`}
          value={value.shareNote}
          maxLength={TEXT_CAPS.shareNote}
          placeholder="e.g. joint with Alex"
          onChange={(e) => onChange({ shareNote: e.target.value })}
        />
      </FormField>
    </div>
  )
}

type LoanPickerProps = Omit<OwnershipStepProps, 'category'> & { id: string }

/** "Any loan against it?" — the loan select (or the no-loans note) and "+ Add a loan". */
function LoanPicker(props: LoanPickerProps) {
  const { id, value, onChange, loans, assetId, currency, errors, onUnreadable } = props
  const loanRefusal = errors.loan_name ?? errors.loan_owed ?? null
  const [adding, setAdding] = useState(loanRefusal != null)
  // A refused staged loan reopens its form, so the reason shows beside the field it names.
  const [seenRefusal, setSeenRefusal] = useState(loanRefusal)
  if (loanRefusal !== seenRefusal) {
    setSeenRefusal(loanRefusal)
    if (loanRefusal != null) setAdding(true)
  }
  const options = loanOptions(loans, assetId, value)

  return (
    <Stack gap="--space-3">
      {options.length > 1 ? (
        <FormField
          label="Any loan against it?"
          htmlFor={`${id}-loan`}
          span="full"
          error={errors.loan_account_id}
          help={value.loanId === NEW_LOAN ? 'Added to Accounts when you save.' : undefined}
        >
          <Select
            id={`${id}-loan`}
            invalid={!!errors.loan_account_id}
            value={value.loanId}
            options={options}
            onChange={(e) => onChange({ loanId: e.target.value })}
          />
        </FormField>
      ) : (
        <FormField label="Any loan against it?" span="full" error={errors.loan_account_id}>
          <Text size="sm" tone="muted">
            No loan accounts yet.
          </Text>
        </FormField>
      )}
      {adding ? (
        <InlineAddLoan
          initial={value.newLoan}
          currency={currency}
          errors={{ name: errors.loan_name, owed: errors.loan_owed }}
          onAdd={(newLoan) => {
            onChange({ newLoan, loanId: NEW_LOAN })
            setAdding(false)
          }}
          onCancel={() => setAdding(false)}
          onGate={props.onLoanForm}
          onUnreadable={(bad) => onUnreadable?.('loan_owed', bad)}
        />
      ) : (
        <div>
          <Button
            variant="ghost"
            size="sm"
            className="as-flush-ghost"
            iconLeft={Icons.plus!({ width: 13, height: 13 })}
            onClick={() => setAdding(true)}
          >
            {value.newLoan ? 'Edit new loan' : 'Add a loan'}
          </Button>
        </div>
      )}
    </Stack>
  )
}

export function OwnershipStep(props: OwnershipStepProps) {
  const { category, value, onChange, errors } = props
  const id = useId()
  // Only the choice itself — the owner's `withOwnership` starts a new share at 50.
  const pickOwner = (owner: Owner) => onChange({ shared: owner === 'shared' })
  // A loan an asset already has stays visible whatever its category, so it can be unlinked.
  const showLoan = canHaveLoan(category) || value.loanId !== '' || value.newLoan != null

  return (
    <Stack gap="--space-6">
      <SegmentedControl<Owner>
        ariaLabel="Is it all yours?"
        autoFocus
        options={[
          { value: 'mine', label: 'All mine' },
          { value: 'shared', label: 'Shared' },
        ]}
        value={value.shared ? 'shared' : 'mine'}
        onChange={pickOwner}
      />
      {value.shared && (
        <SharePanel
          id={id}
          value={value}
          onChange={onChange}
          error={errors.share_pct}
          onUnreadable={props.onUnreadable}
        />
      )}
      {showLoan && <LoanPicker id={id} {...props} />}
    </Stack>
  )
}
