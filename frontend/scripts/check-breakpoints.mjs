#!/usr/bin/env node
/**
 * check-breakpoints.mjs
 *
 * Breakpoint convergence gate (Phase D-1).
 *
 * Scans all CSS source files for literal-px @media width conditions and fails
 * on any NEW raw pixel boundary that is not one of the 4 ladder values
 * (640 / 768 / 1024 / 1280) or their .98px parity forms. Every new breakpoint
 * must go through an @custom-media token from index.breakpoints.generated.css.
 *
 * The ALLOWLIST seeds the current 13 legacy boundaries so the gate is GREEN
 * on day one; each Phase D-2/D-4 PR removes entries as it converts them.
 * Allowlist length = remaining breakpoint debt (measurable convergence).
 *
 * Usage:
 *   node scripts/check-breakpoints.mjs          # exits 0 if only allowlisted literals remain
 *   npm run lint:breakpoints
 *
 * RFC: .claude/rfcs/phaseD-responsive.md  §2.3
 */

import { readFileSync, readdirSync } from 'fs'
import { join, relative } from 'path'
import { fileURLToPath } from 'url'

const ROOT = fileURLToPath(new URL('../../', import.meta.url))
const SRC_DIR = join(ROOT, 'frontend/src')

const CSS_FILES = [
  join(SRC_DIR, 'index.css'),
  join(SRC_DIR, 'chat.css'),
  join(SRC_DIR, 'components/skills/skills.css'),
  // Settings overlay redesign. Registered so the ported sheet cannot reintroduce the
  // prototype's own boundaries (900/700), which put the dialog out of step with the shell —
  // a phone in LANDSCAPE (844-956px) fell past 900 and rendered as a centred popup.
  join(SRC_DIR, 'components/tabs/settings/settings.css'),
]

// ── 4-step canonical ladder + .98px parity forms ────────────────────────────
// These are ALLOWED anywhere: they are the target state.
const LADDER_PX = new Set([
  '640px', '768px', '1024px', '1280px',
  '639.98px', '767.98px', '1023.98px',
])

// ── ALLOWLIST ────────────────────────────────────────────────────────────────
// Legacy literal-px boundaries seeded from the Phase D-1 census (§1.1).
// Entries are removed one-by-one as D-2/D-4 PRs convert them to --tokens.
// Format: 'Npx' — the exact string matched by the scan regex.
//
// 720/760 → round UP to --md (767.98px) per §1.2 default recommendation;
// they remain in the allowlist until D-2 screenshots confirm the round is safe.
const ALLOWLIST = new Set([
  '360px',   // chat.css:1449 — composer-content narrow tweak (convert in D-2)
  '420px',   // index.css:923 — below-sm group (convert in D-2)
  '480px',   // index.css:793, chat.css:16 — below-sm group (convert in D-2)
  '560px',   // index.css:12080 — below-sm group (convert in D-2)
  '639px',   // index.css:2437, skills.css:704 — below-sm parity (convert in D-2)
  '640px',   // index.css (31 sites) — the dominant phone bp (convert in D-2)
  '641px',   // index.css:12838 (combined min-width:641px) — sm-only (convert in D-2)
  '720px',   // index.css:652,4460,5942,5951,14374 — round to --md in D-2
  '760px',   // index.css:12065,12350 — round to --md in D-2
  '900px',   // index.css:4029,4443,13540 — round to --lg in D-2
  '1020px',  // index.css:15550 — round to --lg in D-2
  '1023px',  // index.css:12838, skills.css:752 — below-lg parity (convert in D-2)
  '1024px',  // App.css — App.css deleted; any straggler converts in D-2
])

// ── Regex: @media conditions containing a literal px width ──────────────────
// Matches both (max-width: Npx) and (min-width: Npx) forms.
const MEDIA_PX_RE = /@media[^{]*\(\s*(?:max|min)-width\s*:\s*([0-9]+(?:\.[0-9]+)?px)/g

let errors = 0
let allowlistedCount = 0

for (const cssFile of CSS_FILES) {
  let content
  try {
    content = readFileSync(cssFile, 'utf8')
  } catch {
    continue
  }
  const rel = relative(ROOT, cssFile)
  MEDIA_PX_RE.lastIndex = 0
  let m
  const lines = content.split('\n')
  while ((m = MEDIA_PX_RE.exec(content)) !== null) {
    const px = m[1]
    if (LADDER_PX.has(px)) continue // correct usage of a ladder value
    // Find line number for the match
    const linesBefore = content.slice(0, m.index).split('\n').length
    if (ALLOWLIST.has(px)) {
      allowlistedCount++
      continue
    }
    // New literal-px not in ladder and not in allowlist → FAIL
    console.error(`[check-breakpoints] NEW raw breakpoint ${px} at ${rel}:${linesBefore}`)
    console.error(`  → Use an @custom-media token (--sm/--md/--lg/--xl or --below-* aliases)`)
    console.error(`    or add to ALLOWLIST with a justification comment if it is transitional.`)
    errors++
  }
}

if (allowlistedCount > 0) {
  console.log(`[check-breakpoints] ${allowlistedCount} legacy literal-px breakpoints remain in ALLOWLIST (Phase D debt; convert in D-2/D-4).`)
}

if (errors > 0) {
  console.error(`[check-breakpoints] ${errors} new raw breakpoint(s) found — FAIL`)
  process.exit(1)
}

console.log('[check-breakpoints] OK')
