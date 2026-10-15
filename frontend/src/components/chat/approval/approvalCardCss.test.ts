// The approval-card design system has two text sizes and no others (docs/APPROVAL_CARDS.md).
// A card that grew a 17px figure beside 11px lines is what started it — so the rule is a
// test, not a convention: every font-size on the card — its body (.ac-*), header, buttons and
// grant menu (.change-*, .grant-split*) — must be one of the two tokens.
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(join(__dirname, '../../../chat.css'), 'utf8')
const TOKENS = new Set(['var(--ac-text)', 'var(--ac-caps)'])

const CARD_SELECTOR = /\.(ac-|change-|grant-split)/

function cardRules(): Array<{ selector: string; body: string }> {
  const rules: Array<{ selector: string; body: string }> = []
  const source = css.replace(/\/\*[\s\S]*?\*\//g, '')
  for (const m of source.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selector = (m[1] ?? '').trim()
    if (CARD_SELECTOR.test(selector)) rules.push({ selector, body: m[2] ?? '' })
  }
  return rules
}

describe('approval-card CSS', () => {
  it('defines the primitives', () => {
    const selectors = cardRules().map((r) => r.selector)
    for (const cls of ['.ac-lead', '.ac-meta', '.ac-fact', '.ac-details']) {
      expect(selectors.some((s) => s.split(/[\s,]/).includes(cls))).toBe(true)
    }
  })

  it('sizes text only with the two tokens', () => {
    const offenders = cardRules().flatMap(({ selector, body }) =>
      [...body.matchAll(/font-size:\s*([^;]+);/g)]
        .map((m) => (m[1] ?? '').trim())
        .filter((v) => !TOKENS.has(v))
        .map((v) => `${selector} → ${v}`),
    )
    expect(offenders).toEqual([])
  })

  // Owner, 2026-10-11: "make sure we don't use too many separators". A table on a card has
  // one rule, under its headings; rows are told apart by space, never a line each.
  it('draws no line between table rows', () => {
    // The app's global `td` rule draws one, so the card must turn it off, not just omit it.
    expect(css).toMatch(/\.ac-table td\s*\{[^}]*border-bottom:\s*none;/)
    const offenders = cardRules()
      .filter(({ selector }) => /\btd\b|\btr\b/.test(selector))
      .filter(({ body }) =>
        [...body.matchAll(/border(?:-top|-bottom)?\s*:\s*([^;]+);/g)].some(
          (m) => !/^(none|0)$/.test((m[1] ?? '').trim()),
        ),
      )
      .map(({ selector }) => selector)
    expect(offenders).toEqual([])
  })

  it('the card defines the tokens as 12px and 10px', () => {
    expect(css).toMatch(/\.change-preview\s*\{[^}]*--ac-text:\s*12px;[^}]*--ac-caps:\s*10px;/)
  })
})
