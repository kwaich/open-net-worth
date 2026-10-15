// @vitest-environment jsdom
/**
 * Regression tests for keeping Lucius reachable while a Connectors modal
 * (primitives.tsx's own `Modal` — NOT ui/Modal) is open.
 *
 * Mirrors ui/Modal.luciusDock.test.tsx's structure and intent, but this component's
 * dismiss mechanism is architecturally DIFFERENT from ui/Modal's, which changes which
 * assertions actually pin the real bug:
 *
 *   - ui/Modal (Radix) listens GLOBALLY for any pointerdown anywhere in the document and
 *     treats it as "outside" unless the real click TARGET is inside Dialog.Content or
 *     matches isOutsideExempt() — a check that works regardless of z-index/visual
 *     stacking, because Radix's DismissableLayer sees the real DOM target the browser
 *     hit-tested to.
 *   - This Modal's scrim instead does a plain `onMouseDown` on `.rd-modal-scrim` and only
 *     closes when `e.target === e.currentTarget` (a bare click on the scrim's own
 *     background, not the card). `.lx-side` / `.lx-dock` / a `[data-lucius-portal]` popup
 *     are body-level DOM SIBLINGS of this modal (mounted once in RouterShell, never a
 *     descendant of `.rd-modal-scrim`), so a click that lands on one of THEM never even
 *     bubbles to this handler — UNLESS the scrim visually occludes it first (full-viewport,
 *     `--z-modal`, higher than `.lx-side`'s `--z-overlay`), in which case the browser
 *     delivers the click to the SCRIM (e.target really is the scrim), not to `.lx-side`.
 *     No DOM-ancestry / exempt-selector check on `e.target` can rescue that case — by the
 *     time JS sees the event, the browser has already decided its target. The fix that
 *     actually matters is preventing the occlusion: the scrim insets itself away from the
 *     docked panel by `--chat-width`, exactly like ui/Modal's own `.modal-overlay` does
 *     (see primitives.tsx's SCRIM_STYLE comment for the full trace).
 *
 * jsdom does no real hit-testing (no z-index, no paint order), so the occlusion scenario
 * itself can't be reproduced with fireEvent — a synthetic mousedown targeted at `.lx-side`
 * will never see jsdom "route" it to the scrim the way a real browser would. The first
 * describe block below is therefore a SOURCE-based test (reads primitives.tsx's own text),
 * mirroring the pointer-events describe blocks at the bottom of ui/Modal.luciusDock.test.tsx
 * — that is the one that actually regresses if the inset is ever removed. The behavioral
 * blocks after it document the (already-true, fix-independent) sibling-DOM behavior and the
 * genuinely fix-dependent focus/pointer-events mechanisms.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { createPortal } from 'react-dom'
import { render, fireEvent, cleanup } from '@testing-library/react'
import { Modal } from './primitives'

afterEach(() => {
  cleanup()
  document.body.style.pointerEvents = ''
})

// Mimics dragRightClick.tsx's CtxMenu / DockArtifactPanel.tsx: a Lucius-owned popup
// portaled straight to document.body — a DOM SIBLING of wherever this Modal renders.
function FakeLuciusPortalPopup() {
  return createPortal(
    <button data-testid="lucius-portal-btn" data-lucius-portal>
      Ask Lucius about this
    </button>,
    document.body,
  )
}

// Mimics LuciusDock.tsx's own root — a normal (non-portaled) DOM subtree.
function FakeLuciusDock() {
  return (
    <div className="lx-dock">
      <button data-testid="lucius-dock-btn">Ask Lucius</button>
    </div>
  )
}

// Mimics ChatPanel.tsx's own root — the expanded Lucius side panel, mounted in
// RouterShell as a body-level sibling of wherever this Modal renders.
function FakeLuciusSide() {
  return (
    <div className="lx-side on">
      <input data-testid="lucius-side-input" placeholder="Ask Lucius anything…" />
    </div>
  )
}

/**
 * The load-bearing fix. `.lx-side` (`--z-overlay`, lucius.css) sits BELOW this modal's
 * scrim (`--z-modal`, connectors.css) in the shared z-index stack, so without an explicit
 * inset the scrim visually and functionally covers it — a click "on" the panel is really a
 * click on the opaque scrim on top of it, which closes the modal instead of reaching
 * Lucius. `--chat-width` is `.lx-side`'s own live width while open and `0px` while
 * closed/collapsed (ChatPanel.tsx), the exact variable ui/Modal's `.modal-overlay` already
 * insets by for the identical reason.
 */
