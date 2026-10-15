// @vitest-environment jsdom
//
// Regression: the register's category pill resolved labels/colours against the static
// built-in CAT map, so a user's own categories (e.g. "food", "entertainment") rendered
// as a raw grey key instead of their real label + colour. It must use the live map.

import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { categoryPill } from './TxnRow'
import type { Category } from './types'

afterEach(cleanup)

const live: Record<string, Category> = {
  food: { id: 'food', label: 'Food & Dining', color: '#e0a032', kind: 'expense', group: 'Needs' },
}

describe('categoryPill', () => {
  it('renders a live category by its label + colour', () => {
    const { container } = render(<>{categoryPill('food', live)}</>)
    const pill = container.querySelector('.acct-pill') as HTMLElement
    expect(pill.textContent).toBe('Food & Dining')
    // colour is applied inline (rgb form after parsing)
    expect(pill.style.color).toContain('224, 160, 50')
  })

  it('falls back to a friendly label — NEVER the raw opaque id — when the category is unknown (no map)', () => {
    const { container } = render(<>{categoryPill('cat_da75e76f0000')}</>)
    expect((container.querySelector('.acct-pill') as HTMLElement).textContent).toBe('Uncategorized')
  })
})
