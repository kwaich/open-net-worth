// @vitest-environment jsdom
//
// Plan keeps its three sub-tabs mounted, and the newest facts registration speaks. A pane that
// is mounted but hidden must be able to stay out of the way, and a pane that leaves must hand
// the floor back to the one beneath it instead of leaving Lucius with nothing.

import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, renderHook } from '@testing-library/react'
import {
  getScreenSnapshot,
  screenSuggestions,
  setScreenFacts,
  setScreenPrompts,
} from './screenContract'
import { useRegisterScreenFacts, useRegisterScreenPrompts } from './useScreenContract'

beforeEach(() => {
  document.body.innerHTML = '<h1 data-ctx-title="Plan">Plan</h1>'
})
afterEach(() => {
  cleanup()
  setScreenFacts(null)
  setScreenPrompts(null)
  document.body.innerHTML = ''
})

const factsOf = () => Object.fromEntries(getScreenSnapshot(new Set()).facts)

describe('useRegisterScreenFacts', () => {
  it('registers nothing while inactive, and everything the moment it becomes active', () => {
    const { rerender } = renderHook(
      ({ active }) => useRegisterScreenFacts(() => [['Cash', 'S$1']], active),
      { initialProps: { active: false } },
    )
    expect(factsOf()['Cash']).toBeUndefined()
    rerender({ active: true })
    expect(factsOf()['Cash']).toBe('S$1')
    rerender({ active: false })
    expect(factsOf()['Cash']).toBeUndefined()
  })

  it('an inactive pane mounted last does not displace the pane in front', () => {
    renderHook(() => useRegisterScreenFacts(() => [['Pane', 'budget']]))
    renderHook(() => useRegisterScreenFacts(() => [['Pane', 'cashflow']], false))
    expect(factsOf()['Pane']).toBe('budget')
  })

  it('hands the floor back to the registration beneath when the newest one leaves', () => {
    renderHook(() => useRegisterScreenFacts(() => [['Pane', 'budget']]))
    const { rerender } = renderHook(
      ({ active }) => useRegisterScreenFacts(() => [['Pane', 'cashflow']], active),
      { initialProps: { active: true } },
    )
    expect(factsOf()['Pane']).toBe('cashflow')
    rerender({ active: false })
    expect(factsOf()['Pane']).toBe('budget')
  })

  it('stays registered with the latest closure without re-registering on every render', () => {
    let figure = 'S$1'
    const { rerender } = renderHook(() => useRegisterScreenFacts(() => [['Cash', figure]]))
    figure = 'S$2'
    rerender()
    expect(factsOf()['Cash']).toBe('S$2')
  })
})

describe('useRegisterScreenPrompts', () => {
  it('follows the same active gate', () => {
    const { rerender } = renderHook(
      ({ active }) => useRegisterScreenPrompts(() => ['Ask about cash flow'], active),
      { initialProps: { active: false } },
    )
    expect(screenSuggestions()).not.toContain('Ask about cash flow')
    rerender({ active: true })
    expect(screenSuggestions()).toContain('Ask about cash flow')
  })
})
