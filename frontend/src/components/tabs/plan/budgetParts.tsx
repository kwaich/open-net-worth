// plan/budgetParts.tsx — Budget leaf controls, ported verbatim from
// design_handoffs/plan_handoff/prototype/plan/budget.jsx: Fig, AvailCell figure,
// ColorDot (preset + HSV/hex picker), Kebab (fixed-positioned menu), InlineEdit,
// FilterSelect, AutoAssign, HiddenRow, ReassignDeleteModal.
import { useState, useRef, useEffect, useLayoutEffect } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { createPortal } from 'react-dom'
import * as PD from './planData'
import { Mail, Tag } from 'lucide-react'
import { Chevron, Dots, EyeOff, X } from './icons'
import type { LocalCat, LocalGroup, AutoPrevResult } from './useBudgetModel'
import { Button, Select } from '../../ui'
import { PlanningModal } from './PlanningModal'

const { fBase, fFig, fSmart } = PD

export const NEWCAT_PALETTE = [
  '#34b08a',
  '#3b86dd',
  '#e0a032',
  '#9a7fd0',
  '#22b8cf',
  '#d4537e',
  '#e2554f',
  '#1d9e75',
  '#bd7a1a',
]

// ── figure cell ───────────────────────────────────────────────────────────────
export function Fig({ n, kind }: { n: number; kind?: 'activity' | 'sub' | 'available' }) {
  const { text, isZero, isNeg } = fFig(n)
  if (isZero) return <span className="pl-fig zero">{text}</span>
  let cls = 'pl-fig'
  if (kind === 'activity') cls += ' activity'
  else if (kind === 'sub') cls += ' sub'
  else if (kind === 'available') cls += isNeg ? ' bad' : ' good'
  return <span className={cls + ' mask'}>{text}</span>
}

// ── HSV ↔ hex (ported from the account-creation colour picker) ─────────────────
export function hexToHsv(hex: string) {
  let h = (hex || '#3b86dd').replace('#', '')
  if (h.length === 3)
    h = h
      .split('')
      .map((c) => c + c)
      .join('')
  const r = parseInt(h.slice(0, 2), 16) / 255,
    g = parseInt(h.slice(2, 4), 16) / 255,
    b = parseInt(h.slice(4, 6), 16) / 255
  const mx = Math.max(r, g, b),
    mn = Math.min(r, g, b),
    d = mx - mn
  let hue = 0
  if (d) {
    if (mx === r) hue = ((g - b) / d) % 6
    else if (mx === g) hue = (b - r) / d + 2
    else hue = (r - g) / d + 4
    hue *= 60
    if (hue < 0) hue += 360
  }
  return { h: hue, s: mx ? d / mx : 0, v: mx }
}
export function hsvToHex(h: number, s: number, v: number) {
  const c = v * s,
    x = c * (1 - Math.abs(((h / 60) % 2) - 1)),
    m = v - c
  let r = 0,
    g = 0,
    b = 0
  if (h < 60) [r, g, b] = [c, x, 0]
  else if (h < 120) [r, g, b] = [x, c, 0]
  else if (h < 180) [r, g, b] = [0, c, x]
  else if (h < 240) [r, g, b] = [0, x, c]
  else if (h < 300) [r, g, b] = [x, 0, c]
  else [r, g, b] = [c, 0, x]
  const to = (n: number) =>
    Math.round((n + m) * 255)
      .toString(16)
      .padStart(2, '0')
  return '#' + to(r) + to(g) + to(b)
}

// ── Inline text field for add / rename ─────────────────────────────────────────
export function InlineEdit({
  initial,
  placeholder,
  onSave,
  onCancel,
}: {
  initial?: string
  placeholder?: string
  onSave: (t: string) => void
  onCancel: () => void
}) {
  const [v, setV] = useState(initial || '')
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => {
    const el = ref.current
    if (el) {
      el.focus()
      el.select()
    }
  }, [])
  function commit() {
    const t = v.trim()
    if (t) onSave(t)
    else onCancel()
  }
  return (
    <input
      ref={ref}
      className="pl-inline-input"
      value={v}
      placeholder={placeholder || ''}
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => setV(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault()
          commit()
        } else if (e.key === 'Escape') {
          e.preventDefault()
          e.stopPropagation()
          onCancel()
        }
      }}
    />
  )
}

