// @vitest-environment jsdom

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, cleanup, act } from '@testing-library/react'
import type { Message } from '../../types/chat'
import { TurnActivity, formatElapsed, turnProgress } from './TurnActivity'

function metaText(container: HTMLElement): string {
  return container.querySelector('.lx-activity-meta')?.textContent ?? ''
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  cleanup()
})

describe('formatElapsed', () => {
  it('formats seconds below 60 seconds', () => {
    expect(formatElapsed(0)).toBe('0s')
    expect(formatElapsed(5400)).toBe('5s')
    expect(formatElapsed(59999)).toBe('59s')
  })

  it('formats minutes and seconds above 60 seconds', () => {
    expect(formatElapsed(60000)).toBe('1m 0s')
    expect(formatElapsed(116000)).toBe('1m 56s')
    expect(formatElapsed(3600000)).toBe('60m 0s')
  })
})

describe('TurnActivity persistent startedAt', () => {
  it('renders initial elapsed duration from startedAt timestamp', () => {
    const startedAt = Date.now() - 5000
    const { container } = render(<TurnActivity startedAt={startedAt} />)
    expect(metaText(container)).toContain('(5s')
  })

  it('ticks forward from the persistent startedAt timestamp', () => {
    const startedAt = Date.now() - 5000
    const { container } = render(<TurnActivity startedAt={startedAt} />)
    expect(metaText(container)).toContain('(5s')

    act(() => {
      vi.advanceTimersByTime(2000)
    })
    expect(metaText(container)).toContain('(7s')
  })

  it('unmount and remount preserves elapsed duration without resetting to 0s', () => {
    const startedAt = Date.now() - 5000
    const { container, unmount } = render(<TurnActivity startedAt={startedAt} />)
    expect(metaText(container)).toContain('(5s')

    act(() => {
      vi.advanceTimersByTime(3000)
    })
    expect(metaText(container)).toContain('(8s')

    // Simulate screen/tab navigation away (unmount)
    unmount()

    // 4 seconds pass while navigating other screens
    act(() => {
      vi.advanceTimersByTime(4000)
    })

    // Remount with the same turn's startedAt
    const { container: remountedContainer } = render(<TurnActivity startedAt={startedAt} />)

    // Elapsed duration should reflect total time (5s + 3s + 4s = 12s)
    expect(metaText(remountedContainer)).toContain('(12s')
    expect(metaText(remountedContainer)).not.toContain('(0s')
  })

  it('updates when startedAt prop changes to a new timestamp', () => {
    const startedAt1 = Date.now() - 5000
    const { container, rerender } = render(<TurnActivity startedAt={startedAt1} />)
    expect(metaText(container)).toContain('(5s')

    // Fresh turn started
    const startedAt2 = Date.now()
    rerender(<TurnActivity startedAt={startedAt2} />)
    expect(metaText(container)).toContain('(0s')
  })

  it('falls back to local mount time when startedAt is omitted', () => {
    const { container } = render(<TurnActivity />)
    expect(metaText(container)).toContain('(0s')

    act(() => {
      vi.advanceTimersByTime(3000)
    })
    expect(metaText(container)).toContain('(3s')
  })

  it('displays output tokens and effort when provided', () => {
    const { container } = render(
      <TurnActivity startedAt={Date.now() - 2000} tokens={2500} effort="high" />,
    )
    expect(metaText(container)).toContain('2s')
    expect(metaText(container)).toContain('↓ 2.5k tokens')
    expect(metaText(container)).toContain('thinking with high effort')
  })
})

describe('TurnActivity quiet spell', () => {
  const word = (c: HTMLElement) => c.querySelector('.lx-activity-word')?.textContent ?? ''

  it('says "Still working" with the elapsed time once nothing new has shown for 10s', () => {
    const { container } = render(<TurnActivity startedAt={Date.now()} progress={3} />)
    expect(word(container)).not.toContain('Still working')

    act(() => {
      vi.advanceTimersByTime(10_000)
    })
    expect(word(container)).toBe('Still working…')
    expect(metaText(container)).toContain('(10s')
  })

  it('goes back to the rotating word the moment something new shows', () => {
    const { container, rerender } = render(<TurnActivity startedAt={Date.now()} progress={3} />)
    act(() => {
      vi.advanceTimersByTime(12_000)
    })
    expect(word(container)).toBe('Still working…')

    rerender(<TurnActivity startedAt={Date.now() - 12_000} progress={9} />)
    expect(word(container)).not.toContain('Still working')

    act(() => {
      vi.advanceTimersByTime(9_000)
    })
    expect(word(container)).not.toContain('Still working')
  })

  it('counts text, reasoning and finished steps as new output', () => {
    const msg = (over: Partial<Message>): Message => ({
      id: 'a',
      role: 'assistant',
      text: '',
      ...over,
    })
    const base = turnProgress([msg({})])
    expect(turnProgress([msg({ text: 'Hi' })])).not.toBe(base)
    expect(turnProgress([msg({ thinking: 'hm' })])).not.toBe(base)
    const running = msg({
      blocks: [{ type: 'tool', name: 'x', id: '1', status: 'executing' }],
    })
    const done = msg({ blocks: [{ type: 'tool', name: 'x', id: '1', status: 'done' }] })
    expect(turnProgress([done])).not.toBe(turnProgress([running]))
  })
})
