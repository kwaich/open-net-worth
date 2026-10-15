import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(resolve(__dirname, '../../../lucius.css'), 'utf8')

describe('Lucius side-panel surface', () => {
  it('keeps the dark panel behind the raised composer while preserving bright-theme elevation', () => {
    expect(css).toMatch(
      /\.lx-side\s*\{[\s\S]*?--lx-panel-bg:\s*var\(--bg\);[\s\S]*?background:\s*var\(--lx-panel-bg\);/,
    )
    // The bright themes no longer override the panel colour at all — it is var(--bg)
    // everywhere, and the composer earns its separation from --panel's lift.
    expect(css).not.toMatch(/\[data-theme='light'\]\) \.lx-side\s*\{\s*--lx-panel-bg/)
    // The composer's own glass (tint + blur) lives on .lx-side-foot::before, NOT a flat
    // background on .lx-side-foot itself — a backdrop-filter placed directly on
    // .lx-side-foot would wash out .lx-inputblock (its opaque composer card child) along
    // with it. See lucius.css's own comment beside .lx-side-foot::before for the full
    // reasoning; this assertion was written before that split and never updated to match.
    // The tint is a vertical gradient of the panel colour: translucent under the top fade,
    // opaque by the bottom so the transcript's last line can't show beneath the card.
    expect(css).toMatch(
      /\.lx-side-foot::before\s*\{[\s\S]*?background:\s*linear-gradient\(\s*to bottom,\s*color-mix\(in srgb, var\(--lx-panel-bg\) 55%, transparent\),[\s\S]*?var\(--lx-panel-bg\)\s*\);/,
    )
  })

  it('reserves the floating composer inside the scrollable transcript content', () => {
    expect(css).toMatch(
      /\.lx-side-scroll-inner\s*\{[^}]*padding-bottom:\s*var\(--lx-foot-h, 48px\);/,
    )
  })

  // The side inset is exactly 16px in BOTH scrollbar modes: `stable both-edges` reserves a
  // ~10px track per side under classic scrollbars and nothing under macOS overlay ones, so
  // ChatPanel measures the real reservation into --lx-gutter and the padding subtracts it.
  // A fixed "desktop" padding that assumed the track was there shipped a 5px inset to every
  // trackpad Mac (owner report, 2026-09-15) — no static override may ever come back.
  it('subtracts the measured scrollbar gutter from a 16px inset on transcript and composer', () => {
    const inset = 'calc\\(16px - var\\(--lx-gutter, 0px\\)\\)'
    expect(css).toMatch(new RegExp(`\\.lx-side-scroll\\s*\\{[^}]*padding:\\s*16px ${inset} 0;`))
    expect(css).toMatch(new RegExp(`\\.lx-side-foot\\s*\\{[^}]*padding:\\s*0 ${inset} 12px;`))
    expect(css).toMatch(/\.lx-side-scroll\s*\{[^}]*scrollbar-gutter:\s*stable both-edges;/)
    expect(css).toMatch(/\.lx-side-foot\s*\{[^}]*scrollbar-gutter:\s*stable both-edges;/)
    expect(css).not.toMatch(/\.lx-side-(?:scroll|foot)\s*\{[^}]*padding:[^;]*\b5px\b/)
  })

  // A narrow panel could be dragged sideways (owner report, 2026-09-15, ~330px): the
  // side-panel follow-up chips are `flex: none` buttons, so one longer than the column
  // ("Yes, and draft the StanChart quote request") spilled out of its wrapping row into
  // the transcript's scrollable overflow. The chip must shrink and wrap instead — and the
  // transcript itself can never scroll on the x axis, whatever content lands in it.
  it('never lets the transcript pan sideways: chips wrap, overflow-x is hidden', () => {
    expect(css).toMatch(/\.lx-side-scroll\s*\{[^}]*overflow-x:\s*hidden;/)
    expect(css).toMatch(/\.lx-side-foot\s*\{[^}]*overflow:\s*hidden;/)
    expect(css).toMatch(
      /\.lx-side-followups button\s*\{[^}]*flex:\s*0 1 auto;[^}]*min-width:\s*0;[^}]*white-space:\s*normal;/,
    )
    // A mention chip inside a sent bubble shrinks to the bubble instead of the base .chip's
    // fixed 240px cap — the other reported spiller on a ~300px panel.
    expect(css).toMatch(/\.msg-text \.chip\s*\{[^}]*max-width:\s*100%;/)
  })
})
