import { describe, expect, it } from 'vitest'
import { conversationTitleFromFirstMessage } from './conversationTitle'

describe('conversationTitleFromFirstMessage', () => {
  it('uses a human skill name instead of leaking the serialized skill token', () => {
    expect(
      conversationTitleFromFirstMessage('[skill: account-balance-tracer|account-balance-tracer]'),
    ).toBe('Account balance tracer')
  })

  it('prefers the actual question when a skill token accompanies one', () => {
    expect(
      conversationTitleFromFirstMessage(
        '[skill: account-balance-tracer|Account Balance Tracer] Why is cash off?',
      ),
    ).toBe('Why is cash off?')
  })

  it('humanizes direct slash-only skill runs and keeps trailing task text', () => {
    expect(conversationTitleFromFirstMessage('/account-balance-tracer')).toBe(
      'Account balance tracer',
    )
    expect(conversationTitleFromFirstMessage('/first-check /tax-audit Why is cash off?')).toBe(
      'Why is cash off?',
    )
  })

  it('represents multiple explicitly activated skills without raw tokens', () => {
    expect(
      conversationTitleFromFirstMessage(
        '[skill: first-check|first-check] [skill: tax-audit|tax-audit]',
      ),
    ).toBe('First check + Tax audit')
  })

  it('repairs a legacy title truncated before the skill token closed', () => {
    expect(
      conversationTitleFromFirstMessage(
        '[skill: this-is-a-very-long-skill-name-that-exceeds-the-lega',
      ),
    ).toBe('This is a very long skill name that exceeds the lega')
  })

  it('collapses leading, trailing, and repeated whitespace', () => {
    expect(conversationTitleFromFirstMessage('  Why\t is\n cash   off?  ')).toBe('Why is cash off?')
  })

  it('uses the fallback title for a whitespace-only message', () => {
    expect(conversationTitleFromFirstMessage(' \t\n ')).toBe('New conversation')
  })

  it('does not leave trailing whitespace when truncation lands on a separator', () => {
    expect(conversationTitleFromFirstMessage(`${'a'.repeat(59)} two`)).toBe('a'.repeat(59))
  })
})

describe('conversationTitleFromFirstMessage — the onboarding harness note', () => {
  it('names it explicitly instead of exposing the raw sentinel', () => {
    // `__ONBOARDING_INIT__` starts a first-run conversation with no human message yet —
    // deriving a title from it literally would put the harness's own trigger string into
    // the conversation list.
    expect(conversationTitleFromFirstMessage('__ONBOARDING_INIT__')).toBe(
      'Onboarding to Open Net Worth',
    )
  })

  it('is exact-match, not a substring test', () => {
    // Mirrors the backend's own is_harness_note() rule (2026-08-22): a real user message
    // that happens to CONTAIN the sentinel text is still a real message, not the harness
    // note, and must be titled from its own content like anything else.
    expect(conversationTitleFromFirstMessage('is __ONBOARDING_INIT__ a real thing?')).not.toBe(
      'Onboarding to Open Net Worth',
    )
  })

  it('tolerates surrounding whitespace, matching how the message actually arrives', () => {
    expect(conversationTitleFromFirstMessage('  __ONBOARDING_INIT__  ')).toBe(
      'Onboarding to Open Net Worth',
    )
  })
})
