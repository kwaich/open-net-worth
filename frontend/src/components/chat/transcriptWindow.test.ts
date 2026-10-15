import { describe, expect, it } from 'vitest'

import { TRANSCRIPT_WINDOW_INITIAL, windowedTranscript } from './transcriptWindow'

const msgs = (n: number) => Array.from({ length: n }, (_, i) => `m${i}`)

describe('windowedTranscript', () => {
  it('returns everything (same array) when the transcript fits the window', () => {
    const list = msgs(10)
    const { visible, hiddenCount } = windowedTranscript(list, 40)
    expect(visible).toBe(list) // identity preserved — no pointless re-render churn
    expect(hiddenCount).toBe(0)
  })

  it('keeps only the tail of a long transcript and counts the rest', () => {
    const { visible, hiddenCount } = windowedTranscript(msgs(154), TRANSCRIPT_WINDOW_INITIAL)
    expect(visible.length).toBe(TRANSCRIPT_WINDOW_INITIAL)
    expect(hiddenCount).toBe(154 - TRANSCRIPT_WINDOW_INITIAL)
    expect(visible[0]).toBe(`m${154 - TRANSCRIPT_WINDOW_INITIAL}`) // window is the TAIL…
    expect(visible[visible.length - 1]).toBe('m153') // …and the last message stays last
  })

  it('handles the empty transcript', () => {
    expect(windowedTranscript([], 40)).toEqual({ visible: [], hiddenCount: 0 })
  })

  it('never windows below one message, whatever the window value', () => {
    const { visible, hiddenCount } = windowedTranscript(msgs(5), 0)
    expect(visible).toEqual(['m4'])
    expect(hiddenCount).toBe(4)
  })
})
