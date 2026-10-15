import { expect, it } from 'vitest'
import { isTriggeredNow } from './alertReview'

it('identifies an active price threshold reached by the current quote', () => {
  expect(isTriggeredNow({ op: '<=', value: 100, kind: 'price', enabled: true }, 90)).toBe(true)
  expect(isTriggeredNow({ op: '>=', value: 100, kind: 'price', enabled: true }, 90)).toBe(false)
})

it('does not treat disabled or unevaluable alerts as live triggers', () => {
  expect(isTriggeredNow({ op: '<=', value: 100, kind: 'price', enabled: false }, 90)).toBe(false)
  expect(isTriggeredNow({ op: '<=', value: 5, kind: 'move' }, 90)).toBe(false)
  expect(isTriggeredNow({ op: '<=', value: 100 }, undefined)).toBe(false)
})