// ── Hover "⋯" menu (fixed-positioned so the table's overflow-x box doesn't clip it) ──
export type KebabItem =
  | { label: string; onClick: () => void; danger?: boolean; indent?: boolean; dot?: string }
  | { divider: true }
  | { header: string }
export function Kebab({ items, label }: { items: KebabItem[]; label: string }) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{
    left: number
    top?: number
    bottom?: number
    maxH: number
  } | null>(null)
  const ref = useRef<HTMLSpanElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const popRef = useRef<HTMLDivElement>(null)
  // The table lives inside a horizontal-scroll wrapper (overflow-x:auto), which would clip an
  // absolutely-positioned menu — so the menu is position:fixed, anchored to the button rect.
  // Flip up when the space below can't fit the (now taller) menu AND up is roomier; either way
  // cap the height to the available side so the last items stay reachable (D6, prototype
  // budget.jsx :444-455 — min 200px, overflow-y auto, flip threshold 420px).
  useLayoutEffect(() => {
    if (!open) {
      setPos(null)
      return
    }
    const r = btnRef.current!.getBoundingClientRect()
    const MENU_W = 178
    const below = window.innerHeight - r.bottom - 8
    const above = r.top - 8
    const up = below < 420 && above > below
    const maxH = Math.max(200, Math.round((up ? above : below) - 4))
    const left = Math.round(Math.max(8, Math.min(r.right - MENU_W, window.innerWidth - MENU_W - 8)))
    setPos(
      up
        ? { left, maxH, bottom: Math.round(window.innerHeight - r.top + 4) }
        : { left, maxH, top: Math.round(r.bottom + 4) },
    )
  }, [open])
  useEffect(() => {
    if (!open) return
    // Portaled to '.app-shell' (below) — a menu opened from a modal whose OWN overlay carries
    // backdrop-filter (e.g. CategoriesManager's .pl-ov) would otherwise position itself against
    // that overlay's box instead of the viewport (backdrop-filter creates a containing block
    // for position:fixed descendants). "Inside" must therefore check BOTH the trigger (this
    // span, still a DOM ancestor of nothing once portaled) AND the portaled panel itself, or
    // every click inside the menu would register as "outside" and close it pre-emptively.
    const inside = (t: Node) => !!(ref.current?.contains(t) || popRef.current?.contains(t))
    const h = (e: MouseEvent) => {
      if (!inside(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      e.stopImmediatePropagation()
      setOpen(false)
      btnRef.current?.focus()
    }
    // A popover anchored to its trigger's rect must close when the PAGE scrolls (the anchor
    // moves), but `capture: true` also delivers scrolls from INSIDE the popover's own
    // scroller — so opening the list and reaching for it dismissed it instantly
    // (owner-reported, 2026-09-15). Same guard budgetActivity/budgetMove already use.
    const onScroll = (e: Event) => {
      if (inside(e.target as Node)) return
      setOpen(false)
    }
    const close = () => setOpen(false)
    document.addEventListener('mousedown', h)
    document.addEventListener('keydown', onKey, true)
    window.addEventListener('resize', close)
    window.addEventListener('scroll', onScroll, true)
    return () => {
      document.removeEventListener('mousedown', h)
      document.removeEventListener('keydown', onKey, true)
      window.removeEventListener('resize', close)
      window.removeEventListener('scroll', onScroll, true)
    }
  }, [open])
  return (
    <span className={'pl-kebab-wrap' + (open ? ' open' : '')} ref={ref}>
      <button
        ref={btnRef}
        className="pl-kebab"
        aria-label={label}
        title="Options"
        onClick={(e) => {
          e.stopPropagation()
          setOpen((o) => !o)
        }}
      >
        <Dots />
      </button>
      {open &&
        pos &&
        createPortal(
          <div
            ref={popRef}
            data-modal-escape-layer
            className="pl-menu pl-menu--kebab"
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                e.stopPropagation()
                setOpen(false)
                btnRef.current?.focus()
              }
            }}
            style={{
              position: 'fixed',
              left: pos.left + 'px',
              top: pos.top != null ? pos.top + 'px' : 'auto',
              bottom: pos.bottom != null ? pos.bottom + 'px' : 'auto',
              right: 'auto',
              maxHeight: pos.maxH + 'px',
              overflowY: 'auto',
            }}
          >
            {items.map((it, i) =>
              'divider' in it ? (
                <div key={i} className="pl-menu-div" />
              ) : 'header' in it ? (
                <div key={i} className="pl-menu-lbl">
                  {it.header}
                </div>
              ) : (
                <button
                  key={i}
                  className={
                    'pl-menu-item' + (it.danger ? ' danger' : '') + (it.indent ? ' indent' : '')
                  }
                  onClick={() => {
                    setOpen(false)
                    it.onClick()
                  }}
                >
                  {it.dot && <i className="pl-menu-dot" style={{ background: it.dot }} />}
                  {it.label}
                </button>
              ),
            )}
          </div>,
          ref.current?.closest('.pl-modal') ??
            document.querySelector('.app-shell') ??
            document.body,
        )}
    </span>
  )
}

