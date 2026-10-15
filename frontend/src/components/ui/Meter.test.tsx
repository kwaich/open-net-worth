// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render } from '@testing-library/react'
import { Meter } from './Meter'

afterEach(() => cleanup())

describe('Meter', () => {
  it('renders a single-fill linear meter with the required ARIA contract', () => {
    const { container } = render(
      <Meter variant="linear" value={40} ariaLabel="Groceries budget progress" />,
    )
    const root = container.querySelector('.ui-meter')!
    expect(root.getAttribute('role')).toBe('progressbar')
    expect(root.getAttribute('aria-valuemin')).toBe('0')
    expect(root.getAttribute('aria-valuemax')).toBe('100')
    expect(root.getAttribute('aria-valuenow')).toBe('40')
    expect(root.getAttribute('aria-label')).toBe('Groceries budget progress')
    const fill = container.querySelector('.ui-meter__fill') as HTMLElement
    expect(fill.style.width).toBe('40%')
  })

  it('defaults to linear/md when variant/size are omitted', () => {
    const { container } = render(<Meter value={10} ariaLabel="Default meter" />)
    expect(container.querySelector('.ui-meter--linear.ui-meter--md')).toBeTruthy()
  })

  it('renders a ring meter with the percentage text and required ARIA contract', () => {
    const { container } = render(
      <Meter variant="ring" value={62} ariaLabel="Target progress: 62%" />,
    )
    const root = container.querySelector('.ui-meter--ring')!
    expect(root.getAttribute('role')).toBe('progressbar')
    expect(root.getAttribute('aria-valuenow')).toBe('62')
    expect(container.querySelector('svg')?.textContent).toBe('62%')
  })

  it('renders the completion checkmark instead of the percentage arc when complete', () => {
    const { container } = render(
      <Meter variant="ring" value={100} complete ariaLabel="Target met" />,
    )
    expect(container.querySelector('svg')?.textContent).toBe('')
    expect(container.querySelector('path')).toBeTruthy()
    expect(container.querySelector('.ui-meter')?.getAttribute('aria-label')).toBe('Target met')
  })

  it('lets a raw color win over tone (matches the pre-existing over-budget fill logic verbatim)', () => {
    const { container: overBudget } = render(
      <Meter value={70} tone="good" color="var(--bad)" ariaLabel="Envelope progress" />,
    )
    expect((overBudget.querySelector('.ui-meter__fill') as HTMLElement).style.background).toBe(
      'var(--bad)',
    )

    const { container: toneOnly } = render(
      <Meter value={70} tone="bad" ariaLabel="Envelope progress" />,
    )
    expect((toneOnly.querySelector('.ui-meter__fill') as HTMLElement).style.background).toBe(
      'var(--bad)',
    )
  })

  it('clamps a negative value to an empty track and never emits a negative width', () => {
    const { container } = render(<Meter value={-15} tone="bad" target={80} ariaLabel="Savings" />)
    const fill = container.querySelector('.ui-meter__fill') as HTMLElement
    expect(fill.style.width).toBe('0%')
    expect(container.querySelector('.ui-meter__flag')).toBeTruthy()
  })

  it('does not show the negative flag for a genuine zero (nothing measured yet)', () => {
    const { container } = render(<Meter value={0} tone="bad" target={80} ariaLabel="Savings" />)
    expect(container.querySelector('.ui-meter__flag')).toBeNull()
  })

  it('does not show the negative flag without a target, even when negative', () => {
    const { container } = render(<Meter value={-15} tone="bad" ariaLabel="Savings" />)
    expect(container.querySelector('.ui-meter__flag')).toBeNull()
  })

  it('renders a target tick at the given position', () => {
    const { container } = render(<Meter value={40} target={75} ariaLabel="Wants spend" />)
    const tick = container.querySelector('.ui-meter__target') as HTMLElement
    expect(tick.style.left).toBe('75%')
  })

  it('renders multi-segment tracks and fires each segment event handler with its own element', () => {
    const onClick = vi.fn()
    const onMouseEnter = vi.fn()
    const onFocus = vi.fn()
    const { container } = render(
      <Meter
        variant="linear"
        size="lg"
        ariaLabel="Funding coverage"
        segments={[
          {
            id: 'cash',
            pct: 60,
            color: 'var(--good)',
            ariaLabel: 'Cash: $600',
            content: '$600',
            onClick,
            onMouseEnter,
            onFocus,
          },
          { id: 'gap', pct: 40, className: 'gap', ariaLabel: 'Unfunded: $400', content: '$400' },
        ]}
      />,
    )
    const segments = container.querySelectorAll('.ui-meter__segment')
    expect(segments).toHaveLength(2)

    // Interactive segment renders as a real <button> so it's reachable by tap AND keyboard.
    const cashSeg = container.querySelector('.ui-meter__segment[aria-label="Cash: $600"]')!
    expect(cashSeg.tagName).toBe('BUTTON')
    fireEvent.click(cashSeg)
    fireEvent.mouseEnter(cashSeg)
    fireEvent.focus(cashSeg)
    expect(onClick).toHaveBeenCalledOnce()
    expect(onMouseEnter).toHaveBeenCalledOnce()
    expect(onFocus).toHaveBeenCalledOnce()

    // Non-interactive segment (no handlers) stays a plain, non-focusable <div>.
    const gapSeg = container.querySelector('.ui-meter__segment.gap')!
    expect(gapSeg.tagName).toBe('DIV')

    // aria-valuenow for a segmented meter summarizes total coverage, not a single `value`.
    expect(container.querySelector('.ui-meter')?.getAttribute('aria-valuenow')).toBe('100')
  })

  it('applies the caller className to the root for layout hooks (e.g. flex:1 in a row)', () => {
    const hook = 'caller-layout-hook'
    const { container } = render(<Meter value={20} className={hook} ariaLabel="Mover" />)
    expect(container.querySelector(`.ui-meter.${hook}`)).toBeTruthy()
  })
})
