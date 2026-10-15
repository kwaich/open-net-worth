// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { setBaseCurrency } from '../../../utils/format'
import { Lever, MonthlySavingLever, RetirementSpendingLever } from './outlook'
import { persistFirePlan } from './firePlanPersistence'

afterEach(() => {
  cleanup()
  setBaseCurrency('SGD')
})

describe('RetirementSpendingLever', () => {
  it('accepts monthly spending without a slider and shows the annualized FIRE math', () => {
    const onChange = vi.fn()
    render(
      <RetirementSpendingLever monthlySpending={5_000} withdrawalRate={4} onChange={onChange} />,
    )

    expect(screen.queryByRole('slider')).toBeNull()
    const input = screen.getByRole('textbox', { name: 'Monthly retirement spending' })
    expect(input.getAttribute('value')).toBe('S$5,000')
    expect(screen.getByText(/S\$60,000\/year/)).toBeTruthy()
    expect(screen.getByText('S$1.5M')).toBeTruthy()

    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: '250000' } })
    fireEvent.blur(input)
    expect(onChange).toHaveBeenCalledWith(250_000)
  })
})

describe('MonthlySavingLever', () => {
  it('is an obvious unbounded money input instead of a range slider', () => {
    setBaseCurrency('SGD')
    const onChange = vi.fn()
    render(<MonthlySavingLever saving={20_000} baselineSaving={4_000} onChange={onChange} />)

    expect(screen.queryByRole('slider')).toBeNull()
    const input = screen.getByRole('textbox', { name: 'Monthly saving per month' })
    expect(input.getAttribute('value')).toBe('S$20,000')
    expect(input.getAttribute('inputmode')).toBe('decimal')

    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: '500000' } })
    fireEvent.blur(input)
    expect(onChange).toHaveBeenCalledWith(500_000)
  })
})

describe('bounded slider levers', () => {
  it('clamps an exact typed value to the same maximum shown by its slider', () => {
    const onChange = vi.fn()
    render(
      <Lever
        label="Retirement age"
        display="45"
        value={45}
        min={30}
        max={80}
        step={1}
        onChange={onChange}
      />,
    )

    const input = screen.getByRole('spinbutton', { name: /edit retirement age/i })
    expect(input.getAttribute('max')).toBe('80')
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: '500' } })
    fireEvent.blur(input)
    expect(onChange).toHaveBeenCalledWith(80)
  })

  it('cancels typed changes on Escape and emits only once on Enter', () => {
    const onChange = vi.fn()
    render(
      <Lever
        label="Expected return"
        display="7%"
        value={7}
        min={0}
        max={12}
        step={0.1}
        onChange={onChange}
      />,
    )

    const input = screen.getByRole('spinbutton', { name: /edit expected return/i })
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: '8' } })
    fireEvent.keyDown(input, { key: 'Escape' })
    fireEvent.blur(input)
    expect(onChange).not.toHaveBeenCalled()

    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: '9' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    fireEvent.blur(input)
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenCalledWith(9)
  })
})

describe('FIRE plan persistence', () => {
  it('waits for the durable baseline write before syncing the profile', async () => {
    let finishBaseline!: () => void
    const saveBaseline = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finishBaseline = resolve
        }),
    )
    const syncProfile = vi.fn().mockResolvedValue(undefined)
    const pending = persistFirePlan({ saving: 25_000 }, saveBaseline, syncProfile, false)

    await Promise.resolve()
    expect(saveBaseline).toHaveBeenCalledWith({ saving: 25_000 })
    expect(syncProfile).not.toHaveBeenCalled()

    finishBaseline()
    await expect(pending).resolves.toBe(true)
    expect(syncProfile).toHaveBeenCalledTimes(1)
  })

  it('does not attempt the profile sync when the baseline write fails', async () => {
    const syncProfile = vi.fn()
    await expect(
      persistFirePlan(
        { monthlyRetirementSpending: 5_000 },
        vi.fn().mockRejectedValue(new Error('baseline failed')),
        syncProfile,
        false,
      ),
    ).rejects.toThrow('baseline failed')
    expect(syncProfile).not.toHaveBeenCalled()
  })

  it('retries only the profile sync when the baseline is already durable', async () => {
    const saveBaseline = vi.fn()
    const syncProfile = vi.fn().mockResolvedValue(undefined)
    await expect(
      persistFirePlan({ saving: 25_000 }, saveBaseline, syncProfile, true),
    ).resolves.toBe(true)
    expect(saveBaseline).not.toHaveBeenCalled()
    expect(syncProfile).toHaveBeenCalledTimes(1)
  })
})
