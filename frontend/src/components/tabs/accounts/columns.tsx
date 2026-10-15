/**
 * columns.tsx — Register column-visibility model + the "Columns" toolbar picker.
 *
 * Mirrors the width-persistence pattern in Register.tsx (localStorage `acctColW_v1`):
 * here `acctColVis_v1` stores the HIDDEN set (an array of column keys), so a column
 * added later defaults VISIBLE (absence = shown). cb/clr/amount are structural and can
 * never be hidden — they never enter the set. The picker renders adjacent to the
 * date-range + Add-transaction controls (see SearchFilter.tsx `Filters`).
 */

import { useState, useRef, useEffect } from 'react'
import { REG_COLS, type RegCol } from './Register'
import { AdvCheck } from './pickers'

export const REG_VIS_KEY = 'acctColVis_v1'

// Always-visible, never offered in the picker (structural: the select box, the cleared
// toggle, and the amount are the register's spine).
export const FIXED_COL_KEYS = new Set(['cb', 'clr', 'amount'])

/** The columns a user may toggle — everything but the fixed spine, and `account`
 *  only in the all-accounts view (it's `accountOnly`). REG_COLS order preserved. */
export function toggleableCols(showAccount: boolean): RegCol[] {
  return REG_COLS.filter((c) => !FIXED_COL_KEYS.has(c.key) && (!c.accountOnly || showAccount))
}

export function loadHiddenCols(): Set<string> {
  try {
    const arr = JSON.parse(localStorage.getItem(REG_VIS_KEY) || '[]')
    // Defensive: ignore non-strings and any fixed key that shouldn't be persisted as hidden.
    return new Set(
      Array.isArray(arr) ? arr.filter((k) => typeof k === 'string' && !FIXED_COL_KEYS.has(k)) : [],
    )
  } catch {
    return new Set()
  }
}

function saveHiddenCols(hidden: Set<string>) {
  try {
    localStorage.setItem(REG_VIS_KEY, JSON.stringify([...hidden]))
  } catch {
    /* localStorage unavailable (private mode / quota) — visibility just isn't persisted. */
  }
}

export interface ColumnVisibility {
  hidden: Set<string>
  toggle: (key: string) => void
  reset: () => void
}

/** Owns the hidden-set + its persistence. Called once by AccountsTab; `hidden` flows to
 *  the Register (which filters `cols`), `toggle`/`reset` to the ColumnsPicker. */
export function useColumnVisibility(): ColumnVisibility {
  const [hidden, setHidden] = useState<Set<string>>(loadHiddenCols)
  const toggle = (key: string) =>
    setHidden((prev) => {
      if (FIXED_COL_KEYS.has(key)) return prev // structural columns can't be hidden
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      saveHiddenCols(next)
      return next
    })
  const reset = () =>
    setHidden((prev) => {
      if (prev.size === 0) return prev
      const next = new Set<string>()
      saveHiddenCols(next)
      return next
    })
  return { hidden, toggle, reset }
}

// ── ColumnsPicker ──────────────────────────────────────────────────────────

export interface ColumnsPickerProps {
  showAccount: boolean
  hidden: Set<string>
  onToggle: (key: string) => void
  onReset: () => void
}

export function ColumnsPicker({ showAccount, hidden, onToggle, onReset }: ColumnsPickerProps) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  })
  const cols = toggleableCols(showAccount)
  // "Hidden" only counts columns currently on offer — a key persisted from the all-accounts
  // view (e.g. a hidden `account`) shouldn't light up the button while a single account is open.
  const hiddenCount = cols.filter((c) => hidden.has(c.key)).length
  return (
    <div className="acct-advwrap" ref={ref}>
      <button
        className={'acct-advbtn acct-colbtn' + (hiddenCount > 0 ? ' on' : '')}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="dialog"
        aria-expanded={open}
        title="Show or hide columns"
      >
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <rect x="3" y="4" width="18" height="16" rx="2" />
          <path d="M9 4v16M15 4v16" />
        </svg>
        <span className="acct-viewlbl">
          Columns{hiddenCount > 0 ? ` • ${cols.length - hiddenCount}` : ''}
        </span>
      </button>
      {open && (
        <div className="acct-advpop acct-colpop" role="dialog" aria-label="Show or hide columns">
          <div className="acct-advbody">
            <div className="acct-advgroup">
              <div className="acct-advsec">Columns</div>
              {cols.map((c) => (
                <AdvCheck key={c.key} checked={!hidden.has(c.key)} onChange={() => onToggle(c.key)}>
                  {c.label || c.key}
                </AdvCheck>
              ))}
            </div>
          </div>
          <div className="acct-advfoot">
            <button className="acct-advclear" onClick={onReset} disabled={hiddenCount === 0}>
              Show all
            </button>
            <button className="acct-advdone" onClick={() => setOpen(false)}>
              Done
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
