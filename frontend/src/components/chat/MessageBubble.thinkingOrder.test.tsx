// @vitest-environment jsdom
//
// Reasoning renders WHERE IT HAPPENED.
//
// A turn that thinks, calls a tool, then thinks again used to draw both thoughts merged into
// one block above the first tool row — including the half that came after it. The ordering
// was never lost, only discarded: each provider round appends its own thinking block to the
// transcript, interleaved with that round's tool calls (`assistant.py`, where
// `assistant_thinking` resets per round).

import { cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Message, MessageBlock } from '../../types/chat'
import { MessageBubble } from './MessageBubble'

afterEach(cleanup)
const noop = vi.fn()

vi.mock('../../utils/chatStore', () => ({ fetchToolDetail: vi.fn() }))

const grouped = { value: false }
vi.mock('../../hooks/useChatSuggestionsEnabled', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatThinkingGrouped: () => ({ grouped: grouped.value, loaded: true }),
}))

function bubble(blocks: MessageBlock[], thinking?: string, streaming = false) {
  const message: Message = {
    id: 'sess-1',
    role: 'assistant',
    text: '',
    blocks,
    ...(streaming && { streaming: true }),
    ...(thinking !== undefined && { thinking }),
  }
  return render(<MessageBubble message={message} onSendMessage={noop} />)
}

const toolBlock = (id: string): MessageBlock => ({
  type: 'tool',
  name: 'get_transactions',
  id,
  status: 'done',
  display: {
    label: 'Get transactions',
    verb: 'read',
    noun: 'transactions',
    owner_id: 'core',
    owner_name: '',
    detail: '',
  },
  input: {},
  result: '{"ok":true}',
})

/** Reading order of the things that carry position, top to bottom. */
function order(container: HTMLElement): string[] {
  // Every step — a call or a thought — is a row on the same rail now, so reading order is
  // just document order.
  const nodes = container.querySelectorAll('.msg-toolact__row')
  return [...nodes].map((n) => (n.classList.contains('msg-toolact__row--think') ? 'think' : 'tool'))
}

describe('thinking renders in position', () => {
  it('keeps a second thought AFTER the tool call it followed', () => {
    const { container } = bubble([
      { type: 'thinking', content: 'First I should look at the ledger.' },
      toolBlock('t1'),
      { type: 'thinking', content: 'That was empty, so I will say so.' },
    ])
    expect(order(container)).toEqual(['think', 'tool', 'think'])
  })

  it('renders each round as its own block, not one merged blob', () => {
    const { container } = bubble([
      { type: 'thinking', content: 'AAA' },
      toolBlock('t1'),
      { type: 'thinking', content: 'BBB' },
    ])
    const thoughts = container.querySelectorAll('.msg-toolact__row--think')
    expect(thoughts).toHaveLength(2)
    expect(thoughts[0]?.textContent).toContain('AAA')
    expect(thoughts[1]?.textContent).toContain('BBB')
  })

  it('does not ALSO draw the merged message-level field above the bubble', () => {
    // `thinking` stays populated for older readers, so the fallback has to be suppressed
    // whenever blocks carry the same reasoning — otherwise every thought renders twice.
    const { container } = bubble(
      [{ type: 'thinking', content: 'AAA' }, toolBlock('t1'), { type: 'thinking', content: 'BBB' }],
      'AAABBB',
    )
    expect(container.querySelectorAll('.msg-toolact__row--think')).toHaveLength(2)
    expect(container.textContent?.match(/AAA/g)).toHaveLength(1)
  })

  it('merges into one block above the turn when the reader asked for that', () => {
    // Settings -> AI -> Chat, default OFF. Presentation only: the blocks still carry the
    // rounds, this just chooses not to draw them separately.
    grouped.value = true
    try {
      const { container } = bubble(
        [
          { type: 'thinking', content: 'AAA' },
          toolBlock('t1'),
          { type: 'thinking', content: 'BBB' },
        ],
        'AAABBB',
      )
      expect(container.querySelectorAll('.msg-toolact__row--think')).toHaveLength(1)
      expect(order(container)).toEqual(['think', 'tool'])
    } finally {
      grouped.value = false
    }
  })

  it('falls back to the merged field for a transcript with no thinking blocks', () => {
    // Recorded before thinking became positioned: one string is all there is, so it can only
    // be drawn above everything.
    const { container } = bubble([toolBlock('t1')], 'a thought from an older transcript')
    expect(container.querySelectorAll('.msg-toolact__row--think')).toHaveLength(1)
    expect(order(container)).toEqual(['think', 'tool'])
  })
})

describe('a thought still being written stays open', () => {
  const two = (): MessageBlock[] => [
    { type: 'thinking', content: 'Finished thought.' },
    toolBlock('t1'),
    { type: 'thinking', content: 'Still being written, and quite long.' },
  ]

  it('opens the LAST thought while the turn streams, and only that one', () => {
    // Reasoning arriving two clamped lines at a time is unreadable and reads as broken —
    // the text visibly changes under a preview that never grows (owner, 2026-08-23).
    const { container } = bubble(two(), undefined, true)
    const rows = container.querySelectorAll('.msg-toolact__row--think')
    expect(rows).toHaveLength(2)
    expect(rows[0]?.className).not.toContain('msg-toolact__row--open')
    expect(rows[1]?.className).toContain('msg-toolact__row--open')
  })

  it('folds once the turn is done — finished reasoning is history', () => {
    const { container } = bubble(two(), undefined, false)
    const open = container.querySelectorAll('.msg-toolact__row--think.msg-toolact__row--open')
    expect(open).toHaveLength(0)
  })

  it('a click wins over both, including folding it mid-stream', () => {
    const { container } = bubble(two(), undefined, true)
    const heads = container.querySelectorAll('.msg-toolact__row--think .msg-toolact__head')
    fireEvent.click(heads[1] as HTMLElement)
    const rows = container.querySelectorAll('.msg-toolact__row--think')
    expect(rows[1]?.className).not.toContain('msg-toolact__row--open')
  })
})
