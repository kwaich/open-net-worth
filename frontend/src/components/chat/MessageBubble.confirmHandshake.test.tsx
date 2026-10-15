// @vitest-environment jsdom
//
// Regression test for the owner-reported bug: tapping Confirm on a ledger-write card
// echoed the raw internal handshake — tool name + confirm_token — into the user's own
// chat bubble ("Confirmed — apply the update to the transaction (call update_transaction
// with confirm_token \"...\")" ×7 in one bubble). The fix strips this at RENDER only;
// what ChangePreview actually SENDS to the model is untouched (see ChangePreview.txn.test.tsx).

import { describe, it, expect, vi, afterEach } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { MessageBubble } from './MessageBubble'
import type { Message } from '../../types/chat'

afterEach(cleanup)

const noop = vi.fn()

function renderUserMessage(text: string) {
  const message: Message = { id: 'msg-1', role: 'user', text, streaming: false }
  const { container } = render(<MessageBubble message={message} onSendMessage={noop} />)
  return container
}

describe('MessageBubble — confirm-handshake bubble', () => {
  it('renders a single confirmed card tap as a compact chip, never the tool name or token', () => {
    const container = renderUserMessage(
      'Confirmed — apply the update to the transaction (call update_transaction with confirm_token "xFnfwckbwSc-PL1NyINtsX2s89kRdnZ-")',
    )
    const text = container.querySelector('.msg-text')?.textContent ?? ''
    expect(text).toBe('✓ Confirmed')
    expect(text).not.toContain('update_transaction')
    expect(text).not.toContain('confirm_token')
    expect(text).not.toContain('xFnfwckbwSc')
  })

  it('collapses seven buffered confirms (one bubble, newline-joined) into one compact line', () => {
    const sevenLines = Array(7)
      .fill(0)
      .map(
        (_, i) =>
          `Confirmed — apply the update to the transaction (call update_transaction with confirm_token "tok_${i}")`,
      )
      .join('\n')
    const container = renderUserMessage(sevenLines)
    const text = container.querySelector('.msg-text')?.textContent ?? ''
    expect(text).toBe('✓ Confirmed ×7')
    expect(text).not.toContain('tok_')
  })

  it('leaves an ordinary typed user message alone', () => {
    const container = renderUserMessage('Can you add a $50 Grab expense from today?')
    const text = container.querySelector('.msg-text')?.textContent ?? ''
    expect(text).toBe('Can you add a $50 Grab expense from today?')
  })
})
