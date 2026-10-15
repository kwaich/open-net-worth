// @vitest-environment jsdom
// Regression: a net-ZERO activity row must still be clickable, so offsetting transactions
// (e.g. a proxy loan paid AND repaid in the same month → net 0) are viewable — the bare "0"
// used to render a non-interactive figure that hid them.
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'

import { ActivityCell } from './budgetActivity'

afterEach(cleanup)

describe('ActivityCell — zero activity', () => {
  it('renders a clickable "View transactions" button even when activity is 0', () => {
    render(
      <ActivityCell
        c={{ activity: 0, label: "Jordan's Proxy Loan", color: '#fff', categoryIds: ['cat_x'] }}
        month="2026-04"
      />,
    )
    const btn = screen.getByTitle('View transactions')
    expect(btn.tagName).toBe('BUTTON')
  })

  it('still clickable for a small non-zero activity', () => {
    render(
      <ActivityCell
        c={{ activity: -12.5, label: 'Coffee', color: '#fff', categoryIds: ['cat_y'] }}
        month="2026-04"
      />,
    )
    expect(screen.getByTitle('View transactions').tagName).toBe('BUTTON')
  })
})
