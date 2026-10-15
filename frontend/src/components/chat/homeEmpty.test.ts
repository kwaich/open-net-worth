import { describe, expect, it } from 'vitest'
import { computeHomeEmpty } from './homeEmpty'

// Regression: while Lucius streamed, the panel rendered normally. The instant the turn
// ended, it painted the empty-conversation greeting over a conversation with six messages
// in memory — the server's listing (stale that day) reported `message_count: 0` for that
// session, and the old inline expression trusted the listing over the hydrated slice.

describe('computeHomeEmpty', () => {
  it('is never empty once the hydrated slice actually has messages, regardless of a stale listing', () => {
    expect(
      computeHomeEmpty({
        homeMode: true,
        onboarding: false,
        streaming: false,
        hydrated: true,
        sliceMessageCount: 6,
        listedMessageCount: 0,
      }),
    ).toBe(false)
  })

  it('falls back to the listing when not yet hydrated', () => {
    expect(
      computeHomeEmpty({
        homeMode: true,
        onboarding: false,
        streaming: false,
        hydrated: false,
        sliceMessageCount: 0,
        listedMessageCount: 0,
      }),
    ).toBe(true)
  })

  it('is never empty while streaming', () => {
    expect(
      computeHomeEmpty({
        homeMode: true,
        onboarding: false,
        streaming: true,
        hydrated: false,
        sliceMessageCount: 0,
        listedMessageCount: 0,
      }),
    ).toBe(false)
  })

  it('is never empty during onboarding', () => {
    expect(
      computeHomeEmpty({
        homeMode: true,
        onboarding: true,
        streaming: false,
        hydrated: false,
        sliceMessageCount: 0,
        listedMessageCount: 0,
      }),
    ).toBe(false)
  })

  it('is empty when hydrated with zero messages and no listing yet', () => {
    expect(
      computeHomeEmpty({
        homeMode: true,
        onboarding: false,
        streaming: false,
        hydrated: true,
        sliceMessageCount: 0,
        listedMessageCount: undefined,
      }),
    ).toBe(true)
  })

  it('is not empty while a send is posted but not yet streaming (the onboarding turn)', () => {
    // The onboarding send clears `onboarding` in the same step, its message is hidden, and
    // the reply has not begun: only `sending` says a turn is running.
    expect(
      computeHomeEmpty({
        homeMode: true,
        onboarding: false,
        streaming: false,
        sending: true,
        hydrated: true,
        sliceMessageCount: 0,
        listedMessageCount: 0,
      }),
    ).toBe(false)
  })
})
