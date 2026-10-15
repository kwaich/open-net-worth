// "+ Add a loan" — the two-field form inside OwnershipStep. "Add loan" only STAGES the loan:
// nothing is written until the form the step belongs to is saved, so abandoning the flow
// creates no account (RFC assets-create-edit-redesign §3.10). While the form is open with
// anything not yet added, the dialog's primary waits for it (review ruling R13).
import { useEffect, useId, useRef, useState, type KeyboardEvent, type RefObject } from 'react'
import { fNum, getCurrencySymbol } from '../../../utils/format'
import { Button, FormField, MoneyField, Text, TextField } from '../../ui'
import type { NewLoan } from './api'
import { newLoanErrors, type NewLoanErrors } from './assetForms'
import { useSyncedDraft } from './useSyncedDraft'

/** The open form's say over its dialog's primary (Continue, Add asset, Save changes). */
export interface LoanFormGate {
  /** Asked before the primary goes. Untouched, the form closes as Cancel would and lets it go
   *  (false). Holding changes, or an amount it can't read, it says "Add or cancel this loan
   *  first." beside itself, takes keyboard focus and holds it (true) — what was typed is never
   *  applied, or dropped, without the user choosing. */
  holds(): boolean
}

export interface InlineAddLoanProps {
  /** A loan already staged — reopening the form edits it. */
  initial: NewLoan | null
  currency: string
  /** A server refusal of the staged loan, shown beside the field it names. */
  errors: NewLoanErrors
  onAdd(loan: NewLoan): void
  onCancel(): void
  /** The form's gate while it is open; null once it closes. */
  onGate?(gate: LoanFormGate | null): void
  /** "Amount owed" holds text it can't read (true), or no longer does (false). */
  onUnreadable?(unreadable: boolean): void
}

/** Hands the open form's gate to its dialog; true while it is holding the dialog's primary. */
function useLoanFormGate(
  onGate: InlineAddLoanProps['onGate'],
  box: RefObject<HTMLDivElement | null>,
  touched: () => boolean,
  onCancel: () => void,
) {
  const [holding, setHolding] = useState(false)
  // The gate is handed over once, so it reads the newest of these.
  const current = useRef({ touched, onCancel })
  useEffect(() => {
    current.current = { touched, onCancel }
  })
  useEffect(() => {
    if (!onGate) return
    onGate({
      holds() {
        if (!current.current.touched()) {
          current.current.onCancel()
          return false
        }
        setHolding(true)
        box.current?.focus()
        return true
      },
    })
    return () => onGate(null)
  }, [onGate, box])
  return [holding, setHolding] as const
}

export function InlineAddLoan(props: InlineAddLoanProps) {
  const { initial, currency, errors, onAdd, onCancel } = props
  const id = useId()
  const [draft, update, latest] = useSyncedDraft<{ name: string; owed: number | null }>({
    name: initial?.name ?? '',
    owed: initial?.owed ?? null,
  })
  // What the form opened with — the staged loan's own figures, or nothing yet.
  const [start] = useState(() => ({
    name: (initial?.name ?? '').trim(),
    owed: initial?.owed ?? null,
  }))
  // "Amount owed" holds text it can't read: never stage the figure it had before.
  const owedUnreadable = useRef(false)
  const box = useRef<HTMLDivElement>(null)
  const touched = () => {
    const { name, owed } = latest.current
    return owedUnreadable.current || name.trim() !== start.name || owed !== start.owed
  }
  const [holding, setHolding] = useLoanFormGate(props.onGate, box, touched, onCancel)
  const [checked, setChecked] = useState<NewLoanErrors>({})
  // A server refusal stays beside its field until that field is edited; a new refusal shows.
  const [edited, setEdited] = useState<Partial<Record<keyof NewLoanErrors, true>>>({})
  const [refusal, setRefusal] = useState(errors)
  if (refusal.name !== errors.name || refusal.owed !== errors.owed) {
    setRefusal(errors)
    setEdited({})
  }
  const shown = {
    name: checked.name ?? (edited.name ? undefined : errors.name),
    owed: checked.owed ?? (edited.owed ? undefined : errors.owed),
  }

  function edit(patch: Partial<typeof draft>) {
    update(patch)
    setHolding(false)
    const fields = Object.keys(patch) as (keyof NewLoanErrors)[]
    setChecked((c) => {
      const next = { ...c }
      for (const f of fields) delete next[f]
      return next
    })
    setEdited((e) => ({ ...e, ...Object.fromEntries(fields.map((f) => [f, true])) }))
  }

  function add() {
    if (owedUnreadable.current) return
    const { name, owed } = latest.current
    const problems = newLoanErrors(name, owed)
    setChecked(problems)
    if (problems.name || problems.owed || owed == null) return
    onAdd({ name: name.trim(), owed })
  }

  // Enter adds the loan and Escape closes this form — neither may reach the dialog around it
  // (which would advance the step, or ask to discard the whole asset).
  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'Escape') {
      e.stopPropagation()
      onCancel()
    } else if (e.key === 'Enter' && e.target instanceof HTMLInputElement) {
      if (e.nativeEvent.isComposing) return
      e.preventDefault()
      e.stopPropagation()
      // A held Enter repeats keydown — add once, never again from the same press.
      if (!e.repeat) add()
    }
  }

  return (
    <div
      ref={box}
      className="as-loan-add"
      role="group"
      aria-label="New loan account"
      aria-describedby={holding ? `${id}-hold` : undefined}
      // Where focus goes when this form holds the dialog's primary.
      tabIndex={-1}
      data-modal-escape-layer
      onKeyDown={onKeyDown}
    >
      <Text as="p" size="sm" weight={600}>
        New loan account
      </Text>
      <Text as="p" size="xs" tone="muted">
        Added to Accounts as a loan when you save — what you owe today lowers your net worth.
      </Text>
      <div className="as-form">
        <FormField label="Loan name" htmlFor={`${id}-name`} error={shown.name}>
          <TextField
            id={`${id}-name`}
            autoFocus
            invalid={!!shown.name}
            value={draft.name}
            placeholder="e.g. HDB Mortgage"
            onChange={(e) => edit({ name: e.target.value })}
          />
        </FormField>
        <FormField label="Amount owed" htmlFor={`${id}-owed`} error={shown.owed}>
          <MoneyField
            id={`${id}-owed`}
            allowEmpty
            invalid={!!shown.owed}
            prefix={getCurrencySymbol(currency)}
            value={draft.owed}
            formattedValue={draft.owed == null ? '' : fNum(draft.owed)}
            onValueChange={(owed) => edit({ owed })}
            onInvalidChange={(bad) => {
              owedUnreadable.current = bad
              props.onUnreadable?.(bad)
            }}
          />
        </FormField>
      </div>
      <div className="as-loan-add-row">
        <Button variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        {/* Secondary: the dialog's own footer keeps the one primary action. */}
        <Button variant="secondary" size="sm" onClick={add}>
          Add loan
        </Button>
      </div>
      {holding && (
        <p className="ui-field__error" id={`${id}-hold`} role="alert">
          Add or cancel this loan first.
        </p>
      )}
    </div>
  )
}
