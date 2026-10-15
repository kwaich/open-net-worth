// @vitest-environment jsdom
//
// `skill` is a registered Lucius mention type (multi-surface: "/" verb + "@" entity —
// backend descriptor services/mentions/descriptors/multi_surface.py, order=15). The tree row
// emitted no data-entity-id, so a skill could be invoked with "/" but never dragged into the
// dock or right-clicked "Ask Lucius about this" (owner, 2026-08-23).
//
// Same shape as tabs/commitments + tabs/automations lucius.entityWiring tests: dragRightClick
// binds globally on [data-entity-id][draggable], so the attributes ARE the wiring.

import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { SkillsTree } from './SkillsTree'
import type { Skill } from '../../types/skills'

afterEach(cleanup)

function makeSkill(over: Partial<Skill> = {}): Skill {
  return {
    id: 'weekly-review',
    name: 'Weekly review',
    description: 'Summarise the week and flag anything off-track',
    enabled: true,
    ...over,
  } as Skill
}

function renderTree(skills: Skill[]) {
  return render(
    <SkillsTree
      skills={skills}
      selectedSkillId={null}
      selectedFile={null}
      onSelectSkill={() => {}}
      onSelectFile={() => {}}
      onCreateFirstSkill={() => {}}
      searchQuery=""
      onSearchChange={() => {}}
      bundles={{}}
      bundleLoading={{}}
      onExpandSkill={() => {}}
      expandedSkills={new Set<string>()}
    />,
  )
}

describe('SkillsTree Lucius wiring', () => {
  it('emits the entity id, label and draggable so drag + Ask Lucius work', () => {
    const { container } = renderTree([makeSkill()])

    const row = container.querySelector<HTMLElement>('[data-entity-id="skill:weekly-review"]')
    expect(row).toBeTruthy()
    expect(row!.getAttribute('data-entity-label')).toBe('Weekly review')
    expect(row!.getAttribute('draggable')).toBe('true')
  })

  it('emits only the descriptor-allowlisted context fields', () => {
    const { container } = renderTree([makeSkill()])

    const row = container.querySelector<HTMLElement>('[data-entity-id="skill:weekly-review"]')!
    expect(row.getAttribute('data-ctx-type')).toBe('skill')
    expect(row.getAttribute('data-ctx-fields') ?? '').toContain('Weekly review')
  })

  it('wires a disabled skill too', () => {
    const { container } = renderTree([makeSkill({ id: 'off-skill', name: 'Off', enabled: false })])

    const row = container.querySelector<HTMLElement>('[data-entity-id="skill:off-skill"]')
    expect(row).toBeTruthy()
    expect(row!.getAttribute('draggable')).toBe('true')
  })
})
