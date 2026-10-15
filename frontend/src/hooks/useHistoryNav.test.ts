import { describe, expect, it } from 'vitest'
import { createMemoryHistory } from '@tanstack/history'
import { HistoryReach } from './useHistoryNav'

function tracked() {
  const history = createMemoryHistory({ initialEntries: ['/a'] })
  const reach = new HistoryReach(history)
  history.subscribe(({ action }) => reach.observe(action.type))
  return { history, reach }
}

describe('HistoryReach', () => {
  it('has nowhere to go on a fresh history', () => {
    const { reach } = tracked()
    expect(reach.canGoBack).toBe(false)
    expect(reach.canGoForward).toBe(false)
  })

  it('enables back after a push and forward after going back', () => {
    const { history, reach } = tracked()
    history.push('/b')
    expect(reach.canGoBack).toBe(true)
    expect(reach.canGoForward).toBe(false)
    history.back()
    expect(reach.canGoBack).toBe(false)
    expect(reach.canGoForward).toBe(true)
    history.forward()
    expect(reach.canGoForward).toBe(false)
  })

  it('drops the forward entries when a push follows a back', () => {
    const { history, reach } = tracked()
    history.push('/b')
    history.push('/c')
    history.back()
    expect(reach.canGoForward).toBe(true)
    history.push('/d')
    expect(reach.canGoForward).toBe(false)
  })

  it('keeps forward available across a replace', () => {
    const { history, reach } = tracked()
    history.push('/b')
    history.back()
    history.replace('/a2')
    expect(reach.canGoForward).toBe(true)
  })
})
