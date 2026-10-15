// @vitest-environment jsdom
//
// Modal's phone mode now wires useSheetDragToDismiss (mobile-shell parity with .mb-sheet /
// .kebab-sheet). Desktop must be completely unaffected — same gesture wiring is present on the
// DOM node either way, so what actually gates it is `enabled` inside the hook, exercised here
// rather than assumed.

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, fireEvent, cleanup } from '@testing-library/react'
import { Modal } from './Modal'

/** Same helper as TxnDetail.mobileSheet.test.tsx / KebabMenu.test.tsx — `window.matchMedia` is a
 *  getter-only accessor in this project's jsdom setup, so the property must be redefined rather
 *  than assigned. Answering `matches: isMobile` for every query also reads reduced-motion as
 *  true whenever `isMobile` is true, which is what makes a committed drag call `onDismiss`
 *  synchronously in these tests instead of after the real 200ms exit animation. */
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

/** Pointerdown, a real pause (zeroes the trailing-window velocity so only drag DISTANCE governs
 * the commit decision), then one pointermove of `dy` px and a release — all on the dedicated
 * handle bar, the ONLY drag target now (`.ui-modal-card` itself carries no handlers at all; see
 * useSheetDragToDismiss's own top-of-file note on why a whole-card drag target was unreliable).
 * jsdom does no layout, so the card's offsetHeight is mocked to a realistic px value. */
async function dragCard(dy: number) {
  const card = document.querySelector('.ui-modal-card') as HTMLElement
  const handle = document.querySelector('.ui-modal-handle-wrap') as HTMLElement
  Object.defineProperty(card, 'offsetHeight', { value: 400, configurable: true })
  fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
  await new Promise((resolve) => setTimeout(resolve, 150))
  fireEvent.pointerMove(handle, { clientY: 200 + dy, pointerId: 1 })
  fireEvent.pointerUp(handle, { clientY: 200 + dy, pointerId: 1 })
}

afterEach(() => cleanup())

