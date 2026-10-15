import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * On a phone `.as-page` cancels `.app-layout`'s 16px gutter (`margin: 0 -16px`) so its sticky
 * title and chip bands can bleed edge to edge, and every real section re-adds its own 16px.
 * The loading skeleton renders inside the same `.as-page`, so its body has to re-add the gutter
 * too — without it the placeholder cards, figures and chips sat flush against the screen edges
 * (owner report 2026-09-26) while the loaded page kept a 16px inset.
 */
const css = readFileSync(resolve(__dirname, './assets.css'), 'utf8')

function phoneBlock(): string {
  const start = css.indexOf('@media (max-width: 640px)')
  expect(start).toBeGreaterThan(-1)
  return css.slice(start)
}

describe('assets loading skeleton on a phone', () => {
  it('cancels the shell gutter on the page wrapper', () => {
    expect(phoneBlock()).toMatch(/\.as-page \{[^}]*margin: 0 -16px;/)
  })

  it('re-adds the 16px gutter around the skeleton body, like every loaded section', () => {
    expect(phoneBlock()).toMatch(/\.as-page \.ui-tab-skeleton__assets \{[^}]*padding: 0 16px;/)
  })
})