// The colour marker as a glyph — a tag for a category, an envelope for an envelope.
export function ColorIcon({ icon, color }: { icon: 'tag' | 'envelope'; color: string }) {
  const Glyph = icon === 'tag' ? Tag : Mail
  return (
    <span
      className={'pl-cicon' + (color ? '' : ' pl-cicon--empty')}
      style={color ? ({ '--cicon': color } as CSSProperties) : undefined}
      aria-hidden="true"
    >
      <Glyph size={15} strokeWidth={2.2} />
    </span>
  )
}

/** A menu or list row's colour mark: the category tag or the envelope glyph in its own colour,
 *  in place of a plain dot (owner, 2026-09-25) — the same two icons the table rows use. */
export function MenuGlyph({ icon, color }: { icon: 'tag' | 'envelope'; color: string }) {
  const Glyph = icon === 'tag' ? Tag : Mail
  return (
    <Glyph
      className="pl-menu-glyph"
      size={13}
      strokeWidth={2.2}
      color={color || 'var(--faint)'}
      aria-hidden="true"
    />
  )
}

// ── Clickable category colour dot → palette popover (set-on-create + recolor) ──
export function ColorDot({
  color,
  onPick,
  label,
  icon,
}: {
  color: string
  onPick: (c: string) => void
  label?: string
  icon?: 'tag' | 'envelope'
}) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{ left: number; top?: number; bottom?: number } | null>(null)
  const [custom, setCustom] = useState(false)
  const [hue, setHue] = useState(0)
  const ref = useRef<HTMLSpanElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const svRef = useRef<HTMLDivElement>(null)
  const popRef = useRef<HTMLDivElement>(null)
  const isCustom = !NEWCAT_PALETTE.includes(color)
  useLayoutEffect(() => {
    if (!open) {
      setPos(null)
      return
    }
    setCustom(!NEWCAT_PALETTE.includes(color))
    setHue(hexToHsv(color).h)
    const r = btnRef.current!.getBoundingClientRect()
    const W = 212
    const left = Math.round(Math.max(8, Math.min(r.left - 10, window.innerWidth - W - 8)))
    const up = window.innerHeight - r.bottom < 300
    setPos(
      up
        ? { left, bottom: Math.round(window.innerHeight - r.top + 6) }
        : { left, top: Math.round(r.bottom + 6) },
    )
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!open) return
    // Portaled to '.app-shell' (below) — same fix as Kebab: a color popover opened from inside
    // a backdrop-filter modal (CategoriesManager's .pl-ov) must not center against that
    // overlay's box. "Inside" checks BOTH the trigger and the portaled panel.
    const inside = (t: Node) => !!(ref.current?.contains(t) || popRef.current?.contains(t))
    const h = (e: MouseEvent) => {
      if (!inside(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      e.stopImmediatePropagation()
      setOpen(false)
      btnRef.current?.focus()
    }
    // A popover anchored to its trigger's rect must close when the PAGE scrolls (the anchor
    // moves), but `capture: true` also delivers scrolls from INSIDE the popover's own
    // scroller — so opening the list and reaching for it dismissed it instantly
    // (owner-reported, 2026-09-15). Same guard budgetActivity/budgetMove already use.
    const onScroll = (e: Event) => {
      if (inside(e.target as Node)) return
      setOpen(false)
    }
    const close = () => setOpen(false)
    document.addEventListener('mousedown', h)
    document.addEventListener('keydown', onKey, true)
    window.addEventListener('resize', close)
    window.addEventListener('scroll', onScroll, true)
    return () => {
      document.removeEventListener('mousedown', h)
      document.removeEventListener('keydown', onKey, true)
      window.removeEventListener('resize', close)
      window.removeEventListener('scroll', onScroll, true)
    }
  }, [open])
  const cur = hexToHsv(color)
  const dragSV = (e: React.PointerEvent) => {
    e.stopPropagation()
    const box = svRef.current
    if (!box) return
    const move = (ev: PointerEvent) => {
      const r = box.getBoundingClientRect()
      const x = Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width))
      const y = Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height))
      onPick(hsvToHex(hue, x, 1 - y))
    }
    move(e.nativeEvent)
    const up = () => {
      document.removeEventListener('pointermove', move)
      document.removeEventListener('pointerup', up)
    }
    document.addEventListener('pointermove', move)
    document.addEventListener('pointerup', up)
  }
  const onHue = (e: React.ChangeEvent<HTMLInputElement>) => {
    const nh = +e.target.value
    setHue(nh)
    onPick(hsvToHex(nh, cur.s || 1, cur.v || 1))
  }
  const onHex = (e: React.ChangeEvent<HTMLInputElement>) => {
    let v = e.target.value.trim()
    if (!v.startsWith('#')) v = '#' + v
    if (/^#[0-9a-fA-F]{6}$/.test(v)) onPick(v)
  }
  return (
    <span className={'pl-cdot-wrap' + (open ? ' open' : '')} ref={ref}>
      <button
        ref={btnRef}
        className="pl-cdot--btn"
        title="Set color"
        aria-label={label || 'Set category color'}
        onClick={(e) => {
          e.stopPropagation()
          setOpen((o) => !o)
        }}
      >
        {icon ? (
          <ColorIcon icon={icon} color={color} />
        ) : (
          <i
            className={color ? undefined : 'pl-cdot--empty'}
            style={{ background: color || undefined }}
          />
        )}
      </button>
      {open &&
        pos &&
        createPortal(
          <div
            ref={popRef}
            data-modal-escape-layer
            className="pl-colorpop"
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                e.stopPropagation()
                setOpen(false)
                btnRef.current?.focus()
              }
            }}
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => e.stopPropagation()}
            style={{
              position: 'fixed',
              left: pos.left + 'px',
              top: pos.top != null ? pos.top + 'px' : 'auto',
              bottom: pos.bottom != null ? pos.bottom + 'px' : 'auto',
            }}
          >
            <div className="pl-colorpop-grid">
              {NEWCAT_PALETTE.map((sw) => (
                <button
                  key={sw}
                  className={'pl-swatch' + (sw === color ? ' on' : '')}
                  style={{ background: sw }}
                  title={sw}
                  aria-label={sw}
                  onClick={(e) => {
                    e.stopPropagation()
                    onPick(sw)
                    setCustom(false)
                  }}
                />
              ))}
              <button
                type="button"
                className={'pl-swatch pl-swatch-custom' + (isCustom ? ' on' : '')}
                style={isCustom ? { background: color } : undefined}
                title="Custom color"
                aria-label="Custom color"
                onClick={(e) => {
                  e.stopPropagation()
                  setHue(hexToHsv(color).h)
                  setCustom((c) => !c)
                }}
              >
                {!isCustom && (
                  <svg
                    width="11"
                    height="11"
                    viewBox="0 0 24 24"
                    fill="currentColor"
                    aria-hidden="true"
                  >
                    <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z" />
                  </svg>
                )}
              </button>
            </div>
            {custom && (
              <div className="pl-cp">
                <div
                  className="pl-cp-sv"
                  ref={svRef}
                  onPointerDown={dragSV}
                  style={{
                    background:
                      'linear-gradient(to top, #000, transparent), linear-gradient(to right, #fff, ' +
                      hsvToHex(hue, 1, 1) +
                      ')',
                  }}
                >
                  <span
                    className="pl-cp-thumb"
                    style={{
                      left: cur.s * 100 + '%',
                      top: (1 - cur.v) * 100 + '%',
                      background: color,
                    }}
                  />
                </div>
                <input
                  type="range"
                  className="pl-cp-hue"
                  min="0"
                  max="359"
                  value={Math.round(hue)}
                  onChange={onHue}
                />
                <div className="pl-cp-foot">
                  <span className="pl-cp-prev" style={{ background: color }} />
                  <input
                    className="pl-cp-hex"
                    value={(color || '').toUpperCase()}
                    onChange={onHex}
                    spellCheck={false}
                    maxLength={7}
                  />
                </div>
              </div>
            )}
          </div>,
          ref.current?.closest('.pl-modal') ??
            document.querySelector('.app-shell') ??
            document.body,
        )}
    </span>
  )
}

