// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { KebabMenu } from './KebabMenu'

afterEach(() => {
  cleanup()
})

describe('KebabMenu', () => {
  it('renders the popover as a normal child by default (no portal)', () => {
    const { container } = render(<KebabMenu items={[{ label: 'Do thing', onClick: vi.fn() }]} />)

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))

    const menu = screen.getByRole('menu')
    expect(container.contains(menu)).toBe(true)
  })

  // Regression: the mobile Overview widget header kebab opened but rendered
  // invisible, clipped by an ancestor's overflow:auto (.panel /
  // .ov-responsive-toolbar). Portal mode escapes that by rendering the
  // popover directly under document.body instead of nested under the
  // trigger's own (potentially clipped) ancestor chain.
  it('portal mode renders the popover under document.body, outside the component container', () => {
    const { container } = render(
      <KebabMenu items={[{ label: 'Do thing', onClick: vi.fn() }]} portal />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))

    const menu = screen.getByRole('menu')
    expect(container.contains(menu)).toBe(false)
    expect(document.body.contains(menu)).toBe(true)
  })

  it('portal mode: clicking a menu item still fires its onClick (outside-click check must not eat it)', () => {
    const onClick = vi.fn()
    render(<KebabMenu items={[{ label: 'Do thing', onClick }]} portal />)

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    fireEvent.mouseDown(screen.getByRole('menuitem', { name: 'Do thing' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Do thing' }))

    expect(onClick).toHaveBeenCalledOnce()
  })

  it('closes on an actual outside click, portal mode included', () => {
    render(<KebabMenu items={[{ label: 'Do thing', onClick: vi.fn() }]} portal />)

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    screen.getByRole('menu')

    fireEvent.mouseDown(document.body)

    expect(screen.queryByRole('menu')).toBeNull()
  })

  // A portaled menu is fixed where the trigger was; on scroll it drifted 200px away from its ⋯.
  it('portal mode closes when the page scrolls, but not when the menu itself scrolls', () => {
    render(<KebabMenu items={[{ label: 'Do thing', onClick: vi.fn() }]} portal />)
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))

    fireEvent.scroll(screen.getByRole('menu'))
    expect(screen.queryByRole('menu')).not.toBeNull()

    fireEvent.scroll(window)
    expect(screen.queryByRole('menu')).toBeNull()
  })
})

/** Forces useIsMobile() (jsdom's default matchMedia stub reports desktop — see
 *  vitest.setup.ts). `globalThis.matchMedia` is a getter-only accessor there, so the
 *  property must be redefined rather than assigned (same helper as
 *  TxnDetail.mobileSheet.test.tsx / Register.mobileDateGroups.test.tsx). */
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

