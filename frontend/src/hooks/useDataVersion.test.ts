// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react'
import { beforeEach, expect, it } from 'vitest'
import { _resetBusForTests, emitDataChanged } from '../lucius/bus'
import { useDataVersion } from './useDataVersion'

beforeEach(() => _resetBusForTests())

it('bumps on a matching domain, ignores others, honours "*"', () => {
  const { result } = renderHook(() => useDataVersion(['plan', 'profile']))
  expect(result.current).toBe(0)
  act(() => emitDataChanged(['plan']))
  expect(result.current).toBe(1)
  act(() => emitDataChanged(['ledger']))
  expect(result.current).toBe(1)
  act(() => emitDataChanged(['*']))
  expect(result.current).toBe(2)
})

it('accepts a single-domain string', () => {
  const { result } = renderHook(() => useDataVersion('profile'))
  act(() => emitDataChanged(['profile', 'plan']))
  expect(result.current).toBe(1)
})
