// @vitest-environment jsdom
//
// The budgeting cards on the approval-card primitives (docs/APPROVAL_CARDS.md): a plain-words
// lead with the amount as its figure, the month / new order as the muted line, params as fact
// rows. Every fixture is rendered through the real ChangePreview and must carry its facts
// without a legacy class or an inline style.
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { fBase } from '../../../../utils/format'
import { ChangePreview } from '../../ChangePreview'
import { fBudgetMonth } from '../cardTypes'
import { BUDGET_FIXTURES } from '../fixtures/budget'

afterEach(cleanup)

const LEGACY =
  /\b(change-diff-table|diff-after|diff-before|change-preview-reason|change-preview-label|prompt-diff[\w-]*|permission-[\w-]+|automation-[\w-]+|code-run-source|realized-[\w-]+|dup-[\w-]+)\b/

function card(name: string) {
  const fixture = BUDGET_FIXTURES.find((f) => f.name === name)
  if (!fixture) throw new Error(`no fixture ${name}`)
  return render(<ChangePreview change={fixture.change} onRespond={() => {}} />).container
}

const lead = (c: HTMLElement) => c.querySelector('.ac-lead')?.textContent
const meta = (c: HTMLElement) => c.querySelector('.ac-meta')?.textContent
const facts = (c: HTMLElement) =>
  [...c.querySelectorAll('.ac-fact')].map(
    (r) =>
      `${r.querySelector('.ac-fact-label')?.textContent}=${r.querySelector('.ac-fact-value')?.textContent}`,
  )

describe('every budget fixture', () => {
  it.each(BUDGET_FIXTURES.map((f) => [f.name]))('%s uses only primitives', (name) => {
    const c = card(name)
    expect(c.querySelector('.ac-lead, .ac-note, .ac-facts')).not.toBeNull()
    expect(c.innerHTML).not.toMatch(LEGACY)
    expect(c.querySelector('[style]')).toBeNull()
  })
})

describe('envelope money cards', () => {
  it('assign names the envelope (not its id) with the amount, the month muted', () => {
    const c = card('assign · envelope')
    expect(lead(c)).toBe(`Assign to Groceries${fBase(400.0)}`)
    expect(c.querySelector('.ac-lead-figure')?.textContent).toBe(`${fBase(400.0)}`)
    expect(meta(c)).toBe(fBudgetMonth('2026-06'))
  })

  it('a card staged before names existed still shows its envelope id', () => {
    expect(lead(card('assign · staged before names (id only)'))).toBe(
      `Assign to env_groceries${fBase(400.0)}`,
    )
  })

  it('move_money names both envelopes and the amount', () => {
    const c = card('move_money · between envelopes')
    expect(lead(c)).toBe(`Move from Dining → Groceries${fBase(50.0)}`)
    expect(meta(c)).toBe(fBudgetMonth('2026-06'))
  })

  it('move_money reads the Ready to Assign pool by name', () => {
    expect(lead(card('move_money · out of Ready to Assign'))).toBe(
      `Move from Ready to Assign → Travel${fBase(120.5)}`,
    )
  })
})

describe('targets', () => {
  it('a monthly target leads with the amount and has no extra rows', () => {
    const c = card('set_target · monthly')
    expect(lead(c)).toBe(`Set monthly target on Rent${fBase(1800.0)}`)
    expect(meta(c)).toBeUndefined()
    expect(facts(c)).toEqual([])
  })

  it('a savings target shows its deadline and how progress counts', () => {
    const c = card('set_target · savings by date, balance progress')
    expect(lead(c)).toBe(`Set savings target on Vacation${fBase(3000.0)}`)
    expect(meta(c)).toBe('by Mar 31, 2027')
    expect(facts(c)).toEqual(['Progress counts=Balance'])
  })

  it('clear and snooze lead with the op and the envelope', () => {
    expect(lead(card('set_target · clear'))).toBe('Clear target on Gifts')
    const c = card('set_target · snooze')
    expect(lead(c)).toBe('Snooze target on Gifts')
    expect(meta(c)).toContain('until December')
  })
})

describe('groups and ordering', () => {
  it('group_create: the header says create, the lead names the group', () => {
    const c = card('group_create')
    expect(c.querySelector('.change-preview-header')?.textContent).toBe('Create group')
    expect(lead(c)).toBe('Fun')
  })

  it('group_rename shows the old and new name', () => {
    expect(lead(card('group_rename'))).toBe('Extras → Lifestyle')
  })

  it('group_reorder counts the groups and shows the new order', () => {
    const c = card('group_reorder')
    expect(lead(c)).toBe('Reorder 4 groups')
    expect(meta(c)).toBe('Bills → Everyday → Savings → Fun')
  })

  it('group_delete is destructive and says where the envelopes go, once', () => {
    const c = card('group_delete')
    expect(lead(c)).toBe('Extras')
    // Toned once, on the header — not again on the lead.
    expect(c.querySelector('.change-preview-header')?.className).toContain('--danger')
    expect(c.querySelector('.ac-lead')?.className).not.toContain('ac-tone-bad')
    expect(c.querySelector('.ac-note')?.textContent).toBe('Its envelopes move to Others')
  })

  it('move_category names the category and the group', () => {
    expect(lead(card('move_category'))).toBe('Move Streaming into Fun')
  })

  it('reorder_categories counts the categories, names the group and shows the order', () => {
    const c = card('reorder_categories')
    expect(lead(c)).toBe('Reorder 3 categories in Everyday')
    expect(meta(c)).toBe('Groceries → Dining → Transport')
  })
})

describe('budget-engine cards', () => {
  it('shows the operation params as fact rows with humanized keys', () => {
    const c = card('apply_budget_operation · params')
    expect(facts(c)).toEqual(['Month=2026-06', 'Envelope id=env_food', 'Amount=500'])
  })

  it('joins a list param on one row', () => {
    expect(facts(card('apply_budget_operation · list param'))).toEqual([
      'Category ids=cat_salary, cat_bonus',
    ])
  })

  it('says so when there are no params', () => {
    expect(card('apply_budget_operation · no params').querySelector('.ac-note')?.textContent).toBe(
      'No parameters.',
    )
  })

  it('switching the home budget: the header names the method once, the body reassures in one line', () => {
    const c = card('set_home_budget_engine')
    expect(c.querySelector('.change-preview-header')?.textContent).toContain(
      'Switch home budget lens to 50/30/20',
    )
    // Said once — the body does not repeat the header as a lead.
    expect(c.querySelector('.ac-lead')).toBeNull()
    expect(c.querySelectorAll('.ac-note')).toHaveLength(1)
    expect(c.querySelector('.ac-note')?.textContent).toBe(
      'Other methods stay untouched; switching back loses nothing',
    )
  })
})
