// @vitest-environment jsdom
/**
 * Regression tests for the Lucius-dock outside-click exemption in ui/Modal.
 *
 * The Lucius dock (LuciusDock.tsx) and its floating Lucius-owned popups — e.g. the
 * "Ask Lucius about this" context menu / drag-shortfall toast in dragRightClick.tsx,
 * both portaled straight to document.body — must never register as an "outside click"
 * for the currently open modal's Dialog.Content. See isOutsideExempt in Modal.tsx.
 * (That same guard also exempts TxnDetail's `.acct-detail-wrap` — its own sibling-portal
 * drawer opened from inside a modal — covered in the last describe block below.)
 *
 * Bug this covers: a prior fix recognized clicks on normal DOM descendants of
 * `.lx-dock`, but Radix determines "outside" by DOM ancestry of the click TARGET, not
 * by z-index/visual stacking — so a Lucius-owned popup that escapes `.lx-dock`'s own
 * DOM subtree via React.createPortal (a DOM SIBLING of the dock, not a descendant)
 * still read as a genuine outside click and dismissed/close-confirmed whatever modal
 * happened to be open. Those portals now carry `data-lucius-portal`, which the guard
 * also matches.
 *
 * A genuine, unrelated outside click must still dismiss the modal — the fix must not
 * over-widen the exemption.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { createPortal } from 'react-dom'
import { render, fireEvent, cleanup, act } from '@testing-library/react'
import { Modal } from './Modal'

afterEach(() => cleanup())

// Radix's outside-pointerdown listener registers itself via a setTimeout(0) in an
// effect (so it doesn't fire for the SAME pointerdown that opened the dialog) — flush
// that before firing a synthetic outside event, or the listener isn't attached yet and
// a "not called" assertion would pass for the wrong reason (no listener, not exemption).
async function flushOutsideClickRegistration() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
}

// Mimics dragRightClick.tsx's CtxMenu / toast: a Lucius-owned popup portaled straight
// to document.body — a DOM SIBLING of the dock, not a descendant of it.
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
// RouterShell as a body-level sibling of wherever the modal's portal renders.
function FakeLuciusSide() {
  return (
    <div className="lx-side on">
      <input data-testid="lucius-side-input" placeholder="Ask Lucius anything…" />
    </div>
  )
}

describe('Modal outside-click exemption for the Lucius dock', () => {
  it('does not close when a normal descendant of .lx-dock is clicked', async () => {
    const onClose = vi.fn()
    render(
      <>
        <Modal open onClose={onClose} title="Import">
          <button>Inside</button>
        </Modal>
        <FakeLuciusDock />
      </>,
    )
    await flushOutsideClickRegistration()
    const btn = document.querySelector<HTMLElement>('[data-testid="lucius-dock-btn"]')!
    fireEvent.pointerDown(btn)
    fireEvent.click(btn)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('does not close when a Lucius popup portaled OUTSIDE .lx-dock is clicked (regression)', async () => {
    const onClose = vi.fn()
    render(
      <>
        <Modal open onClose={onClose} title="Import">
          <button>Inside</button>
        </Modal>
        <FakeLuciusDock />
        <FakeLuciusPortalPopup />
      </>,
    )
    await flushOutsideClickRegistration()
    const btn = document.querySelector<HTMLElement>('[data-testid="lucius-portal-btn"]')!
    // Confirm the popup really did escape .lx-dock's DOM subtree — otherwise this test
    // would pass for the wrong reason (the old DOM-ancestry-only check would already
    // have covered it).
    expect(btn.closest('.lx-dock')).toBeNull()
    fireEvent.pointerDown(btn)
    fireEvent.click(btn)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('does not close when the .lx-side panel is clicked', async () => {
    const onClose = vi.fn()
    render(
      <>
        <Modal open onClose={onClose} title="Import">
          <button>Inside</button>
        </Modal>
        <FakeLuciusSide />
      </>,
    )
    await flushOutsideClickRegistration()
    const input = document.querySelector<HTMLElement>('[data-testid="lucius-side-input"]')!
    fireEvent.pointerDown(input)
    fireEvent.click(input)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('still closes on a genuine outside click (no over-widening)', async () => {
    const onClose = vi.fn()
    const outside = document.createElement('button')
    outside.textContent = 'Unrelated'
    document.body.appendChild(outside)
    render(
      <>
        <Modal open onClose={onClose} title="Import">
          <button>Inside</button>
        </Modal>
        <FakeLuciusDock />
      </>,
    )
    await flushOutsideClickRegistration()
    fireEvent.pointerDown(outside)
    fireEvent.click(outside)
    expect(onClose).toHaveBeenCalledTimes(1)
    outside.remove()
  })
})

/**
 * Second half of "Lucius stays reachable with a modal open": not dismissing the modal is
 * useless if you still can't TYPE into Lucius. Radix's FocusScope is `trapped` while the
 * dialog is modal — it watches focusin/focusout on `document` and yanks focus straight back
 * into the dialog whenever it lands anywhere else. The exempt surfaces must be able to hold
 * focus; everything else must still be trapped (Modal.focustrap.test.tsx guards that side).
 */
