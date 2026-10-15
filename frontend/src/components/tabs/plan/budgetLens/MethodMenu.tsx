// budgetLens/MethodMenu.tsx — the budgeting-method control on the Plan sub-tabs row (Budget only,
// desktop only; Plan.tsx gates both). ONE control: it names the current method, and its menu
// switches to the other (through SwitchMethodModal's confirmation) or opens the read-only
// comparison. It replaces the info button + segmented lens switcher + preview bar (owner,
// 2026-09-25 — the Budget Onboarding (cards) design's MethodMenu; no more previewing a method
// you haven't switched to).
import { useEffect, useRef, useState } from 'react'
import { Button, Skeleton } from '../../../ui'
import { Check } from './MethodCard'
import { METHODS, methodById } from './methodsData'
import type { BudgetLensState } from './useBudgetLens'

export function MethodMenu({ lens }: { lens: BudgetLensState }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    window.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])

  if (lens.loading) return <Skeleton width={120} height={30} radius="var(--radius-md)" />
  // An engine this menu has no card for still shows its own name, so the book can switch away.
  const homeName =
    methodById(lens.homeId)?.name ?? lens.engines.find((e) => e.id === lens.homeId)?.name
  if (!homeName) return null

  return (
    <div className="bo-mm" ref={ref}>
      <Button
        variant="secondary"
        size="dense"
        className="bo-mm-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        {homeName}
        <svg
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M3 4.5l3 3 3-3" />
        </svg>
      </Button>
      {open && (
        <div className="pl-menu bo-mm-menu" role="menu">
          {METHODS.map((m) => (
            <button
              key={m.id}
              type="button"
              role="menuitemradio"
              aria-checked={m.id === lens.homeId}
              className="pl-menu-item bo-mm-item"
              onClick={() => {
                setOpen(false)
                if (m.id !== lens.homeId) lens.requestSwitch(m.id)
              }}
            >
              {m.name}
              {m.id === lens.homeId && <Check size={12} />}
            </button>
          ))}
          <div className="pl-menu-div" />
          <button
            type="button"
            role="menuitem"
            className="pl-menu-item"
            onClick={() => {
              setOpen(false)
              lens.openCompare()
            }}
          >
            Compare methods…
          </button>
        </div>
      )}
    </div>
  )
}
