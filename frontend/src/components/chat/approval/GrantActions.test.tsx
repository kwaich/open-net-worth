// @vitest-environment jsdom
//
// A permission card's buttons (owner, 2026-10-11): with room, [Allow once] [Always allow] as
// separate buttons; on a narrow card they group into one [Allow once ▾] split button. Deny is
// last either way, pushed to the row's right end.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { GrantActions } from './GrantActions'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function rowWidth(width: number) {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      private cb: ResizeObserverCallback
      constructor(cb: ResizeObserverCallback) {
        this.cb = cb
      }
      observe() {
        act(() => this.cb([{ contentRect: { width } } as ResizeObserverEntry], this as never))
      }
      disconnect() {}
    },
  )
}

function mount() {
  const handlers = { onOnce: vi.fn(), onAlways: vi.fn(), onDeny: vi.fn() }
  const { container } = render(<GrantActions busy={null} disabled={false} {...handlers} />)
  const labels = [
    ...container.querySelectorAll('.change-preview-actions > button, .grant-split > button'),
  ].map((b) => b.textContent || b.getAttribute('aria-label'))
  return { container, labels, ...handlers }
}

describe('permission card buttons', () => {
  it('on a wide card shows both allow scopes as buttons, Deny last on the right', () => {
    rowWidth(360)
    const { container, labels, onAlways } = mount()
    expect(labels).toEqual(['Allow once', 'Always allow', 'Deny'])
    expect(container.querySelector('.grant-split')).toBeNull()
    const always = screen.getByText('Always allow')
    expect(always.getAttribute('title')).toMatch(/every future call, any amount/)
    fireEvent.click(always)
    expect(onAlways).toHaveBeenCalledOnce()
    expect(
      container.querySelector('.change-preview-actions')?.lastElementChild?.className,
    ).toContain('change-grant-btn--deny')
  })

  it('on a narrow card groups the allow scopes into a split button, Deny still last', () => {
    rowWidth(240)
    const { container, labels, onAlways } = mount()
    expect(labels).toEqual(['Allow once', 'Standing permission', 'Deny'])
    expect(screen.queryByText('Always allow')).toBeNull()
    fireEvent.click(screen.getByLabelText('Standing permission'))
    fireEvent.click(screen.getByText('Always allow'))
    expect(onAlways).toHaveBeenCalledOnce()
    expect(container.querySelector('.change-preview-actions')?.lastElementChild?.textContent).toBe(
      'Deny',
    )
  })

  it('stays grouped where the width cannot be measured', () => {
    const { labels } = mount()
    expect(labels).toEqual(['Allow once', 'Standing permission', 'Deny'])
  })
})
