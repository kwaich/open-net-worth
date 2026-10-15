// @vitest-environment jsdom
//
// Every skill / agent approval card, rendered through ChangePreview from synthetic payloads in
// the shape the backend stages. Pins the approval-card contract (docs/APPROVAL_CARDS.md): the
// body is built only from the ac-* primitives — no legacy classes, no inline styles — and each
// card keeps the facts a user decides on, the auto-apply grant above all.
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ChangePreview } from '../../ChangePreview'
import type { ProposedChange } from '../../../../types/chat'
import { SKILL_AGENT_FIXTURES } from '../fixtures/skillsAgents'

afterEach(cleanup)

const LEGACY = [
  'change-diff-table',
  'diff-after',
  'diff-before',
  'change-preview-reason',
  'change-preview-label',
  'prompt-diff',
  'prompt-expand-btn',
  'permission-',
  'automation-',
  'code-run-source',
  'realized-',
  'dup-',
]

const fixture = (name: string): ProposedChange => {
  const f = SKILL_AGENT_FIXTURES.find((x) => x.name === name)
  if (!f) throw new Error(`no fixture ${name}`)
  return f.change
}

const mount = (name: string) =>
  render(<ChangePreview change={fixture(name)} onRespond={() => {}} />).container

/** Everything between the shared header and the shared action row. */
const bodyElements = (container: HTMLElement) =>
  [...container.querySelectorAll('.change-preview *')].filter(
    (el) => !el.closest('.change-preview-header, .change-preview-actions'),
  )

const fact = (container: HTMLElement, label: string) =>
  [...container.querySelectorAll('.ac-fact')]
    .find((r) => r.querySelector('.ac-fact-label')?.textContent === label)
    ?.querySelector('.ac-fact-value') as HTMLElement | undefined

const header = (container: HTMLElement) =>
  container.querySelector('.change-preview-header')?.textContent
// The header names the action and its subject; the body never restates it as a lead.
const noLead = (container: HTMLElement) => expect(container.querySelector('.ac-lead')).toBeNull()

describe('skill and agent cards use only the approval primitives', () => {
  it.each(SKILL_AGENT_FIXTURES.map((f) => f.name))('%s', (name) => {
    const container = mount(name)
    const body = bodyElements(container)

    expect(container.querySelector('.ac-lead')).toBeNull()
    for (const el of body) {
      expect(el.getAttribute('style')).toBeNull()
      expect(
        [...el.classList].filter((c) => LEGACY.some((l) => c.startsWith(l))),
        `${name}: <${el.tagName.toLowerCase()} class="${el.className}">`,
      ).toEqual([])
    }
  })
})

describe('skill cards', () => {
  it('create is named once, by the header', () => {
    const c = mount('skill_create · new playbook')
    expect(header(c)).toBe('Create skillWeekly review')
    noLead(c)
  })

  it('update names the skill and the fields it changes', () => {
    const c = mount('skill_update · changed fields')
    expect(header(c)).toBe('Update skillWeekly review')
    noLead(c)
    expect(fact(c, 'Changes')?.textContent).toBe('description, enabled')
  })

  it('a body edit shows the removed and the added fragment', () => {
    const c = mount('skill_update · body edit')
    expect(fact(c, 'Changes')?.textContent).toBe('body')
    expect(c.querySelector('.ac-diff-removed')?.textContent).toContain(
      '3. Flag anything over its envelope.',
    )
    expect(c.querySelector('.ac-diff-added')?.textContent).toContain('by more than 10%')
  })

  it('delete is named once, by a header in the warning tone', () => {
    const c = mount('skill_delete · saved playbook')
    expect(header(c)).toBe('Delete skillWeekly review')
    expect(c.querySelector('.change-preview-header')?.className).toContain('--danger')
    noLead(c)
  })
})