describe('connectors Modal scrim insets itself away from the docked Lucius panel', () => {
  const primitivesSrc = readFileSync(resolve(__dirname, './primitives.tsx'), 'utf8')

  it('SCRIM_STYLE insets the right edge by --chat-width', () => {
    const start = primitivesSrc.indexOf('const SCRIM_STYLE')
    expect(start, 'no `const SCRIM_STYLE` in primitives.tsx').toBeGreaterThan(-1)
    const body = primitivesSrc.slice(start, primitivesSrc.indexOf('\n', start))
    expect(body).toMatch(/right:\s*['"]var\(--chat-width/)
  })

  it('the scrim element applies that style', () => {
    const start = primitivesSrc.indexOf('className="rd-modal-scrim"')
    expect(start, 'no `.rd-modal-scrim` element in primitives.tsx').toBeGreaterThan(-1)
    const nearby = primitivesSrc.slice(start, start + 200)
    expect(nearby).toMatch(/style=\{SCRIM_STYLE\}/)
  })

  // `.lx-dock` needs no equivalent fix — confirm why, rather than assume: its own z-index
  // tier (`--z-toast` + 10, lucius.css) already sits above `--z-modal` (connectors.css /
  // ui/Modal's `.modal-overlay` both use the same token), so it is never occluded by this
  // (or any) full-viewport modal scrim regardless of `--chat-width`.
  it('.lx-dock already sits above --z-modal (no inset needed)', () => {
    const luciusCss = readFileSync(resolve(__dirname, '../../../lucius.css'), 'utf8')
    const tokens = readFileSync(resolve(__dirname, '../../../index.tokens.generated.css'), 'utf8')
    const zToast = Number(/--z-toast:\s*(\d+)/.exec(tokens)?.[1])
    const zModal = Number(/--z-modal:\s*(\d+)/.exec(tokens)?.[1])
    expect(Number.isFinite(zToast) && Number.isFinite(zModal)).toBe(true)

    const dockStart = luciusCss.indexOf('.lx-dock {')
    expect(dockStart, 'no `.lx-dock {` rule in lucius.css').toBeGreaterThan(-1)
    const dockRule = luciusCss.slice(dockStart, luciusCss.indexOf('}', dockStart))
    expect(dockRule).toMatch(/z-index:\s*calc\(var\(--z-toast\)\s*\+\s*10\)/)
    expect(zToast + 10).toBeGreaterThan(zModal)
  })
})

describe('connectors Modal outside-click behavior for the Lucius surfaces', () => {
  it('does not close when a normal descendant of .lx-dock is clicked (sibling, never bubbles here)', () => {
    const onClose = vi.fn()
    render(
      <>
        <Modal title="Import" onClose={onClose}>
          <button>Inside</button>
        </Modal>
        <FakeLuciusDock />
      </>,
    )
    const btn = document.querySelector<HTMLElement>('[data-testid="lucius-dock-btn"]')!
    fireEvent.mouseDown(btn)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('does not close when a Lucius popup portaled to document.body is clicked (sibling, never bubbles here)', () => {
    const onClose = vi.fn()
    render(
      <>
        <Modal title="Import" onClose={onClose}>
          <button>Inside</button>
        </Modal>
        <FakeLuciusPortalPopup />
      </>,
    )
    const btn = document.querySelector<HTMLElement>('[data-testid="lucius-portal-btn"]')!
    expect(btn.closest('.rd-modal-scrim')).toBeNull()
    fireEvent.mouseDown(btn)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('does not close when the .lx-side panel is clicked (sibling, never bubbles here)', () => {
    const onClose = vi.fn()
    render(
      <>
        <Modal title="Import" onClose={onClose}>
          <button>Inside</button>
        </Modal>
        <FakeLuciusSide />
      </>,
    )
    const input = document.querySelector<HTMLElement>('[data-testid="lucius-side-input"]')!
    fireEvent.mouseDown(input)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('does not close when a click inside the card is released (no over-widening the other way)', () => {
    const onClose = vi.fn()
    render(
      <Modal title="Import" onClose={onClose}>
        <button data-testid="inside-btn">Inside</button>
      </Modal>,
    )
    fireEvent.mouseDown(document.querySelector<HTMLElement>('[data-testid="inside-btn"]')!)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('still closes on a genuine click on the bare scrim (no over-widening)', () => {
    const onClose = vi.fn()
    render(
      <>
        <Modal title="Import" onClose={onClose}>
          <button>Inside</button>
        </Modal>
        <FakeLuciusDock />
      </>,
    )
    fireEvent.mouseDown(document.querySelector<HTMLElement>('.rd-modal-scrim')!)
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

/**
 * Mechanism 2 (focus-trap yield) — CONFIRMED NON-ISSUE for this Modal. Unlike ui/Modal's
 * Radix FocusScope, this component has no ongoing focus trap: it grabs focus ONCE on mount
 * (`cardRef.current?.focus()`) and never re-asserts it afterward (no focusin/focusout
 * listener). There is nothing to "yield" — focus is simply never pulled back. This test
 * documents that rather than assuming it.
 */
describe('connectors Modal has no focus trap to yield from Lucius surfaces', () => {
  it('lets the .lx-side composer take and keep focus with the modal open', () => {
    render(
      <>
        <Modal title="Import" onClose={vi.fn()}>
          <button>Inside</button>
        </Modal>
        <FakeLuciusSide />
      </>,
    )
    const input = document.querySelector<HTMLElement>('[data-testid="lucius-side-input"]')!
    input.focus()
    expect(document.activeElement).toBe(input)
  })
})

/**
 * Mechanism 3 (pointer-events re-enablement) — CONFIRMED NON-ISSUE for this Modal. Unlike
 * Radix (which sets `body { pointer-events: none }` for the whole time a dialog is open,
 * requiring `.lx-side` / `[data-lucius-portal]` to opt back in via lucius.css), this Modal
 * only ever touches `document.body.style.overflow` — never `pointer-events`. There is
 * nothing to escape, so lucius.css's `pointer-events: auto` declarations are simply inert
 * (harmless) for this Modal rather than load-bearing. Pinned here so a future change that
 * copies the Radix pattern into this component doesn't silently reintroduce the problem.
 */
describe('connectors Modal never sets body pointer-events', () => {
  it('leaves document.body.style.pointerEvents untouched while open', () => {
    render(
      <Modal title="Import" onClose={vi.fn()}>
        <button>Inside</button>
      </Modal>,
    )
    expect(document.body.style.pointerEvents).toBe('')
  })
})
