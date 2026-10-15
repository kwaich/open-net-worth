// Update value and Correct this value — one form, two dialogs. Updating records a NEW dated
// value (the old one stays in history); correcting replaces the value it was opened on, in
// place (RFC assets-create-edit-redesign §3.5–3.6). A value can't be dated in the future, nor
// before the purchase — the picker stops at both, and the server says why if it refuses.
import { useId } from 'react'
import type { AssetMark, OwnedAsset } from '../../../types/assets'
import { fNum, getCurrencySymbol, localIso } from '../../../utils/format'
import { Button, DateField, FormField, Modal, MoneyField, Stack, Text, TextField } from '../../ui'
import * as api from './api'
import { notePlaceholder } from './assetCategoryRules'
import { fmt, monthYear, sharePct } from './assetsData'
import { EditorFooter, SaveError, useEditorSave } from './editorKit'
import { useSyncedDraft } from './useSyncedDraft'

export interface ValueDraft {
  value: number | null
  date: string
  note: string
}

export type ValueErrors = Partial<Record<'value' | 'date', string>>

/** `today` is the local ISO date. An empty date is left to the server (today, or unchanged). */
function valueErrors(d: ValueDraft, today: string): ValueErrors {
  const errors: ValueErrors = {}
  if (d.value == null || d.value <= 0) errors.value = 'Enter a value greater than 0.'
  if (d.date && d.date > today) errors.date = "Pick a date that isn't in the future."
  return errors
}

const FIELDS = ['value', 'date', 'note'] as const

interface ValueFormProps {
  a: OwnedAsset
  /** Correcting: the value can also be removed from here — unless it is the last one. */
  remove?: { removable: boolean; onRemove(): void }
  title: string
  valueLabel: string
  submitLabel: string
  initial: ValueDraft
  /** A plain line above the fields — the current value, when updating. */
  reference?: string | null
  hint?: string
  /** The write to run, or null when there is nothing to write. */
  submit(d: ValueDraft): (() => Promise<unknown>) | null
  onClose(): void
  onSaved(): void
}

interface ValueFieldsProps {
  a: OwnedAsset
  label: string
  /** A line under As of — what saving this does. */
  dateHelp?: string
  draft: ValueDraft
  update(patch: Partial<ValueDraft>): void
  today: string
  errors: Partial<Record<'value' | 'date' | 'note', string>>
  /** A money field's text can't be read (true) or can again (false) — its owner must not save. */
  onUnreadable?(field: string, unreadable: boolean): void
}

/** New value · As of · Note — bounded by today and, when there is one, the purchase date. */
function ValueFields(props: ValueFieldsProps) {
  const { a, label, dateHelp, draft, update, today, errors } = props
  const id = useId()
  const share = a.share_pct ?? 100
  return (
    <>
      <FormField
        label={label}
        htmlFor={`${id}-value`}
        // A co-owner who typed their half here would halve what net worth counts.
        help={
          share < 100
            ? `The whole asset's value — net worth counts your ${sharePct(share)}%.`
            : undefined
        }
        error={errors.value}
      >
        <MoneyField
          id={`${id}-value`}
          autoFocus
          allowEmpty
          invalid={!!errors.value}
          prefix={getCurrencySymbol(a.currency)}
          value={draft.value}
          formattedValue={draft.value == null ? '' : fNum(draft.value)}
          onValueChange={(value) => update({ value })}
          onInvalidChange={(bad) => props.onUnreadable?.('value', bad)}
        />
      </FormField>
      <FormField label="As of" htmlFor={`${id}-date`} help={dateHelp} error={errors.date}>
        <DateField
          id={`${id}-date`}
          invalid={!!errors.date}
          max={today}
          min={a.cost_date ?? undefined}
          value={draft.date}
          onChange={(e) => update({ date: e.target.value })}
        />
      </FormField>
      <FormField label="Note (optional)" htmlFor={`${id}-note`} error={errors.note}>
        <TextField
          id={`${id}-note`}
          invalid={!!errors.note}
          value={draft.note}
          maxLength={api.TEXT_CAPS.valueNote}
          placeholder={notePlaceholder(a.category)}
          onChange={(e) => update({ note: e.target.value })}
        />
      </FormField>
    </>
  )
}

