// @vitest-environment jsdom
//
// The 50/30/20 hero before anything is spent: Lucius offers caps only while an envelope has none.
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PropHero } from './PropHero'
import { derive } from './propMath'
import type { PropEnvelope, PropMonth } from './propApi'

const env = (id: string, cap: number | null): PropEnvelope => ({
  id,
  label: id,
  color: '#3b86dd',
  group_id: 'bills',
  group: 'bills',
  category_ids: ['cat_' + id],
  cap,
  activity: 0,
  history: { last: 0, avg3: 0, last_by_day: 0, avg3_by_day: 0 },
})

const month = (envelopes: PropEnvelope[]): PropMonth => ({
  month: '2026-10',
  status: 'current',
  day: 1,
  days_in_month: 31,
  configured: true,
  groups: [
    {
      id: 'bills',
      name: 'Bills',
      target: 50,
      excluded: false,
      synced: false,
      activity: 0,
    },
  ],
  savings: { amount: 1000, target: 50 },
  income: { amount: 1000, source: 'profile', profile: 1000, actual: 0 },
  income_categories: [],
  unassigned: { uncategorized: 0, no_envelope: 0 },
  envelopes,
})

const hero = (envelopes: PropEnvelope[]) => {
  const m = month(envelopes)
  render(<PropHero m={m} d={derive(m)} onLucius={vi.fn()} onConfigure={vi.fn()} />)
}

afterEach(cleanup)

describe('PropHero before any spend', () => {
  it('offers Lucius caps while an envelope has none', () => {
    hero([env('rent', 300), env('power', null)])
    expect(screen.getByText('Let Lucius suggest caps')).toBeTruthy()
  })

  it('hides the offer once every envelope has a cap', () => {
    hero([env('rent', 300), env('power', 50)])
    expect(screen.queryByText('Let Lucius suggest caps')).toBeNull()
  })
})

describe('PropHero with spend', () => {
  const spent = (income: PropMonth['income']): PropMonth => {
    const m = month([{ ...env('rent', 300), activity: 400 }])
    return { ...m, income, savings: { amount: 600, target: 50 }, day: 14 }
  }
  const mount = (m: PropMonth) =>
    render(<PropHero m={m} d={derive(m)} onLucius={vi.fn()} onConfigure={vi.fn()} />)

  it('names the usual income a running month is measured against', () => {
    mount(spent({ amount: 1000, source: 'profile', profile: 1000, actual: 0 }))
    expect(screen.getByText('October, measured against your usual monthly income')).toBeTruthy()
  })

  it('names the income that arrived for a closed month', () => {
    const m = {
      ...spent({ amount: 900, source: 'actual', profile: 1000, actual: 900 }),
      status: 'past' as const,
    }
    mount(m)
    expect(screen.getByText('October, from the income that arrived')).toBeTruthy()
  })

  it('says what the pace chip compares', () => {
    const m = spent({ amount: 1000, source: 'profile', profile: 1000, actual: 0 })
    m.envelopes[0]!.history = { last: 500, avg3: 500, last_by_day: 100, avg3_by_day: 100 }
    mount(m)
    expect(screen.getByText(/spending vs/).textContent).toBe('+S$300 spending vs Sep by the 14th')
  })
})
