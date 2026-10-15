// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ActionMenu } from './ActionMenu'

afterEach(() => cleanup())

describe('ActionMenu', () => {
  it('moves through actions with the keyboard and returns focus on Escape', () => {
    const run = vi.fn()
    render(
      <ActionMenu
        label="Automation actions"
        items={[
          { label: 'Run now', onSelect: run },
          { label: 'Delete', tone: 'danger', onSelect: vi.fn() },
        ]}
      />,
    )

    const trigger = screen.getByRole('button', { name: 'Automation actions' })
    trigger.focus()
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })

    const first = screen.getByRole('menuitem', { name: 'Run now' })
    expect(document.activeElement).toBe(first)
    fireEvent.keyDown(first, { key: 'ArrowDown' })
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: 'Delete' }))
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' })

    expect(screen.queryByRole('menu')).toBeNull()
    expect(document.activeElement).toBe(trigger)
  })

  it('selects an action and dismisses on outside click', () => {
    const run = vi.fn()
    render(<ActionMenu items={[{ label: 'Run now', onSelect: run }]} />)

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Run now' }))
    expect(run).toHaveBeenCalledOnce()
    expect(screen.queryByRole('menu')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    fireEvent.pointerDown(document.body)
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('has no backdrop on desktop — a click outside still lands where it points', () => {
    const elsewhere = vi.fn()
    render(
      <>
        <button onClick={elsewhere}>Another row</button>
        <ActionMenu items={[{ label: 'Run now', onSelect: vi.fn() }]} />
      </>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    const row = screen.getByRole('button', { name: 'Another row' })
    fireEvent.pointerDown(row)
    expect(screen.queryByRole('menu')).toBeNull()
    fireEvent.click(row, { detail: 1 })
    expect(elsewhere).toHaveBeenCalledOnce()
  })
})

/** Same helper as KebabMenu.test.tsx / TxnDetail.mobileSheet.test.tsx — `window.matchMedia` is a
 *  getter-only accessor in this project's jsdom setup, so the property must be redefined rather
 *  than assigned. Answering `matches: isMobile` for every query also reads reduced-motion as
 *  true whenever `isMobile` is true, which is what makes a committed drag call `onDismiss`
 *  synchronously below instead of after the real exit animation. */
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

async function dragHandle(dy: number) {
  const sheet = document.querySelector('.kebab-sheet') as HTMLElement
  const handle = sheet.querySelector('.kebab-sheet-handle-wrap') as HTMLElement
  Object.defineProperty(sheet, 'offsetHeight', { value: 400, configurable: true })
  fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
  await new Promise((resolve) => setTimeout(resolve, 150))
  fireEvent.pointerMove(handle, { clientY: 200 + dy, pointerId: 1 })
  fireEvent.pointerUp(handle, { clientY: 200 + dy, pointerId: 1 })
}

describe('ActionMenu — mobile bottom sheet', () => {
  it('below the phone breakpoint, opens as a .kebab-sheet and Escape closes it (existing mechanism)', () => {
    const restore = mockMobile(true)
    try {
      render(<ActionMenu items={[{ label: 'Run now', onSelect: vi.fn() }]} label="Row actions" />)
      fireEvent.click(screen.getByRole('button', { name: 'Row actions' }))
      const menu = screen.getByRole('menu')
      expect(menu.classList.contains('kebab-sheet')).toBe(true)

      fireEvent.keyDown(menu, { key: 'Escape' })
      expect(screen.queryByRole('menu')).toBeNull()
    } finally {
      restore()
    }
  })

  // iPhone Safari closes the sheet on the finger's press, then delivers the tap's click to
  // whatever is under the finger once the backdrop is gone (owner: "when tapping outside to
  // dismiss the modal, the click goes through and triggers others").
  it('a tap on the backdrop only closes the sheet — its click never opens the row beneath', () => {
    const restore = mockMobile(true)
    try {
      const rowOpened = vi.fn()
      render(
        <>
          <button onClick={rowOpened}>Row beneath</button>
          <ActionMenu items={[{ label: 'Run now', onSelect: vi.fn() }]} />
        </>,
      )
      fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
      fireEvent.pointerDown(document.querySelector('.kebab-sheet-scrim')!)
      expect(screen.queryByRole('menu')).toBeNull()

      const row = screen.getByRole('button', { name: 'Row beneath' })
      fireEvent.click(row, { detail: 1 })
      expect(rowOpened).not.toHaveBeenCalled()

      fireEvent.pointerDown(row) // the next tap is the user's own
      fireEvent.click(row, { detail: 1 })
      expect(rowOpened).toHaveBeenCalledOnce()
    } finally {
      restore()
    }
  })

  it('a downward drag on the handle past the distance threshold dismisses it', async () => {
    const restore = mockMobile(true)
    try {
      render(<ActionMenu items={[{ label: 'Run now', onSelect: vi.fn() }]} />)
      fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
      await dragHandle(200)
      expect(screen.queryByRole('menu')).toBeNull()
    } finally {
      restore()
    }
  })

  it('a short drag on the handle springs back without dismissing it', async () => {
    const restore = mockMobile(true)
    try {
      render(<ActionMenu items={[{ label: 'Run now', onSelect: vi.fn() }]} />)
      fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
      await dragHandle(30)
      screen.getByRole('menu')
    } finally {
      restore()
    }
  })

  it('dragging the item list directly (no scrollable overflow) drags the whole sheet, same as the handle', async () => {
    const restore = mockMobile(true)
    try {
      render(<ActionMenu items={[{ label: 'Run now', onSelect: vi.fn() }]} />)
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

  it('dragging DOWN on scrolled item-list content is left to the browser — the sheet never moves and never dismisses', async () => {
    const restore = mockMobile(true)
    try {
      render(<ActionMenu items={[{ label: 'Run now', onSelect: vi.fn() }]} />)
      fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
      const sheet = document.querySelector('.kebab-sheet') as HTMLElement
      const list = sheet.querySelector('.kebab-sheet-body') as HTMLElement
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
