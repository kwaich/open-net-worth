// @vitest-environment jsdom
//
// A stray fence must never swallow the reply that follows it.
//
// Persona QA 2026-10-14: a streamed answer drew as an EMPTY code box plus ONE monospace line,
// cut at the panel edge. The text block was "```You have S$788 …\n\nThose are the only …" — a
// fence glued onto the first paragraph, which markdown reads as the fence's info string, so
// that paragraph disappeared and the second became code. The backend no longer streams the
// fence; this keeps any text block that carries one (an older transcript, another transport)
// from drawing it.

import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Message, MessageBlock } from '../../types/chat'
import { MessageBubble } from './MessageBubble'

afterEach(cleanup)
vi.mock('../../utils/chatStore', () => ({ fetchToolDetail: vi.fn() }))

const FIRST = 'You have S$788 left across the two, Tom: S$500 in Groceries and S$288 in Dining out.'
const SECOND = 'Those are the only amounts envelope-funded for food and fun.'

function bubble(blocks: MessageBlock[], streaming = false) {
  const message: Message = { id: 'sess-1', role: 'assistant', text: '', blocks, streaming }
  return render(<MessageBubble message={message} onSendMessage={vi.fn()} />)
}

describe('MessageBubble stray fences', () => {
  it.each([
    ['glued onto the first paragraph', `\`\`\`${FIRST}\n\n${SECOND}`],
    ['opening the message, never closed', `\`\`\`\n\n${FIRST}\n\n${SECOND}`],
    ['after a blank line', `\n\`\`\`${FIRST}\n\n${SECOND}`],
  ])('draws both paragraphs as prose when a fence is %s', (_name, content) => {
    for (const streaming of [true, false]) {
      const { container, unmount } = bubble([{ type: 'text', content }], streaming)
      expect(container.querySelector('pre')).toBeNull()
      const paragraphs = [...container.querySelectorAll('.msg-text p')].map((p) => p.textContent)
      expect(paragraphs).toEqual([FIRST, SECOND])
      unmount()
    }
  })

  it('drops a fence the message ends on', () => {
    const { container } = bubble([{ type: 'text', content: `${FIRST}\n\`\`\`` }])
    expect(container.querySelector('pre')).toBeNull()
    expect(container.querySelector('.msg-text')?.textContent).toBe(FIRST)
  })

  it('still draws code that is streaming in', () => {
    const { container } = bubble(
      [{ type: 'text', content: 'Run this:\n\n```python\nprint(1)' }],
      true,
    )
    expect(container.querySelector('pre code')?.textContent).toContain('print(1)')
  })

  it('still draws a closed code block between paragraphs', () => {
    const { container } = bubble([
      { type: 'text', content: `${FIRST}\n\n\`\`\`\nls -la\n\`\`\`\n\n${SECOND}` },
    ])
    expect(container.querySelector('pre')?.textContent).toContain('ls -la')
    expect(container.querySelectorAll('.msg-text p')).toHaveLength(2)
  })
})
