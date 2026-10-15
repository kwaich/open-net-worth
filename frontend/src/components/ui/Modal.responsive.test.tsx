// @vitest-environment node
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const css = readFileSync(fileURLToPath(new URL('../../index.css', import.meta.url)), 'utf8')
const planCss = readFileSync(
  fileURLToPath(new URL('../tabs/plan/plan.css', import.meta.url)),
  'utf8',
)

describe('shared responsive modal CSS contract', () => {
  it('turns every standard modal into the same bottom-docked phone sheet', () => {
    // The ceiling is the shared `--sheet-max-height` token (a fixed gap under the status bar),
    // never a viewport share of the sheet's own — see sheet-ceiling.test.ts for every root.
    expect(css).toMatch(
      /\.ui-modal-card:not\(\.ui-modal-card--workspace\)\s*\{[^}]*max-height: var\(--sheet-visible-max-height\)/,
    )
    expect(css).toMatch(/\.modal-overlay[\s\S]*?align-items: flex-end/)
    // Docked to the VISIBLE bottom: lifted over the iOS keyboard, which never shrinks the
    // layout viewport (owner report: Rename in Documents sat behind the keyboard).
    expect(css).toMatch(
      /\.modal-overlay:not\(\.modal-overlay--workspace\)\s*\{[^}]*padding-bottom: calc\(\s*100dvh - var\(--visual-viewport-top, 0px\) - var\(--visual-viewport-height, 100dvh\)\s*\)/,
    )
    expect(css).toMatch(/\.ui-modal__footer[\s\S]*?env\(safe-area-inset-bottom\)/)
  })

  it('keeps long modal bodies vertically scrollable, natively (no touch-action opt-in)', () => {
    // No `touch-action` here: content scrolls natively now (see useSheetDragToDismiss's
    // top-of-file note) — only the sheet root/handles carry `touch-action: none`.
    expect(css).toMatch(
      /\.ui-modal__body\s*\{[\s\S]*?overflow:\s*auto[\s\S]*?-webkit-overflow-scrolling:\s*touch[\s\S]*?overscroll-behavior:\s*contain/,
    )
  })

  it('gives the legacy planning modal family the shared phone geometry once', () => {
    expect(planCss).toMatch(
      /@media \(max-width: 640px\)[\s\S]*?\.pl-ov[\s\S]*?align-items: flex-end[\s\S]*?\.pl-modal[\s\S]*?max-height: var\(--sheet-max-height\) !important/,
    )
    expect(planCss).toMatch(/\.pl-ov\s*\{[\s\S]*?z-index:\s*var\(--z-modal\)/)
    // No `touch-action` here: PlanningModal wires useSheetDragToDismiss on `.pl-modal` (the
    // sheet root) only, so `.pl-modal-body` scrolls natively (see the hook's top-of-file note).
    expect(planCss).toMatch(
      /\.pl-modal-body\s*\{[\s\S]*?flex:\s*1 1 auto[\s\S]*?overflow-y:\s*auto[\s\S]*?-webkit-overflow-scrolling:\s*touch[\s\S]*?overscroll-behavior:\s*contain/,
    )
  })

  it('removes modal motion when the user requests reduced motion', () => {
    expect(css).toMatch(
      /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.ui-modal-card[\s\S]*?animation: none/,
    )
  })
})
