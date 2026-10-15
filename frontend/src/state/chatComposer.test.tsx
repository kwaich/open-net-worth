// @vitest-environment jsdom
import { describe, it, expect } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { ChatComposerProvider, useChatComposer } from './chatComposer'
import type { MentionRef } from '../types/mentions'

const wrapper = ({ children }: { children: ReactNode }) => (
  <ChatComposerProvider>{children}</ChatComposerProvider>
)

const nw: MentionRef = { type: 'net_worth', ref: '', label: 'Net worth' }
const aapl: MentionRef = { type: 'holding', ref: 'pos_123', label: 'AAPL holding' }

describe('chatComposer store', () => {
  it('adds a mention', () => {
    const { result } = renderHook(() => useChatComposer(), { wrapper })
    act(() => result.current.addMention(nw))
    expect(result.current.pendingMentions).toEqual([nw])
  })

  it('dedups by (type, ref)', () => {
    const { result } = renderHook(() => useChatComposer(), { wrapper })
    act(() => result.current.addMention(nw))
    act(() => result.current.addMention({ ...nw, label: 'Net worth (again)' }))
    expect(result.current.pendingMentions).toHaveLength(1)
  })

  it('removes by index', () => {
    const { result } = renderHook(() => useChatComposer(), { wrapper })
    act(() => result.current.addMention(nw))
    act(() => result.current.addMention(aapl))
    act(() => result.current.removeMention(0))
    expect(result.current.pendingMentions).toEqual([aapl])
  })

  it('clears all', () => {
    const { result } = renderHook(() => useChatComposer(), { wrapper })
    act(() => result.current.addMention(nw))
    act(() => result.current.addMention(aapl))
    act(() => result.current.clearMentions())
    expect(result.current.pendingMentions).toEqual([])
  })

  it('addMention fires requestOpen for subscribers (default open:true)', () => {
    const { result } = renderHook(() => useChatComposer(), { wrapper })
    let opened = 0
    act(() => {
      result.current.subscribeOpen(() => {
        opened += 1
      })
    })
    act(() => result.current.addMention(nw))
    expect(opened).toBe(1)
  })

  it('addMention with { open: false } does NOT fire requestOpen', () => {
    const { result } = renderHook(() => useChatComposer(), { wrapper })
    let opened = 0
    act(() => {
      result.current.subscribeOpen(() => {
        opened += 1
      })
    })
    act(() => result.current.addMention(nw, { open: false }))
    expect(opened).toBe(0)
    // The mention is still staged.
    expect(result.current.pendingMentions).toEqual([nw])
  })

  it('addMention with { open: true } fires requestOpen', () => {
    const { result } = renderHook(() => useChatComposer(), { wrapper })
    let opened = 0
    act(() => {
      result.current.subscribeOpen(() => {
        opened += 1
      })
    })
    act(() => result.current.addMention(nw, { open: true }))
    expect(opened).toBe(1)
  })

  it('subscribeOpen returns an unsubscribe', () => {
    const { result } = renderHook(() => useChatComposer(), { wrapper })
    let opened = 0
    let unsub: () => void = () => {}
    act(() => {
      unsub = result.current.subscribeOpen(() => {
        opened += 1
      })
    })
    act(() => unsub())
    act(() => result.current.addMention(nw))
    expect(opened).toBe(0)
  })

  // ── P3-UI: respondingAgent ────────────────────────────────────────────────

  it('setRespondingAgent stores the agent (single-select)', () => {
    const { result } = renderHook(() => useChatComposer(), { wrapper })
    act(() => result.current.setRespondingAgent({ value: 'lucius', label: 'Lucius' }))
    expect(result.current.respondingAgent).toEqual({ value: 'lucius', label: 'Lucius' })
    // Replacing with another agent overwrites (single-select).
    act(() => result.current.setRespondingAgent({ value: 'agent-1', label: 'My Analyst' }))
    expect(result.current.respondingAgent).toEqual({ value: 'agent-1', label: 'My Analyst' })
  })

  it('setRespondingAgent(null) clears the agent', () => {
    const { result } = renderHook(() => useChatComposer(), { wrapper })
    act(() => result.current.setRespondingAgent({ value: 'lucius', label: 'Lucius' }))
    act(() => result.current.setRespondingAgent(null))
    expect(result.current.respondingAgent).toBeNull()
  })

  it('clearMentions also resets respondingAgent to null', () => {
    const { result } = renderHook(() => useChatComposer(), { wrapper })
    act(() => result.current.addMention(nw))
    act(() => result.current.setRespondingAgent({ value: 'agent-1', label: 'My Analyst' }))
    act(() => result.current.clearMentions())
    expect(result.current.pendingMentions).toEqual([])
    expect(result.current.respondingAgent).toBeNull()
  })

  it('initial respondingAgent is null', () => {
    const { result } = renderHook(() => useChatComposer(), { wrapper })
    expect(result.current.respondingAgent).toBeNull()
  })

  // ── composerDraft (shared draft persistence) ──────────────────────────────

  it('composerDraft starts empty', () => {
    const { result } = renderHook(() => useChatComposer(), { wrapper })
    expect(result.current.composerDraft).toEqual({ text: '', mentions: [] })
  })

  it('setComposerDraft stores text and mentions', () => {
    const { result } = renderHook(() => useChatComposer(), { wrapper })
    act(() => {
      result.current.setComposerDraft({
        text: 'hello',
        mentions: [{ type: 'account', ref: 'acc-1', label: 'Meridian Bank' }],
      })
    })
    expect(result.current.composerDraft.text).toBe('hello')
    expect(result.current.composerDraft.mentions).toHaveLength(1)
    expect(result.current.composerDraft.mentions[0]!.label).toBe('Meridian Bank')
  })

  it('clearComposerDraft resets to empty', () => {
    const { result } = renderHook(() => useChatComposer(), { wrapper })
    act(() => {
      result.current.setComposerDraft({
        text: 'some draft',
        mentions: [nw],
      })
    })
    act(() => result.current.clearComposerDraft())
    expect(result.current.composerDraft).toEqual({ text: '', mentions: [] })
  })

  it('setComposerDraft replaces previous draft (last-write wins)', () => {
    const { result } = renderHook(() => useChatComposer(), { wrapper })
    act(() => result.current.setComposerDraft({ text: 'first', mentions: [nw] }))
    act(() => result.current.setComposerDraft({ text: 'second', mentions: [aapl] }))
    expect(result.current.composerDraft.text).toBe('second')
    expect(result.current.composerDraft.mentions[0]!.label).toBe('AAPL holding')
  })
})
