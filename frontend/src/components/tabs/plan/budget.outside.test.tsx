// @vitest-environment jsdom
//
// Handoff item 7 — the "Outside envelopes" section replaces UnsortedRow + UnbudgetedGroup with
// one derived, read-only section: Uncategorized (no category), Uncategorized income (already in
// Ready to Assign), and per-category Unbudgeted lines. Fixture-payload rendering coverage for
// OutsideEnvelopes + the pure buildOutsideRows row-builder.
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { OutsideEnvelopes, buildOutsideRows, outsideAvailableSubtotal } from './budget'
import type { OutsideEnvelopesData } from './budget'

afterEach(() => cleanup())

const EMPTY: OutsideEnvelopesData = {
  unsortedActivity: 0,
  unsortedAvailable: 0,
  uncatIncome: 0,
  unbudgeted: [],
}

describe('buildOutsideRows — pure row-building rules', () => {
  it('produces no rows when everything is ~zero (the backend >= 0.005 gate)', () => {
    const rows = buildOutsideRows(
      {
        unsortedActivity: 0.001,
        unsortedAvailable: 0,
        uncatIncome: 0.001,
        unbudgeted: [
          { key: 'gifts', label: 'Gifts', activity: 0.001, available: 0.001, color: '#000' },
        ],
      },
      vi.fn(),
      vi.fn(),
      vi.fn(),
    )
    expect(rows).toHaveLength(0)
  })

  it('Uncategorized row: amber, status text, "Review in Accounts" action', () => {
    const onReview = vi.fn()
    const rows = buildOutsideRows(
      { unsortedActivity: -145.2, unsortedAvailable: -145.2, uncatIncome: 0, unbudgeted: [] },
      onReview,
      vi.fn(),
      vi.fn(),
    )
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({
      kindClass: 'unsorted',
      label: 'Uncategorized',
      status: 'transactions missing a category',
      actionLabel: 'Review in Accounts',
    })
    rows[0]!.onAction()
    expect(onReview).toHaveBeenCalled()
  })

  // BUD-SWEEP-07 — the Uncategorized row's own activity popover must scope to EXPENSE only (the
  // uncat-income row below already scopes to kind:'income'); without this the popover listed
  // both kinds and its footer net contradicted the rows it just showed.
  it('Uncategorized row scopes its activity popover query to kind:"expense"', () => {
    const rows = buildOutsideRows(
      { unsortedActivity: -145.2, unsortedAvailable: -145.2, uncatIncome: 0, unbudgeted: [] },
      vi.fn(),
      vi.fn(),
      vi.fn(),
    )
    expect(rows[0]!.cellTarget).toMatchObject({ uncategorized: true, kind: 'expense' })
  })

  it('Uncategorized income row only appears when POSITIVE (never for ~zero or negative)', () => {
    expect(
      buildOutsideRows(
        { unsortedActivity: 0, unsortedAvailable: 0, uncatIncome: 0, unbudgeted: [] },
        vi.fn(),
        vi.fn(),
        vi.fn(),
      ),
    ).toHaveLength(0)
    const rows = buildOutsideRows(
      { unsortedActivity: 0, unsortedAvailable: 0, uncatIncome: 120, unbudgeted: [] },
      vi.fn(),
      vi.fn(),
      vi.fn(),
    )
    expect(rows[0]).toMatchObject({
      kindClass: 'uncatincome',
      label: 'Uncategorized income',
      status: 'already in Ready to Assign',
      actionLabel: 'Label it',
    })
  })

  it('per-category Unbudgeted rows: own color, "Add to envelope…" carries the key + label', () => {
    const onAdd = vi.fn()
    const rows = buildOutsideRows(
      {
        unsortedActivity: 0,
        unsortedAvailable: 0,
        uncatIncome: 0,
        unbudgeted: [
          {
            key: 'pet_care',
            label: 'Pet care',
            activity: -86.4,
            available: -86.4,
            color: '#d4537e',
          },
        ],
      },
      vi.fn(),
      vi.fn(),
      onAdd,
    )
    expect(rows[0]).toMatchObject({
      kindClass: 'unbudgeted',
      label: 'Pet care',
      color: '#d4537e',
      status: 'category not in an envelope',
    })
    rows[0]!.onAction()
    expect(onAdd).toHaveBeenCalledWith('pet_care', 'Pet care')
  })

  it('filters out near-zero unbudgeted lines individually while keeping the rest', () => {
    const rows = buildOutsideRows(
      {
        unsortedActivity: 0,
        unsortedAvailable: 0,
        uncatIncome: 0,
        unbudgeted: [
          { key: 'gifts', label: 'Gifts', activity: -45.5, available: -45.5, color: '#000' },
          { key: 'quiet', label: 'Quiet', activity: 0, available: 0, color: '#000' },
        ],
      },
      vi.fn(),
      vi.fn(),
      vi.fn(),
    )
    expect(rows.map((r) => r.label)).toEqual(['Gifts'])
  })

  it('keeps a line whose Activity nets to ~0 but which still HOLDS a hole', () => {
    // Review 2026-09-10 — the display half of the `_unbudgeted_keys` fix. A cash expense
    // cancelled on Activity by a same-label transfer/sell landing on a CARD nets to 0.00 while
    // still holding the whole cash hole. `outsideAvailableSubtotal` sums that hold whether or
    // not the row rendered, so filtering on Activity alone published a subtotal with no row
    // under it to explain it.
    const data: OutsideEnvelopesData = {
      unsortedActivity: 0,
      unsortedAvailable: 0,
      uncatIncome: 0,
      unbudgeted: [
        { key: 'subs', label: 'Subscriptions', activity: 0, available: -100, color: '#000' },
        { key: 'quiet', label: 'Quiet', activity: 0, available: 0, color: '#000' },
      ],
    }
    const rows = buildOutsideRows(data, vi.fn(), vi.fn(), vi.fn())
    expect(rows.map((r) => r.label)).toEqual(['Subscriptions'])
    expect(rows[0]!.available).toBe(-100)
    // The listed rows now account for the whole subtotal — nothing hidden.
    expect(outsideAvailableSubtotal(data)).toBe(-100)
  })
})

