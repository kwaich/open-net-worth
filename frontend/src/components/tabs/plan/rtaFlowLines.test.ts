import { describe, expect, it, vi } from 'vitest'
import { buildRtaFlowLines, partitionRtaFlowLines } from './rtaLines'

const BASE = { monthLabel: 'August 2026', prevMonthLabel: 'July 2026' }

describe('buildRtaFlowLines', () => {
  it("uses YNAB's literal wording, with the real month names interpolated and the year dropped", () => {
    const lines = buildRtaFlowLines({
      ...BASE,
      carriedIn: 500,
      inflow: 300,
      assigned: 200,
      cashOverspendingPrev: 50,
      assignedInFuture: 0,
      rta: 550,
    })
    expect(lines.find((l) => l.key === 'carriedin')!.label).toBe(
      'Ready to Assign left over from July',
    )
    expect(lines.find((l) => l.key === 'inflow')!.label).toBe(
      'Inflow: Ready to Assign transactions in August',
    )
    expect(lines.find((l) => l.key === 'assigned')!.label).toBe('Assigned in August')
    expect(lines.find((l) => l.key === 'cashoverprev')!.label).toBe('Cash overspending in July')
  })

  it('sums exactly to rta with no unexplained row', () => {
    const lines = buildRtaFlowLines({
      ...BASE,
      carriedIn: 500,
      inflow: 300,
      assigned: 200,
      cashOverspendingPrev: 50,
      assignedInFuture: 0,
      rta: 550,
    })
    expect(lines.find((l) => l.key === 'other')).toBeUndefined()
    const sum = lines.reduce((s, l) => s + l.value, 0)
    expect(Math.round(sum * 100) / 100).toBeCloseTo(550, 2)
  })

  it('hides cashoverprev and future rows at zero', () => {
    const lines = buildRtaFlowLines({
      ...BASE,
      carriedIn: 100,
      inflow: 0,
      assigned: 100,
      cashOverspendingPrev: 0,
      assignedInFuture: 0,
      rta: 0,
    })
    expect(lines.find((l) => l.key === 'cashoverprev')).toBeUndefined()
    expect(lines.find((l) => l.key === 'future')).toBeUndefined()
  })

  it('the future-assigned row stays generic — the one place a single month cannot be named', () => {
    const lines = buildRtaFlowLines({
      ...BASE,
      carriedIn: 100,
      inflow: 0,
      assigned: 0,
      cashOverspendingPrev: 0,
      assignedInFuture: 40,
      rta: 60,
    })
    expect(lines.find((l) => l.key === 'future')!.label).toBe('Assigned in future months')
  })

  it('always sections cashOverspendingPrev as deducted, even at its floor', () => {
    const lines = buildRtaFlowLines({
      ...BASE,
      carriedIn: 100,
      inflow: 0,
      assigned: 0,
      cashOverspendingPrev: 0.01,
      assignedInFuture: 0,
      rta: 99.99,
    })
    const cashOver = lines.find((l) => l.key === 'cashoverprev')!
    expect(cashOver.section).toBe('deducted')
  })

  it('sections carriedin and inflow by their own sign', () => {
    const lines = buildRtaFlowLines({
      ...BASE,
      carriedIn: -50,
      inflow: -20,
      assigned: 0,
      cashOverspendingPrev: 0,
      assignedInFuture: 0,
      rta: -70,
    })
    expect(lines.find((l) => l.key === 'carriedin')!.section).toBe('deducted')
    expect(lines.find((l) => l.key === 'inflow')!.section).toBe('deducted')
  })

  it('flags a genuine mismatch with an Unexplained row and exactly one console.warn', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const lines = buildRtaFlowLines({
      ...BASE,
      carriedIn: 100,
      inflow: 0,
      assigned: 0,
      cashOverspendingPrev: 0,
      assignedInFuture: 0,
      rta: 999, // deliberately wrong
    })
    expect(lines.find((l) => l.key === 'other')).toBeTruthy()
    expect(warn).toHaveBeenCalledTimes(1)
    warn.mockRestore()
  })

  it('partitionRtaFlowLines splits by section', () => {
    const lines = buildRtaFlowLines({
      ...BASE,
      carriedIn: 100,
      inflow: 50,
      assigned: 80,
      cashOverspendingPrev: 10,
      assignedInFuture: 0,
      rta: 60,
    })
    const { added, deducted } = partitionRtaFlowLines(lines)
    expect(added.every((l) => l.section === 'added')).toBe(true)
    expect(deducted.every((l) => l.section !== 'added')).toBe(true)
  })
})
