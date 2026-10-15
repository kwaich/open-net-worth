import { describe, expect, it, vi } from 'vitest'
import { buildSkillRunMessage } from './Skills'
import type { Skill } from '../../types/skills'

function skill(overrides: Partial<Skill> = {}): Skill {
  return {
    id: 'monthly-close',
    name: 'monthly-close',
    description: '',
    body: '',
    enabled: true,
    author: 'builtin',
    created_at: '',
    updated_at: '',
    ...overrides,
  }
}

describe('buildSkillRunMessage', () => {
  it('stages only the immutable id and never submits a turn', () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    const selected = Object.freeze(
      skill({ id: 'skill-immutable-4f', name: 'renamed display label' }),
    )

    const staged = buildSkillRunMessage(selected)

    expect(staged).toBe('/skill-immutable-4f')
    expect(fetchSpy).not.toHaveBeenCalled()
    fetchSpy.mockRestore()
  })

  it('stages monthly-close canonically without submitting or spending', () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch')

    expect(buildSkillRunMessage(skill())).toBe('/monthly-close')
    expect(fetchSpy).not.toHaveBeenCalled()
    fetchSpy.mockRestore()
  })
})
