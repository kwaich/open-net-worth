// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { LuciusCta } from './LuciusCta'
import { takeSendIntent } from '../../state/luciusSendIntent'

afterEach(() => {
  cleanup()
  takeSendIntent()
})

it('asks Lucius with its message and flags the prefill to be sent', () => {
  const onAskLucius = vi.fn()
  render(
    <LuciusCta label="Add with Lucius" message="Help me add things." onAskLucius={onAskLucius} />,
  )

  const button = screen.getByRole('button', { name: 'Add with Lucius' })
  expect(button.className).toContain('fi-lucius')
  expect(button.querySelector('svg')).not.toBeNull()

  fireEvent.click(button)

  expect(onAskLucius).toHaveBeenCalledWith('Help me add things.')
  expect(takeSendIntent()).toBe(true)
})

it('defaults the label to "Ask Lucius"', () => {
  render(<LuciusCta message="Hi." onAskLucius={vi.fn()} />)
  expect(screen.getByRole('button', { name: 'Ask Lucius' })).toBeTruthy()
})

it('renders nothing where there is no chat to hand off to', () => {
  const { container } = render(<LuciusCta message="Hi." />)
  expect(container.firstChild).toBeNull()
})
