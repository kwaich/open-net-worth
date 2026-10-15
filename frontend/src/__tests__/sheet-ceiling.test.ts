// Every phone bottom sheet stops at ONE ceiling — `--sheet-max-height`, a fixed gap under the
// status bar (iOS's large-detent rule) — never at its own share of the viewport. jsdom cannot
// compute stylesheet geometry, so this guards the CSS text: a sheet root that grows back a
// `92dvh` / `100dvh` of its own is the regression this exists to catch (the Manage payees
// sheet reached the very top of the phone screen that way).
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const read = (rel: string) => readFileSync(resolve(__dirname, rel), 'utf8')

const SHEET_ROOTS: Array<{ file: string; selector: string }> = [
  { file: '../index.css', selector: '.ui-modal-card:not(.ui-modal-card--workspace)' },
  { file: '../components/tabs/plan/plan.css', selector: '.pl-modal' },
  { file: '../components/tabs/accounts/accounts.css', selector: '.acct-detail.sheet' },
  { file: '../components/tabs/accounts/accounts.css', selector: '.ui-modal-card:has(.pm-body)' },
  { file: '../components/tabs/accounts/accounts.css', selector: '.acct-side-sheet.ui-modal-card' },
  {
    file: '../components/tabs/settings/settings.css',
    selector: '.ui-modal-card--workspace.onw-settings',
  },
  { file: '../index.css', selector: '.ui-modal-card--workspace.asd-chart-fs-modal' },
  { file: '../index.css', selector: '.mb-sheet' },
]

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

const PHONE_MARKERS = ['@media (max-width: 640px) {', '@media (max-width: 639.98px) {']

/** The contents of every phone-tier media block — where sheets exist. Desktop rules for the
 * same selectors (a centered 92vh workspace, say) are not sheets. */
function phoneTier(css: string): string {
  let out = ''
  for (const marker of PHONE_MARKERS) {
    for (let at = css.indexOf(marker); at !== -1; at = css.indexOf(marker, at + marker.length)) {
      let depth = 1
      let i = at + marker.length
      while (i < css.length && depth > 0) {
        if (css[i] === '{') depth += 1
        else if (css[i] === '}') depth -= 1
        i += 1
      }
      out += css.slice(at + marker.length, i - 1) + '\n'
    }
  }
  return out
}

/** Bodies of every phone-tier rule whose selector list is exactly `selector`. */
function ruleBodies(css: string, selector: string): string[] {
  const re = new RegExp(`(?:^|[}\\n])\\s*${escape(selector)}\\s*\\{([^}]*)\\}`, 'g')
  return Array.from(phoneTier(css).matchAll(re), (m) => m[1] ?? '')
}

describe('phone sheet ceiling', () => {
  it('defines the ceiling from the status-bar inset, not a viewport share', () => {
    const tokens = read('../index.tokens.generated.css')
    expect(tokens).toMatch(/--sheet-top-inset: calc\(env\(safe-area-inset-top, 0px\) \+ \d+px\);/)
    expect(tokens).toMatch(/--sheet-max-height: calc\(100dvh - var\(--sheet-top-inset\)\);/)
  })

  it('its keyboard-aware form never exceeds that ceiling', () => {
    // A field in a sheet raises the iOS keyboard; the sheet then fits what is VISIBLE above it
    // (owner report: Rename showed only the keyboard). min() — so never taller than the ceiling.
    expect(read('../index.css')).toMatch(
      /--sheet-visible-max-height:\s*min\(\s*var\(--sheet-max-height\),\s*calc\(var\(--visual-viewport-height, 100dvh\) - var\(--sheet-top-inset\)\)\s*\)/,
    )
  })

  it.each(SHEET_ROOTS)('$selector caps itself with the shared token', ({ file, selector }) => {
    const bodies = ruleBodies(read(file), selector)
    expect(bodies.length).toBeGreaterThan(0)
    expect(bodies.some((b) => /max-height:\s*var\(--sheet-(?:visible-)?max-height\)/.test(b))).toBe(
      true,
    )
    // Any viewport unit in a root's own height — `92dvh`, `calc(100vh - 48px)`, `88svh` — is the
    // regression, whether it replaces the token or is added beside it.
    for (const body of bodies) {
      expect(body).not.toMatch(/(?:^|[\s;])(?:max-)?height:[^;]*\d(?:dvh|svh|vh)\b/)
    }
  })

  it('Manage payees no longer pads its header under the status bar (the ceiling handles it)', () => {
    const css = read('../components/tabs/accounts/accounts.css')
    const heads = ruleBodies(css, '.ui-modal-card:has(.pm-body) .ui-modal__head')
    expect(heads.length).toBeGreaterThan(0)
    for (const body of heads) expect(body).not.toContain('safe-area-inset-top')
  })
})
