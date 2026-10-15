import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const css = readFileSync(new URL('./commitments.css', import.meta.url), 'utf8')

describe('Commitments page alignment', () => {
  it('uses the shared page container gutter without adding a second centered max-width', () => {
    const pageRule = css.match(/\.c2-page\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(pageRule).not.toContain('max-width')
    expect(pageRule).not.toContain('margin-inline')
    expect(css).not.toMatch(/\.c2-page\s*\{[^}]*padding-(?:left|right)\s*:/)
  })

  it('matches Plan vertical clearance without stacking extra top padding', () => {
    const pageRule = css.match(/\.c2-page\s*\{([^}]*)\}/)?.[1] ?? ''
    const headerRule = css.match(/\.c2-page\s*>\s*\.ui-page-header\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(pageRule).toContain('padding-bottom: 96px')
    expect(headerRule).toContain('padding: 0 0 14px')
    expect(css).toMatch(
      /@media \(max-width: 560px\)\s*\{\s*\.c2-page\s*\{\s*padding-bottom:\s*110px;\s*\}/,
    )
  })

  it('keeps the detail subtab switcher within a phone viewport', () => {
    const tabsRule = css.match(/\.c3-dettabs\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(tabsRule).toContain('max-width: 100%')
    expect(css).toMatch(
      /@media \(max-width: 440px\)\s*\{[\s\S]*?\.c3-dettabs\s*\{[^}]*width:\s*100%;[^}]*\}[\s\S]*?\.c3-dettabs button\s*\{[^}]*flex:\s*1 1 auto;[^}]*min-width:\s*0;/,
    )
    expect(css).toMatch(
      /@media \(max-width: 440px\)\s*\{[\s\S]*?\.c3-dettabs \.c3-tabn,\s*\.c3-dettabs \.c3-tabdot\s*\{[^}]*display:\s*none;/,
    )
  })
})
