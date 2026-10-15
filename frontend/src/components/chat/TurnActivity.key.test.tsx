// @vitest-environment jsdom
//
// Live incident 2026-08-10: "the timer resets whenever I send a queued message." Root
// cause: the caller (ChatPanel.tsx) keyed <TurnActivity key={messages.length} .../> —
// and useChat.sendMessage optimistically appends a QUEUED message to that array the
// instant it's sent (before the server has done anything with it), bumping
// messages.length and remounting the clock for a turn that hasn't advanced at all.
//
// Fix: key off the count of ASSISTANT messages instead — the only thing that should
// mean "a new step really started" (a queued USER message never changes that count).
// This file exercises the exact key EXPRESSION ChatPanel.tsx uses, not the component
// itself, since TurnActivity has no `messages` prop — the caller computes the key.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, cleanup, act } from '@testing-library/react'
import { TurnActivity } from './TurnActivity'
import type { Message } from '../../types/chat'

function assistantMsg(id: string): Message {
  return {
    id,
    role: 'assistant',
    text: 'hi',
    streaming: false,
    toolCalls: [],
    blocks: [],
    proposedChanges: [],
  }
}
function userMsg(id: string, pending?: 'queued'): Message {
  return { id, role: 'user', text: 'hello', ...(pending ? { pending } : {}) }
}

// Mirrors ChatPanel.tsx's actual JSX exactly.
function Harness({ messages }: { messages: Message[] }) {
  return <TurnActivity key={messages.filter((m) => m.role === 'assistant').length} />
}

function elapsedText(container: HTMLElement): string {
  return container.querySelector('.lx-activity-meta')?.textContent ?? ''
}

beforeEach(() => {
  vi.useFakeTimers()
})
afterEach(() => {
  vi.useRealTimers()
  cleanup()
})

describe('TurnActivity remount key (ChatPanel.tsx)', () => {
  it('a queued USER message appended mid-turn does NOT reset the clock', () => {
    const messages: Message[] = [userMsg('u1'), assistantMsg('a1')]
    const { container, rerender } = render(<Harness messages={messages} />)

    act(() => {
      vi.advanceTimersByTime(5000)
    })
    expect(elapsedText(container)).toContain('5s')

    // Exactly what useChat.sendMessage does when queuing into a busy turn:
    // optimistically append a new user message to the SAME array.
    const withQueued = [...messages, userMsg('u2', 'queued')]
    rerender(<Harness messages={withQueued} />)

    // No remount → the interval keeps running, elapsed keeps counting from the
    // ORIGINAL start — never resets to 0/1s.
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(elapsedText(container)).toContain('6s')
    expect(elapsedText(container)).not.toContain('(0s')
    expect(elapsedText(container)).not.toContain('(1s')
  })

  it('a genuinely new assistant reply starting DOES reset the clock', () => {
    const messages: Message[] = [userMsg('u1'), assistantMsg('a1')]
    const { container, rerender } = render(<Harness messages={messages} />)

    act(() => {
      vi.advanceTimersByTime(5000)
    })
    expect(elapsedText(container)).toContain('5s')

    // A genuinely new turn: a fresh user message AND a fresh assistant bubble.
    const nextTurn = [...messages, userMsg('u2'), assistantMsg('a2')]
    rerender(<Harness messages={nextTurn} />)

    // Remounted (assistant count 1 → 2) → the clock is back to counting from zero.
    expect(elapsedText(container)).toContain('0s')
  })
})
