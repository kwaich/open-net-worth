// @vitest-environment jsdom
//
// Tests for modelChipLabel — the per-message model label in the cost chip.
// Guards that EVERY assistant turn shows which model answered. The "(auto)" suffix is
// gone with automatic model switching (ADR 2026-08-06) — there is no router to attribute.

import { describe, it, expect } from 'vitest'
import { modelChipLabel } from './MessageBubble'

describe('modelChipLabel', () => {
  it('shows the model that answered', () => {
    expect(modelChipLabel({ model: 'grok-4.5' })).toBe('grok-4.5')
    expect(modelChipLabel({ model: 'gpt-4o' })).toBe('gpt-4o')
  })

  it('strips a provider prefix and 8-digit date suffix', () => {
    expect(modelChipLabel({ model: 'anthropic:claude-haiku-4-5-20251001' })).toBe('haiku-4-5')
    expect(modelChipLabel({ model: 'xai:grok-4.5' })).toBe('grok-4.5')
  })

  it('never appends an "(auto)" suffix — nothing routes a turn any more', () => {
    expect(modelChipLabel({ model: 'claude-haiku-4-5' })).toBe('haiku-4-5')
  })

  it('returns null when no model was recorded', () => {
    expect(modelChipLabel({})).toBeNull()
    expect(modelChipLabel({ model: '' })).toBeNull()
  })
})
