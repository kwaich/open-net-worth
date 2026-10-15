// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TabSkeleton } from './TabSkeleton'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const delay = (container: HTMLElement) =>
  (container.querySelector('.ui-tab-skeleton') as HTMLElement).style.animationDelay

describe('skeleton handoff', () => {
  it('resumes the same fade when a skeleton takes over from one on screen', () => {
    const clock = vi.spyOn(performance, 'now').mockReturnValue(100_000)
    const first = render(<TabSkeleton variant="budget" label="Loading budget…" />)
    expect(delay(first.container)).toBe('160ms')

    clock.mockReturnValue(100_500)
    const second = render(<TabSkeleton variant="budget" label="Loading budget…" header={false} />)
    expect(delay(second.container)).toBe('-340ms')

    first.unmount()
    second.unmount()
    clock.mockReturnValue(100_700)
    const third = render(<TabSkeleton variant="budget" label="Loading budget…" />)
    expect(delay(third.container)).toBe('-540ms')
  })

  it('starts a fresh fade for a new wait', () => {
    const clock = vi.spyOn(performance, 'now').mockReturnValue(200_000)
    render(<TabSkeleton variant="list" label="Loading…" />).unmount()
    clock.mockReturnValue(205_000)
    const { container } = render(<TabSkeleton variant="list" label="Loading…" />)

    expect(delay(container)).toBe('160ms')
  })

  it('locks every bar to one breathe cycle so a late mount does not restart the pulse', () => {
    vi.spyOn(performance, 'now').mockReturnValue(300_900)
    const { container } = render(<TabSkeleton variant="list" label="Loading…" />)
    const delays = new Set(
      [...container.querySelectorAll<HTMLElement>('.ui-skeleton')].map(
        (el) => el.style.animationDelay,
      ),
    )

    expect([...delays]).toEqual([`-${300_900 % 2400}ms`])
  })
})
