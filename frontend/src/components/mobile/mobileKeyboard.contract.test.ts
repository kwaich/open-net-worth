// The keyboard is the BROWSER's job.
//
// `interactive-widget=resizes-content` makes the software keyboard shrink the layout
// viewport, so `100dvh` and `position: fixed; bottom: 0` stay truthful on their own. The only
// thing left for us is a preference — while you are typing, the navigation gets out of the
// way — and focus expresses that exactly, in CSS.
//
// This replaced a JS hook that tried to infer the keyboard from `visualViewport` deltas. It
// took six failed attempts to accept that it could not work: engines disagree about what a
// keyboard shrinks, and the hook needed three invented constants (a 120px threshold, a 400ms
// settle, a 350ms assume) that were wrong in a different way each round. None of those
// numbers described anything real.
//
// These tests exist to stop that machinery growing back.

import { existsSync, readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const indexCss = readFileSync(new URL('../../index.css', import.meta.url), 'utf8')
const indexHtml = readFileSync(new URL('../../../index.html', import.meta.url), 'utf8')

describe('mobile keyboard ↔ tab bar contract', () => {
  it('asks the browser to resize the layout viewport for the keyboard', () => {
    expect(indexHtml).toMatch(/name="viewport"[^>]*interactive-widget=resizes-content/)
  })

  it('hides the tab bar from focus, in CSS, with no JavaScript involved', () => {
    const hiders = [
      ...indexCss.matchAll(/([^{}]*\.mb-tabbar)\s*\{[^{}]*display:\s*none[^{}]*\}/g),
    ].map((m) => m[1]!.replace(/\/\*[\s\S]*?\*\//g, '').trim())

    expect(hiders).toHaveLength(1)
    expect(hiders[0]).toContain(':focus')
    expect(hiders[0]).toContain('textarea')
    expect(hiders[0]).toContain("[contenteditable='true']")
  })

  it("releases the bar's reserved height at the same time, so no dead band is left", () => {
    const zeroers = [...indexCss.matchAll(/([^{}]*)\{[^{}]*--mb-tabbar-h:\s*0px[^{}]*\}/g)].map(
      (m) => m[1]!.replace(/\/\*[\s\S]*?\*\//g, '').trim(),
    )

    expect(zeroers).toHaveLength(1)
    expect(zeroers[0]).toContain(':focus')
  })

  it('excludes the controls that raise no keyboard', () => {
    // <select> opens a picker; file/checkbox/radio/range/color/button/submit raise nothing.
    //
    // Whitespace is collapsed first. The assertion is about WHICH types are excluded, and the
    // selector holding them is long enough that Prettier wraps it wherever the surrounding
    // lines happen to push it — `:not(\n  [type='submit']\n)` is the same contract and used to
    // fail this test for a formatting reason that says nothing about the keyboard.
    const flat = indexCss.replace(/\s+/g, '')
    for (const type of ['checkbox', 'radio', 'range', 'color', 'submit', 'button', 'file']) {
      expect(flat).toContain(`:not([type='${type}'])`)
    }
    expect(indexCss).not.toMatch(/\.mb-shell:has\(\s*select:focus/)
  })

  // The whole point. If this file comes back, so do the magic numbers.
  it('has no keyboard-measuring hook at all', () => {
    expect(existsSync(new URL('../../hooks/useMobileKeyboardLayout.ts', import.meta.url))).toBe(
      false,
    )
    expect(indexCss).not.toContain('mobile-keyboard-open')
    expect(indexCss).not.toContain('mobile-keyboard-measured')
    expect(indexCss).not.toContain('--mobile-keyboard-inset')
  })

  // It DID come back once, through CSS rather than through the hook file: a mobile-search
  // change added `--mobile-visual-viewport-top/height` and pointed the command palette at
  // them. Nothing set them once the hook was gone, so the palette silently fell back to its
  // defaults. Those names stay banned.
  //
  // What IS allowed, and is what the palette uses now, is `--visual-viewport-*`: a plain
  // readout of `visualViewport.height/offsetTop` from useVisualViewportVars. The line is
  // between REPORTING and INFERRING. Reporting two numbers the browser already knows has
  // nothing to be wrong about. Inferring "is a keyboard open" from deltas needs thresholds
  // and timers, and that is what failed six times.
  //
  // A full-screen FIXED overlay genuinely needs the measurement: iOS does not shrink the
  // layout viewport for the keyboard, so `100dvh` runs underneath it. The in-flow tab bar
  // needs nothing, and must not acquire it.
  it('reads no visual-viewport measurement from CSS either', () => {
    // Comments are stripped first (as the tab-bar assertions above do): prose explaining WHY a
    // property is not used is documentation, not a read, and banning the words would delete
    // the one place the reasoning is written down.
    const declarations = indexCss.replace(/\/\*[\s\S]*?\*\//g, '')
    expect(declarations).not.toContain('--mobile-visual-viewport-top')
    expect(declarations).not.toContain('--mobile-visual-viewport-height')
    expect(declarations).not.toContain('--mobile-visual-viewport-bottom')

    // The permitted measurement is scoped to the full-screen overlay that cannot live
    // without it. If the tab bar ever reads it, the machinery is growing back.
    const tabbarRules = [...declarations.matchAll(/([^{}]*\.mb-tabbar[^{}]*)\{([^{}]*)\}/g)]
    for (const [, , body] of tabbarRules) {
      expect(body).not.toContain('--visual-viewport')
    }
  })
})
