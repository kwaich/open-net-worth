// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { EmptyHoldings } from './EmptyHoldings'
import { takeSendIntent } from '../../../state/luciusSendIntent'

afterEach(() => {
  cleanup()
  takeSendIntent()
})

it('offers Lucius beside Add holding and invites a screenshot or statement', () => {
  const onAddHolding = vi.fn()
  const onAskLucius = vi.fn()
  render(<EmptyHoldings onAddHolding={onAddHolding} onAskLucius={onAskLucius} />)

  fireEvent.click(screen.getByRole('button', { name: 'Add with Lucius' }))

  expect(onAskLucius).toHaveBeenCalledWith(expect.stringMatching(/screenshot or statement/))
  expect(takeSendIntent()).toBe(true)
  expect(onAddHolding).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: 'Add holding' })).toBeTruthy()
})
