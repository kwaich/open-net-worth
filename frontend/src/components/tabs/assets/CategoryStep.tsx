// "What is it?" — the create wizard's step 1: a tile per category (tapping one advances), a
// last "New category" tile that opens its name field under the grid, and — as the step's
// footer — the quiet Lucius link.
import { useId, useState } from 'react'
import type { AssetCategory } from '../../../types/assets'
import { Spark } from '../../../lucius/identity'
import { Button, Text, TextField } from '../../ui'
import { getCurrencySymbol } from '../../../utils/format'
import { AssetsApiError, TEXT_CAPS } from './api'
import { Icons } from './icons'

const otherIcon = Icons.other!
const plusIcon = Icons.plus!

export interface CategoryStepProps {
  cats: AssetCategory[]
  /** The "Create a category" field's text — held by the caller, since it counts as input. */
  newCatLabel: string
  onNewCatLabel(label: string): void
  onPick(key: string): void
  onCreateCategory(label: string): Promise<AssetCategory>
}

export function CategoryStep({
  cats,
  newCatLabel,
  onNewCatLabel,
  onPick,
  onCreateCategory,
}: CategoryStepProps) {
  const [open, setOpen] = useState(newCatLabel !== '')
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')

  function close() {
    setOpen(false)
    onNewCatLabel('')
    setError('')
  }

  async function create() {
    const label = newCatLabel.trim()
    if (!label || creating) return
    setCreating(true)
    setError('')
    try {
      const created = await onCreateCategory(label)
      onNewCatLabel('')
      setOpen(false)
      onPick(created.key)
    } catch (err) {
      setError(err instanceof AssetsApiError ? err.message : "That didn't save — try again.")
    } finally {
      setCreating(false)
    }
  }

  return (
    <>
      <div className="as-pick">
        {cats.map((k) => (
          <button key={k.key} type="button" onClick={() => onPick(k.key)}>
            <span style={{ color: k.color }}>
              {(Icons[k.key] ?? otherIcon)({ width: 24, height: 24 })}
            </span>
            {k.label}
          </button>
        ))}
        <button
          type="button"
          className="as-pick-new"
          aria-expanded={open}
          onClick={() => (open ? close() : setOpen(true))}
        >
          {plusIcon({ width: 24, height: 24 })}
          New category
        </button>
      </div>
      {open && (
        <div className="as-wizard-pinned">
          <div className="as-newcat">
            <TextField
              autoFocus
              value={newCatLabel}
              maxLength={TEXT_CAPS.categoryLabel}
              placeholder="e.g. Boats, Wine collection"
              aria-label="New category name"
              onChange={(e) => onNewCatLabel(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void create()
              }}
            />
            <Button
              variant="primary"
              disabled={!newCatLabel.trim() || creating}
              onClick={() => void create()}
            >
              Create
            </Button>
            <Button variant="ghost" onClick={close}>
              Cancel
            </Button>
          </div>
          {error && (
            <div role="alert">
              <Text size="xs" tone="bad">
                {error}
              </Text>
            </div>
          )}
        </div>
      )}
    </>
  )
}

/** Step 1's footer: hand the whole job to Lucius instead — a card of its own, tinted with
 *  Lucius's colour, showing what to say rather than describing it (owner, 2026-09-28). */
export function DescribeToLucius({ onAsk }: { onAsk(): void }) {
  const id = useId()
  const example = `my 2019 Rolex, bought for ${getCurrencySymbol()}9,000`
  return (
    <button
      type="button"
      className="as-lucius-cta"
      aria-labelledby={`${id}-title`}
      aria-describedby={`${id}-sub`}
      onClick={onAsk}
    >
      <span className="as-lucius-cta-icon" aria-hidden="true">
        <Spark size={16} />
      </span>
      <span className="as-lucius-cta-text">
        <span id={`${id}-title`} className="as-lucius-cta-title">
          Describe it to Lucius
        </span>
        <span id={`${id}-sub`} className="as-lucius-cta-sub">
          Say it in your own words, like “{example}”. Lucius adds it after you confirm.
        </span>
      </span>
    </button>
  )
}