describe('agent_create', () => {
  it('shows tools, model and channels, and says it only proposes without a grant', () => {
    const c = mount('agent_create · propose-only')
    expect(header(c)).toBe('Create agentBudget coach')
    noLead(c)
    expect(c.querySelector('.ac-meta')?.textContent).toBe(
      'Watches month-to-date spending against the plan.',
    )
    expect(fact(c, 'Tools')?.textContent).toBe(
      'get_budget_month, get_spending_summary, telegram_send_message',
    )
    expect(fact(c, 'Model')?.textContent).toBe('claude-sonnet-4')
    expect(fact(c, 'Channels')?.textContent).toBe('telegram')
    expect(fact(c, 'Auto-apply')?.textContent).toBe('Propose only')
  })

  it('folds the prompt behind a summary that carries its opening words', () => {
    const c = mount('agent_create · propose-only')
    const prompt = c.querySelector('details.ac-details') as HTMLDetailsElement
    expect(prompt.open).toBe(false)
    expect(prompt.querySelector('summary')?.textContent).toContain('You are a budget coach')
    expect(prompt.textContent).toContain('Message the owner only when')
  })

  it('shows an auto-apply grant as full-weight facts, never folded away', () => {
    const c = mount('agent_create · with auto-apply grant')
    const grant = fact(c, 'Auto-apply')!
    expect(grant.textContent).toBe('May apply: categorize, tag')
    expect(grant.className).toContain('ac-tone-bad')
    expect(fact(c, 'Unattended')?.textContent).toBe('categorize')
    expect(fact(c, 'Max writes per run')?.textContent).toBe('25')
    expect(fact(c, 'Max amount')?.textContent).toBe('500')
    expect(grant.closest('details')).toBeNull()
    expect(fact(c, 'Model')).toBeUndefined()
    expect(fact(c, 'Channels')).toBeUndefined()
  })
})

describe('agent_update', () => {
  it('a prompt edit shows the exact fragment', () => {
    const c = mount('agent_update · prompt edit')
    expect(header(c)).toBe('Update agentWeekly reviewer')
    noLead(c)
    expect(c.querySelector('.ac-section-title')?.textContent).toBe('Prompt edit')
    expect(c.querySelector('.ac-diff-removed')?.textContent).toContain('more than 20%')
    expect(c.querySelector('.ac-diff-added')?.textContent).toContain('more than 10%')
  })

  it('a whole-prompt rewrite offers an expandable line diff with its size', () => {
    const c = mount('agent_update · whole prompt replaced')
    expect(c.querySelector('.ac-diff')).toBeNull()

    const toggle = screen.getByRole('button', { name: /Show diff · \+2 −1 lines/ })
    fireEvent.click(toggle)

    expect(c.querySelector('.ac-diff-removed')?.textContent).toContain('more than 20%')
    expect(c.querySelector('.ac-diff-added')?.textContent).toContain('more than 10%')
    expect(c.querySelector('.ac-diff-added:last-child')?.textContent).toContain(
      'Keep the summary under 120 words.',
    )
    expect(c.querySelector('.ac-diff-context')?.textContent).toContain('weekly money reviewer')
    expect(screen.getByRole('button', { name: 'Hide diff' })).toBeTruthy()
  })

  it('tools, model, channels and enabled each show their new value', () => {
    const c = mount('agent_update · tools, model, channels, enabled')
    expect(fact(c, 'Tools')?.textContent).toBe('get_budget_month, get_spending_summary')
    expect(fact(c, 'Model')?.textContent).toBe('claude-haiku-4')
    expect(fact(c, 'Channels')?.textContent).toBe('—')
    expect(fact(c, 'Enabled')?.textContent).toBe('No')
    expect(c.querySelector('.ac-meta')).toBeNull()
  })

  it('names a changed field that has no value row of its own', () => {
    const c = mount('agent_update · rename and description')
    expect(header(c)).toBe('Update agentSpending coach')
    noLead(c)
    expect(c.querySelector('.ac-meta')?.textContent).toBe('Also changes name')
    expect(fact(c, 'Description')?.textContent).toBe('Flags categories drifting off plan.')
  })

  it('an auto-apply change shows the new grant prominently', () => {
    const c = mount('agent_update · auto-apply change')
    const grant = fact(c, 'Auto-apply')!
    expect(grant.textContent).toBe('May apply: categorize')
    expect(grant.className).toContain('ac-tone-bad')
    expect(fact(c, 'Unattended')?.textContent).toBe('categorize')
    expect(fact(c, 'Max writes per run')?.textContent).toBe('10')
    expect(fact(c, 'Max amount')).toBeUndefined()
  })

  it('revoking the grant says the agent only proposes', () => {
    const c = mount('agent_update · auto-apply revoked')
    expect(fact(c, 'Auto-apply')?.textContent).toBe('Propose only')
  })
})
