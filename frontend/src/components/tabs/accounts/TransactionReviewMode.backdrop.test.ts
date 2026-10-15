import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(resolve(__dirname, './accounts.css'), 'utf8')
const sharedCss = readFileSync(resolve(__dirname, '../../..', 'index.css'), 'utf8')

function ruleBody(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`))?.[1] ?? ''
}

describe('Transaction Review mode backdrop', () => {
  it('shares the workspace modal backdrop paint without overriding its stacking or layout', () => {
    const body =
      sharedCss.match(
        /\.modal-overlay--workspace,\s*\.modal-overlay\.acct-review-overlay\s*\{([^}]*)\}/,
      )?.[1] ?? ''
    expect(body).toContain('background: rgba(6, 8, 12, 0.62)')
    expect(body).toContain('backdrop-filter: blur(3px)')
    expect(body).toContain('-webkit-backdrop-filter: blur(3px)')
    // The production minifier drops the unprefixed declaration when it precedes the -webkit-
    // one, leaving Chrome and Firefox without blur; the prefixed form must come first.
    expect(body.indexOf('-webkit-backdrop-filter')).toBeLessThan(
      body.indexOf('\n    backdrop-filter'),
    )
    expect(body).not.toMatch(/z-index|padding|right:/)
    expect(ruleBody('.acct-review-overlay')).not.toMatch(/background:|backdrop-filter:/)
  })
  it('deepens the scrim on phones, where the register sits right behind the card', () => {
    const mobile =
      css.match(
        /@media \(max-width: 639\.98px\) \{[\s\S]*?\.modal-overlay\.acct-review-overlay \{([^}]*)\}/,
      )?.[1] ?? ''
    expect(mobile).toContain('background: rgba(6, 8, 12, 0.82)')
    expect(mobile).toContain('-webkit-backdrop-filter: blur(10px)')
    expect(mobile).toContain('backdrop-filter: blur(10px)')
    expect(mobile.indexOf('-webkit-backdrop-filter')).toBeLessThan(
      mobile.indexOf('\n    backdrop-filter'),
    )
  })

  it('covers the open Lucius panel instead of inheriting the shared chat-width inset', () => {
    expect(ruleBody('.acct-review-overlay')).toMatch(/right:\s*0/)
  })
})
