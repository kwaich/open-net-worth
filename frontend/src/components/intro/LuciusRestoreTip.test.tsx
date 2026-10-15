// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LuciusRestoreTip } from './LuciusTours'
import { resetGuidedForTests } from './tourTarget'

const seen = vi.hoisted(() => ({ unseen: true, markSeen: vi.fn() }))
vi.mock('./useSeenIntro', () => ({ useSeenIntro: () => seen }))

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  resetGuidedForTests()
  cleanup()
  vi.useRealTimers()
  seen.unseen = true
  seen.markSeen.mockClear()
})

function restoreButton() {
  return (
    <button
      data-tour="lucius-restore"
      ref={(el) => {
        if (el)
          el.getBoundingClientRect = () =>
            ({ top: 300, bottom: 320, left: 600, right: 680, width: 80, height: 20 }) as DOMRect
      }}
    >
      Restore
    </button>
  )
}

it("points at Lucius's first Restore and is remembered once dismissed", async () => {
  render(
    <>
      {restoreButton()}
      <LuciusRestoreTip />
    </>,
  )
  await act(async () => vi.advanceTimersByTime(800))
  await act(async () => vi.advanceTimersByTime(50))
  expect(screen.getByRole('dialog', { name: 'Undo anything Lucius changes' })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Done' }))
  expect(seen.markSeen).toHaveBeenCalledOnce()
})

it('waits while there is nothing to undo yet', async () => {
  render(<LuciusRestoreTip />)
  await act(async () => vi.advanceTimersByTime(3000))
  expect(screen.queryByRole('dialog')).toBeNull()
})

it('never shows again once seen', async () => {
  seen.unseen = false
  render(
    <>
      {restoreButton()}
      <LuciusRestoreTip />
    </>,
  )
  await act(async () => vi.advanceTimersByTime(3000))
  expect(screen.queryByRole('dialog')).toBeNull()
})

it('waits for a later visit when another guide has already shown in this one', async () => {
  const { markGuided } = await import('./tourTarget')
  markGuided() // the paperclip guide Lucius asked for took this visit
  render(
    <>
      {restoreButton()}
      <LuciusRestoreTip />
    </>,
  )
  await act(async () => vi.advanceTimersByTime(3000))
  expect(screen.queryByRole('dialog', { name: 'Undo anything Lucius changes' })).toBeNull()
  expect(seen.markSeen).not.toHaveBeenCalled() // still unseen: it shows on a later visit
})
