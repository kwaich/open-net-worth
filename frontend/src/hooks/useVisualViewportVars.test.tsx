// @vitest-environment jsdom

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { useVisualViewportVars } from './useVisualViewportVars'

class VisualViewportStub extends EventTarget {
  height = 844
  offsetTop = 0
}

const original = window.visualViewport

afterEach(() => {
  Object.defineProperty(window, 'visualViewport', { configurable: true, value: original })
  document.documentElement.style.removeProperty('--visual-viewport-height')
  document.documentElement.style.removeProperty('--visual-viewport-top')
})

describe('useVisualViewportVars', () => {
  it('publishes the visible viewport, and tracks it as the keyboard opens and closes', () => {
    const viewport = new VisualViewportStub()
    Object.defineProperty(window, 'visualViewport', { configurable: true, value: viewport })
    const root = document.documentElement

    const { unmount } = renderHook(() => useVisualViewportVars())

    expect(root.style.getPropertyValue('--visual-viewport-height')).toBe('844px')
    expect(root.style.getPropertyValue('--visual-viewport-top')).toBe('0px')

    // Keyboard up. A full-screen fixed overlay reads these to stop where the keyboard starts.
    act(() => {
      viewport.height = 510
      viewport.offsetTop = 0
      viewport.dispatchEvent(new Event('resize'))
    })
    expect(root.style.getPropertyValue('--visual-viewport-height')).toBe('510px')

    // iOS also scrolls the visual viewport within the layout viewport; a fixed element is
    // positioned against the LATTER, so the offset has to come through too.
    act(() => {
      viewport.offsetTop = 90
      viewport.dispatchEvent(new Event('scroll'))
    })
    expect(root.style.getPropertyValue('--visual-viewport-top')).toBe('90px')

    act(() => {
      viewport.height = 844
      viewport.offsetTop = 0
      viewport.dispatchEvent(new Event('resize'))
    })
    expect(root.style.getPropertyValue('--visual-viewport-height')).toBe('844px')

    unmount()
    expect(root.style.getPropertyValue('--visual-viewport-height')).toBe('')
  })

  // The point of the whole design: it reports, it never decides. No thresholds, no timers,
  // no "is the keyboard open" — that inference is what failed six times and got deleted.
  it('carries no threshold or timer — it reports numbers and nothing else', () => {
    const src = readFileSync(resolve(__dirname, 'useVisualViewportVars.ts'), 'utf8')
    const code = src.replace(/\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '')

    expect(code).not.toMatch(/setTimeout|setInterval/)
    // No bare pixel/millisecond constants to be wrong about.
    expect(code).not.toMatch(/\b\d{2,}\b/)
  })

  it('is inert where the browser has no visualViewport', () => {
    Object.defineProperty(window, 'visualViewport', { configurable: true, value: undefined })

    renderHook(() => useVisualViewportVars())

    // CSS keeps its own fallbacks (100dvh / 0px) rather than being handed a guess.
    expect(document.documentElement.style.getPropertyValue('--visual-viewport-height')).toBe('')
  })
})
