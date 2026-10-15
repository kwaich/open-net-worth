// dragRightClick.tsx — global delegated drag + right-click / long-press context menu.
//
// Ports prototype app.jsx lines 1288-1348. Mounts as a mounted-effect-only component: no DOM
// output of its own, just global event listeners attached on mount and cleaned up on unmount.
//
// Right-click / touch long-press on [data-entity-id] (gestureMenu.ts) → CtxMenu (ctxMenu.tsx)
// "Ask Lucius about this" → addMention(ref) + open dock.
//
// Drag: dragstart/dragend on [data-entity-id][draggable] → drag-ghost
// .mx-drag-chip + drop into a [data-dropzone] → addMention(ref) + open dock.
//
// Entity ref: parsed from data-entity-id="type:value" + data-entity-label.
// Color swatch: read from data-entity-dot (optional).

import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { CtxMenu, type CtxMenuState } from './ctxMenu'
import { useGestureMenu, type OpenGestureMenu } from './gestureMenu'
import { useChatComposer } from '../../../state/chatComposer'
import type { MentionRef } from '../../../types/mentions'
import { isSafeCssColor } from '../../../utils/mentionable'
import { byId } from '../../../lucius/objectFields'
import { resolveChipMeta } from '../../../lucius/chipMeta'
import { pluralize } from '../../../utils/pluralize'

// ── Parse helpers ─────────────────────────────────────────────────────────────

/** Parse an entity reference from the data-entity-id attr ("type:value"). */
function parseRef(el: Element): MentionRef | null {
  const attr = el.getAttribute('data-entity-id')
  if (!attr) return null
  const colon = attr.indexOf(':')
  if (colon < 1) return null
  const type = attr.slice(0, colon) as MentionRef['type']
  const ref = attr.slice(colon + 1)
  const label = el.getAttribute('data-entity-label') || el.textContent?.trim() || ref
  const mention: MentionRef = { type, ref, label }

  // Prefer on-screen data-ctx-fields via byId (same source as the output chip)
  // so input and output chips are byte-for-byte identical for any entity type.
  // Fall back to sublabel parsing for transactions when the entity is off-screen.
  const onScreenRecord = byId(type, ref)
  const sublabel = el.getAttribute('data-entity-sublabel')
  const meta = resolveChipMeta(type, onScreenRecord, sublabel)
  if (meta.chipDate !== undefined) mention.chipDate = meta.chipDate
  if (meta.chipAmt !== undefined) mention.chipAmt = meta.chipAmt
  if (meta.chipAmtNeg !== undefined) mention.chipAmtNeg = meta.chipAmtNeg

  // Carry the entity's own display color from data-entity-color (account tint,
  // envelope/category dot) — any type; validated before use.
  const rawColor = el.getAttribute('data-entity-color') ?? ''
  if (rawColor && isSafeCssColor(rawColor)) {
    mention.color = rawColor
  }

  return mention
}

// Collect the drag batch for a grabbed row. A row that is NOT part of a selection
// drags itself only (byte-identical to the pre-batch single-item path). A SELECTED
// row (data-entity-selected) drags EVERY selected row inside its data-select-scope,
// in DOM order — no cap. `total` is the true selected count (from data-selected-total);
// it can exceed the collected count when some selected rows aren't loaded into the DOM
// yet (a select-all-matching selection over an unloaded page), which the drop reports.
function collectDragBatch(el: Element): { refs: MentionRef[]; total: number } {
  if (!el.hasAttribute('data-entity-selected')) {
    const single = parseRef(el)
    return { refs: single ? [single] : [], total: single ? 1 : 0 }
  }
  const scope = el.closest('[data-select-scope]')
  const nodes = scope
    ? Array.from(scope.querySelectorAll('[data-entity-id][data-entity-selected]'))
    : [el]
  const refs = nodes.map((n) => parseRef(n)).filter((r): r is MentionRef => !!r)
  const total = scope
    ? Number(scope.getAttribute('data-selected-total')) || refs.length
    : refs.length
  return { refs, total }
}

