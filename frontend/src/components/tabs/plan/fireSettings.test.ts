import { describe, expect, it, vi } from 'vitest'
import { authFetch } from '../../../api/client'
import { annualRetirementSpending } from './planData'
import {
  EMPTY_FIRE_DRAFT,
  fireNumberOf,
  fireSwrNote,
  saveFireDraft,
  seedFireDraft,
} from './fireSettings'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))

describe('monthly retirement spending settings contract', () => {
  it('seeds the monthly value without converting it to annual spending', () => {
    expect(seedFireDraft({ fire_monthly_retirement_spending: 5000 })).toMatchObject({
      monthlyRetirementSpending: 5000,
    })
  })

  it('annualizes only at the FIRE-number calculation boundary', () => {
    expect(annualRetirementSpending(5000)).toBe(60000)
    expect(
      fireNumberOf({
        monthlyRetirementSpending: 5000,
        withdrawalRate: 4,
        expectedReturn: '',
        volatility: '',
        targetAge: '',
        lifeExpectancy: '',
        monthlySaving: '',
      }),
    ).toBe(1_500_000)
  })

  it('persists the monthly profile field', async () => {
    vi.mocked(authFetch).mockResolvedValue(new Response('{}', { status: 200 }))
    await saveFireDraft({
      monthlyRetirementSpending: 5000,
      withdrawalRate: '',
      expectedReturn: '',
      volatility: '',
      targetAge: '',
      lifeExpectancy: '',
      monthlySaving: '',
    })
    expect(JSON.parse(String(vi.mocked(authFetch).mock.calls[0]?.[1]?.body))).toEqual({
      fire_monthly_retirement_spending: 5000,
    })
  })
})

describe('monthly saving is its own stored fact', () => {
  const blank = {
    monthlyRetirementSpending: '',
    withdrawalRate: '',
    expectedReturn: '',
    volatility: '',
    targetAge: '',
    lifeExpectancy: '',
  } as const

  it('saves as monthly_saving and never writes income', async () => {
    vi.mocked(authFetch).mockClear()
    vi.mocked(authFetch).mockResolvedValue(new Response('{}', { status: 200 }))
    await saveFireDraft({ ...blank, monthlySaving: 1200 })
    expect(JSON.parse(String(vi.mocked(authFetch).mock.calls[0]?.[1]?.body))).toEqual({
      monthly_saving: 1200,
    })
  })

  it('a stated zero is saved, not skipped', async () => {
    vi.mocked(authFetch).mockClear()
    vi.mocked(authFetch).mockResolvedValue(new Response('{}', { status: 200 }))
    await saveFireDraft({ ...blank, monthlySaving: 0 })
    expect(JSON.parse(String(vi.mocked(authFetch).mock.calls[0]?.[1]?.body))).toEqual({
      monthly_saving: 0,
    })
  })

  it('leaves a saving the user did not change alone, so a derived figure is never pinned', async () => {
    vi.mocked(authFetch).mockClear()
    vi.mocked(authFetch).mockResolvedValue(new Response('{}', { status: 200 }))
    const profile = { monthly_net_income: 6100, monthly_expenses: 2100 }
    const seeded = seedFireDraft(profile)
    expect(seeded.monthlySaving).toBe(4000)

    await saveFireDraft({ ...seeded, withdrawalRate: 4 }, profile)
    expect(JSON.parse(String(vi.mocked(authFetch).mock.calls[0]?.[1]?.body))).toEqual({
      fire_monthly_retirement_spending: 2100,
      fire_withdrawal_rate_pct: 4,
    })

    vi.mocked(authFetch).mockClear()
    await saveFireDraft({ ...seeded, monthlySaving: 4500 }, profile)
    expect(JSON.parse(String(vi.mocked(authFetch).mock.calls[0]?.[1]?.body)).monthly_saving).toBe(
      4500,
    )
  })

  it('sends a negative saving: spending more than you earn is a real fact', async () => {
    vi.mocked(authFetch).mockClear()
    vi.mocked(authFetch).mockResolvedValue(new Response('{}', { status: 200 }))
    await saveFireDraft({ ...blank, monthlySaving: -350 })
    expect(JSON.parse(String(vi.mocked(authFetch).mock.calls[0]?.[1]?.body))).toEqual({
      monthly_saving: -350,
    })
  })

  it('seeds the stated saving over what income minus spending would give', () => {
    expect(
      seedFireDraft({ monthly_saving: 1200, monthly_net_income: 6100, monthly_expenses: 4000 }),
    ).toMatchObject({ monthlySaving: 1200 })
  })

  it('falls back to income minus spending when none was stated', () => {
    expect(seedFireDraft({ monthly_net_income: 6100, monthly_expenses: 4000 })).toMatchObject({
      monthlySaving: 2100,
    })
  })
})

describe('volatility and life expectancy show the value the plan uses', () => {
  it('seeds 15% and 90 when the profile has set neither', () => {
    expect(seedFireDraft({})).toMatchObject({ volatility: 15, lifeExpectancy: 90 })
  })

  it("keeps the user's own figures", () => {
    expect(seedFireDraft({ fire_volatility_pct: 18, fire_life_expectancy: 95 })).toMatchObject({
      volatility: 18,
      lifeExpectancy: 95,
    })
  })

  it('does not save a value in use that the user never set, only one they changed', async () => {
    vi.mocked(authFetch).mockClear()
    vi.mocked(authFetch).mockResolvedValue(new Response('{}', { status: 200 }))
    const profile = {}
    await saveFireDraft({ ...seedFireDraft(profile), withdrawalRate: 4 }, profile)
    expect(JSON.parse(String(vi.mocked(authFetch).mock.calls[0]?.[1]?.body))).toEqual({
      fire_withdrawal_rate_pct: 4,
    })

    vi.mocked(authFetch).mockClear()
    await saveFireDraft({ ...seedFireDraft(profile), volatility: 12, lifeExpectancy: 88 }, profile)
    expect(JSON.parse(String(vi.mocked(authFetch).mock.calls[0]?.[1]?.body))).toEqual({
      fire_volatility_pct: 12,
      fire_life_expectancy: 88,
    })
  })
})

describe('withdrawal-rate suggestion note', () => {
  const draft = (over: Record<string, number | ''>) => ({
    ...EMPTY_FIRE_DRAFT,
    targetAge: 62,
    lifeExpectancy: 90,
    ...over,
  })

  it('says why a plan below the suggestion keeps its rate', () => {
    expect(fireSwrNote(draft({ withdrawalRate: 4 }))).toBe(
      'A 28-yr retirement usually allows 4.25%. Your plan keeps 4%, the more cautious choice.',
    )
  })

  it('says a plan above the suggestion is the more aggressive one', () => {
    expect(fireSwrNote(draft({ withdrawalRate: 5 }))).toBe(
      'A 28-yr retirement is safer at 4.25%. Your plan uses 5%, which is more aggressive.',
    )
  })

  it('names the usual rate when none is set, and says nothing when it matches or has no horizon', () => {
    expect(fireSwrNote(draft({ withdrawalRate: '' }))).toBe(
      '4.25% is the usual rate for a 28-yr retirement.',
    )
    expect(fireSwrNote(draft({ withdrawalRate: 4.25 }))).toBeNull()
    expect(fireSwrNote({ ...EMPTY_FIRE_DRAFT, withdrawalRate: 4 })).toBeNull()
  })
})
