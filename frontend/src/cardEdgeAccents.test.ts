import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'

const files = [
  'src/index.css',
  'src/components/skills/skills.css',
  'src/components/tabs/accounts/accounts.css',
  'src/components/tabs/investments/design.css',
  'src/components/tabs/plan/plan.css',
  'src/components/tabs/builder.css',
  'src/components/tabs/commitments/commitments.css',
]

const css = files.map((file) => readFileSync(file, 'utf8')).join('\n')

function rule(selector: string) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`))?.[1] ?? ''
}

describe('card-edge accent convention', () => {
  it.each([
    '.agent-improvement',
    '.inbox-item',
    '.builder-thesis',
    '.builder-authority__explain',
    '.builder-result',
    '.imp-pair-row',
    '.imp-verdict2',
    '.fire-formula',
  ])('%s does not use a card-edge rail', (selector) => {
    expect(rule(selector)).not.toMatch(/border-left(?:-width|-color)?\s*:/)
  })

  it('does not restore the Intel or holding full-height stripe primitives', () => {
    expect(css).not.toContain('.intel-story-stripe')
    expect(css).not.toContain('.mk-hold-stripe')
    expect(rule('.mk-hold-marker')).not.toMatch(/\b(?:top|bottom|height)\s*:\s*100%/)
  })

  it('keeps legitimate structural border-left uses available', () => {
    expect(rule('.conn-dir__regional-guide')).toMatch(/border-left\s*:/)
  })
})
