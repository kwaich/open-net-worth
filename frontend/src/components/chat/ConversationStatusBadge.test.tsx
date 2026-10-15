// @vitest-environment jsdom
//
// ConversationStatusBadge — pinning "not colour alone" (acceptance E5): needs_you and
// failed are their own SHAPES (Bell / AlertCircle), not a coloured variant of the dot
// working/done share.

import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { ConversationStatusBadge, conversationStatusLabel } from './ConversationStatusBadge'

afterEach(cleanup)

describe('ConversationStatusBadge', () => {
  it('renders nothing for idle — absence of a marker IS the idle state', () => {
    const { container } = render(<ConversationStatusBadge status="idle" />)
    expect(container.firstChild).toBeNull()
  })

  it('needs_you renders a Bell glyph, not a dot', () => {
    const { container } = render(<ConversationStatusBadge status="needs_you" />)
    expect(container.querySelector('.conv-row-status--needs-you')).not.toBeNull()
    expect(container.querySelector('.conv-row-status-icon')).not.toBeNull()
    expect(container.querySelector('.conv-row-status-dot')).toBeNull()
  })

  it('failed renders an AlertCircle glyph, not a dot', () => {
    const { container } = render(<ConversationStatusBadge status="failed" />)
    expect(container.querySelector('.conv-row-status--failed')).not.toBeNull()
    expect(container.querySelector('.conv-row-status-icon')).not.toBeNull()
    expect(container.querySelector('.conv-row-status-dot')).toBeNull()
  })

  it('working and done both render a dot, in different variant classes', () => {
    const working = render(<ConversationStatusBadge status="working" />)
    expect(working.container.querySelector('.conv-row-status-dot')).not.toBeNull()
    expect(working.container.querySelector('.conv-row-status--working')).not.toBeNull()
    cleanup()

    const done = render(<ConversationStatusBadge status="done" />)
    expect(done.container.querySelector('.conv-row-status-dot')).not.toBeNull()
    expect(done.container.querySelector('.conv-row-status--done')).not.toBeNull()
  })

  it('the badge itself is decorative — aria-hidden, since the visible word is real text elsewhere', () => {
    const { container } = render(<ConversationStatusBadge status="needs_you" />)
    expect(container.querySelector('.conv-row-status')?.getAttribute('aria-hidden')).toBe('true')
  })
})

describe('conversationStatusLabel', () => {
  it('reuses the exact "Needs you" vocabulary already established by AutomationHistory', () => {
    expect(conversationStatusLabel('needs_you')).toBe('Needs you')
  })

  it('idle has no label — the row falls back to its timestamp', () => {
    expect(conversationStatusLabel('idle')).toBeNull()
  })

  it('every non-idle status has a distinct word', () => {
    const labels = (['needs_you', 'working', 'done', 'failed'] as const).map(
      conversationStatusLabel,
    )
    expect(new Set(labels).size).toBe(4)
  })
})
