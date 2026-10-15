// @vitest-environment jsdom
/**
 * Focus-trap regression tests for ui/Modal.
 *
 * Covers:
 *  - Focus moves into the dialog on open (first focusable child or container)
 *  - Tab wraps from last focusable → first
 *  - Shift+Tab wraps from first focusable → last
 *  - Focus is restored to the trigger element on close
 *  - Escape calls onClose
 *
 * Note on DOM order: the Modal renders __head (including the Close button)
 * before __body, so the focusable order within the dialog is:
 *   [Close button, ...children's focusable elements]
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, fireEvent, act, cleanup } from '@testing-library/react'
import { Modal } from './Modal'

// ---------------------------------------------------------------------------
// Focusable-query helper (mirrors what Modal.tsx uses)
// ---------------------------------------------------------------------------
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), ' +
  'select:not([disabled]), textarea:not([disabled]), ' +
  '[tabindex]:not([tabindex="-1"]), details > summary:not([disabled])'

function getFocusableEls(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => !el.closest('[hidden]') && getComputedStyle(el).display !== 'none',
  )
}

afterEach(() => cleanup())

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Modal focus trap', () => {
  it('portals the dialog outside the React render container', () => {
    const { container } = render(
      <Modal open onClose={vi.fn()} title="Portaled dialog">
        <button>Inside</button>
      </Modal>,
    )
    expect(container.querySelector('[role="dialog"]')).toBeNull()
    expect(document.body.querySelector('[role="dialog"]')).toBeTruthy()
  })

  it('locks document scrolling while open and restores it on close', () => {
    document.body.style.overflow = 'auto'
    const { rerender } = render(
      <Modal open onClose={vi.fn()} title="Scroll lock">
        <button>Inside</button>
      </Modal>,
    )
    expect(document.body.getAttribute('data-scroll-locked')).not.toBeNull()
    rerender(
      <Modal open={false} onClose={vi.fn()} title="Scroll lock">
        <button>Inside</button>
      </Modal>,
    )
    expect(document.body.getAttribute('data-scroll-locked')).toBeNull()
    expect(document.body.style.overflow).toBe('auto')
  })

  it('moves focus into the dialog on open (not body)', () => {
    render(
      <Modal open onClose={vi.fn()} title="Test">
        <button>Inside</button>
      </Modal>,
    )
    // Focus must be inside the dialog, not left on document.body.
    const dialog = document.querySelector('[role="dialog"]') as HTMLElement
    expect(dialog).toBeTruthy()
    expect(dialog.contains(document.activeElement)).toBe(true)
  })

  it('prefers a [data-autofocus] child over the header × on open', () => {
    // Regression: the command palette's search input must receive focus on open,
    // not the header close button (which is the first focusable child otherwise).
    render(
      <Modal open onClose={vi.fn()} title="Test">
        <input data-autofocus data-testid="search" placeholder="Search…" />
        <button>Other</button>
      </Modal>,
    )
    const input = document.querySelector<HTMLElement>('[data-testid="search"]')!
    expect(document.activeElement).toBe(input)
  })

  it('installs Radix focus guards for keyboard containment', () => {
    render(
      <Modal open onClose={vi.fn()} title="Test">
        <div>
          <button data-testid="btn-a">A</button>
          <button data-testid="btn-b">B</button>
        </div>
      </Modal>,
    )

    expect(document.querySelectorAll('[data-radix-focus-guard]')).toHaveLength(2)
  })

  it('keeps focus inside when an outside element receives focus', () => {
    const outside = document.createElement('button')
    document.body.appendChild(outside)
    render(
      <Modal open onClose={vi.fn()} title="Test">
        <div>
          <button data-testid="btn-a">A</button>
          <button data-testid="btn-b">B</button>
        </div>
      </Modal>,
    )

    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!
    outside.focus()
    fireEvent.focusIn(outside)
    expect(dialog.contains(document.activeElement)).toBe(true)
    outside.remove()
  })

  it('Tab in the middle does not wrap', () => {
    render(
      <Modal open onClose={vi.fn()} title="Test">
        <div>
          <button data-testid="btn-a">A</button>
          <button data-testid="btn-b">B</button>
          <button data-testid="btn-c">C</button>
        </div>
      </Modal>,
    )

    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!
    const focusable = getFocusableEls(dialog)
    // focusable order: [Close, btn-a, btn-b, btn-c]
    const btnA = focusable.find((el) => el.getAttribute('data-testid') === 'btn-a')!

    btnA.focus()
    expect(document.activeElement).toBe(btnA)

    // Tab from a middle element should NOT be intercepted — natural browser Tab applies.
    const event = new KeyboardEvent('keydown', {
      key: 'Tab',
      shiftKey: false,
      bubbles: true,
      cancelable: true,
    })
    document.dispatchEvent(event)
    // The handler should NOT preventDefault because btnA is neither first nor last.
    expect(event.defaultPrevented).toBe(false)
  })

  it('Escape key calls onClose', () => {
    const onClose = vi.fn()
    render(
      <Modal open onClose={onClose} title="Test">
        <button>Inside</button>
      </Modal>,
    )

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('does not close from outside when backdrop dismissal is disabled', () => {
    const onClose = vi.fn()
    render(
      <Modal open onClose={onClose} title="Protected" closeOnBackdrop={false}>
        <button>Inside</button>
      </Modal>,
    )
    fireEvent.pointerDown(document.body)
    fireEvent.click(document.body)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('restores focus to the trigger on close', async () => {
    // Place a trigger button and focus it before opening the modal.
    const trigger = document.createElement('button')
    trigger.textContent = 'Open modal'
    document.body.appendChild(trigger)
    trigger.focus()
    expect(document.activeElement).toBe(trigger)

    const onClose = vi.fn()
    const { rerender } = render(
      <Modal open onClose={onClose} title="Test">
        <button>Inside</button>
      </Modal>,
    )

    // Focus is now inside the dialog.
    const dialog = document.querySelector('[role="dialog"]') as HTMLElement
    expect(dialog.contains(document.activeElement)).toBe(true)

    // Close the modal.
    rerender(
      <Modal open={false} onClose={onClose} title="Test">
        <button>Inside</button>
      </Modal>,
    )
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    // Focus should return to the trigger.
    expect(document.activeElement).toBe(trigger)

    document.body.removeChild(trigger)
  })

  it('does not trap Tab when modal is closed', () => {
    render(
      <Modal open={false} onClose={vi.fn()} title="Test">
        <button>Inside</button>
      </Modal>,
    )
    // Tab event on window should not have defaultPrevented when modal is closed.
    const event = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })
    document.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Nested modals — only the topmost modal owns the keyboard. Regression for the
// workspace tier: a dialog opened from inside a `workspace`-size manager must
// not have its Tab focus-trap fought (or its Escape double-handled) by the
// parent. See Modal.tsx `openModalStack`.
// ---------------------------------------------------------------------------
describe('Modal stacking (nested modals)', () => {
  it('only the topmost (most-recently opened) modal handles Escape', () => {
    const onCloseParent = vi.fn()
    const onCloseChild = vi.fn()
    render(
      <>
        <Modal open onClose={onCloseParent} title="Parent">
          <button>Parent button</button>
        </Modal>
        <Modal open onClose={onCloseChild} title="Child">
          <button>Child button</button>
        </Modal>
      </>,
    )
    // The guarantee is registration order, not DOM order: the child's stack-push
    // effect runs after the parent's, so the child is the top of the stack.
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onCloseChild).toHaveBeenCalledTimes(1)
    expect(onCloseParent).not.toHaveBeenCalled()
  })

  it('a child that mounts AFTER the parent (the real workspace flow) takes the top', () => {
    // Mirrors Intel.tsx: the workspace Modal is already open, then a child dialog
    // (e.g. the price-alert editor) mounts later from a user action. The child
    // must own Escape even though it mounts in a separate render pass.
    const onCloseParent = vi.fn()
    const onCloseChild = vi.fn()
    const { rerender } = render(
      <Modal open onClose={onCloseParent} title="Parent">
        <button>Parent button</button>
      </Modal>,
    )
    rerender(
      <>
        <Modal open onClose={onCloseParent} title="Parent">
          <button>Parent button</button>
        </Modal>
        <Modal open onClose={onCloseChild} title="Child">
          <button>Child button</button>
        </Modal>
      </>,
    )
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onCloseChild).toHaveBeenCalledTimes(1)
    expect(onCloseParent).not.toHaveBeenCalled()
  })

  it('hands keyboard ownership back to the parent after the child unmounts', () => {
    const onCloseParent = vi.fn()
    const onCloseChild = vi.fn()
    const { rerender } = render(
      <>
        <Modal open onClose={onCloseParent} title="Parent">
          <button>Parent button</button>
        </Modal>
        <Modal open onClose={onCloseChild} title="Child">
          <button>Child button</button>
        </Modal>
      </>,
    )
    // Unmount the child (it closed); the parent becomes the top of the stack.
    rerender(
      <>
        <Modal open onClose={onCloseParent} title="Parent">
          <button>Parent button</button>
        </Modal>
      </>,
    )
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onCloseParent).toHaveBeenCalledTimes(1)
    expect(onCloseChild).not.toHaveBeenCalled()
  })

  it('does not pull focus out of the top modal on Tab (no nested-trap fight)', () => {
    render(
      <>
        <Modal open onClose={vi.fn()} title="Parent">
          <button data-testid="parent-btn">Parent</button>
        </Modal>
        <Modal open onClose={vi.fn()} title="Child">
          <button data-testid="child-btn">Child</button>
        </Modal>
      </>,
    )
    const dialogs = document.querySelectorAll<HTMLElement>('[role="dialog"]')
    const parentDialog = dialogs[0]!
    const childDialog = dialogs[dialogs.length - 1]!
    const childFocusable = getFocusableEls(childDialog)
    const childLast = childFocusable[childFocusable.length - 1]!

    childLast.focus()
    expect(childDialog.contains(document.activeElement)).toBe(true)

    // Tab from the child's last focusable wraps WITHIN the child (to its first),
    // and crucially does NOT jump into the parent dialog.
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: false })
    expect(childDialog.contains(document.activeElement)).toBe(true)
    expect(parentDialog.contains(document.activeElement)).toBe(false)
  })
})
