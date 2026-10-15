import { describe, it, expect } from 'vitest'
import { modelRejectsImages, noVisionLabel } from './useModels'
import type { ModelOption } from '../api/models'

// ADR 2026-07-26. The asymmetry under test: only an explicit `vision: false` warns. Absent
// (unknown) must NOT, because a warning that a capability is missing when it isn't is worse
// than a missing warning — the backend handles unknown by trying it and learning the answer.

const MODELS: ModelOption[] = [
  { id: 'glm-4.6', provider_key: 'glm', vision: false },
  { id: 'gpt-4o', provider_key: 'openai', vision: true },
  { id: 'mystery-v9', provider_key: 'custom' }, // no `vision` key at all
]

describe('modelRejectsImages', () => {
  it('warns for a model the backend says has no vision', () => {
    expect(modelRejectsImages('glm:glm-4.6', MODELS, '')).toBe(true)
  })

  it('does not warn for a vision-capable model', () => {
    expect(modelRejectsImages('openai:gpt-4o', MODELS, '')).toBe(false)
  })

  it('does not warn when the capability is UNKNOWN', () => {
    expect(modelRejectsImages('custom:mystery-v9', MODELS, '')).toBe(false)
  })

  it('does not warn for a model missing from the list entirely', () => {
    expect(modelRejectsImages('nope:not-here', MODELS, '')).toBe(false)
  })

  it('matches a bare model id as well as the compound ref', () => {
    expect(modelRejectsImages('glm-4.6', MODELS, '')).toBe(true)
  })

  it('falls back to the instance default when no override is selected', () => {
    expect(modelRejectsImages('', MODELS, 'glm:glm-4.6')).toBe(true)
    expect(modelRejectsImages('', MODELS, 'openai:gpt-4o')).toBe(false)
  })

  it('never warns for the auto sentinel — the routed target is not known yet', () => {
    expect(modelRejectsImages('unknown-model', MODELS, 'glm:glm-4.6')).toBe(false)
  })

  it('does not warn when nothing is selected and there is no default', () => {
    expect(modelRejectsImages('', MODELS, '')).toBe(false)
  })
})

describe('noVisionLabel', () => {
  it('strips the provider prefix — the provider is noise in the sentence', () => {
    expect(noVisionLabel('glm:glm-4.6', MODELS, '')).toBe('glm-4.6')
  })

  it('returns null for any model that is not known-blind', () => {
    expect(noVisionLabel('openai:gpt-4o', MODELS, '')).toBeNull()
    expect(noVisionLabel('custom:mystery-v9', MODELS, '')).toBeNull()
  })
})
