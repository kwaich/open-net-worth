// @vitest-environment jsdom
//
// One confirm card on screen at a time.
//
// A single provider round can gate several tool calls, and every one stages its own card
// (ADR `2026-08-25-one-confirm-mechanism-for-every-tool`). Rendering all of them at once put
// a stack of decisions on screen and then moved nothing until the last was tapped — the turn
// genuinely cannot resume until every sibling tool_use has a result
// (`assistant.resolve_pending_tool_use`), so the first taps look like they did nothing.
//
// This is a RENDERING rule and nothing else: every card is already in the message, so hiding
// one loses nothing and un-hiding is free. The tests that matter are the ones proving nothing
// is ever stranded — an answered card keeps its verdict, and the queue always advances.

import { describe, it, expect, vi, afterEach } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { MessageBubble } from './MessageBubble'
import type { Message, ProposedChange } from '../../types/chat'

afterEach(cleanup)

const noop = vi.fn()

function card(id: string, over: Partial<ProposedChange> = {}): ProposedChange {
  return {
    type: 'remove',
    grant_tool: 'remove_positions',
    id,
    session_id: 'sess-1',
    display: { symbol: `SYM${id}`, name: `Holding ${id}` },
    ...over,
  }
}

function renderCards(changes: ProposedChange[]) {
  const message: Message = {
    id: 'm1',
    role: 'assistant',
    text: '',
    blocks: changes.map((change) => ({ type: 'change' as const, change })),
    proposedChanges: changes,
  }
  return render(<MessageBubble message={message} onSendMessage={noop} />)
}

const cards = (c: HTMLElement) => c.querySelectorAll('.change-preview')

describe('MessageBubble — one confirm card at a time', () => {
  it('shows only the first card when three are staged in one round', () => {
    const { container } = renderCards([card('a'), card('b'), card('c')])
    expect(cards(container)).toHaveLength(1)
    expect(container.textContent).toContain('Holding a')
    expect(container.textContent).not.toContain('Holding b')
    expect(container.textContent).not.toContain('Holding c')
  })

  it('advances to the next card once the first is answered', () => {
    const { container } = renderCards([card('a', { status: 'confirmed' }), card('b'), card('c')])
    // The answered one STAYS — it carries the ✓ record of what was agreed to.
    expect(container.textContent).toContain('Holding a')
    expect(container.textContent).toContain('Holding b')
    expect(container.textContent).not.toContain('Holding c')
  })

  it('a DENIED card also advances the queue — a decision is a decision', () => {
    const { container } = renderCards([card('a', { status: 'dismissed' }), card('b')])
    expect(container.textContent).toContain('Holding b')
  })

  it('shows every card once all are answered — nothing is stranded', () => {
    const { container } = renderCards([
      card('a', { status: 'confirmed' }),
      card('b', { status: 'dismissed' }),
      card('c', { status: 'confirmed' }),
    ])
    expect(cards(container)).toHaveLength(3)
  })

  it('a REFUSED approval comes back as the card to answer, not a silent gap', () => {
    // `refused` ⇒ the server could not redeem it, so NOTHING ran. The card must be
    // actionable again — a hidden refusal is a write the reader believes happened.
    const { container } = renderCards([card('a', { status: undefined, refused: true }), card('b')])
    expect(container.textContent).toContain('Holding a')
    expect(container.textContent).not.toContain('Holding b')
  })

  it('leaves a single card exactly as it was', () => {
    const { container } = renderCards([card('solo')])
    expect(cards(container)).toHaveLength(1)
    expect(container.textContent).toContain('Holding solo')
  })

  it('applies the same rule on the legacy no-blocks message shape', () => {
    // An old conversation cached before cards became positioned blocks renders through
    // `m.proposedChanges` instead. Same rule, or a card hides in a new conversation and
    // stays in an old one.
    const message: Message = {
      id: 'm2',
      role: 'assistant',
      text: '',
      proposedChanges: [card('a'), card('b')],
    }
    const { container } = render(<MessageBubble message={message} onSendMessage={noop} />)
    expect(cards(container)).toHaveLength(1)
    expect(container.textContent).toContain('Holding a')
  })
})
