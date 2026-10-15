import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { GRANT_SCOPE_LABELS, type GrantScope } from '../../../api/toolPermissions'
import { ChevronDown } from '../../../ui/icons'

const ALWAYS_NOTE = 'every future call, any amount • revoke in Settings'

// The row needs this much width for [Allow once] [Always allow] … [Deny] on one line.
const WIDE_MIN_PX = 300

/** A permission card's buttons: the two allow scopes on the left, Deny pushed to the right.
 * With room, both scopes are their own buttons; on a narrow card (the side panel) they group
 * into one [Allow once ▾] split button with "Always allow" behind the caret — the safe,
 * ordinary choice stays the obvious one either way (owner, 2026-10-11). Width is measured on
 * the row itself, so the same card adapts between the side panel, the dock and the Lucius
 * tab. Where it can't be measured (no ResizeObserver) it stays grouped. */
export function GrantActions({
  busy,
  disabled,
  onOnce,
  onAlways,
  onDeny,
}: {
  busy: GrantScope | null
  disabled: boolean
  onOnce: () => void
  onAlways: () => void
  onDeny: () => void
}) {
  const rowRef = useRef<HTMLDivElement | null>(null)
  const wide = useFitsWidth(rowRef, WIDE_MIN_PX)
  const locked = busy !== null || disabled
  return (
    <div className="change-preview-actions change-actions-wrap" ref={rowRef}>
      {wide ? (
        <>
          <button
            className="change-confirm-btn change-grant-btn change-grant-btn--once"
            disabled={locked}
            onClick={onOnce}
          >
            {busy === 'once' ? 'Saving…' : GRANT_SCOPE_LABELS.once}
          </button>
          <button
            className="change-confirm-btn change-grant-btn change-grant-btn--always"
            disabled={locked}
            title={ALWAYS_NOTE}
            onClick={onAlways}
          >
            {busy === 'always' ? 'Saving…' : GRANT_SCOPE_LABELS.always}
          </button>
        </>
      ) : (
        <GrantSplit busy={busy} locked={locked} onOnce={onOnce} onAlways={onAlways} />
      )}
      <button
        className="change-discard-btn change-grant-btn--deny"
        disabled={busy !== null}
        onClick={onDeny}
      >
        Deny
      </button>
    </div>
  )
}

/** [Allow once ▾] — "Always allow" behind the caret, with its standing-scope caveat ON the
 * item (owner, 2026-07-21: shown under an "Allow once" default it read as if allow-once
 * applied forever). */
function GrantSplit({
  busy,
  locked,
  onOnce,
  onAlways,
}: {
  busy: GrantScope | null
  locked: boolean
  onOnce: () => void
  onAlways: () => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement | null>(null)
  // Closes on outside click and Escape — a permission menu that stays open after you look
  // away is a menu you can mis-click.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])
  return (
    <div className="grant-split" ref={ref}>
      <button
        className="change-confirm-btn change-grant-btn change-grant-btn--once"
        disabled={locked}
        onClick={onOnce}
      >
        {/* The in-flight label lives on the visible button, not the menu item that started
            it: the menu closes on click, so a "Saving…" inside it would vanish at once. */}
        {busy ? 'Saving…' : GRANT_SCOPE_LABELS.once}
      </button>
      <button
        className="change-grant-btn grant-split__caret"
        disabled={locked}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Standing permission"
        onClick={() => setOpen((v) => !v)}
      >
        <ChevronDown size={12} strokeWidth={2} aria-hidden="true" />
      </button>
      {open && (
        <div className="grant-split__menu grant-split__menu--above" role="menu">
          <button
            role="menuitem"
            className="grant-split__item grant-split__item--always"
            disabled={locked}
            onClick={() => {
              setOpen(false)
              onAlways()
            }}
          >
            <span className="grant-split__item-label">{GRANT_SCOPE_LABELS.always}</span>
            <span className="grant-split__item-note">{ALWAYS_NOTE}</span>
          </button>
        </div>
      )}
    </div>
  )
}

function useFitsWidth(ref: React.RefObject<HTMLElement | null>, minWidth: number): boolean {
  const [fits, setFits] = useState(false)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setFits(entry.contentRect.width >= minWidth)
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [ref, minWidth])
  return fits
}
