// @vitest-environment node

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const css = readFileSync(fileURLToPath(new URL('../index.css', import.meta.url)), 'utf8')

describe('CommandPalette responsive surface contract', () => {
  it('turns only the phone palette into a layout-viewport-sized full-screen surface', () => {
    // Two halves that must NOT drift apart:
    //
    //   the backdrop (overlay + card) stays a full 100dvh, so the content behind the keyboard
    //   stays covered — the owner asked for this explicitly;
    //   the .cmdk grid (search bar + result list) is sized to the VISIBLE area, so the list's
    //   last rows can actually be scrolled to.
    //
    // Sizing the backdrop itself to the visible area (an earlier attempt) satisfied the second
    // and broke the first. Sizing neither — `top: 0; height: 100dvh` throughout, on the belief
    // that `interactive-widget=resizes-content` shrinks the layout viewport — broke the second
    // on iOS, where that meta value is unimplemented (owner, 2026-07-31, with a screenshot of
    // the account list cut off).
    expect(css).toMatch(
      /@media \(max-width: 640px\)[\s\S]*?\.modal-overlay:has\(\.cmdk\)\s*\{[^}]*top:\s*0;[^}]*height:\s*100dvh;[^}]*overflow:\s*hidden;/,
    )
    // The grid is positioned against the card, not flowed inside it: intervening padding would
    // otherwise push it down and carry its bottom edge back under the keyboard.
    expect(css).toMatch(
      /@media \(max-width: 640px\)[\s\S]*?\.cmdk\s*\{[\s\S]*?position:\s*absolute;[\s\S]*?top:\s*var\(--visual-viewport-top, 0px\);[\s\S]*?height:\s*var\(--visual-viewport-height, 100dvh\);/,
    )
    expect(css).toMatch(
      /@media \(max-width: 640px\)[\s\S]*?\.ui-modal-card:has\(\.cmdk\)\s*\{[^}]*height:\s*100%;[^}]*max-height:\s*none;[^}]*margin-top:\s*0;[^}]*border-radius:\s*0;/,
    )
  })

  it('keeps the search header fixed while results alone own touch scrolling', () => {
    expect(css).toMatch(
      // Header row auto, results row takes the rest. The grid's own height is the visible
      // area (asserted above) rather than 100% — otherwise the results row extends under the
      // keyboard and its last entries cannot be reached.
      /@media \(max-width: 640px\)[\s\S]*?\.cmdk\s*\{[^}]*display:\s*grid;[^}]*grid-template-rows:\s*auto minmax\(0,\s*1fr\);/,
    )
    expect(css).toMatch(
      /\.cmdk-results\s*\{[^}]*overflow-y:\s*auto;[^}]*overscroll-behavior:\s*contain;[^}]*-webkit-overflow-scrolling:\s*touch;/,
    )
  })

  it('keeps the desktop command palette top-anchored and its keyboard footer visible', () => {
    expect(css).toMatch(
      /\.ui-modal-card:has\(\.cmdk\)\s*\{[^}]*align-self:\s*flex-start;[^}]*margin-top:\s*8vh;/,
    )
    expect(css).not.toMatch(
      /@media \(min-width: 641px\)[\s\S]*?\.cmdk-footer\s*\{[^}]*display:\s*none/,
    )
  })
})