describe('Modal — drag to dismiss (phone mode)', () => {
  it('on a phone, a downward drag past the distance threshold calls onClose', async () => {
    const restore = mockMobile(true)
    try {
      const onClose = vi.fn()
      render(
        <Modal open onClose={onClose} title="Sheet">
          <button>Inside</button>
        </Modal>,
      )
      await dragCard(200)
      expect(onClose).toHaveBeenCalledTimes(1)
    } finally {
      restore()
    }
  })

  it('on a phone, a short drag springs back without calling onClose', async () => {
    const restore = mockMobile(true)
    try {
      const onClose = vi.fn()
      render(
        <Modal open onClose={onClose} title="Sheet">
          <button>Inside</button>
        </Modal>,
      )
      await dragCard(30)
      expect(onClose).not.toHaveBeenCalled()
    } finally {
      restore()
    }
  })

  it('desktop is completely unaffected: the same drag sequence never calls onClose', async () => {
    const restore = mockMobile(false)
    try {
      const onClose = vi.fn()
      render(
        <Modal open onClose={onClose} title="Dialog">
          <button>Inside</button>
        </Modal>,
      )
      await dragCard(200)
      expect(onClose).not.toHaveBeenCalled()
    } finally {
      restore()
    }
  })

  it('size="workspace" never drag-dismisses, even on a phone', async () => {
    const restore = mockMobile(true)
    try {
      const onClose = vi.fn()
      render(
        <Modal open onClose={onClose} title="Workspace" size="workspace">
          <button>Inside</button>
        </Modal>,
      )
      await dragCard(200)
      expect(onClose).not.toHaveBeenCalled()
    } finally {
      restore()
    }
  })

  it('a modal hosting the command palette is vetoed: dragging its handle never calls onClose', async () => {
    const restore = mockMobile(true)
    try {
      const onClose = vi.fn()
      render(
        <Modal open onClose={onClose} title="Search">
          <div className="cmdk">
            <input />
          </div>
        </Modal>,
      )
      await dragCard(200)
      expect(onClose).not.toHaveBeenCalled()
    } finally {
      restore()
    }
  })

  it('dragging the body content (not the handle, no scrollable overflow) drags the whole card, same as the handle', async () => {
    const restore = mockMobile(true)
    try {
      const onClose = vi.fn()
      render(
        <Modal open onClose={onClose} title="Sheet">
          <button>Inside</button>
        </Modal>,
      )
      const body = document.querySelector('.ui-modal__body') as HTMLElement
      const card = document.querySelector('.ui-modal-card') as HTMLElement
      Object.defineProperty(card, 'offsetHeight', { value: 400, configurable: true })

      fireEvent.pointerDown(body, { clientY: 200, pointerId: 1 })
      await new Promise((resolve) => setTimeout(resolve, 150))
      fireEvent.pointerMove(body, { clientY: 400, pointerId: 1 })
      fireEvent.pointerUp(body, { clientY: 400, pointerId: 1 })
      await new Promise((resolve) => setTimeout(resolve, 250))

      expect(onClose).toHaveBeenCalled()
    } finally {
      restore()
    }
  })

  it('dragging DOWN on scrolled body content is left to the browser — the sheet never moves and never dismisses', async () => {
    const restore = mockMobile(true)
    try {
      const onClose = vi.fn()
      render(
        <Modal open onClose={onClose} title="Sheet">
          <button>Inside</button>
        </Modal>,
      )
      const body = document.querySelector('.ui-modal__body') as HTMLElement
      const card = document.querySelector('.ui-modal-card') as HTMLElement
      Object.defineProperty(card, 'offsetHeight', { value: 400, configurable: true })
      Object.defineProperty(body, 'scrollHeight', { value: 600, configurable: true })
      Object.defineProperty(body, 'clientHeight', { value: 300, configurable: true })
      body.style.overflowY = 'auto'
      body.scrollTop = 150

      fireEvent.pointerDown(body, { clientY: 200, pointerId: 1 })
      await new Promise((resolve) => setTimeout(resolve, 150))
      fireEvent.pointerMove(body, { clientY: 500, pointerId: 1 }) // 300px pull, well past the 35% dismiss threshold
      fireEvent.pointerUp(body, { clientY: 500, pointerId: 1 })

      expect(body.scrollTop).toBe(150) // never touched — the hook never writes scrollTop
      expect(card.style.transform).toBe('') // never armed — declined at its first move
      expect(onClose).not.toHaveBeenCalled()
    } finally {
      restore()
    }
  })

  // Existing dismiss mechanisms (Escape, backdrop, ✕, focus trap) are covered by
  // Modal.focustrap.test.tsx — unaffected here since `{...dragHandlers}` adds only pointer
  // event props, and this suite exists to confirm the drag gesture itself, not re-cover those.

  it('closeOnBackdrop={false} leaves drag-to-dismiss enabled by default', async () => {
    const restore = mockMobile(true)
    try {
      const onClose = vi.fn()
      render(
        <Modal open onClose={onClose} title="Protected backdrop" closeOnBackdrop={false}>
          <button>Inside</button>
        </Modal>,
      )
      expect(document.querySelector('.ui-modal-handle-wrap')).toBeTruthy()
      await dragCard(200)
      expect(onClose).toHaveBeenCalledTimes(1)
    } finally {
      restore()
    }
  })

  it('dragToDismiss={false} disables the gesture without changing backdrop policy', async () => {
    const restore = mockMobile(true)
    try {
      const onClose = vi.fn()
      render(
        <Modal open onClose={onClose} title="Fixed dialog" dragToDismiss={false}>
          <button>Inside</button>
        </Modal>,
      )
      const body = document.querySelector('.ui-modal__body') as HTMLElement
      expect(document.querySelector('.ui-modal-handle-wrap')).toBeNull()
      fireEvent.pointerDown(body, { clientY: 200, pointerId: 1 })
      fireEvent.pointerMove(body, { clientY: 400, pointerId: 1 })
      fireEvent.pointerUp(body, { clientY: 400, pointerId: 1 })
      expect(onClose).not.toHaveBeenCalled()
    } finally {
      restore()
    }
  })

  it('closeOnBackdrop={true} (the default) still shows the handle bar', () => {
    const restore = mockMobile(true)
    try {
      render(
        <Modal open onClose={vi.fn()} title="Sheet">
          <button>Inside</button>
        </Modal>,
      )
      expect(document.querySelector('.ui-modal-handle-wrap')).toBeTruthy()
    } finally {
      restore()
    }
  })
})