// ── Compact filter dropdown (replaces the chip row at narrow widths) ───────────
export function FilterSelect({
  filters,
  counts,
  value,
  onChange,
}: {
  filters: [string, string][]
  counts: Record<string, number>
  value: string | null
  onChange: (v: string | null) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open])
  const cur = filters.find(([k]) => k === (value || 'all')) || filters[0]!
  return (
    <div className="pl-filter-dd" ref={ref}>
      <Button
        variant="secondary"
        size="dense"
        className="pl-filter-ddbtn"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className="pl-filter-ddlbl">{cur[1]}</span>
        <span className="c">{counts[cur[0]]}</span>
        <span className="car">
          <Chevron dir="down" size={12} />
        </span>
      </Button>
      {open && (
        <div
          className="pl-menu"
          style={{ left: 0, right: 'auto', top: 'calc(100% + 6px)', minWidth: 220 }}
          role="listbox"
        >
          {filters.map(([k, label]) => (
            <button
              key={k}
              className={'pl-menu-item' + ((value || 'all') === k ? ' on' : '')}
              onClick={() => {
                onChange(k === 'all' ? null : k)
                setOpen(false)
              }}
            >
              {label}
              <span className="hint">{counts[k]}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Auto-assign menu ────────────────────────────────────────────────────────────
export interface AutoBudget {
  underfundedCount: number
  underfundedAmount: number
  overspentCount: number
  overspentAmount: number
  totals: { assigned: number; available: number }
  /** "Assigned Last Month" / "Spent Last Month" / "Average Assigned" / "Average Spent" hints,
   *  keyed by the SAME label buildAutoAssignItems uses. NOTHING SUPPLIES THIS RIGHT NOW: the
   *  hook that did fetched four previews per month change and was removed the same day for
   *  melting the server (see useBudgetModel's note). The field and its rendering stay so the
   *  totals can be restored from the month payload — one request, one context build — without
   *  re-deriving the display. Absent → the row simply renders no hint. */
  historyAutoTotals?: Record<string, number> | null
  /** Card-payment envelopes with underfunded debt (owner ask, 2026-09-15) — folded INTO the
   *  "Underfunded" hint below so there's one "Underfunded" row, one number, matching real
   *  YNAB exactly (a separate "Cards underfunded" row read as a confusing near-duplicate).
   *  The ACTION funds the same total (2026-09-28: it used to fund only the targets, so the
   *  button promised more than it did) — `buildClientAutoSteps` adds each card's shortfall
   *  after the targets. */
  cardUnderfundedAmount?: number
}
// Shared by the toolbar dropdown (below) AND the desktop rail's Auto-assign panel
// (budgetRail.tsx) — a pure builder so the two never drift on labels, grouping or hints.
// Labels/grouping/hint format match real YNAB's own Auto-Assign menu EXACTLY (owner ask,
// 2026-09-15, verified against a real YNAB screenshot): 3 groups (Underfunded · the 4 history
// modes together · the 2 resets), every hint a PLAIN dollar figure — no count prefix, no
// "releases " text. `__div` is the existing dropdown-divider marker; the rail skips it.
export function buildAutoAssignItems(b: AutoBudget): [string, ReactNode | null, boolean][] {
  const underfundedTotal =
    Math.round((b.underfundedAmount + (b.cardUnderfundedAmount ?? 0)) * 100) / 100
  return [
    [
      'Underfunded',
      b.underfundedCount || b.cardUnderfundedAmount ? (
        <span className="mask">{fBase(underfundedTotal)}</span>
      ) : null,
      false,
    ],
    ['__div', null, false],
    ...(
      ['Assigned Last Month', 'Spent Last Month', 'Average Assigned', 'Average Spent'] as const
    ).map((label): [string, ReactNode | null, boolean] => [
      label,
      b.historyAutoTotals && label in b.historyAutoTotals ? (
        <span className="mask">{fBase(b.historyAutoTotals[label]!)}</span>
      ) : null,
      false,
    ]),
    ['__div', null, false],
    ['Reset Available Amounts', <span className="mask">{fBase(b.totals.available)}</span>, true],
    ['Reset Assigned Amounts', <span className="mask">{fBase(b.totals.assigned)}</span>, true],
  ]
}
export function AutoAssign({ onPick, b }: { onPick: (label: string) => void; b: AutoBudget }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open])
  const items = buildAutoAssignItems(b)
  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <Button variant="primary" size="dense" onClick={() => setOpen((o) => !o)}>
        Auto-assign{' '}
        <span className="car">
          <Chevron dir="down" size={12} />
        </span>
      </Button>
      {open && (
        <div className="pl-menu">
          <div className="pl-menu-lbl">Assign money automatically</div>
          {items.map(([label, hint, danger], i) =>
            label === '__div' ? (
              <div key={i} className="pl-menu-div" />
            ) : (
              <button
                key={i}
                className={'pl-menu-item' + (danger ? ' danger' : '')}
                onClick={() => {
                  setOpen(false)
                  onPick(label)
                }}
              >
                <span>{label}</span>
                {hint && <span className="hint">{hint}</span>}
              </button>
            ),
          )}
        </div>
      )}
    </div>
  )
}

// ── Auto-assign preview modal (handoff delta #10) ──────────────────────────────
// EVERY toolbar mode previews before applying — ported verbatim from prototype/plan/budget.jsx
// :1577-1608 (DOM/classes/copy exact). `prev` is built by useBudgetModel.buildAutoPreview();
// confirming calls applyAutoPreview(prev) verbatim (no re-derive — what's shown IS what lands).
export function AutoAssignPreviewModal({
  prev,
  onCancel,
  onConfirm,
}: {
  prev: AutoPrevResult
  onCancel: () => void
  onConfirm: () => void
}) {
  const { label, steps, total } = prev
  return (
    <PlanningModal onClose={onCancel} label="Auto-assign preview">
      <div className="pl-modal-head">
        <h3>{label}</h3>
        <button className="pl-modal-x" onClick={onCancel} aria-label="Close">
          <X size={16} />
        </button>
      </div>
      <div className="pl-modal-body">
        <p className="pl-modal-intro">
          Preview — nothing changes until you confirm. {steps.length} envelope
          {steps.length === 1 ? '' : 's'} affected.
        </p>
        <div className="pl-autoprev">
          {steps.map((s) => (
            <div key={s.key} className="pl-autoprev-row">
              <span className="pl-autoprev-name">
                <MenuGlyph icon="envelope" color={s.color} />
                {s.label}
              </span>
              <span className="pl-autoprev-nums mask">
                {fSmart(s.cur)} → <b>{fSmart(s.next)}</b>
              </span>
              <span className={'pl-autoprev-delta mask ' + (s.delta > 0 ? 'pos' : 'neg')}>
                {s.delta > 0 ? '+' : '−'}
                {fSmart(Math.abs(s.delta))}
              </span>
            </div>
          ))}
        </div>
      </div>
      <div className="pl-modal-foot" style={{ justifyContent: 'space-between' }}>
        <span className="pl-autoprev-total">
          {total >= 0 ? 'Assigns ' : 'Releases '}
          <b className="mask">{fSmart(Math.abs(total))}</b>
          {total >= 0 ? ' from' : ' to'} Ready to Assign
        </span>
        <span style={{ display: 'inline-flex', gap: 10 }}>
          <Button variant="secondary" size="dense" onClick={onCancel}>
            Cancel
          </Button>
          <Button variant="primary" size="dense" onClick={onConfirm}>
            {total >= 0
              ? `Assign ${fSmart(Math.abs(total))}`
              : `Release ${fSmart(Math.abs(total))}`}
          </Button>
        </span>
      </div>
    </PlanningModal>
  )
}

// ── Bottom-of-table "Hidden categories" row + popover ──────────────────────────
// CONF-8: the prototype's HiddenRow is eye + label + chevron ONLY (no count badge); the shipped
// version had grown an extra .pl-gcount pill nothing in the design called for.
interface HiddenCatGroup {
  id: string
  name: string
  cats: LocalCat[]
}
export function HiddenRow({
  hiddenGroups,
  catGroups,
  onUnhideGroup,
  onUnhideOneFromGroup,
  onUnhideCat,
  onShowAll,
}: {
  hiddenGroups: LocalGroup[]
  catGroups: HiddenCatGroup[]
  onUnhideGroup: (gid: string) => void
  onUnhideOneFromGroup: (gid: string, key: string) => void
  onUnhideCat: (key: string) => void
  onShowAll: () => void
}) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{ left: number; bottom: number; root: Element } | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  const popRef = useRef<HTMLDivElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  useLayoutEffect(() => {
    if (!open) {
      setPos(null)
      return
    }
    const r = btnRef.current!.getBoundingClientRect()
    const W = 280
    const left = Math.round(Math.max(8, Math.min(r.left, window.innerWidth - W - 8)))
    // Picked here, not during render: the portal target (the modal the trigger sits in, else the
    // app shell) is read off the trigger's DOM, which render may not touch.
    const root =
      ref.current?.closest('.pl-modal') ?? document.querySelector('.app-shell') ?? document.body
    setPos({ left, bottom: Math.round(window.innerHeight - r.top + 6), root })
  }, [open])
  useEffect(() => {
    if (!open) return
    // The panel is portaled out of the table (below), so "inside" is the trigger OR the panel.
    const inside = (t: Node) => !!(ref.current?.contains(t) || popRef.current?.contains(t))
    const onDoc = (e: MouseEvent) => {
      if (!inside(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    // A popover anchored to its trigger's rect must close when the PAGE scrolls (the anchor
    // moves), but `capture: true` also delivers scrolls from INSIDE the popover's own
    // scroller — so opening the list and reaching for it dismissed it instantly
    // (owner-reported, 2026-09-15). Same guard budgetActivity/budgetMove already use.
    const onScroll = (e: Event) => {
      if (inside(e.target as Node)) return
      setOpen(false)
    }
    const close = () => setOpen(false)
    document.addEventListener('mousedown', onDoc)
    window.addEventListener('keydown', onKey)
    window.addEventListener('resize', close)
    window.addEventListener('scroll', onScroll, true)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('resize', close)
      window.removeEventListener('scroll', onScroll, true)
    }
  }, [open])
  const Dot = ({ c }: { c: string }) => <span className="pl-hd-dot" style={{ background: c }} />
  return (
    <div className="pl-trow pl-hiddenfoot" ref={ref}>
      <button
        ref={btnRef}
        className="pl-hidden-toggle"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className="pl-hd-eye">
          <EyeOff size={13} />
        </span>
        <span className="pl-hd-label">Hidden envelopes</span>
        <Chevron dir={open ? 'down' : 'right'} size={12} />
      </button>
      {/* Portaled to the app shell: the desktop table's content carries a transform (the
          workspace scroll hold, plan.css), which would otherwise capture this fixed panel —
          positioning it against the table instead of the viewport and clipping it. */}
      {open &&
        pos &&
        createPortal(
          <div
            ref={popRef}
            data-modal-escape-layer
            className="pl-hidpop"
            role="dialog"
            aria-label="Hidden envelopes"
            style={{
              position: 'fixed',
              left: pos.left + 'px',
              bottom: pos.bottom + 'px',
              top: 'auto',
            }}
          >
            <div className="pl-hidpop-title">Click to unhide</div>
            <div className="pl-hidpop-scroll">
              {hiddenGroups.map((g) => (
                <div key={g.id} className="pl-hidpop-sect">
                  <button
                    className="pl-hidpop-glabel pl-hidpop-glabel--btn"
                    onClick={() => {
                      onUnhideGroup(g.id)
                      setOpen(false)
                    }}
                    title="Restore whole group"
                  >
                    {g.name}
                    <span className="pl-hidpop-grestore">Restore all</span>
                  </button>
                  {g.cats.map((c) => (
                    <button
                      key={c.key}
                      className="pl-hidpop-cat"
                      onClick={() => {
                        onUnhideOneFromGroup(g.id, c.key)
                        setOpen(false)
                      }}
                    >
                      <Dot c={c.color} />
                      {c.label}
                    </button>
                  ))}
                </div>
              ))}
              {catGroups.map((g) => (
                <div key={g.id} className="pl-hidpop-sect">
                  <div className="pl-hidpop-glabel">{g.name}</div>
                  {g.cats.map((c) => (
                    <button
                      key={c.key}
                      className="pl-hidpop-cat"
                      onClick={() => onUnhideCat(c.key)}
                    >
                      <Dot c={c.color} />
                      {c.label}
                    </button>
                  ))}
                </div>
              ))}
            </div>
            <button
              className="pl-hidpop-all"
              onClick={() => {
                onShowAll()
                setOpen(false)
              }}
            >
              Show all hidden envelopes
            </button>
          </div>,
          pos.root,
        )}
    </div>
  )
}

// ── Shared reassign-then-delete dialog (single category or whole group) ────────
export function ReassignDeleteModal({
  title,
  intro,
  txns,
  options,
  confirmLabel,
  onCancel,
  onConfirm,
  bullets,
}: {
  title: string
  intro: ReactNode
  txns?: number
  options: { name: string; cats: { key: string; label: string }[] }[]
  confirmLabel: string
  onCancel: () => void
  onConfirm: (label: string, key: string) => void
  bullets?: ReactNode[]
}) {
  const [target, setTarget] = useState('')
  const label = (() => {
    for (const g of options) {
      const c = g.cats.find((x) => x.key === target)
      if (c) return c.label
    }
    return ''
  })()
  return (
    <PlanningModal onClose={onCancel} label={title}>
      <div className="pl-modal-head">
        <h3>{title}</h3>
        <button className="pl-modal-x" onClick={onCancel} aria-label="Close">
          <X size={16} />
        </button>
      </div>
      <div className="pl-modal-body">
        <p className="pl-modal-intro">{intro}</p>
        <label className="pl-modal-label" htmlFor="pl-reassign-sel">
          Reassign activity to
        </label>
        <div className="pl-selwrap">
          <Select
            dense
            id="pl-reassign-sel"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            placeholder="Select a category…"
            options={options.map((g) => ({
              group: g.name,
              options: g.cats.map((c) => ({ value: c.key, label: c.label })),
            }))}
          />
          <span className="pl-sel-caret">
            <Chevron dir="down" size={13} />
          </span>
        </div>
        <p className="pl-modal-sub">What moves to the new category:</p>
        <ul className="pl-modal-list">
          {bullets ? (
            bullets.map((b, i) => <li key={i}>{b}</li>)
          ) : (
            <>
              <li>
                All transactions <b>{txns == null ? '…' : txns}</b>
              </li>
              <li>All assigned amounts</li>
              <li>Any remaining available amount</li>
            </>
          )}
        </ul>
      </div>
      <div className="pl-modal-foot">
        <Button variant="secondary" size="dense" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          variant="danger"
          size="dense"
          disabled={!target}
          onClick={() => onConfirm(label, target)}
        >
          {confirmLabel}
        </Button>
      </div>
    </PlanningModal>
  )
}

// ── Delete-group confirmation ───────────────────────────────────────────────────
// Unlike delete-category, the backend has no per-group reassignment target — a deleted
// group's categories always land in "Others" (services/planning._op_group_delete). This used
// to reuse ReassignDeleteModal's picker, which was silently ignored (the pick never reached
// the request); this is the truthful copy instead — no fake picker, no dead choice.
export function ConfirmDeleteGroupModal({
  groupName,
  count,
  txns,
  onCancel,
  onConfirm,
}: {
  groupName: string
  count: number
  txns?: number
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <PlanningModal onClose={onCancel} label="Delete category group">
      <div className="pl-modal-head">
        <h3>Delete category group</h3>
        <button className="pl-modal-x" onClick={onCancel} aria-label="Close">
          <X size={16} />
        </button>
      </div>
      <div className="pl-modal-body">
        <p className="pl-modal-intro">
          All <b>{count}</b> categor{count === 1 ? 'y' : 'ies'} in <b>{groupName}</b> will move to{' '}
          <b>Others</b>, then the group is removed.
        </p>
        <p className="pl-modal-sub">What moves to Others:</p>
        <ul className="pl-modal-list">
          <li>
            All transactions <b>{txns == null ? '…' : txns}</b>
          </li>
          <li>All assigned amounts</li>
          <li>Any remaining available amount</li>
        </ul>
      </div>
      <div className="pl-modal-foot">
        <Button variant="secondary" size="dense" onClick={onCancel}>
          Cancel
        </Button>
        <Button variant="danger" size="dense" onClick={onConfirm}>
          Delete group
        </Button>
      </div>
    </PlanningModal>
  )
}
