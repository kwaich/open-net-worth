// @vitest-environment jsdom

import { renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { useIsCoarsePointer } from './useIsCoarsePointer'

const original = Object.getOwnPropertyDescriptor(window, 'matchMedia')

function setPrimaryPointer(pointer: 'coarse' | 'fine') {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: ((query: string) => ({
      matches: query === '(pointer: coarse)' && pointer === 'coarse',
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as typeof window.matchMedia,
  })
}

afterEach(() => {
  if (original) Object.defineProperty(window, 'matchMedia', original)
})

describe('useIsCoarsePointer', () => {
  it('identifies iPad-style primary touch input without treating viewport width as input capability', () => {
    setPrimaryPointer('coarse')
    expect(renderHook(() => useIsCoarsePointer()).result.current).toBe(true)

    setPrimaryPointer('fine')
    expect(renderHook(() => useIsCoarsePointer()).result.current).toBe(false)
  })
})