describe('OutsideEnvelopes — rendering', () => {
  it('renders nothing when there is nothing to show', () => {
    const { container } = render(
      <OutsideEnvelopes
        data={EMPTY}
        month="2026-07"
        onReviewInAccounts={vi.fn()}
        onLabelIncome={vi.fn()}
        onAddToEnvelope={vi.fn()}
      />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('renders the section header with a count + subtotal, and every row by label', () => {
    const data: OutsideEnvelopesData = {
      unsortedActivity: -145.2,
      unsortedAvailable: 0,
      uncatIncome: 120,
      unbudgeted: [
        { key: 'pet_care', label: 'Pet care', activity: -86.4, available: -86.4, color: '#d4537e' },
      ],
    }
    render(
      <OutsideEnvelopes
        data={data}
        month="2026-07"
        onReviewInAccounts={vi.fn()}
        onLabelIncome={vi.fn()}
        onAddToEnvelope={vi.fn()}
      />,
    )
    expect(screen.getByText('Outside envelopes')).toBeTruthy()
    expect(screen.getByText('3')).toBeTruthy()
    expect(screen.getByText('Uncategorized')).toBeTruthy()
    expect(screen.getByText('Uncategorized income')).toBeTruthy()
    expect(screen.getByText('Pet care')).toBeTruthy()
  })

  it('"Review in Accounts" and "Label it" fire their callbacks', () => {
    const onReview = vi.fn()
    const onLabel = vi.fn()
    const data: OutsideEnvelopesData = {
      unsortedActivity: -50,
      unsortedAvailable: -50,
      uncatIncome: 10,
      unbudgeted: [],
    }
    render(
      <OutsideEnvelopes
        data={data}
        month="2026-07"
        onReviewInAccounts={onReview}
        onLabelIncome={onLabel}
        onAddToEnvelope={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Review in Accounts' }))
    fireEvent.click(screen.getByRole('button', { name: 'Label it' }))
    expect(onReview).toHaveBeenCalled()
    expect(onLabel).toHaveBeenCalled()
  })
})

// ── The Available column (ADR 2026-09-10) ────────────────────────────────────────────────
// These two buckets HOLD their spending until month-end now, so the column that used to be a
// literal em dash — there was nothing to hold, so there was no figure — carries a real number.

describe('Available column — what the no-bucket buckets hold', () => {
  it('carries the row Available straight through, per bucket', () => {
    const rows = buildOutsideRows(
      {
        unsortedActivity: -15,
        unsortedAvailable: -15,
        uncatIncome: 120,
        unbudgeted: [
          { key: 'subs', label: 'Subscriptions', activity: -8, available: -8, color: '#000' },
        ],
      },
      vi.fn(),
      vi.fn(),
      vi.fn(),
    )
    expect(rows.map((r) => [r.kindClass, r.available])).toEqual([
      ['unsorted', -15],
      // Uncategorized income holds NOTHING — it is already inside Ready to Assign — so it
      // keeps the em dash (`null`) rather than printing a misleading S$0.
      ['uncatincome', null],
      ['unbudgeted', -8],
    ])
  })

  it('Available is NOT Activity: a card swipe shows activity but holds nothing', () => {
    // The cash/card split, on the row (backend `_cash_activity_map`): a card-only unlinked
    // spend renders its −20 of activity and an Available of 0, because the debt is the card's.
    const rows = buildOutsideRows(
      {
        unsortedActivity: -20,
        unsortedAvailable: 0,
        uncatIncome: 0,
        unbudgeted: [
          { key: 'subs', label: 'Subscriptions', activity: -8, available: 0, color: '#000' },
        ],
      },
      vi.fn(),
      vi.fn(),
      vi.fn(),
    )
    expect(rows.map((r) => r.available)).toEqual([0, 0])
  })

  it('subtotals the held amounts separately from the activity subtotal', () => {
    const data: OutsideEnvelopesData = {
      unsortedActivity: -20, // −15 cash + −5 on a card
      unsortedAvailable: -15,
      uncatIncome: 120, // excluded from BOTH subtotals
      unbudgeted: [
        { key: 'subs', label: 'Subscriptions', activity: -8, available: -8, color: '#000' },
      ],
    }
    expect(outsideAvailableSubtotal(data)).toBe(-23)
  })

  it('renders the held figures, red, in the Available column', () => {
    // Uncategorized's activity (−165.20) deliberately differs from what it HOLDS (−145.20 — the
    // other S$20 was a card swipe), so the two subtotal cells can't pass by coincidence.
    const data: OutsideEnvelopesData = {
      unsortedActivity: -165.2,
      unsortedAvailable: -145.2,
      uncatIncome: 120,
      unbudgeted: [
        { key: 'pet_care', label: 'Pet care', activity: -86.4, available: -86.4, color: '#d4537e' },
      ],
    }
    const { container } = render(
      <OutsideEnvelopes
        data={data}
        month="2026-07"
        onReviewInAccounts={vi.fn()}
        onLabelIncome={vi.fn()}
        onAddToEnvelope={vi.fn()}
      />,
    )
    // Same `Fig kind="available"` every envelope row uses: `pl-fig bad` + `mask`, cents only
    // when fractional — so a held bucket reads exactly like an overspent envelope.
    const bad = Array.from(container.querySelectorAll('.pl-fig.bad')).map((n) => n.textContent)
    expect(bad).toContain('−S$145.20')
    expect(bad).toContain('−S$86.40')
    expect(container.querySelectorAll('.pl-fig.bad.mask').length).toBe(bad.length)
    // ...and the group header's own subtotal of the two, DISTINCT from the activity subtotal
    // beside it (−251.60): what these buckets hold is not what they spent.
    const subtotals = Array.from(container.querySelectorAll('.pl-gsub')).map((n) => n.textContent)
    expect(subtotals).toContain('−S$251.60')
    expect(subtotals).toContain('−S$231.60')
    // The uncategorized-income row still shows the em dash — it holds nothing.
    expect(
      Array.from(container.querySelectorAll('.pl-fig.zero')).map((n) => n.textContent),
    ).toContain('—')
  })
})
