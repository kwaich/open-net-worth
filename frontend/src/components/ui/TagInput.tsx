import { useState } from 'react'
import type { ClipboardEvent, KeyboardEvent } from 'react'
import { Tag } from './Tag'

export interface TagInputProps {
  value: string[]
  onChange: (next: string[]) => void
  placeholder?: string
  id?: string
  /** Caller-side cap — once value.length reaches it, further adds (typed or pasted) are
   *  blocked. A typed draft is left in place (not cleared) so the user sees why Enter
   *  did nothing; excess pasted tokens are silently dropped. Omit for no cap. */
  max?: number
  /** Forwarded onto the underlying text input — lets FormField's aria wiring reach it. */
  'aria-describedby'?: string
  'aria-invalid'?: boolean
}

/**
 * TagInput — chips + free-text entry on one wrapping row. Enter or comma adds
 * the current entry; Backspace on an empty field removes the last tag; pasting
 * a whitespace/comma-separated block splits it into multiple tags at once.
 * Token-driven mirror of .txn-tag-input.
 */
export function TagInput({
  value,
  onChange,
  placeholder,
  id,
  max,
  'aria-describedby': ariaDescribedBy,
  'aria-invalid': ariaInvalid,
}: TagInputProps) {
  const [draft, setDraft] = useState('')
  const atCap = max != null && value.length >= max

  const add = () => {
    const t = draft.trim()
    if (atCap) return
    if (t && !value.includes(t)) onChange([...value, t])
    setDraft('')
  }
  const removeAt = (i: number) => onChange(value.filter((_, idx) => idx !== i))

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      add()
    } else if (e.key === 'Backspace' && draft === '' && value.length > 0) {
      onChange(value.slice(0, -1))
    }
  }

  // Additive: a paste that splits into MULTIPLE tokens is treated as a bulk add. A
  // single-token paste falls through to the browser's default paste-into-input
  // behavior (still editable text, not an implicit add) — existing txn-tag usage is
  // unaffected since it never pastes a multi-token block.
  const onPaste = (e: ClipboardEvent<HTMLInputElement>) => {
    const text = e.clipboardData.getData('text')
    const tokens = text
      .split(/[\s,]+/)
      .map((t) => t.trim())
      .filter(Boolean)
    if (tokens.length <= 1) return
    e.preventDefault()
    const room = max != null ? Math.max(0, max - value.length) : tokens.length
    const seen = new Set(value)
    const toAdd: string[] = []
    for (const t of tokens) {
      if (toAdd.length >= room) break
      if (seen.has(t)) continue
      seen.add(t)
      toAdd.push(t)
    }
    if (toAdd.length) onChange([...value, ...toAdd])
    setDraft('')
  }

  return (
    <div className="ui-taginput">
      {value.map((t, i) => (
        <Tag key={t} onRemove={() => removeAt(i)}>
          {t}
        </Tag>
      ))}
      <input
        id={id}
        value={draft}
        placeholder={value.length === 0 ? placeholder : undefined}
        aria-describedby={ariaDescribedBy}
        aria-invalid={ariaInvalid || undefined}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKeyDown}
        onPaste={onPaste}
        onBlur={add}
      />
    </div>
  )
}
