// @vitest-environment jsdom

import { describe, expect, it } from 'vitest'
import { stickyTopbarOffset } from './stickyTopbarOffset'

describe('stickyTopbarOffset', () => {
  it('keeps document-sticky tablet content below the rendered shell bar', () => {
    const topbar = document.createElement('div')
    Object.defineProperty(topbar, 'offsetHeight', { value: 76 })

    expect(stickyTopbarOffset(topbar, 1024)).toBe(76)
  })

  it('uses zero inside the phone app-layout scroller', () => {
    const topbar = document.createElement('div')
    Object.defineProperty(topbar, 'offsetHeight', { value: 52 })

    expect(stickyTopbarOffset(topbar, 639)).toBe(0)
  })
})
