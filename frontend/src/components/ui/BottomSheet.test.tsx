// @vitest-environment jsdom
//
// BottomSheet owns the lifecycle side effects every phone sheet needs — Escape, scroll lock,
// initial focus. Pinned here because the lock is the easy thing to break: a re-run of the effect
// on re-render captures `prev = 'hidden'` and the page stays locked after the sheet closes.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { BottomSheet } from './BottomSheet'

afterEach(() => {
  cleanup()
  document.body.style.overflow = ''
})

function Host({ onClosed }: { onClosed?: () => void }) {
  const [open, setOpen] = useState(true)
  const [tick, setTick] = useState(0)
  return (
    <>
      <button type="button" onClick={() => setTick((t) => t + 1)}>
        rerender {tick}
      </button>
      {open && (
        <BottomSheet
          title="Pick one"
          onClose={() => {
            setOpen(false)
            onClosed?.()
          }}
        >
          <button type="button" className="kebab-sheet-item">
            Option A
          </button>
        </BottomSheet>
      )}
    </>
  )
}

describe('BottomSheet', () => {
  it('renders the standard sheet chrome as a labelled dialog portaled to <body>', () => {
    render(<Host />)
    const dialog = screen.getByRole('dialog', { name: 'Pick one' })
    expect(dialog.classList.contains('kebab-sheet')).toBe(true)
    expect(dialog.parentElement).toBe(document.body)
    expect(dialog.querySelector('.kebab-sheet-handle')).toBeTruthy()
    expect(dialog.querySelector('.kebab-sheet-title')?.textContent).toBe('Pick one')
    expect(document.querySelector('.kebab-sheet-scrim')).toBeTruthy()
  })

  it('locks page scroll while open and restores it on close, even after re-renders', () => {
    document.body.style.overflow = 'auto'
    render(<Host />)
    expect(document.body.style.overflow).toBe('hidden')
    // Parent re-renders hand the sheet a fresh inline onClose each time — the lock must not
    // re-run and capture 'hidden' as the value to restore.
    fireEvent.click(screen.getByRole('button', { name: /rerender/ }))
    fireEvent.click(screen.getByRole('button', { name: /rerender/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.body.style.overflow).toBe('auto')
  })

  it('closes on Escape and on a scrim tap', () => {
    const onClosed = vi.fn()
    const { unmount } = render(<Host onClosed={onClosed} />)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClosed).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('dialog')).toBeNull()
    unmount()

    render(<Host onClosed={onClosed} />)
    fireEvent.click(document.querySelector('.kebab-sheet-scrim')!)
    expect(onClosed).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('the Escape listener uses the LATEST onClose, not the one from first mount', () => {
    const first = vi.fn()
    const second = vi.fn()
    const { rerender } = render(
      <BottomSheet title="T" onClose={first}>
        <span />
      </BottomSheet>,
    )
    rerender(
      <BottomSheet title="T" onClose={second}>
        <span />
      </BottomSheet>,
    )
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(first).not.toHaveBeenCalled()
    expect(second).toHaveBeenCalledTimes(1)
  })

  it('moves focus to the first control inside the sheet', async () => {
    render(<Host />)
    await act(() => new Promise<void>((r) => requestAnimationFrame(() => r())))
    expect(document.activeElement?.getAttribute('aria-label')).toBe('Close')
  })
})
