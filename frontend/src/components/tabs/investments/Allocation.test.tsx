// @vitest-environment jsdom

import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { Allocation } from './Allocation'
import type { DerivedBook } from './useInvestmentsBook'

afterEach(cleanup)

// Allocation reads only `alloc`/`onSaveTargets`/`d.thresholdPp` — every other DerivedBook field
// is unused here.
const D = { thresholdPp: 5 } as unknown as DerivedBook

describe('Allocation — Lucius entity wiring', () => {
  it('renders each allocation slice as a draggable allocation_slice mention entity', () => {
    const { container } = render(
      <Allocation
        d={D}
        alloc={[
          { cls: 'equity', cur: 62, tgt: 50 },
          { cls: 'bond', cur: 38, tgt: 50 },
        ]}
      />,
    )

    // The synthesized ref is "asset_class:<cls>" so the data-entity-id is
    // "allocation_slice:asset_class:equity" (drag's parseRef splits on the first colon).
    const row = container.querySelector<HTMLElement>(
      '[data-entity-id="allocation_slice:asset_class:equity"]',
    )
    expect(row).toBeTruthy()
    expect(row!.getAttribute('draggable')).toBe('true')
    expect(row!.getAttribute('data-ctx-type')).toBe('allocation_slice')
    expect(row!.getAttribute('data-entity-sublabel')).toBe('62% • target 50%')

    const fields = JSON.parse(row!.getAttribute('data-ctx-fields') ?? '{}')
    expect(fields.axis).toBe('asset_class')
    expect(fields.selector).toBe('equity')

    // Both slices are mentionable.
    expect(container.querySelectorAll('[data-entity-id^="allocation_slice:"]').length).toBe(2)
  })
})

describe('Allocation — MATH-37 "off target" follows the server threshold_pp', () => {
  // The badge always renders once targets are set (it shows the +/- drift figure either way);
  // "off target" is whether it's styled with the warn color, not whether it exists.
  function isFlagged(container: HTMLElement): boolean {
    const badge = container.querySelector('.mk-atl-drift')
    return !!badge && (badge.getAttribute('style') ?? '').includes('var(--warn)')
  }

  it('does not flag a class whose drift is below the server threshold, never a hardcoded 4', () => {
    // drift is 4pp — below a 5pp server threshold — even though the old hardcoded `>= 4`
    // would have flagged it.
    const { container } = render(
      <Allocation
        d={{ thresholdPp: 5 } as unknown as DerivedBook}
        alloc={[{ cls: 'equity', cur: 54, tgt: 50 }]}
      />,
    )
    expect(isFlagged(container)).toBe(false)
  })

  it('flags a class once drift reaches a lower server threshold', () => {
    const { container } = render(
      <Allocation
        d={{ thresholdPp: 3 } as unknown as DerivedBook}
        alloc={[{ cls: 'equity', cur: 54, tgt: 50 }]}
      />,
    )
    expect(isFlagged(container)).toBe(true)
  })
})

describe('Allocation — no target set', () => {
  it('shows no target legend, marker or drift until a target exists, and offers to set one', () => {
    const { container, getByText, queryByText } = render(
      <Allocation d={D} alloc={[{ cls: 'equity', cur: 100, tgt: 0 }]} />,
    )
    expect(getByText('Allocation')).toBeTruthy()
    expect(queryByText('Allocation vs target')).toBeNull()
    expect(getByText('Set targets')).toBeTruthy()
    expect(getByText(/No target set/)).toBeTruthy()
    expect(container.querySelector('.mk-tgt-key')).toBeNull()
    expect(container.querySelector('.mk-atl-tgt')).toBeNull()
    expect(container.querySelector('.mk-atl-drift')).toBeNull()
    expect(container.textContent).not.toMatch(/target 0%/)
  })

  it('shows the target legend once any class has a target', () => {
    const { container, getByText } = render(
      <Allocation
        d={D}
        alloc={[
          { cls: 'equity', cur: 62, tgt: 50 },
          { cls: 'bond', cur: 38, tgt: 50 },
        ]}
      />,
    )
    expect(getByText('Allocation vs target')).toBeTruthy()
    expect(getByText('Adjust')).toBeTruthy()
    expect(container.querySelector('.mk-tgt-key')).not.toBeNull()
  })
})