// Ghost for a single-entity drag — the original chip (dot • TYPE • label). Built with
// createElement + textContent only — NEVER innerHTML (label/type come from DOM attrs
// that may carry externally-sourced names, so innerHTML would be a DOM-XSS sink).
function buildSingleGhost(el: Element, ref: MentionRef): HTMLElement {
  // data-entity-dot (explicit ghost dot) wins; data-entity-color (the entity's own
  // display color — envelope/category/account) is the fallback before the default.
  const rawDot =
    (el as HTMLElement).dataset.entityDot ||
    (el as HTMLElement).dataset.entityColor ||
    'var(--lucius)'
  // Only accept a recognisable CSS color/token for the dot; otherwise fall back.
  const dot = /^(#[0-9a-fA-F]{3,8}|var\(--[a-z0-9-]+\)|[a-zA-Z]+)$/.test(rawDot)
    ? rawDot
    : 'var(--lucius)'
  const g = document.createElement('div')
  g.className = 'mx-drag-chip'
  const dotEl = document.createElement('span')
  dotEl.style.cssText = 'font-weight:700'
  dotEl.style.color = dot
  dotEl.textContent = '•'
  const typeEl = document.createElement('span')
  typeEl.style.cssText =
    'font-size:9px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:var(--faint)'
  typeEl.textContent = ref.type
  const labelEl = document.createElement('span')
  labelEl.style.cssText = 'font-weight:600;color:var(--ink)'
  labelEl.textContent = ref.label
  g.append(dotEl, typeEl, labelEl)
  return g
}

// Ghost for a multi-row drag: a count badge + "N <type>s" label. Same createElement/
// textContent discipline as the single ghost (never innerHTML).
function buildCountGhost(refs: MentionRef[]): HTMLElement {
  const n = refs.length
  const g = document.createElement('div')
  g.className = 'mx-drag-chip'
  const badge = document.createElement('span')
  badge.className = 'mx-drag-count'
  badge.textContent = String(n)
  const label = document.createElement('span')
  label.style.cssText = 'font-weight:600;color:var(--ink)'
  label.textContent = `${n} ${pluralize(refs[0]!.type, n)}`
  g.append(badge, label)
  return g
}

// Remove the drop-target highlight from every dropzone. Called on dragend, drop,
// and when the pointer leaves all dropzones mid-drag.
function clearDropHighlight() {
  document
    .querySelectorAll('[data-dropzone].drop-target')
    .forEach((n) => n.classList.remove('drop-target'))
}

// ── Main component ────────────────────────────────────────────────────────────

export function DragRightClick() {
  const { addMention } = useChatComposer()
  const [ctxMenu, setCtxMenu] = useState<CtxMenuState | null>(null)
  const closeCtxMenu = useCallback(() => setCtxMenu(null), [])
  const dragItems = useRef<MentionRef[]>([])
  const dragTotal = useRef<number>(0)

  // Floating toast for the rare "some selected rows aren't loaded yet" shortfall — shown near
  // the dock, decoupled from its open/collapsed state, auto-dismissing (5s) + click to dismiss.
  const [notice, setNotice] = useState<string | null>(null)
  const noticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const showNotice = useCallback((msg: string) => {
    setNotice(msg)
    if (noticeTimer.current) clearTimeout(noticeTimer.current)
    noticeTimer.current = setTimeout(() => setNotice(null), 5000)
  }, [])
  const dismissNotice = useCallback(() => {
    if (noticeTimer.current) clearTimeout(noticeTimer.current)
    setNotice(null)
  }, [])
  useEffect(
    () => () => {
      if (noticeTimer.current) clearTimeout(noticeTimer.current)
    },
    [],
  )

  // Right-click + touch long-press → the same menu (gestureMenu.ts owns the gesture quirks).
  const openMenu = useCallback<OpenGestureMenu>((el, x, y) => {
    const ref = parseRef(el)
    if (ref) setCtxMenu({ x, y, ref, anchor: el })
  }, [])
  useGestureMenu(openMenu)

  // Drag start/end handlers.
  useEffect(() => {
    const onStart = (e: DragEvent) => {
      const target = e.target as Element
      const el = target.closest?.('[data-ctx-id][draggable], [data-entity-id][draggable]')
      if (!el) return
      // No `[data-dropzone]` anywhere in the document means there's nowhere this drag could
      // ever land — below the phone breakpoint, ChatPanel/LuciusDock (the only two dropzones
      // in the app) aren't rendered at all (RouterShell docks Lucius as an ordinary tab
      // instead), so a drag started here can never succeed. Bailing on the ABSENCE of a valid
      // target, rather than re-deriving "is this mobile" from scratch, means this stays
      // correct even if a future dropzone is added or removed elsewhere — no second place to
      // keep in sync with RouterShell's own isMobile gate on those two components. `EntityRow`
      // still stamps `draggable="true"` unconditionally (its own file may not import
      // ONW-domain hooks like useIsMobile — see its header comment); this is the one global
      // choke point where the drag is actually vetoed, without touching any of EntityRow's
      // ~25 call sites individually. `preventDefault()`, not just an early return: a bare
      // return only skips OUR ghost/dataTransfer logic — the native HTML5 drag itself would
      // still proceed (the browser's own default drag image, dragover/drop still dispatching
      // elsewhere), which is still "a drag experience with nowhere to go", just without our
      // custom pill. preventDefault() on dragstart is the spec's actual mechanism for
      // cancelling a drag outright, so nothing happens at all — no native ghost either.
      if (!document.querySelector('[data-dropzone]')) {
        e.preventDefault()
        return
      }
      const { refs, total } = collectDragBatch(el)
      if (!refs.length) return

      dragItems.current = refs
      dragTotal.current = total
      if (e.dataTransfer) {
        e.dataTransfer.effectAllowed = 'copy'
        e.dataTransfer.setData('text/plain', refs.map((r) => r.ref).join(','))
      }

      // Lift every row in the batch (single or many).
      if (refs.length > 1) {
        const keys = new Set(refs.map((r) => `${r.type}:${r.ref}`))
        el.closest('[data-select-scope]')
          ?.querySelectorAll('[data-entity-id][data-entity-selected]')
          .forEach((n) => {
            if (keys.has(n.getAttribute('data-entity-id') || '')) n.classList.add('dragging')
          })
      } else {
        el.classList.add('dragging')
      }

      const g = refs.length > 1 ? buildCountGhost(refs) : buildSingleGhost(el, refs[0]!)
      document.body.appendChild(g)
      try {
        e.dataTransfer?.setDragImage(g, 16, 18)
      } catch {
        // Some browsers block this — safe to ignore.
      }
      setTimeout(() => g.remove(), 0)
    }

    const onEnd = () => {
      document.querySelectorAll('.dragging').forEach((n) => n.classList.remove('dragging'))
      clearDropHighlight()
      dragItems.current = []
      dragTotal.current = 0
    }

    document.addEventListener('dragstart', onStart)
    document.addEventListener('dragend', onEnd)
    return () => {
      document.removeEventListener('dragstart', onStart)
      document.removeEventListener('dragend', onEnd)
    }
  }, [])

  // Drag-over / drop handlers.
  useEffect(() => {
    const over = (e: DragEvent) => {
      const target = e.target as Element
      const dz = target.closest?.('[data-dropzone]') as Element | null
      if (dragItems.current.length && dz) {
        e.preventDefault()
        if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'
        // Arm the highlight on the matched zone; clear any other zones.
        document.querySelectorAll('[data-dropzone]').forEach((el) => {
          el.classList.toggle('drop-target', el === dz)
        })
      } else if (dragItems.current.length) {
        // Active drag but outside every zone — clear any stale highlight.
        clearDropHighlight()
      }
    }

    const drop = (e: DragEvent) => {
      const target = e.target as Element
      const dz = target.closest?.('[data-dropzone]')
      clearDropHighlight()
      const refs = dragItems.current
      if (!refs.length || !dz) return
      e.preventDefault()
      if (dz.getAttribute('data-dropzone') === 'dock') {
        // Stage each mention WITHOUT opening the side panel; the dock opens itself.
        refs.forEach((ref) => addMention(ref, { open: false }))
        window.dispatchEvent(new Event('lx-dock-open'))
        // No cap on how many ride in; a shortfall only happens when some selected rows
        // aren't loaded into the register yet (select-all-matching over an unloaded page).
        if (dragTotal.current > refs.length) {
          showNotice(
            `Added ${refs.length} of ${dragTotal.current} selected — scroll to load the rest, then drag again.`,
          )
        }
      } else {
        // Side panel (or any other future dropzone) — default: open the side panel.
        refs.forEach((ref) => addMention(ref))
      }
    }

    document.addEventListener('dragover', over)
    document.addEventListener('drop', drop)
    return () => {
      document.removeEventListener('dragover', over)
      document.removeEventListener('drop', drop)
    }
  }, [addMention, showNotice])

  // Right-click "Ask Lucius about this" — opens the dock (same surface as drag).
  const handleAsk = (ref: MentionRef) => {
    addMention(ref, { open: false })
    window.dispatchEvent(new Event('lx-dock-open'))
  }

  return (
    <>
      {ctxMenu && <CtxMenu state={ctxMenu} onAsk={handleAsk} onClose={closeCtxMenu} />}
      {notice &&
        createPortal(
          <div
            className="mx-drag-toast"
            role="status"
            data-ctx-ignore
            // Portaled to document.body — same rationale as CtxMenu's marker above.
            data-lucius-portal
            onMouseDown={(e) => e.stopPropagation()}
            onClick={dismissNotice}
          >
            {notice}
          </div>,
          document.body,
        )}
    </>
  )
}
