// @vitest-environment jsdom

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, cleanup, fireEvent, renderHook, waitFor } from '@testing-library/react'

const mockAuthFetch = vi.fn()
vi.mock('../../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))

import { IntelStoryDot } from './IntelStoryDot'
import {
  requestIntelStoryFocus,
  takeIntelStoryFocus,
  useIntelStoryIndex,
} from '../../hooks/useIntelStoryIndex'
import { invalidateQueryCache } from '../../state/queryCache'

beforeEach(() => {
  invalidateQueryCache(() => true)
  mockAuthFetch.mockReset()
  sessionStorage.clear()
  localStorage.clear()
})
afterEach(cleanup)

describe('useIntelStoryIndex', () => {
  it('indexes LIVE stories by upper-cased ticker, newest first, counting unread', async () => {
    mockAuthFetch.mockResolvedValue({
      ok: true,
      json: async () => [
        { id: 'a', ticker: 'nvda', updated_at: '2026-09-24', read: true },
        { id: 'b', ticker: 'NVDA', updated_at: '2026-09-25', read: false },
        { id: 'c', ticker: 'NVDA', updated_at: '2026-09-26', dismissed: true },
        { id: 'd', updated_at: '2026-09-26' },
      ],
    })
    const { result } = renderHook(() => useIntelStoryIndex())
    await waitFor(() => expect(result.current.get('NVDA')).toBeTruthy())
    expect(result.current.get('NVDA')).toEqual({
      count: 2,
      newestId: 'b',
      unread: 1,
      newestAt: '2026-09-25',
    })
    expect(result.current.size).toBe(1)
  })
})

describe('IntelStoryDot', () => {
  it('renders nothing without stories', () => {
    const { container } = render(<IntelStoryDot stories={undefined} onOpen={vi.fn()} />)
    expect(container.textContent).toBe('')
  })

  it('opens the newest story without triggering the row click', () => {
    const onOpen = vi.fn()
    const onRow = vi.fn()
    const { getByRole } = render(
      <div onClick={onRow}>
        <IntelStoryDot stories={{ count: 2, newestId: 'b', unread: 1 }} onOpen={onOpen} />
      </div>,
    )
    const dot = getByRole('button')
    expect(dot.getAttribute('aria-label')).toBe('2 live Intel stories, 1 unread — open in Intel')
    fireEvent.click(dot)
    expect(onOpen).toHaveBeenCalledWith('b')
    expect(onRow).not.toHaveBeenCalled()
  })
})

describe('focus hand-off', () => {
  it('is one-shot and lands on the Holdings tab', () => {
    requestIntelStoryFocus('s1')
    expect(sessionStorage.getItem('intel-subtab')).toBe('holdings')
    expect(takeIntelStoryFocus()).toBe('s1')
    expect(takeIntelStoryFocus()).toBeNull()
  })
})
