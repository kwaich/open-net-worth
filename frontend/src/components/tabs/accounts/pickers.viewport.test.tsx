// @vitest-environment jsdom

import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRef } from 'react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { useAnchoredFixed } from './pickers'

const css = readFileSync(resolve(__dirname, 'accounts.css'), 'utf8')

function stubMobile(matches: boolean) {
  vi.stubGlobal('matchMedia', () => ({
    matches,
    media: '(max-width: 639.98px)',
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }))
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useAnchoredFixed', () => {
  it('leaves phone pickers in their anchor coordinate space', () => {
    stubMobile(true)
    const anchor = document.createElement('div')
    anchor.getBoundingClientRect = () =>
      ({
        top: 340,
        right: 564,
        bottom: 390,
        left: 24,
        width: 540,
        height: 50,
        x: 24,
        y: 340,
        toJSON: () => ({}),
      }) as DOMRect
    const ref = createRef<HTMLElement>()
    ref.current = anchor

    const { result } = renderHook(() => useAnchoredFixed(true, ref, 300))

    act(() => window.dispatchEvent(new Event('resize')))
    expect(result.current).toBeNull()
  })

  it('anchors phone picker menus above their field while the keyboard is present', () => {
    expect(css).toMatch(
      /@media \(max-width: 640px\)[\s\S]*\.acct-payf-pop,[\s\S]*\.acct-acctsel-pop,[\s\S]*\.acct-datef-pop[\s\S]*top: auto;[\s\S]*bottom: calc\(100% \+ 4px\);/,
    )
  })
})
