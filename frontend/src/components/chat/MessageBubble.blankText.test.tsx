// @vitest-environment jsdom
//
// A text block that is only whitespace or a stray markdown fence is not a message.
//
// Live 2026-09-30: a CLI-transport round wrapped its tool call in a fence and saved the
// opening one as its text — `"\n\n\n```"` — which ReactMarkdown drew as an empty code box
// between two tool rows. The backend now drops that fence; this keeps an older transcript
// (or any other path that leaks one) from drawing the box.

import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Message, MessageBlock } from '../../types/chat'
import { MessageBubble } from './MessageBubble'

afterEach(cleanup)
vi.mock('../../utils/chatStore', () => ({ fetchToolDetail: vi.fn() }))

function bubble(blocks: MessageBlock[]) {
  const message: Message = { id: 'sess-1', role: 'assistant', text: '', blocks }
  return render(<MessageBubble message={message} onSendMessage={vi.fn()} />)
}

describe('MessageBubble blank text blocks', () => {
  it.each(['\n\n\n```', '```xml\n', '  \n ', '```\n\n```'])('draws nothing for %j', (content) => {
    const { container } = bubble([{ type: 'text', content }])
    expect(container.querySelector('.msg-text')).toBeNull()
    expect(container.querySelector('pre')).toBeNull()
  })

  it('still draws a real code block', () => {
    const { container } = bubble([{ type: 'text', content: '```\nprint(1)\n```' }])
    expect(container.querySelector('pre')?.textContent).toContain('print(1)')
  })
})
