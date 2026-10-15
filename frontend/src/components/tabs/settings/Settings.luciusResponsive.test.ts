// @vitest-environment node
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const css = readFileSync(fileURLToPath(new URL('./settings.css', import.meta.url)), 'utf8')
const settings = readFileSync(fileURLToPath(new URL('../Settings.tsx', import.meta.url)), 'utf8')

describe('Settings beside docked Lucius', () => {
  it('responds to the remaining workspace width instead of the viewport alone', () => {
    expect(css).toMatch(/container-type:\s*inline-size/)
    expect(css).toMatch(/@container settings-workspace \(width < 720px\)/)
    expect(css).toMatch(/\.rd-set-navlabel\s*\{\s*display:\s*none/)
    expect(css).toMatch(/\.rd-set-topbar\s*\{[\s\S]*?padding:\s*16px 14px 10px/)
    expect(css).toMatch(/\.rd-set-body\s*\.rd-field\s*\{[\s\S]*?grid-template-columns:\s*1fr/)
    expect(settings).toContain('className="rd-set-navlabel"')
  })
})