describe('Modal focus trap yields to the Lucius surfaces', () => {
  it('lets the .lx-side composer keep focus (regression)', () => {
    render(
      <>
        <Modal open onClose={vi.fn()} title="Import">
          <button>Inside</button>
        </Modal>
        <FakeLuciusSide />
      </>,
    )
    const input = document.querySelector<HTMLElement>('[data-testid="lucius-side-input"]')!
    input.focus()
    fireEvent.focusIn(input)
    expect(document.activeElement).toBe(input)
  })

  it('lets a Lucius surface take focus AWAY from a focused modal control', () => {
    render(
      <>
        <Modal open onClose={vi.fn()} title="Import">
          <button data-testid="modal-btn">Inside</button>
        </Modal>
        <FakeLuciusSide />
      </>,
    )
    const modalBtn = document.querySelector<HTMLElement>('[data-testid="modal-btn"]')!
    const input = document.querySelector<HTMLElement>('[data-testid="lucius-side-input"]')!
    modalBtn.focus()
    fireEvent.focusIn(modalBtn)
    expect(document.activeElement).toBe(modalBtn)

    // Clicking into the composer: focusout on the modal control naming the panel as the
    // relatedTarget, then focusin on the panel.
    input.focus()
    fireEvent.focusOut(modalBtn, { relatedTarget: input })
    fireEvent.focusIn(input)
    expect(document.activeElement).toBe(input)
  })

  it('lets a portaled Lucius popup keep focus', () => {
    render(
      <>
        <Modal open onClose={vi.fn()} title="Import">
          <button>Inside</button>
        </Modal>
        <FakeLuciusPortalPopup />
      </>,
    )
    const btn = document.querySelector<HTMLElement>('[data-testid="lucius-portal-btn"]')!
    btn.focus()
    fireEvent.focusIn(btn)
    expect(document.activeElement).toBe(btn)
  })

  it('still traps focus for an unrelated outside element (no over-widening)', () => {
    const outside = document.createElement('button')
    document.body.appendChild(outside)
    render(
      <Modal open onClose={vi.fn()} title="Import">
        <button>Inside</button>
      </Modal>,
    )
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!
    outside.focus()
    fireEvent.focusIn(outside)
    expect(dialog.contains(document.activeElement)).toBe(true)
    outside.remove()
  })

  it('stops yielding once the modal is closed', () => {
    const { rerender } = render(
      <>
        <Modal open onClose={vi.fn()} title="Import">
          <button>Inside</button>
        </Modal>
        <FakeLuciusSide />
      </>,
    )
    rerender(
      <>
        <Modal open={false} onClose={vi.fn()} title="Import">
          <button>Inside</button>
        </Modal>
        <FakeLuciusSide />
      </>,
    )
    const spy = vi.fn()
    document.addEventListener('focusin', spy)
    const input = document.querySelector<HTMLElement>('[data-testid="lucius-side-input"]')!
    fireEvent.focusIn(input)
    expect(spy).toHaveBeenCalled()
    document.removeEventListener('focusin', spy)
  })
})

