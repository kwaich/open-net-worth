// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PlanningModal } from './PlanningModal'
import { ColorDot } from './budgetParts'

describe('PlanningModal', () => {
  it('uses the shared library-backed dialog contract', async () => {
    const onClose = vi.fn()
    const opener = document.createElement('button')
    document.body.appendChild(opener)
    opener.focus()

    const { container, unmount } = render(
      <PlanningModal onClose={onClose} label="Plan settings">
        <div className="pl-modal-head">
          <h3>Plan settings</h3>
        </div>
        <div className="pl-modal-body">
          <button type="button">Inside</button>
        </div>
        <div className="pl-modal-foot">
          <button type="button">Save</button>
        </div>
      </PlanningModal>,
    )

    expect(container.querySelector('[role="dialog"]')).toBeNull()
    expect(screen.getByRole('dialog', { name: 'Plan settings' })).toBeTruthy()
    expect(document.body.getAttribute('data-scroll-locked')).toBe('1')

    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)

    unmount()
    await waitFor(() => expect(document.activeElement).toBe(opener))
    opener.remove()
  })

  it('keeps portaled Planning popovers inside the active modal boundary', async () => {
    const onPick = vi.fn()
    const onClose = vi.fn()
    render(
      <PlanningModal onClose={onClose} label="Manage categories">
        <ColorDot color="#3b82f6" onPick={onPick} label="Set Food color" />
      </PlanningModal>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Set Food color' }))
    const dialog = screen.getByRole('dialog', { name: 'Manage categories' })
    const swatch = dialog.querySelector<HTMLButtonElement>(
      '.pl-swatch[aria-label]:not([aria-label="Custom color"])',
    )!
    const picked = swatch.getAttribute('aria-label')
    expect(dialog.contains(swatch)).toBe(true)
    fireEvent.click(swatch)
    expect(onPick).toHaveBeenCalledWith(picked)

    fireEvent.keyDown(document.activeElement ?? document, { key: 'Escape' })
    expect(dialog.querySelector('[data-modal-escape-layer]')).toBeNull()
    expect(onClose).not.toHaveBeenCalled()

    fireEvent.keyDown(dialog, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

describe('PlanningModal — open auto-focus', () => {
  it('focuses the [data-autofocus] target, not Radix default first-tabbable', async () => {
    const onClose = vi.fn()
    render(
      <PlanningModal onClose={onClose} label="Manage categories">
        <div className="pl-modal-head">
          <button type="button">i</button>
          <button type="button" aria-label="Close">
            X
          </button>
        </div>
        <div className="pl-modal-body" tabIndex={-1} data-autofocus data-testid="body">
          content
        </div>
      </PlanningModal>,
    )
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId('body')))
  })

  it('with no [data-autofocus] target, falls back to Radix default (first tabbable)', async () => {
    const onClose = vi.fn()
    render(
      <PlanningModal onClose={onClose} label="Merge envelope">
        <div className="pl-modal-body">
          <button type="button">First</button>
          <button type="button">Second</button>
        </div>
      </PlanningModal>,
    )
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole('button', { name: 'First' })),
    )
  })
})

/** `window.matchMedia` is a getter-only accessor in this project's jsdom setup — same helper as
 *  Modal.dragToDismiss.test.tsx / KebabMenu.test.tsx. */
function mockMobile(isMobile: boolean) {
  const original = Object.getOwnPropertyDescriptor(window, 'matchMedia')
  const fn = ((query: string) => ({
    matches: isMobile,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
  Object.defineProperty(window, 'matchMedia', { value: fn, configurable: true, writable: true })
  return () => {
    if (original) Object.defineProperty(window, 'matchMedia', original)
  }
}

describe('PlanningModal — mobile drag to dismiss', () => {
  it('on a phone, a downward drag on the card past the distance threshold calls onClose', async () => {
    const restore = mockMobile(true)
    try {
      const onClose = vi.fn()
      render(
        <PlanningModal onClose={onClose} label="Manage categories">
          <div className="pl-modal-body">content</div>
        </PlanningModal>,
      )
      const card = screen.getByRole('dialog', { name: 'Manage categories' })
      Object.defineProperty(card, 'offsetHeight', { value: 400, configurable: true })

      fireEvent.pointerDown(card, { clientY: 200, pointerId: 1 })
      await new Promise((resolve) => setTimeout(resolve, 150))
      fireEvent.pointerMove(card, { clientY: 400, pointerId: 1 })
      fireEvent.pointerUp(card, { clientY: 400, pointerId: 1 })
      await new Promise((resolve) => setTimeout(resolve, 250))

      expect(onClose).toHaveBeenCalledTimes(1)
    } finally {
      restore()
    }
  })

  it('desktop is completely unaffected: the same drag sequence never calls onClose', async () => {
    const restore = mockMobile(false)
    try {
      const onClose = vi.fn()
      render(
        <PlanningModal onClose={onClose} label="Manage categories">
          <div className="pl-modal-body">content</div>
        </PlanningModal>,
      )
      const card = screen.getByRole('dialog', { name: 'Manage categories' })
      Object.defineProperty(card, 'offsetHeight', { value: 400, configurable: true })

      fireEvent.pointerDown(card, { clientY: 200, pointerId: 1 })
      await new Promise((resolve) => setTimeout(resolve, 150))
      fireEvent.pointerMove(card, { clientY: 400, pointerId: 1 })
      fireEvent.pointerUp(card, { clientY: 400, pointerId: 1 })
      await new Promise((resolve) => setTimeout(resolve, 250))

      expect(onClose).not.toHaveBeenCalled()
    } finally {
      restore()
    }
  })

  it('dragging DOWN on scrolled body content is left to the browser — the sheet never moves and never dismisses', async () => {
    const restore = mockMobile(true)
    try {
      const onClose = vi.fn()
      render(
        <PlanningModal onClose={onClose} label="Manage categories">
          <div className="pl-modal-body">content</div>
        </PlanningModal>,
      )
      const card = screen.getByRole('dialog', { name: 'Manage categories' })
      const body = card.querySelector('.pl-modal-body') as HTMLElement
      Object.defineProperty(card, 'offsetHeight', { value: 400, configurable: true })
      Object.defineProperty(body, 'scrollHeight', { value: 600, configurable: true })
      Object.defineProperty(body, 'clientHeight', { value: 300, configurable: true })
      body.style.overflowY = 'auto'
      body.scrollTop = 150

      fireEvent.pointerDown(body, { clientY: 200, pointerId: 1 })
      await new Promise((resolve) => setTimeout(resolve, 150))
      fireEvent.pointerMove(body, { clientY: 500, pointerId: 1 }) // 300px pull, well past the 35% dismiss threshold
      fireEvent.pointerUp(body, { clientY: 500, pointerId: 1 })
      await new Promise((resolve) => setTimeout(resolve, 250))

      expect(body.scrollTop).toBe(150) // never touched — the hook never writes scrollTop
      expect((card as HTMLElement).style.transform).toBe('') // never armed — declined at its first move
      expect(onClose).not.toHaveBeenCalled()
    } finally {
      restore()
    }
  })
})
