import { describe, expect, it } from 'vitest'
import { buildWhatifState } from './stageWhatif'

const CTX = {
  base: {
    saving: 3000,
    ret: 7,
    vol: 15,
    monthlyRetirementSpending: 5000,
    swr: 4,
    lifeExp: 90,
    retireAge: 60,
  },
  baseEvents: [{ id: 'e1', label: '🏠 Home', amount: 150000, age: 40 }],
  baseStreams: [{ label: 'Rental', amount: 1200, age: 55, endAge: null }],
  currentAge: 35,
  lifeExp: 90,
}

describe('buildWhatifState', () => {
  it('maps camelCase levers and leaves the rest for applyState baseline reset', () => {
    const { partial } = buildWhatifState({ retireAge: 45, saving: 4000 }, CTX)
    expect(partial.retireAge).toBe(45)
    expect(partial.saving).toBe(4000)
    expect(partial.ret).toBeUndefined()
  })

  it('tolerates legacy snake_case args silently (ephemeral staging never refuses)', () => {
    const { partial, streams } = buildWhatifState(
      {
        retire_age: 45,
        life_exp: 70,
        add_streams: [{ label: 'P', amount: 100, age: 60, end_age: 65 }],
      },
      CTX,
    )
    expect(partial.retireAge).toBe(45)
    expect(partial.lifeExp).toBe(70)
    expect(streams.find((s) => s.label === 'P')?.endAge).toBe(65)
  })

  it('clamps levers into range', () => {
    const { partial } = buildWhatifState(
      { retireAge: 400, swr: -3, saving: -100, monthlyRetirementSpending: -1 },
      CTX,
    )
    expect(partial.retireAge).toBe(150)
    expect(partial.swr).toBe(0)
    expect(partial.saving).toBe(0)
    expect(partial.monthlyRetirementSpending).toBe(0)
  })

  it('events = baseline ∖ removes ∪ adds, ages clamped, labels capped', () => {
    const { partial } = buildWhatifState(
      {
        removeEvents: ['🏠 home'],
        addEvents: [
          { label: 'Baby', amount: 90000, age: 36 },
          { label: 'TooYoung', amount: 1000, age: 10 },
          { label: 'no amount' },
        ],
      },
      CTX,
    )
    expect(partial.events).toHaveLength(2)
    expect(partial.events?.[0]?.label).toBe('Baby')
    expect(partial.events?.[1]?.age).toBe(35) // clamped to currentAge
  })

  // Regression (live incident): a model asked to remove "the old Porsche" recalled it as
  // "porsche", not the exact staged label "Buy Porsche (Vietnam)" — an EXACT match removed
  // nothing, so the model concluded there was no way to drop just one item and reached for
  // plan.reset instead, wiping the user's other staged events too. removeEvents/removeStreams
  // now match a case-insensitive substring either way, so an approximate recall still works.
  it('removeEvents matches a case-insensitive partial label, not just an exact one', () => {
    const ctx = {
      ...CTX,
      baseEvents: [
        { id: 'e1', label: '🏠 Home', amount: 500000, age: 30 },
        { id: 'e2', label: 'Buy Porsche (Vietnam)', amount: 300000, age: 35 },
      ],
    }
    const { partial } = buildWhatifState({ removeEvents: ['porsche'] }, ctx)
    expect(partial.events?.map((e) => e.label)).toEqual(['🏠 Home'])
  })

  it('removeStreams matches a case-insensitive partial label too', () => {
    const ctx = {
      ...CTX,
      baseStreams: [
        { label: 'CPF LIFE', amount: 2000, age: 65, endAge: null },
        { label: 'Rental income (Jakarta)', amount: 1200, age: 55, endAge: null },
      ],
    }
    const { streams } = buildWhatifState({ removeStreams: ['jakarta'] }, ctx)
    expect(streams.map((s) => s.label)).toEqual(['CPF LIFE'])
  })

  it('ignores a removeEvents token under 3 chars — too unspecific to safely match on', () => {
    const ctx = {
      ...CTX,
      baseEvents: [{ id: 'e1', label: 'Home', amount: 500000, age: 30 }],
    }
    const { partial } = buildWhatifState({ removeEvents: ['ho'] }, ctx)
    expect(partial.events?.map((e) => e.label)).toEqual(['Home'])
  })

  it('streams fold mirrors events and drops endAge ≤ age', () => {
    const { streams } = buildWhatifState(
      {
        removeStreams: ['rental'],
        addStreams: [{ label: 'Pension', amount: 2000, age: 65, endAge: 60 }],
      },
      CTX,
    )
    expect(streams).toHaveLength(1)
    expect(streams[0]?.label).toBe('Pension')
    expect(streams[0]?.endAge).toBeNull()
  })

  it('what-if lifeExp is used for event-age clamping', () => {
    const { partial } = buildWhatifState(
      { lifeExp: 70, addEvents: [{ label: 'Late', amount: 1, age: 85 }] },
      CTX,
    )
    expect(partial.events?.find((e) => e.label === 'Late')?.age).toBe(69)
  })

  // Regression: a fractional ctx.currentAge (e.g. 28.1 from a date_of_birth) leaking into a
  // clamped age — a too-young event snapped UP to the raw fractional bound instead of a whole
  // year, and the default "5 years from now" fallback inherited the fraction too.
  it('never returns a fractional age even when ctx.currentAge is fractional', () => {
    const fractionalCtx = { ...CTX, currentAge: 28.1 }
    const { partial } = buildWhatifState(
      {
        addEvents: [
          { label: 'Too young', amount: 1000, age: 10 }, // clamps UP to currentAge
          { label: 'Defaulted', amount: 2000 }, // no age → ctx.currentAge + 5 fallback
        ],
      },
      fractionalCtx,
    )
    const ages = partial.events?.map((e) => e.age) ?? []
    expect(ages.every(Number.isInteger)).toBe(true)
    expect(partial.events?.find((e) => e.label === 'Too young')?.age).toBe(28)
    expect(partial.events?.find((e) => e.label === 'Defaulted')?.age).toBe(33)
  })

  it('never returns a fractional age for a stream when ctx.currentAge is fractional', () => {
    const fractionalCtx = { ...CTX, currentAge: 28.1 }
    const { streams } = buildWhatifState(
      { addStreams: [{ label: 'Too young', amount: 100, age: 10 }] },
      fractionalCtx,
    )
    expect(streams.find((s) => s.label === 'Too young')?.age).toBe(28)
  })

  it('accepts label synonyms (note/name) — live repro: grok sent note:"Ferrari purchase"', () => {
    const { partial } = buildWhatifState(
      {
        retireAge: 52,
        addEvents: [
          { age: 35, type: 'one_time_expense', amount: 1000000, note: 'Ferrari purchase' },
        ],
      },
      CTX,
    )
    const added = partial.events?.find((e) => e.amount === 1000000)
    expect(added?.label).toBe('Ferrari purchase')
  })

  it('shock booleans pass through', () => {
    const { partial } = buildWhatifState({ crash: true, highInfl: true }, CTX)
    expect(partial.crash).toBe(true)
    expect(partial.highInfl).toBe(true)
    expect(partial.weakSeq).toBeUndefined()
  })

  it('no event args → events echoes the baseline (applyState default parity)', () => {
    const { partial, streams } = buildWhatifState({ retireAge: 50 }, CTX)
    expect(partial.events).toEqual(CTX.baseEvents)
    expect(streams).toEqual(CTX.baseStreams)
  })

  // Regression: a live conversation staged "buy a house at 45", then a follow-up call staged
  // "buy a Porsche" without re-listing the house — and the house silently vanished, because
  // outlook.tsx re-based every call on the SAVED baseline instead of what was already staged.
  // outlook.tsx now passes the PREVIOUS call's returned events/streams back in as
  // ctx.baseEvents/baseStreams, so add(Events|Streams) accumulate across calls; this pins that
  // calling contract at the pure-fold level.
  it('accumulates across calls when the caller re-bases ctx on the prior staged events', () => {
    const first = buildWhatifState(
      { addEvents: [{ label: 'Buy house at 45', amount: 1000000, age: 45 }] },
      CTX,
    )
    expect(first.partial.events?.map((e) => e.label)).toEqual(['🏠 Home', 'Buy house at 45'])

    const second = buildWhatifState(
      { addEvents: [{ label: 'Buy Porsche', amount: 400000, age: 40 }] },
      { ...CTX, baseEvents: first.partial.events! },
    )
    expect(second.partial.events?.map((e) => e.label)).toEqual([
      '🏠 Home',
      'Buy house at 45',
      'Buy Porsche',
    ])
  })
})
