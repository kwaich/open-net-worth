// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MessageBubble } from './MessageBubble'

afterEach(cleanup)

describe('provider error recovery', () => {
  it.each([false, true])('offers AI settings for setupHint=%s', (setupHint) => {
    const openSettings = vi.fn()
    render(
      <MessageBubble
        message={{
          id: 'error',
          role: 'assistant',
          text: '',
          error: 'Codex CLI not found',
          setupHint,
        }}
        onSendMessage={vi.fn()}
        onOpenAiSettings={openSettings}
      />,
    )

    expect(screen.getByRole('alert').textContent).toContain('Codex CLI not found')
    fireEvent.click(screen.getByRole('button', { name: 'Open AI settings' }))
    expect(openSettings).toHaveBeenCalledOnce()
  })
})
