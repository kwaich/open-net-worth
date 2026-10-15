import { describe, it, expect } from 'vitest'
import { effortLevelsFor, modelSupportsReasoning, clampEffort } from './useModels'
import type { ModelOption } from '../api/models'

const FULL = ['low', 'medium', 'high', 'xhigh', 'max']

const models: ModelOption[] = [
  {
    id: 'claude-sonnet-5',
    provider_key: 'anthropic',
    effort_levels: ['low', 'medium', 'high', 'max'],
  },
  { id: 'gpt-4o', provider_key: 'openai', effort_levels: [] },
  { id: 'o3-mini', provider_key: 'openai', effort_levels: ['low', 'medium', 'high'] },
  { id: 'grok-3-mini', provider_key: 'xai', effort_levels: ['low', 'high'] },
  { id: 'grok-4.5', provider_key: 'xai', effort_levels: ['low', 'medium', 'high'] },
  { id: 'moonshotai/kimi-k3', provider_key: 'openrouter', effort_levels: FULL },
  { id: 'someco/plain-chat', provider_key: 'openrouter', effort_levels: [] },
]

describe('effortLevelsFor', () => {
  it('auto → full ladder (target unknown)', () => {
    expect(effortLevelsFor('unknown-model', models, 'gpt-4o')).toEqual(FULL)
  })

  it('empty selector resolves to the instance default', () => {
    expect(effortLevelsFor('', models, 'gpt-4o')).toEqual([])
    expect(effortLevelsFor('', models, 'grok-4.5')).toEqual(['low', 'medium', 'high'])
  })

  it('returns exactly the model’s accepted tiers', () => {
    expect(effortLevelsFor('grok-3-mini', models, '')).toEqual(['low', 'high'])
    expect(effortLevelsFor('o3-mini', models, '')).toEqual(['low', 'medium', 'high'])
  })

  it('matches a provider-prefixed selector', () => {
    expect(effortLevelsFor('openrouter:moonshotai/kimi-k3', models, '')).toEqual(FULL)
    expect(effortLevelsFor('openrouter:someco/plain-chat', models, '')).toEqual([])
  })

  it('unknown / missing effort_levels → full ladder', () => {
    expect(effortLevelsFor('nonexistent', models, '')).toEqual(FULL)
    const m: ModelOption[] = [{ id: 'mystery', provider_key: 'x' }]
    expect(effortLevelsFor('mystery', m, '')).toEqual(FULL)
  })
})

describe('modelSupportsReasoning', () => {
  it('reflects whether any tier is accepted', () => {
    expect(modelSupportsReasoning('gpt-4o', models, '')).toBe(false)
    expect(modelSupportsReasoning('grok-4.5', models, '')).toBe(true)
    expect(modelSupportsReasoning('unknown-model', models, 'gpt-4o')).toBe(true)
  })
})

describe('clampEffort', () => {
  it('keeps a supported level unchanged', () => {
    expect(clampEffort('high', ['low', 'medium', 'high'])).toBe('high')
  })

  it('falls back down to the nearest supported level', () => {
    expect(clampEffort('xhigh', ['low', 'medium', 'high'])).toBe('high')
    expect(clampEffort('max', ['low', 'high'])).toBe('high')
    expect(clampEffort('medium', ['low', 'high'])).toBe('low')
  })

  it('when nothing is ≤ current, picks the lowest supported', () => {
    expect(clampEffort('low', ['medium', 'high'])).toBe('medium')
  })

  it('empty levels returns the input unchanged', () => {
    expect(clampEffort('xhigh', [])).toBe('xhigh')
  })
})
