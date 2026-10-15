// @vitest-environment jsdom
//
// CompactingActivity — the real-percentage progress line shown while a context fold
// streams (backend/ai/assistant.py::_generate_fold_summary, RFC context-folding-v2).
// Mounting/unmounting it is the CALLER's job (ChatPanel.tsx/LuciusDock.tsx: `compacting
// ? <CompactingActivity .../> : ...`), so "renders nothing when not compacting" is not
// this component's own contract to test — same split TurnActivity's own test file uses.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, cleanup, act } from '@testing-library/react'
import { CompactingActivity } from './CompactingActivity'

function bar(container: HTMLElement): HTMLElement | null {
  return container.querySelector('[role="progressbar"]')
}
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

describe('CompactingActivity', () => {
  it('renders the static label and a real-percentage progress bar', () => {
    const { container, getByText } = render(<CompactingActivity percent={42} />)
    expect(getByText('Compacting conversation…')).toBeTruthy()
    const el = bar(container)
    expect(el).toBeTruthy()
    expect(el?.getAttribute('aria-valuenow')).toBe('42')
    expect(el?.getAttribute('aria-valuetext')).toBe('42%')
  })

  it('the bar value updates immediately on every percent change (no 1/sec throttle)', () => {
    const { container, rerender } = render(<CompactingActivity percent={10} />)
    expect(bar(container)?.getAttribute('aria-valuenow')).toBe('10')

    rerender(<CompactingActivity percent={11} />)
    rerender(<CompactingActivity percent={12} />)
    rerender(<CompactingActivity percent={13} />)
    // No act(advanceTimers) between rerenders — a progressbar's value is free to move
    // every frame; only the aria-live text below is throttled.
    expect(bar(container)?.getAttribute('aria-valuenow')).toBe('13')
  })

  it('percent is clamped into 0–100 for the bar', () => {
    const { container, rerender } = render(<CompactingActivity percent={-5} />)
    expect(bar(container)?.getAttribute('aria-valuenow')).toBe('0')
    rerender(<CompactingActivity percent={150} />)
    expect(bar(container)?.getAttribute('aria-valuenow')).toBe('100')
  })

  it('elapsed time ticks on its own clock, independent of percent updates', () => {
    const { container, rerender } = render(<CompactingActivity percent={0} />)
    expect(metaText(container)).toContain('0s')

    act(() => {
      vi.advanceTimersByTime(5000)
    })
    expect(metaText(container)).toContain('5s')

    // A percent-only rerender (no remount) must not reset the clock.
    rerender(<CompactingActivity percent={50} />)
    expect(metaText(container)).toContain('5s')
  })

  it('calculates elapsed time from startedAt and preserves duration across remounts', () => {
    const startedAt = Date.now() - 5000
    const { container, unmount } = render(<CompactingActivity percent={25} startedAt={startedAt} />)
    expect(metaText(container)).toContain('5s')

    act(() => {
      vi.advanceTimersByTime(3000)
    })
    expect(metaText(container)).toContain('8s')

    unmount()

    act(() => {
      vi.advanceTimersByTime(2000)
    })

    const { container: remounted } = render(
      <CompactingActivity percent={40} startedAt={startedAt} />,
    )
    expect(metaText(remounted)).toContain('10s')
  })

  it('seeds the token count immediately (no reason to withhold data already known)', () => {
    const { container } = render(<CompactingActivity percent={0} outputTokens={100} />)
    expect(metaText(container)).toContain('↓ 100 tokens')
  })

  it('after the initial seed, the token count is throttled to the 1s tick, not every prop change', () => {
    const { container, rerender } = render(<CompactingActivity percent={0} outputTokens={100} />)
    expect(metaText(container)).toContain('↓ 100 tokens')

    // Rapid prop changes between ticks must NOT each re-render the live-region text —
    // that's exactly the screen-reader-spam this throttle exists to prevent.
    rerender(<CompactingActivity percent={5} outputTokens={200} />)
    rerender(<CompactingActivity percent={9} outputTokens={300} />)
    expect(metaText(container)).toContain('↓ 100 tokens')
    expect(metaText(container)).not.toContain('300')

    act(() => {
      vi.advanceTimersByTime(1000)
    })
    // The tick commits whatever the LATEST prop value was, not an intermediate one.
    expect(metaText(container)).toContain('↓ 300 tokens')
  })

  it('formats a large token count with a k suffix, matching TurnActivity', () => {
    const { container, rerender } = render(<CompactingActivity percent={0} outputTokens={2100} />)
    rerender(<CompactingActivity percent={1} outputTokens={2100} />)
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(metaText(container)).toContain('↓ 2.1k tokens')
  })
})
