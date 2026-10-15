import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(resolve(__dirname, 'accounts.css'), 'utf8')

describe('Sidebar account rows', () => {
  it('does not reserve an icon column after account colours are removed', () => {
    const rule = css.match(/\.acct-row\s*\{([^}]*)\}/)?.[1]
    expect(rule).toContain('grid-template-columns: minmax(0, 1fr) auto')
  })
})

describe('Account editor identity', () => {
  it('centres the decorative account icon without shifting the form fields', () => {
    const rule = css.match(/\.acct-avatar-row\s*\{([^}]*)\}/)?.[1]
    expect(rule).toContain('justify-content: center')
  })
})

describe('Account editor mobile sheet', () => {
  it('uses its content height until it reaches the shared sheet ceiling', () => {
    const mobile = css.match(/@media \(max-width: 640px\) \{([\s\S]*)/)?.[1]
    expect(mobile).not.toMatch(
      /\.acct-account-modal--edit\s*\{[^}]*height: var\(--sheet-max-height\)/,
    )
    expect(mobile).toMatch(
      /\.acct-account-modal--edit \.ui-modal__body\s*\{[^}]*display: block[^}]*overflow-x: hidden[^}]*overflow-y: auto/,
    )
    expect(mobile).toMatch(
      /\.acct-account-modal--edit \.acct-wizard--edit\s*\{[^}]*height: auto[^}]*flex: none[^}]*overflow: visible[^}]*margin-inline: 0[^}]*padding-inline: 0/,
    )
  })
})
