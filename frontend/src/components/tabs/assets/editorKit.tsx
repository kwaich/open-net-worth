// What every dialog on the asset page does with a save: it stays open until the server
// answers, sends one request at a time (a double-click saves once), and shows a problem — its
// own check or the server's refusal — beside the field it names, or at the dialog's own level
// when it names none of this dialog's fields (RFC assets-create-edit-redesign §3.1). Editing a
// field clears what was said beside it. Nothing typed is lost on a refusal.
import { useCallback, useRef, useState, type ReactNode } from 'react'
import { Button, Text } from '../../ui'
import { AssetsApiError } from './api'

export interface EditorSave {
  saving: boolean
  /** Runs one write. Resolves true once it succeeded; a call while one is running is ignored. */
  save(write: () => Promise<unknown>): Promise<boolean>
  /** Shows the dialog's own checks beside their fields (replacing the last ones). True when
   *  there is nothing to show — the save may go ahead. */
  check(problems: Partial<Record<string, string>>): boolean
  /** The error beside `field`: the dialog's own check first, else the server's refusal. */
  errorFor(field: string): string | undefined
  /** The user changed these fields — whatever was said beside them no longer applies. */
  edited(...fields: string[]): void
  /** A money field's report that its text can't be read: while any can't, check() refuses to
   *  save — the last good figure is never sent in its place (the field says why itself). */
  unreadable(field: string, unreadable: boolean): void
  /** The refusal's sentence when it names none of `fields` — said at the dialog's level. */
  dialogError(fields: readonly string[]): string | null
}

function asApiError(e: unknown): AssetsApiError {
  if (e instanceof AssetsApiError) return e
  const message = e instanceof Error && e.message ? e.message : "That didn't save. Try again."
  return new AssetsApiError(message, 0, null, null, null, null)
}

function without<T extends object>(map: T, keys: readonly string[]): T {
  if (!keys.some((k) => k in map)) return map
  return Object.fromEntries(Object.entries(map).filter(([k]) => !keys.includes(k))) as T
}

export function useEditorSave(): EditorSave {
  const [saving, setSaving] = useState(false)
  const [refusal, setRefusal] = useState<AssetsApiError | null>(null)
  const [checked, setChecked] = useState<Record<string, string>>({})
  const running = useRef(false)
  // Read synchronously: the blur that reports unreadable text lands in the same gesture as
  // the Save click (or Enter) that asks to save.
  const unreadableFields = useRef(new Set<string>())

  const save = useCallback(async (write: () => Promise<unknown>) => {
    if (running.current) return false
    running.current = true
    setSaving(true)
    setRefusal(null)
    try {
      await write()
      return true
    } catch (e) {
      setRefusal(asApiError(e))
      return false
    } finally {
      running.current = false
      setSaving(false)
    }
  }, [])

  const check = useCallback((problems: Partial<Record<string, string>>) => {
    const unreadable = unreadableFields.current
    // A field holding unreadable text shows its own message — no second one beside it.
    const found = Object.fromEntries(
      Object.entries(problems).filter(
        (entry): entry is [string, string] => Boolean(entry[1]) && !unreadable.has(entry[0]),
      ),
    )
    setChecked(found)
    return Object.keys(found).length === 0 && unreadable.size === 0
  }, [])

  const unreadable = useCallback((field: string, bad: boolean) => {
    if (bad) unreadableFields.current.add(field)
    else unreadableFields.current.delete(field)
  }, [])

  const edited = useCallback((...fields: string[]) => {
    setChecked((c) => without(c, fields))
    setRefusal((r) => (r?.field && fields.includes(r.field) ? null : r))
  }, [])

  return {
    saving,
    save,
    check,
    edited,
    unreadable,
    errorFor: (field) => checked[field] ?? (refusal?.field === field ? refusal.message : undefined),
    dialogError: (fields) =>
      refusal && !(refusal.field && fields.includes(refusal.field)) ? refusal.message : null,
  }
}

/** A dialog-level refusal (AccountModal's save-error pattern). */
export function SaveError({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <div role="alert">
      <Text size="xs" tone="bad">
        {message}
      </Text>
    </div>
  )
}

export interface EditorFooterProps {
  /** A side action on the footer's left — e.g. Correct this value's "Remove value". */
  start?: ReactNode
  saving: boolean
  /** The primary button's label while idle. */
  label: string
  onCancel(): void
  onSave(): void
  disabled?: boolean
}

export function EditorFooter(props: EditorFooterProps) {
  const { start, saving, label, onCancel, onSave, disabled } = props
  return (
    <>
      {start}
      <Button variant="ghost" size="sm" onClick={onCancel}>
        Cancel
      </Button>
      <Button variant="primary" size="sm" disabled={saving || disabled} onClick={onSave}>
        {saving ? 'Saving…' : label}
      </Button>
    </>
  )
}
