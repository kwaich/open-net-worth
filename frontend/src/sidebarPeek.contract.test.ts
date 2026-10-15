import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

// The hover peek is a separate overlay layer. These contracts keep it from leaking into the
// docked sidebar (square edges, no shadow) or into the page layout (main column, topbar).
const css = readFileSync(join(__dirname, 'index.css'), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/\s+/g, ' ')
const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({
  selector: (m[1] ?? '').trim(),
  body: m[2] ?? '',
}))
const shell = readFileSync(join(__dirname, 'RouterShell.tsx'), 'utf8')

describe('sidebar styling', () => {
  const sidebarRule = (r: { selector: string }) =>
    /(^|[\s,])\.sidebar(\.|\s|,|$|:)/.test(r.selector) && !/\.sidebar-/.test(r.selector)

  it('has no border-radius on the docked rail, the peek, or anything inside them', () => {
    const offenders = rules.filter(
      (r) =>
        /(^|[\s,])\.sidebar(\.|\s|,|$|:|-)/.test(r.selector) &&
        /border-radius/.test(r.body) &&
        /(^|[\s,])\.sidebar(\.expanded|\.peek)?(\s*,|\s*$)/.test(r.selector),
    )
    expect(offenders.map((r) => r.selector)).toEqual([])
  })

  it('confines a sidebar shadow to the peek (and the pre-existing phone drawer)', () => {
    const shadowed = rules.filter((r) => sidebarRule(r) && /box-shadow/.test(r.body))
    expect(shadowed.filter((r) => r.selector === '.sidebar.peek')).toHaveLength(1)
    // The phone drawer's own shadow predates the peek and sits in a phone-only media query.
    for (const r of shadowed.filter((r) => r.selector !== '.sidebar.peek'))
      expect(r.selector).toBe('.sidebar')
  })

  it('scopes the 48px title-bar height under the macOS marker', () => {
    expect(rules.some((r) => /data-desktop-chrome/.test(r.selector))).toBe(true)
    for (const r of rules) {
      if (/(^|\s)\.(sidebar-header|topbar)\b/.test(r.selector) && /height: 48px/.test(r.body))
        expect(r.selector).toContain("[data-desktop-chrome='mac']")
    }
  })
})

describe('peek leaves the page layout alone', () => {
  it('no rule that sizes or pads the page reads the peek state', () => {
    const readsPeek = rules.filter((r) => /\.peek\b/.test(r.selector))
    for (const r of readsPeek) {
      expect(r.selector).not.toMatch(/\.app-main|\.topbar|\.app-layout|\.app-shell/)
    }
  })

  it('the topbar inset keys off the docked rail only', () => {
    const topbar = rules.filter((r) => /\.topbar/.test(r.selector) && /padding-left/.test(r.body))
    for (const r of topbar) expect(r.selector).not.toMatch(/peek/)
  })

  it('renders the peek as its own overlay, never on the docked rail, with no spacer', () => {
    const docked = shell.match(/<nav\s+className=\{`sidebar[^`]*`\}/)
    expect(docked?.[0]).not.toContain('peek')
    expect(shell).toContain('className="sidebar peek"')
    expect(shell).not.toContain('sidebar-peek-spacer')
    expect(css).not.toContain('sidebar-peek-spacer')
  })
})