describe('KebabMenu — mobile bottom sheet', () => {
  it('below the phone breakpoint, opens as a sheet portaled to document.body instead of an anchored dropdown', () => {
    const restore = mockMobile(true)
    try {
      const { container } = render(
        <KebabMenu items={[{ label: 'Do thing', onClick: vi.fn() }]} label="Widget options" />,
      )

      fireEvent.click(screen.getByRole('button', { name: 'Widget options' }))

      const menu = screen.getByRole('menu')
      expect(container.contains(menu)).toBe(false)
      expect(document.body.contains(menu)).toBe(true)
      expect(menu.classList.contains('kebab-sheet')).toBe(true)
      expect(screen.getByText('Widget options')).toBeTruthy()
    } finally {
      restore()
    }
  })

  it('mobile sheet: clicking an item fires its onClick and closes the sheet', () => {
    const restore = mockMobile(true)
    try {
      const onClick = vi.fn()
      render(<KebabMenu items={[{ label: 'Do thing', onClick }]} />)

      fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
      fireEvent.click(screen.getByRole('menuitem', { name: 'Do thing' }))

      expect(onClick).toHaveBeenCalledOnce()
      expect(screen.queryByRole('menu')).toBeNull()
    } finally {
      restore()
    }
  })

  it('mobile sheet: the ✕ close button closes it', () => {
    const restore = mockMobile(true)
    try {
      render(<KebabMenu items={[{ label: 'Do thing', onClick: vi.fn() }]} />)

      fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
      screen.getByRole('menu')
      fireEvent.click(screen.getByRole('button', { name: 'Close' }))

      expect(screen.queryByRole('menu')).toBeNull()
    } finally {
      restore()
    }
  })

  it('mobile sheet: clicking the scrim closes it', () => {
    const restore = mockMobile(true)
    try {
      render(<KebabMenu items={[{ label: 'Do thing', onClick: vi.fn() }]} />)

      fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
      screen.getByRole('menu')
      fireEvent.click(document.querySelector('.kebab-sheet-scrim') as HTMLElement)

      expect(screen.queryByRole('menu')).toBeNull()
    } finally {
      restore()
    }
  })

  it('mobile sheet: a downward drag on the handle past the distance threshold dismisses it', async () => {
    const restore = mockMobile(true)
    try {
      render(<KebabMenu items={[{ label: 'Do thing', onClick: vi.fn() }]} />)

      fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
      await dragHandle('.kebab-sheet', 200)

      expect(screen.queryByRole('menu')).toBeNull()
    } finally {
      restore()
    }
  })

  it('mobile sheet: a short drag on the handle springs back without dismissing it', async () => {
    const restore = mockMobile(true)
    try {
      render(<KebabMenu items={[{ label: 'Do thing', onClick: vi.fn() }]} />)

      fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
      await dragHandle('.kebab-sheet', 30)

      screen.getByRole('menu')
    } finally {
      restore()
    }
  })

  it('mobile sheet: dragging the item list directly (no scrollable overflow) drags the whole sheet, same as the handle', async () => {
    const restore = mockMobile(true)
    try {
      render(<KebabMenu items={[{ label: 'Do thing', onClick: vi.fn() }]} />)

      fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
      const sheet = document.querySelector('.kebab-sheet') as HTMLElement
      Object.defineProperty(sheet, 'offsetHeight', { value: 400, configurable: true })
      fireEvent.pointerDown(sheet, { clientY: 200, pointerId: 1 })
      await new Promise((resolve) => setTimeout(resolve, 150))
      fireEvent.pointerMove(sheet, { clientY: 400, pointerId: 1 })
      fireEvent.pointerUp(sheet, { clientY: 400, pointerId: 1 })

      expect(screen.queryByRole('menu')).toBeNull()
    } finally {
      restore()
    }
  })

  it('mobile sheet: dragging DOWN on scrolled item-list content is left to the browser — the sheet never moves and never dismisses', async () => {
    const restore = mockMobile(true)
    try {
      render(<KebabMenu items={[{ label: 'Do thing', onClick: vi.fn() }]} />)

      fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
      const sheet = document.querySelector('.kebab-sheet') as HTMLElement
      const list = document.querySelector('.kebab-sheet-body') as HTMLElement
      Object.defineProperty(sheet, 'offsetHeight', { value: 400, configurable: true })
      Object.defineProperty(list, 'scrollHeight', { value: 600, configurable: true })
      Object.defineProperty(list, 'clientHeight', { value: 300, configurable: true })
      list.style.overflowY = 'auto'
      list.scrollTop = 150

      fireEvent.pointerDown(list, { clientY: 200, pointerId: 1 })
      await new Promise((resolve) => setTimeout(resolve, 150))
      fireEvent.pointerMove(list, { clientY: 500, pointerId: 1 }) // 300px pull, well past the 35% dismiss threshold
      fireEvent.pointerUp(list, { clientY: 500, pointerId: 1 })

      expect(list.scrollTop).toBe(150) // never touched — the hook never writes scrollTop
      expect(sheet.style.transform).toBe('') // never armed — declined at its first move
      screen.getByRole('menu') // still open, never dismissed
    } finally {
      restore()
    }
  })
})

/** Simulates useSheetDragToDismiss's live drag on the dedicated handle inside `sheetSelector` —
 * the ONLY drag target now: pointerdown, a real pause (so the trailing-window velocity calc
 * reads ~0 and only the drag DISTANCE governs commit — see the hook's VELOCITY_WINDOW_MS), then
 * one pointermove of `dy` px and a release. jsdom does no layout, so the sheet's offsetHeight is
 * mocked to a realistic px value the distance ratio can compare against (real setPointerCapture
 * is unavailable in jsdom too — the hook calls it optionally). */
async function dragHandle(sheetSelector: string, dy: number) {
  const sheet = document.querySelector(sheetSelector) as HTMLElement
  const handle = sheet.querySelector('.kebab-sheet-handle-wrap') as HTMLElement
  Object.defineProperty(sheet, 'offsetHeight', { value: 400, configurable: true })
  fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
  await new Promise((resolve) => setTimeout(resolve, 150))
  fireEvent.pointerMove(handle, { clientY: 200 + dy, pointerId: 1 })
  fireEvent.pointerUp(handle, { clientY: 200 + dy, pointerId: 1 })
}
