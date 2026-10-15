import { describe, it, expect } from 'vitest'
import {
  MODEL_CONFIG_CHANGED_EVENT,
  resolveAutomationConversationSelection,
  resolveModelOverride,
} from './useModels'
import type { ModelOption } from '../api/models'

// ── helpers ──────────────────────────────────────────────────────────────────

const claudeModels: ModelOption[] = [
  { id: 'claude-sonnet-4-6', provider_key: 'anthropic', provider_label: 'Claude' },
  { id: 'claude-haiku-4-5', provider_key: 'anthropic', provider_label: 'Claude' },
]

const openaiModels: ModelOption[] = [
  { id: 'gpt-4o', provider_key: 'openai', provider_label: 'OpenAI' },
  { id: 'gpt-4o-mini', provider_key: 'openai', provider_label: 'OpenAI' },
]

const mixedModels = [...claudeModels, ...openaiModels]

const singleProviderModels: ModelOption[] = [
  { id: 'claude-sonnet-4-6' },
  { id: 'claude-haiku-4-5' },
]

// ── resolveModelOverride ──────────────────────────────────────────────────────

describe('resolveModelOverride', () => {
  // Automatic model switching was removed (ADR 2026-08-06): "auto" is no longer a
  // sentinel, so it must fall through the normal not-found path to the instance default.
  describe('the retired "auto" sentinel', () => {
    it('is no longer special — it resolves to "" like any unknown model', () => {
      expect(resolveModelOverride('auto', claudeModels, 'claude-sonnet-4-6')).toBe('')
      expect(resolveModelOverride('auto', [], '')).toBe('')
      expect(resolveModelOverride('auto', mixedModels, 'claude-sonnet-4-6')).toBe('')
    })
  })

  describe('empty override', () => {
    it('returns "" unchanged (use instance default)', () => {
      expect(resolveModelOverride('', claudeModels, 'claude-sonnet-4-6')).toBe('')
    })

    it('returns "" when models list is empty', () => {
      expect(resolveModelOverride('', [], '')).toBe('')
    })
  })

  describe('valid plain model id', () => {
    it('returns the override when id is in models list (single-provider)', () => {
      expect(
        resolveModelOverride('claude-haiku-4-5', singleProviderModels, 'claude-sonnet-4-6'),
      ).toBe('claude-haiku-4-5')
    })

    it('returns the override when id is in multi-provider models list', () => {
      expect(resolveModelOverride('gpt-4o', mixedModels, 'claude-sonnet-4-6')).toBe('gpt-4o')
    })

    it('returns the override when it matches the defaultModel (belt-and-suspenders)', () => {
      // defaultModel may have been injected into the list or may just be tracked separately
      expect(resolveModelOverride('claude-sonnet-4-6', [], 'claude-sonnet-4-6')).toBe(
        'claude-sonnet-4-6',
      )
    })
  })

  describe('valid cross-provider key:model_id value', () => {
    it('returns "anthropic:claude-haiku-4-5" when present', () => {
      expect(
        resolveModelOverride('anthropic:claude-haiku-4-5', mixedModels, 'claude-sonnet-4-6'),
      ).toBe('anthropic:claude-haiku-4-5')
    })

    it('returns "openai:gpt-4o-mini" when present', () => {
      expect(resolveModelOverride('openai:gpt-4o-mini', mixedModels, 'claude-sonnet-4-6')).toBe(
        'openai:gpt-4o-mini',
      )
    })
  })

  describe('stale override — model not in current list', () => {
    it('falls back to "" when override is a plain id from a now-disabled provider', () => {
      // User had haiku set, then switched to OpenAI-only; haiku is gone from the list
      expect(resolveModelOverride('claude-haiku-4-5', openaiModels, 'gpt-4o')).toBe('')
    })

    it('falls back to "" when override is a cross-provider key not in list', () => {
      expect(resolveModelOverride('anthropic:claude-haiku-4-5', openaiModels, 'gpt-4o')).toBe('')
    })

    it('falls back to "" when override is an unknown model id entirely', () => {
      expect(resolveModelOverride('some-old-model-v1', claudeModels, 'claude-sonnet-4-6')).toBe('')
    })

    it('falls back to "" when override is a cross-provider value with wrong key', () => {
      expect(resolveModelOverride('groq:llama-3', claudeModels, 'claude-sonnet-4-6')).toBe('')
    })
  })

  describe('before models list loads (modelInfo.loaded=false)', () => {
    // The component passes storedModel directly when !loaded; test that resolveModelOverride
    // itself returns the value when the list happens to be empty (loaded=false state).
    // The component wraps the call with `modelInfo.loaded ? resolve(...) : storedModel`,
    // so this edge is handled at the component level; here we just confirm the pure fn
    // returns '' for an unknown id against an empty list.
    it('returns "" when list is empty and override is not the defaultModel', () => {
      expect(resolveModelOverride('claude-haiku-4-5', [], '')).toBe('')
    })

    it('returns the override when it matches the defaultModel even with empty list', () => {
      expect(resolveModelOverride('claude-haiku-4-5', [], 'claude-haiku-4-5')).toBe(
        'claude-haiku-4-5',
      )
    })
  })
})

describe('resolveAutomationConversationSelection', () => {
  const enabledModels: ModelOption[] = [
    {
      id: 'gpt-default',
      provider_key: 'openai',
      provider_label: 'OpenAI',
      effort_levels: ['low', 'medium'],
    },
    {
      id: 'gpt-reasoning',
      provider_key: 'openai',
      provider_label: 'OpenAI',
      effort_levels: ['low', 'high'],
    },
  ]

  it('adopts an available Automation model and clamps its effort to that model', () => {
    expect(
      resolveAutomationConversationSelection(
        { model: 'openai:gpt-reasoning', effort: 'max' },
        enabledModels,
        'gpt-default',
      ),
    ).toEqual({ model: 'openai:gpt-reasoning', effort: 'high' })
  })

  it('falls an unavailable Automation model back to the default model and its effort choices', () => {
    expect(
      resolveAutomationConversationSelection(
        { model: 'anthropic:claude-disabled', effort: 'xhigh' },
        enabledModels,
        'gpt-default',
      ),
    ).toEqual({ model: '', effort: 'medium' })
  })
})

// ── the cross-picker refresh contract ────────────────────────────────────────
//
// The "Preferred" group is a `preferred` flag the backend stamps onto GET /api/models, and
// useModels caches that response at MODULE level. Saving the shortlist in Settings
// therefore has to announce itself, or every other picker (chat, dock, task models, the
// Automation editor) keeps the old group until a full page reload. ModelSection's
// putPreferred fires this event for exactly that reason.
describe('MODEL_CONFIG_CHANGED_EVENT', () => {
  it('has a stable name — it is a cross-module contract string, not an implementation detail', () => {
    expect(MODEL_CONFIG_CHANGED_EVENT).toBe('modelconfigchanged')
  })
})
