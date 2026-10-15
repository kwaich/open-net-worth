// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { CatchingUp, ChatLoading } from './ChatLoading'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('chat loading states appear only when the wait is noticeable', () => {
  it('"Loading messages" stays hidden for a fast load, then shows', () => {
    vi.useFakeTimers()
    render(<ChatLoading />)
    expect(screen.queryByText('Loading messages')).toBeNull()
    act(() => vi.advanceTimersByTime(300))
    expect(screen.getByRole('status').textContent).toContain('Loading messages')
  })

  it('"Catching up…" waits longer — most re-reads finish before it', () => {
    vi.useFakeTimers()
    render(<CatchingUp />)
    act(() => vi.advanceTimersByTime(300))
    expect(screen.queryByText('Catching up…')).toBeNull()
    act(() => vi.advanceTimersByTime(300))
    expect(screen.getByText('Catching up…')).toBeTruthy()
  })
})
