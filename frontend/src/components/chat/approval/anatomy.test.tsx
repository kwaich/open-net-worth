// @vitest-environment jsdom
//
// The approval-card contract (docs/APPROVAL_CARDS.md), checked on EVERY fixture card in
// approval/fixtures/*.ts: a card body is built only from the design-system primitives. The
// CSS test pins the two sizes; this pins that no card routes around them — no legacy
// per-card markup (each class carried its own size) and no inline style (a size or colour
// a primitive can't see). A new card type with a fixture is covered automatically.
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { ChangePreview } from '../ChangePreview'
import type { CardFixture } from './fixtures/types'

afterEach(cleanup)

const modules = import.meta.glob<Record<string, unknown>>('./fixtures/*.ts', { eager: true })
const FIXTURES = Object.entries(modules)
  .filter(([path]) => !path.endsWith('/types.ts'))
  .flatMap(([, mod]) => Object.values(mod).filter(Array.isArray).flat() as CardFixture[])

const LEGACY = [
  'change-diff-table',
  'diff-after',
  'diff-before',
  'change-preview-reason',
  'prompt-diff-view',
  'prompt-diff-line',
  'prompt-diff-block',
  'prompt-expand-btn',
  'permission-arguments-preview',
  'permission-argument-meta',
  'permission-argument-diff',
  'permission-raw-arguments',
  'automation-updates-preview',
  'automation-field-diff',
  'code-run-source',
  'realized-detail',
  'realized-lots',
  'dup-review-table',
]

describe('every approval card is built from the primitives', () => {
  it('has fixtures for the card types', () => {
    expect(FIXTURES.length).toBeGreaterThan(30)
    expect(new Set(FIXTURES.map((f) => f.name)).size).toBe(FIXTURES.length)
  })

  it.each(FIXTURES.map((f) => [f.name, f] as const))('%s', (_name, fixture) => {
    const { container } = render(<ChangePreview change={fixture.change} onRespond={() => {}} />)
    const card = container.querySelector('.change-preview')!

    const legacy = LEGACY.filter((cls) => card.querySelector(`.${cls}`))
    expect(legacy).toEqual([])
    const styled = [...card.querySelectorAll('[style]')].map((el) => el.outerHTML.slice(0, 80))
    expect(styled).toEqual([])
    // Never a blind card: a body, or — when there is nothing to add (a skill create/delete)
    // — a header that names the subject itself.
    expect(
      card.querySelector('.ac-lead, .ac-facts, .ac-meta, .ac-note, .change-preview-label'),
    ).not.toBeNull()
  })
})
