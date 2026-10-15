import { describe, it, expect } from 'vitest'
import { buildActivityQuery, buildRegisterFilter } from './budgetActivity'

describe('buildActivityQuery — the Activity popover transaction fetch', () => {
  it('a plain 1:1 row fetches its single category', () => {
    const qs = buildActivityQuery(
      { activity: -120, label: 'Groceries', color: '#fff', categoryIds: ['groceries'] },
      '2026-06',
    )
    const p = new URLSearchParams(qs)
    expect(p.getAll('category')).toEqual(['groceries'])
    expect(p.get('ym')).toBe('2026-06')
  })

  it('a merged N>1 envelope row fetches EVERY member category as repeated params', () => {
    const qs = buildActivityQuery(
      { activity: -300, label: 'Fun', color: '#fff', categoryIds: ['fun', 'hobby', 'games'] },
      '2026-06',
    )
    const p = new URLSearchParams(qs)
    expect(p.getAll('category')).toEqual(['fun', 'hobby', 'games'])
  })

  it('a ZERO-category envelope (payment/commitments) fetches by its envelope id, not an empty category', () => {
    // A payment envelope has categoryIds [] but its activity is keyed by the envelope id — without
    // this fallback the fetch sends no `category` and the popover shows 0 txns for a nonzero total.
    const qs = buildActivityQuery(
      {
        activity: -130,
        label: 'Meridian Bank WWDC',
        color: '#fff',
        categoryIds: [],
        envelopeId: 'env_cc',
      },
      '2026-06',
    )
    const p = new URLSearchParams(qs)
    expect(p.getAll('category')).toEqual(['env_cc'])
  })

  it('the derived Uncategorized row fetches the blank-category sentinel (category= with no value)', () => {
    const qs = buildActivityQuery(
      { activity: -45, label: 'Uncategorized', color: 'var(--warn)', uncategorized: true },
      '2026-06',
    )
    const p = new URLSearchParams(qs)
    expect(p.getAll('category')).toEqual([''])
    expect(p.getAll('kind')).toEqual([])
  })

  // Handoff item 7 — the Outside-envelopes "Uncategorized income" row composes `kind=income`
  // with the blank-category sentinel to isolate uncategorized INCOME from Uncategorized EXPENSE
  // (both share the same "" bucket server-side — services.planning._activity_by_category).
  it('the Uncategorized-income row composes the blank sentinel with kind=income', () => {
    const qs = buildActivityQuery(
      {
        activity: 120,
        label: 'Uncategorized income',
        color: 'var(--good)',
        uncategorized: true,
        kind: 'income',
      },
      '2026-06',
    )
    const p = new URLSearchParams(qs)
    expect(p.getAll('category')).toEqual([''])
    expect(p.getAll('kind')).toEqual(['income'])
  })

  it('a non-uncategorized row never sends a kind param, even if one is set on the target', () => {
    const qs = buildActivityQuery(
      {
        activity: -20,
        label: 'Groceries',
        color: '#fff',
        categoryIds: ['groceries'],
        kind: 'income',
      },
      '2026-06',
    )
    const p = new URLSearchParams(qs)
    expect(p.getAll('kind')).toEqual([])
  })
})

// ── buildRegisterFilter — the popover's "Open in Accounts" deep-link (handoff item 14) ──────
describe('buildRegisterFilter — Activity popover "Open in Accounts"', () => {
  it('a plain 1:1 row deep-links its single category + month', () => {
    const f = buildRegisterFilter(
      { activity: -120, label: 'Groceries', color: '#fff', categoryIds: ['groceries'] },
      '2026-06',
    )
    expect(f).toEqual({ category: 'groceries', month: '2026-06' })
  })

  it('a merged N>1 row carries EVERY member (FEB-07 — the register must show the same set the popover just listed)', () => {
    const f = buildRegisterFilter(
      { activity: -300, label: 'Fun', color: '#fff', categoryIds: ['fun', 'hobby', 'games'] },
      '2026-06',
    )
    expect(f.category).toEqual(['fun', 'hobby', 'games'])
  })

  it('the Uncategorized row deep-links uncategorized=true, no category/kind', () => {
    const f = buildRegisterFilter(
      { activity: -45, label: 'Uncategorized', color: 'var(--warn)', uncategorized: true },
      '2026-06',
    )
    expect(f).toEqual({ uncategorized: true, month: '2026-06' })
  })

  it('the Uncategorized-income row composes uncategorized=true with kind=income', () => {
    const f = buildRegisterFilter(
      {
        activity: 120,
        label: 'Uncategorized income',
        color: 'var(--good)',
        uncategorized: true,
        kind: 'income',
      },
      '2026-06',
    )
    expect(f).toEqual({ uncategorized: true, month: '2026-06', kind: 'income' })
  })
})
