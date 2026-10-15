// @vitest-environment jsdom
import { renderHook } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'

beforeEach(() => {
  sessionStorage.clear()
  vi.resetModules()
})

it('a draft saved before a gateway re-sign-in is back in the composer afterwards', async () => {
  sessionStorage.setItem(
    'onw-reauth-drafts',
    JSON.stringify({
      at: Date.now(),
      drafts: { composer: { text: 'what did I spend on food', mentions: [] } },
    }),
  )
  const { ChatComposerProvider, useChatComposer } = await import('./chatComposer')

  const { result } = renderHook(() => useChatComposer(), { wrapper: ChatComposerProvider })

  expect(result.current.composerDraft.text).toBe('what did I spend on food')
  expect(result.current.composerDraftRef.current.text).toBe('what did I spend on food')
})

it('starts empty when nothing was saved', async () => {
  const { ChatComposerProvider, useChatComposer } = await import('./chatComposer')
  const { result } = renderHook(() => useChatComposer(), { wrapper: ChatComposerProvider })
  expect(result.current.composerDraft).toEqual({ text: '', mentions: [] })
})
