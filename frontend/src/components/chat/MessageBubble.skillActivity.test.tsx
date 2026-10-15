// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Message } from '../../types/chat'
import { MessageBubble } from './MessageBubble'

afterEach(cleanup)

const noop = vi.fn()

function renderSkill(status: 'loading' | 'loaded' | 'failed', error?: string) {
  const message: Message = {
    id: 'a1',
    role: 'assistant',
    text: '',
    blocks: [
      {
        type: 'skill',
        id: 'skill:0:user:account-balance-tracer',
        skillId: 'account-balance-tracer',
        name: 'account-balance-tracer',
        status,
        source: 'user',
        ...(error ? { error } : {}),
      },
    ],
  }
  return render(<MessageBubble message={message} onSendMessage={noop} />)
}

describe('skill activity row', () => {
  it('shows loading and loaded states as activity, not a generic tool pill', () => {
    const view = renderSkill('loading')
    expect(screen.getByText('Loading account-balance-tracer skill')).toBeTruthy()
    expect(view.container.querySelector('.msg-skill-activity')).not.toBeNull()
    expect(view.container.querySelector('.msg-action-pill--tool')).toBeNull()

    view.unmount()
    renderSkill('loaded')
    expect(screen.getByText('Loaded account-balance-tracer skill')).toBeTruthy()
  })

  it('shows the failure reason without claiming the skill loaded', () => {
    renderSkill('failed', 'Skill is disabled.')
    expect(screen.getByText('Could not load account-balance-tracer skill')).toBeTruthy()
    expect(screen.getByText('Skill is disabled.')).toBeTruthy()
  })
})
