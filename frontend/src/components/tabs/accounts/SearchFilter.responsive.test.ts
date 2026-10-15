import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(resolve(__dirname, 'accounts.css'), 'utf8')
const source = readFileSync(resolve(__dirname, 'SearchFilter.tsx'), 'utf8')

describe('Accounts filter toolbar responsive contract', () => {
  it('keeps the Accounts status bar on the shared desktop page inset', () => {
    // Since 2026-08-24 .topbar is a SIBLING of .app-layout (.app-main > .topbar + .app-layout),
    // so the Accounts panel's left-inset zeroing cannot reach it and accounts.css no longer
    // needs to hand it its inset back — the shared `.topbar { padding-inline: … }` in index.css
    // owns it for every tab. Assert the shared rule, and that this file does NOT re-inset it.
    const shell = readFileSync(resolve(__dirname, '../../../index.css'), 'utf8')
    expect(shell).toMatch(/\.topbar \{[^}]*padding-inline: max\(24px/s)
    expect(css).not.toContain('.acct-shell) .topbar')
  })

  it('keeps controls on one row and hides date/column labels on narrow screens', () => {
    const narrow = css.match(/@media \(max-width: 960px\) \{([\s\S]*?)\n\}/)?.[1] ?? ''

    expect(narrow).toContain('.acct-filters')
    expect(narrow).toContain('flex-wrap: nowrap')
    expect(narrow).toContain('.acct-filters .acct-viewlbl')
    expect(narrow).toContain('display: none')
    expect(narrow).toContain('.acct-filters .acct-viewcaret')
    expect(narrow).not.toMatch(/\.acct-filter-add\s*\{[^}]*display:\s*none/)
    expect(narrow).toContain('.acct-searchwrap .acct-search { min-width: 0')
  })

  it('keeps a dynamic accessible name on the icon-only date control', () => {
    expect(source).toContain('aria-label={`Date range: ${dateLabel}`}')
    expect(source).toContain('title={`Date range: ${dateLabel}`}')
  })

  it('shortens the add transaction label to Add on narrow screens', () => {
    const narrow = css.match(/@media \(max-width: 960px\) \{([\s\S]*?)\n\}/)?.[1] ?? ''

    expect(source).toContain('aria-label="Add transaction"')
    expect(source).toContain('<span className="acct-filter-add-full">Add transaction</span>')
    expect(source).toContain('<span className="acct-filter-add-short">Add</span>')
    expect(css).toContain('.acct-filter-add-short { display: none; }')
    expect(narrow).toContain('.acct-filter-add-full { display: none; }')
    expect(narrow).toContain('.acct-filter-add-short { display: inline; }')
  })
})
