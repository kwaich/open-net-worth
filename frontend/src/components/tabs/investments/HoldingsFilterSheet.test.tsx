// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

import { HoldingsFilterSheet } from './HoldingsFilterSheet'

const baseProps = {
  classOptions: [
    { id: 'all', label: 'All', count: 34 },
    { id: 'equity', label: 'Equities', count: 23 },
  ],
  classValue: 'all',
  onClassChange: vi.fn(),
  accountOptions: [
    { id: '', label: 'All accounts' },
    { id: 'Vantage Brokerage', label: 'Vantage Brokerage' },
  ],
  accountValue: '',
  onAccountChange: vi.fn(),
}

afterEach(() => {
  cleanup()
  document.body.style.overflow = ''
  vi.clearAllMocks()
})

describe('HoldingsFilterSheet', () => {
  it('opens a compact anchored popover by default without locking the page', () => {
    render(<HoldingsFilterSheet {...baseProps} />)

    fireEvent.click(screen.getByRole('button', { name: 'Filters' }))

    expect(
      screen
        .getByRole('dialog', { name: 'Filter holdings' })
        .classList.contains('mk-filter-popover'),
    ).toBe(true)
    expect(document.querySelector('.kebab-sheet')).toBeNull()
    expect(document.body.style.overflow).toBe('')
  })

  it('keeps the full bottom sheet and page lock for a real phone', () => {
    render(<HoldingsFilterSheet {...baseProps} presentation="sheet" />)

    fireEvent.click(screen.getByRole('button', { name: 'Filters' }))

    expect(
      screen.getByRole('dialog', { name: 'Filter holdings' }).classList.contains('kebab-sheet'),
    ).toBe(true)
    expect(document.querySelector('.mk-filter-popover')).toBeNull()
    expect(document.body.style.overflow).toBe('hidden')
  })

  it('closes on scrim click (existing dismiss mechanism)', () => {
    render(<HoldingsFilterSheet {...baseProps} presentation="sheet" />)

    fireEvent.click(screen.getByRole('button', { name: 'Filters' }))
    screen.getByRole('dialog', { name: 'Filter holdings' })
    fireEvent.click(document.querySelector('.kebab-sheet-scrim') as HTMLElement)

    expect(screen.queryByRole('dialog', { name: 'Filter holdings' })).toBeNull()
  })

  it('a downward drag on the handle past the distance threshold dismisses the sheet', async () => {
    render(<HoldingsFilterSheet {...baseProps} presentation="sheet" />)

    fireEvent.click(screen.getByRole('button', { name: 'Filters' }))
    const sheet = screen.getByRole('dialog', { name: 'Filter holdings' })
    const handle = sheet.querySelector('.kebab-sheet-handle-wrap') as HTMLElement
    Object.defineProperty(sheet, 'offsetHeight', { value: 400, configurable: true })

    // No matchMedia override in this file (see vitest.setup.ts's default stub), so
    // prefers-reduced-motion reads false — await the real ~200ms commit animation.
    fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
    await new Promise((resolve) => setTimeout(resolve, 150))
    fireEvent.pointerMove(handle, { clientY: 400, pointerId: 1 })
    fireEvent.pointerUp(handle, { clientY: 400, pointerId: 1 })
    await new Promise((resolve) => setTimeout(resolve, 250))

    expect(screen.queryByRole('dialog', { name: 'Filter holdings' })).toBeNull()
  })

  it('a short drag on the handle springs back without dismissing the sheet', async () => {
    render(<HoldingsFilterSheet {...baseProps} presentation="sheet" />)

    fireEvent.click(screen.getByRole('button', { name: 'Filters' }))
    const sheet = screen.getByRole('dialog', { name: 'Filter holdings' })
    const handle = sheet.querySelector('.kebab-sheet-handle-wrap') as HTMLElement
    Object.defineProperty(sheet, 'offsetHeight', { value: 400, configurable: true })

    fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
    await new Promise((resolve) => setTimeout(resolve, 150))
    fireEvent.pointerMove(handle, { clientY: 230, pointerId: 1 })
    fireEvent.pointerUp(handle, { clientY: 230, pointerId: 1 })

    screen.getByRole('dialog', { name: 'Filter holdings' })
  })

  it('dragging the filter list directly (no scrollable overflow) drags the whole sheet, same as the handle', async () => {
    render(<HoldingsFilterSheet {...baseProps} presentation="sheet" />)

    fireEvent.click(screen.getByRole('button', { name: 'Filters' }))
    const sheet = screen.getByRole('dialog', { name: 'Filter holdings' })
    Object.defineProperty(sheet, 'offsetHeight', { value: 400, configurable: true })

    fireEvent.pointerDown(sheet, { clientY: 200, pointerId: 1 })
    await new Promise((resolve) => setTimeout(resolve, 150))
    fireEvent.pointerMove(sheet, { clientY: 400, pointerId: 1 })
    fireEvent.pointerUp(sheet, { clientY: 400, pointerId: 1 })
    await new Promise((resolve) => setTimeout(resolve, 250))

    expect(screen.queryByRole('dialog', { name: 'Filter holdings' })).toBeNull()
  })

  it('dragging DOWN on scrolled filter-list content is left to the browser — the sheet never moves and never dismisses', async () => {
    render(<HoldingsFilterSheet {...baseProps} presentation="sheet" />)

    fireEvent.click(screen.getByRole('button', { name: 'Filters' }))
    const sheet = screen.getByRole('dialog', { name: 'Filter holdings' })
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
    await new Promise((resolve) => setTimeout(resolve, 250))

    expect(list.scrollTop).toBe(150) // never touched — the hook never writes scrollTop
    expect(sheet.style.transform).toBe('') // never armed — declined at its first move
    screen.getByRole('dialog', { name: 'Filter holdings' }) // still open, never dismissed
  })

  it('shows only the Account group when no class options are passed (Holdings uses pills)', () => {
    render(
      <HoldingsFilterSheet
        accountOptions={baseProps.accountOptions}
        accountValue=""
        onAccountChange={baseProps.onAccountChange}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Filters' }))

    expect(screen.queryByText('Type')).toBeNull()
    expect(screen.getByText('Account')).toBeTruthy()
    expect(screen.getByText('Vantage Brokerage')).toBeTruthy()
  })
})
