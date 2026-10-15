// The shared phone kebab sheet (`.kebab-sheet` + `.kebab-sheet-scrim`, portaled to <body>) can
// be opened from INSIDE a Radix modal — Manage categories' Kind filter. That dialog switches
// `pointer-events` off body-wide, so the pair must switch its own back on or every tap falls
// through to the dialog's overlay and dismisses the PARENT sheet; and the scrim must sit at the
// modal level or it paints behind the parent. jsdom cannot compute cascade, so this guards the
// CSS text (owner report 2026-09-03).
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(resolve(__dirname, '../index.css'), 'utf8')

/** The body of the FIRST rule whose selector list is exactly `selector`. */
function ruleBody(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const m = css.match(new RegExp(`(?:^|[}\\n])\\s*${escaped}\\s*\\{([^}]*)\\}`))
  expect(m, `no rule for ${selector}`).toBeTruthy()
  return m?.[1] ?? ''
}

describe('kebab sheet as a layer above a modal', () => {
  it('the sheet and its scrim restore their own pointer events', () => {
    expect(ruleBody('.kebab-sheet')).toMatch(/pointer-events:\s*auto/)
    expect(ruleBody('.kebab-sheet-scrim')).toMatch(/pointer-events:\s*auto/)
  })

  it('the scrim stacks at the modal level, never below the modal it covers', () => {
    expect(ruleBody('.kebab-sheet-scrim')).toMatch(/z-index:\s*var\(--z-modal\)/)
    expect(ruleBody('.kebab-sheet')).toMatch(/z-index:\s*var\(--z-modal\)/)
  })
})
