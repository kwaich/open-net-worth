import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(resolve(__dirname, '../../../lucius.css'), 'utf8')

function rule(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`))?.[1] ?? ''
}

describe('composer primary controls', () => {
  it('uses the primary token for screen-awareness active states', () => {
    expect(rule('.lx-aw.on .lx-aw-toggle')).toContain('color: var(--primary)')
    expect(rule('.lx-scope-ic')).toContain('color: var(--primary)')
    expect(rule('.lx-switch.on')).toContain('background: var(--primary)')
    expect(rule('.lx-switch.on')).toContain('border-color: var(--primary)')
    expect(rule('.lx-scope-peek.open')).toContain('border-color: var(--primary)')
  })

  it('uses the primary token for the send control', () => {
    expect(rule('.lx-send')).toContain('background: var(--primary)')
  })

  it('keeps the STOP state off the accent entirely', () => {
    // Stop is not a primary action — it is the way out of one — so it sits on the neutral
    // ramp in both states: surface2/muted/line at rest, and the matching hover tokens on
    // hover. This assertion used to demand `border-color: var(--primary)` on hover, which
    // put the accent on the one control whose job is to CANCEL the accented action.
    expect(rule('.lx-send.lx-stop')).toContain('background: var(--surface2)')
    const hover = rule('.lx-send.lx-stop:hover')
    expect(hover).toContain('background: var(--hover-surface2)')
    expect(hover).toContain('border-color: var(--hover-line)')
    expect(hover).not.toContain('var(--primary)')
  })
})