/**
 * Third half of the same guarantee, and the one that actually made the panel unusable:
 * Radix sets `body { pointer-events: none }` for the whole time a modal dialog is open —
 * only its own layer re-enables it. `pointer-events` is inherited, so a body-level sibling
 * like `.lx-side` goes completely dead behind ANY modal: taps hit-test past it to <html>,
 * which is both "can't interact with Lucius" and "the tap dismissed the modal" (the guard
 * above never sees a `.lx-side` target — it sees <html>). The dock only escaped this because
 * `.lx-dock-pill` / `.lx-dock-inner` set `pointer-events: auto` explicitly.
 *
 * jsdom does no hit-testing, so this is asserted against the stylesheet source.
 */
describe('Lucius surfaces re-enable pointer events under a modal', () => {
  const luciusCss = readFileSync(resolve(__dirname, '../../lucius.css'), 'utf8')

  function ruleBody(css: string, selector: string): string {
    const start = css.indexOf(selector + ' {')
    expect(start, `no \`${selector} {\` rule in lucius.css`).toBeGreaterThan(-1)
    return css.slice(start, css.indexOf('}', start))
  }

  it('.lx-side declares pointer-events: auto', () => {
    expect(ruleBody(luciusCss, '.lx-side')).toMatch(/pointer-events:\s*auto/)
  })

  it('[data-lucius-portal] declares pointer-events: auto', () => {
    expect(ruleBody(luciusCss, '[data-lucius-portal]')).toMatch(/pointer-events:\s*auto/)
  })
})

/**
 * Mobile-only regression, ORIGINAL cause: `.app-layout--lucius .lx-side` (index.css) was once
 * a HIGHER-specificity override of the base `.lx-side` rule above, active whenever Lucius
 * rendered as the mobile tab (RouterShell's `mobileNav === 'lucius'`). It set
 * `pointer-events: inherit` — undoing the exemption this whole file guards — on the assumption
 * that a modal always fully covers the tab beneath it on a phone. That's false for
 * `size="workspace"` modals (Settings.tsx), a 92dvh sheet that deliberately leaves the top ~8%
 * of the viewport showing whatever's behind it, and there is a real one-render window on ANY
 * modal where Lucius stays mounted after `routeTab` flips (RouterShell.tsx) but before
 * useLuciusTab's own route-change effect has closed it. A tap on "Back to chat"
 * (ConversationMenu's `.conv-menu-x`) landing in either window hit-tested through to something
 * outside the reachable surface, which isOutsideExempt() does not recognize — so it read as an
 * outside click and dismissed the modal instead of the button's own onClick ever firing (owner
 * report 2026-08-10).
 *
 * 2026-08-13: the mobile Lucius tab was rebuilt as its own purpose-built shell
 * (LuciusMobile.tsx/.lucius-mobile-shell) instead of bending ChatPanel's `.lx-side` into a
 * mobile shape — `.app-layout--lucius .lx-side` no longer exists (nothing renders `.lx-side`
 * as the mobile tab's root anymore), so that CSS selector is gone. The RISK it guarded against
 * is unchanged (a workspace modal / route-flip render window can still leave this tab partly
 * reachable), so `.lucius-mobile-shell` itself now carries the same `pointer-events: auto` and
 * isOutsideExempt() now lists it directly instead of a CSS-selector-specificity trick.
 */
describe('Lucius surfaces re-enable pointer events under a modal — mobile tab mode', () => {
  const mobileCss = readFileSync(resolve(__dirname, '../tabs/lucius/lucius-mobile.css'), 'utf8')

  function ruleBody(css: string, selector: string): string {
    const start = css.indexOf(selector + ' {')
    expect(start, `no \`${selector} {\` rule in lucius-mobile.css`).toBeGreaterThan(-1)
    return css.slice(start, css.indexOf('}', start))
  }

  it('.lucius-mobile-shell declares pointer-events: auto', () => {
    expect(ruleBody(mobileCss, '.lucius-mobile-shell')).toMatch(/pointer-events:\s*auto/)
  })

  it('isOutsideExempt recognizes .lucius-mobile-shell as an always-reachable Lucius surface', () => {
    const modalTsx = readFileSync(resolve(__dirname, './Modal.tsx'), 'utf8')
    const start = modalTsx.indexOf('function isOutsideExempt')
    expect(start).toBeGreaterThan(-1)
    const body = modalTsx.slice(start, modalTsx.indexOf('}', start))
    expect(body).toMatch(/\.lucius-mobile-shell/)
  })
})
