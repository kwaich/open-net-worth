// @vitest-environment jsdom
//
// A cancelled turn used to render NOTHING in the conversation. The reasoning was "the user
// stopped it themselves and already knows, and the run card covers it" — and both halves
// were wrong. A server restart cancels turns identically and nobody pressed anything; and
// the run card is a different surface entirely, so opening the conversation from the
// sidebar, the dock or the Lucius tab showed a wall of tool pills and no explanation
// anywhere (owner, 2026-08-22).
//
// The explanation now rides the same `error` field — the text is the explanation either way
// — with `notice` switching the styling. Red would be wrong: nothing broke.

import { describe, it, expect, afterEach } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MessageBubble } from './MessageBubble'

const noop = () => {}
afterEach(cleanup)

const CANCELLED = {
  id: 'm1',
  role: 'assistant' as const,
  text: '',
  error: 'Did not finish — the server restarted while this turn was working.',
}

describe('MessageBubble — a cancelled turn explains itself', () => {
  it('shows the explanation as a notice, not as an error', () => {
    const { container } = render(
      <MessageBubble message={{ ...CANCELLED, notice: true }} onSendMessage={noop} />,
    )
    expect(screen.getByText(/the server restarted while this turn was working/)).toBeTruthy()
    expect(container.querySelector('.msg-notice')).toBeTruthy()
    expect(container.querySelector('.msg-error')).toBeNull()
  })

  it('leaves a real failure red', () => {
    const { container } = render(
      <MessageBubble message={{ ...CANCELLED, error: 'API Error: 400' }} onSendMessage={noop} />,
    )
    expect(container.querySelector('.msg-error')).toBeTruthy()
    expect(container.querySelector('.msg-notice')).toBeNull()
  })

  it('offers no Retry on a notice — there is nothing that failed to retry', () => {
    const { container } = render(
      <MessageBubble
        message={{ ...CANCELLED, notice: true }}
        onSendMessage={noop}
        onRetry={noop}
      />,
    )
    expect(container.querySelector('.msg-retry-btn')).toBeNull()
  })
})
