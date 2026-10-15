// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { FeatureIntro } from './FeatureIntro'
import { markGuided, resetGuidedForTests, tourShowing } from './tourTarget'

afterEach(() => {
  cleanup()
  resetGuidedForTests()
})

it('an intro waits while a tour or tip is on screen, then opens', () => {
  const release = tourShowing()
  render(
    <FeatureIntro
      open
      steps={[{ title: 'Hello', points: [] }]}
      lucius={{ label: 'Set up with Lucius', onClick: vi.fn() }}
      secondary={{ label: 'Got it', onClick: vi.fn() }}
      onClose={vi.fn()}
    />,
  )
  expect(screen.queryByRole('dialog')).toBeNull()
  act(() => release())
  expect(screen.getByRole('dialog', { name: 'Hello' })).toBeTruthy()
})

const intro = (onClose = vi.fn()) => (
  <FeatureIntro
    open
    steps={[{ title: 'Hello', points: [] }]}
    lucius={{ label: 'Set up with Lucius', onClick: vi.fn() }}
    secondary={{ label: 'Got it', onClick: vi.fn() }}
    onClose={onClose}
  />
)

it('an intro waits for a later visit once another guide has shown in this one', () => {
  markGuided() // e.g. the paperclip guide Lucius asked for during onboarding
  const onClose = vi.fn()
  render(intro(onClose))
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(onClose).not.toHaveBeenCalled() // still unseen, so it comes back next visit
})

it('the intro that took the visit stays open', () => {
  const { rerender } = render(intro())
  expect(screen.getByRole('dialog', { name: 'Hello' })).toBeTruthy()
  rerender(intro())
  expect(screen.getByRole('dialog', { name: 'Hello' })).toBeTruthy()
})
