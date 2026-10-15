// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useStoryFocus } from './useStoryFocus'

function card(): HTMLElement {
  const el = document.createElement('div')
  document.body.appendChild(el)
  return el
}

beforeEach(() => {
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
    cb(0)
    return 0
  })
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo
})
afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('useStoryFocus', () => {
  it('scrolls to a card already on the page', () => {
    const { result } = renderHook(() => useStoryFocus())
    result.current.registerRef('s1', card())
    result.current.scrollToStory('s1')
    expect(window.scrollTo).toHaveBeenCalledTimes(1)
  })

  it('waits for a card that is not mounted yet, and scrolls once it is', () => {
    const { result } = renderHook(() => useStoryFocus())
    result.current.scrollToStory('s2')
    expect(window.scrollTo).not.toHaveBeenCalled()
    result.current.registerRef('other', card())
    expect(window.scrollTo).not.toHaveBeenCalled()
    result.current.registerRef('s2', card())
    expect(window.scrollTo).toHaveBeenCalledTimes(1)
    // A later re-render re-registering the same card doesn't scroll again.
    result.current.registerRef('s2', card())
    expect(window.scrollTo).toHaveBeenCalledTimes(1)
  })

  it('treats an unmounted card as not on the page', () => {
    const { result } = renderHook(() => useStoryFocus())
    result.current.registerRef('s3', card())
    result.current.registerRef('s3', null)
    result.current.scrollToStory('s3')
    expect(window.scrollTo).not.toHaveBeenCalled()
  })
})
