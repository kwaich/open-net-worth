// EnvCatsBadge display rules — pure-function coverage for the chip text and the row's render
// gate, without mounting the popover. Owner, 2026-09-25: a count on every envelope that funds
// at least one category ("1 category" / "N categories"), replacing "+ {other}" / "+ N more".
import { describe, it, expect } from 'vitest'
import { envCatsChipText, envCatsBadgeVisible } from './budget'

describe('envCatsChipText', () => {
  it('one category → "1 category" (whatever the envelope is named)', () => {
    expect(envCatsChipText(1)).toBe('1 category')
  })

  it('two or more → "N categories" (no "+ {other}" / "+ N more" forms)', () => {
    expect(envCatsChipText(2)).toBe('2 categories')
    expect(envCatsChipText(4)).toBe('4 categories')
  })

  it('zero members → no chip', () => {
    expect(envCatsChipText(0)).toBeNull()
  })
})

describe('envCatsBadgeVisible — the row render gate', () => {
  it('shows on every envelope with at least one category, same-name 1:1 included', () => {
    expect(envCatsBadgeVisible(['groceries'])).toBe(true)
    expect(envCatsBadgeVisible(['dining', 'hobby'])).toBe(true)
  })

  it('a zero-category envelope is NOT visible (it gets the "0 categories" hint)', () => {
    expect(envCatsBadgeVisible([])).toBe(false)
  })
})