function ValueForm(props: ValueFormProps) {
  const { a, initial, submit, onClose, onSaved } = props
  const today = localIso()
  const [draft, update, latest] = useSyncedDraft<ValueDraft>(initial)
  const { saving, save, check, errorFor, edited, unreadable, dialogError } = useEditorSave()
  // Editing a field clears whatever was said beside it.
  const edit = (patch: Partial<ValueDraft>) => {
    update(patch)
    edited(...Object.keys(patch))
  }

  async function onSave() {
    if (!check(valueErrors(latest.current, today))) return
    const write = submit(latest.current)
    if (!write) return onClose()
    if (await save(write)) onSaved()
  }

  return (
    <Modal
      className="as-modal"
      open
      onClose={onClose}
      title={props.title}
      subtitle={a.name}
      size="sm"
      footer={
        <EditorFooter
          start={
            props.remove && (
              <Button
                variant="ghost"
                size="sm"
                className="as-footer-danger"
                disabled={!props.remove.removable || saving}
                onClick={props.remove.onRemove}
              >
                Remove value
              </Button>
            )
          }
          saving={saving}
          label={props.submitLabel}
          onCancel={onClose}
          onSave={onSave}
        />
      }
    >
      <Stack gap="--space-6">
        {props.reference && (
          <Text as="p" size="sm" tone="muted">
            {props.reference}
          </Text>
        )}
        <ValueFields
          a={a}
          label={props.valueLabel}
          dateHelp={props.hint}
          draft={draft}
          update={edit}
          today={today}
          errors={{ value: errorFor('value'), date: errorFor('date'), note: errorFor('note') }}
          onUnreadable={unreadable}
        />
        {props.remove && !props.remove.removable && (
          <Text as="p" size="xs" tone="muted">
            An asset needs at least one value.
          </Text>
        )}
        <SaveError message={dialogError(FIELDS)} />
      </Stack>
    </Modal>
  )
}

export interface UpdateValueDialogProps {
  a: OwnedAsset
  onClose(): void
  /** Called with the toast to show once the value is recorded. */
  onSaved(message: string): void
}

export function UpdateValueDialog({ a, onClose, onSaved }: UpdateValueDialogProps) {
  return (
    <ValueForm
      a={a}
      title="Update value"
      valueLabel="New value"
      submitLabel="Update value"
      initial={{ value: null, date: localIso(), note: '' }}
      reference={
        a.value == null ? null : `Current value: ${fmt(a.value)} · valued ${monthYear(a.valued_on)}`
      }
      hint="This is saved as a new dated value — the old one stays in history."
      submit={(d) => () =>
        api.addMark(a.id, {
          value: d.value!,
          ...(d.date ? { date: d.date } : {}),
          ...(d.note.trim() ? { note: d.note.trim() } : {}),
        })
      }
      onClose={onClose}
      onSaved={() => onSaved('Value updated.')}
    />
  )
}

export interface CorrectValueDialogProps extends UpdateValueDialogProps {
  /** The recorded value being corrected — never the purchase point. */
  mark: AssetMark
  /** False for the asset's last value: it can be corrected, never removed. */
  removable: boolean
  /** Asks to remove this value (the caller confirms, then removes it). */
  onRemove(): void
}

export function CorrectValueDialog(props: CorrectValueDialogProps) {
  const { a, mark, onClose, onSaved } = props
  // Only what changed is sent: a correction that keeps a value's date must never be refused
  // for that date (a legacy value dated before the purchase stays correctable).
  const changes = (d: ValueDraft): api.CorrectMarkBody => ({
    ...(d.value !== mark.value ? { value: d.value! } : {}),
    ...(d.date && d.date !== mark.date ? { date: d.date } : {}),
    ...(d.note.trim() !== mark.note ? { note: d.note.trim() } : {}),
  })
  return (
    <ValueForm
      a={a}
      title="Correct this value"
      valueLabel="Value"
      submitLabel="Save correction"
      initial={{ value: mark.value, date: mark.date, note: mark.note }}
      remove={{ removable: props.removable, onRemove: props.onRemove }}
      submit={(d) => {
        const body = changes(d)
        return Object.keys(body).length ? () => api.correctMark(a.id, mark.id, body) : null
      }}
      onClose={onClose}
      onSaved={() => onSaved('Value corrected.')}
    />
  )
}
