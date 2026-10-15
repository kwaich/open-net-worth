import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  _resetBusForTests,
  dispatchScreenCommand,
  emitDataChanged,
  onDataChanged,
  onScreenCommand,
} from './bus'

beforeEach(() => _resetBusForTests())

describe('data_changed', () => {
  it('fans out to subscribers and stops after unsubscribe', () => {
    const cb = vi.fn()
    const off = onDataChanged(cb)
    emitDataChanged(['plan'])
    off()
    emitDataChanged(['plan'])
    expect(cb).toHaveBeenCalledTimes(1)
    expect(cb).toHaveBeenCalledWith(['plan'])
  })

  it('a throwing subscriber never breaks the others', () => {
    const good = vi.fn()
    onDataChanged(() => {
      throw new Error('boom')
    })
    onDataChanged(good)
    emitDataChanged(['ledger'])
    expect(good).toHaveBeenCalledOnce()
  })
})

describe('screen commands', () => {
  it('delivers to a live prefix subscriber (consumed, not parked)', () => {
    const h = vi.fn()
    onScreenCommand('plan.', h)
    dispatchScreenCommand({ name: 'plan.reset', args: {} })
    const late = vi.fn()
    onScreenCommand('plan.', late)
    expect(h).toHaveBeenCalledOnce()
    expect(late).not.toHaveBeenCalled() // consumed — no replay
  })

  it('parks with no live subscriber and replays ONCE to the next non-passive subscriber', () => {
    dispatchScreenCommand({ name: 'plan.stage_whatif', args: { retire_age: 45 } })
    const h = vi.fn()
    onScreenCommand('plan.', h)
    expect(h).toHaveBeenCalledWith({ name: 'plan.stage_whatif', args: { retire_age: 45 } })
    const h2 = vi.fn()
    onScreenCommand('plan.', h2)
    expect(h2).not.toHaveBeenCalled() // replay-once
  })

  it('parked is last-write-wins per command name', () => {
    dispatchScreenCommand({ name: 'plan.stage_whatif', args: { retire_age: 45 } })
    dispatchScreenCommand({ name: 'plan.stage_whatif', args: { retire_age: 50 } })
    const h = vi.fn()
    onScreenCommand('plan.', h)
    expect(h).toHaveBeenCalledTimes(1)
    expect(h).toHaveBeenCalledWith({ name: 'plan.stage_whatif', args: { retire_age: 50 } })
  })

  it('a passive subscriber neither consumes nor replays', () => {
    const passive = vi.fn()
    onScreenCommand('plan.', passive, { passive: true })
    dispatchScreenCommand({ name: 'plan.reset', args: {} })
    expect(passive).toHaveBeenCalledOnce() // sees it live…
    const real = vi.fn()
    onScreenCommand('plan.', real)
    expect(real).toHaveBeenCalledOnce() // …but did not consume: still parked for the real one
  })
})
