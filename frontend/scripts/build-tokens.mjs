#!/usr/bin/env node
// Generates the design-token artifacts from src/design/tokens.source.mjs:
//   • src/index.tokens.generated.css        — the :root { --* } block
//   • src/components/ui/tokens.generated.ts  — typed token unions + var() helpers
//
// Also generates the breakpoint @custom-media artifact from src/design/breakpoints.source.mjs:
//   • src/index.breakpoints.generated.css   — @custom-media definitions
//
// Run directly (`node scripts/build-tokens.mjs`), via `npm run build:tokens`, or
// automatically before `npm run build` (the `prebuild` lifecycle hook). The generated
// files are checked in; vitest drift guards fail CI if they diverge from their sources.
//
// RFC: .claude/rfcs/frontend-styling-pwa-ideal.md  (Phase A)
// RFC: .claude/rfcs/phaseD-responsive.md           (Phase D — D-1)

import { writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { tokenGroups } from '../src/design/tokens.source.mjs'
import { BP_SOURCE } from '../src/design/breakpoints.source.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const CSS_OUT = resolve(here, '../src/index.tokens.generated.css')
const TS_OUT = resolve(here, '../src/components/ui/tokens.generated.ts')
const BP_OUT = resolve(here, '../src/index.breakpoints.generated.css')

const WARN =
  'GENERATED — DO NOT EDIT. Source: src/design/tokens.source.mjs. Regenerate: npm run build:tokens'
const BP_WARN =
  'GENERATED — DO NOT EDIT. Source: src/design/breakpoints.source.mjs. Regenerate: npm run build:tokens'

/** Emit the dark-default `:root { … }` custom-property block wrapped in
 *  `@layer tokens`, followed by complete direct Warm and Light theme blocks.
 *  Tuple element three is Warm; element four is an optional Light override.
 *  Light inherits each missing value from Warm at generation time, never through
 *  a hidden runtime selector.
 *  Dark is the default theme — the first block is unchanged from the single-theme
 *  era except for the added `color-scheme`, so an install that never sets a theme
 *  renders exactly as before. `data-theme` is stamped on <html> pre-paint by the
 *  inline script in index.html and kept current by src/theme.ts (`system` resolves
 *  to a concrete named theme there, so CSS needs no media query).
 *  RFC Phase C-1: the generated artifact is placed in the `tokens` cascade layer
 *  so it participates in the declared layer order (reset, tokens, base, …, app). */
export function generateCss() {
  const lines = [`/* ${WARN} */`, '@layer tokens {', ':root {', '  color-scheme: dark;']
  tokenGroups.forEach((group, i) => {
    if (i > 0) lines.push('')
    lines.push(`  /* ${group.label} */`)
    for (const [name, value] of group.tokens) lines.push(`  --${name}: ${value};`)
  })
  lines.push('}', '')
  lines.push('/* Warm theme — every forked token, directly addressable */')
  lines.push(":root[data-theme='warm'] {", '  color-scheme: light;')
  for (const group of tokenGroups) {
    const forked = group.tokens.filter((t) => t.length > 2)
    if (!forked.length) continue
    lines.push(`  /* ${group.label} */`)
    for (const [name, , warm] of forked) lines.push(`  --${name}: ${warm};`)
  }
  lines.push('}', '')
  lines.push('/* Light theme — complete block; missing fourth values resolve from Warm */')
  lines.push(":root[data-theme='light'] {", '  color-scheme: light;')
  for (const group of tokenGroups) {
    const forked = group.tokens.filter((t) => t.length > 2)
    if (!forked.length) continue
    lines.push(`  /* ${group.label} */`)
    for (const [name, , warm, light] of forked) lines.push(`  --${name}: ${light ?? warm};`)
  }
  lines.push('}', '}')
  return lines.join('\n') + '\n'
}

const TYPE_NAME = {
  space: 'SpaceToken',
  text: 'TextToken',
  radius: 'RadiusToken',
  color: 'ColorToken',
  shadow: 'ShadowToken',
  z: 'ZToken',
}

/** Emit the typed token unions + `var()` helper functions. */
export function generateTs() {
  const byCategory = {}
  for (const group of tokenGroups) {
    byCategory[group.category] ??= []
    for (const [name] of group.tokens) byCategory[group.category].push(`--${name}`)
  }
  const union = (names) => names.map((n) => `  | '${n}'`).join('\n')
  const block = (jsdoc, type, category) =>
    `/** ${jsdoc} */\nexport type ${type} =\n${union(byCategory[category])}`

  return (
    `// ${WARN}\n\n` +
    block(
      'Spacing scale (`--space-0`…`--space-9`), touch target (`--tap-min`), and safe-area insets (`--safe-top`, `--safe-bottom`, `--safe-left`, `--safe-right`).',
      'SpaceToken',
      'space',
    ) +
    '\n\n' +
    block('Type scale (`--text-2xs`…`--text-2xl`).', 'TextToken', 'text') +
    '\n\n' +
    block('Radius scale (`--radius-xs`…`--radius-pill`).', 'RadiusToken', 'radius') +
    '\n\n' +
    block('Semantic color tokens.', 'ColorToken', 'color') +
    '\n\n' +
    block('Elevation / shadow tokens.', 'ShadowToken', 'shadow') +
    '\n\n' +
    block('Z-index scale (`--z-base`…`--z-toast`).', 'ZToken', 'z') +
    '\n\n' +
    'export type DesignToken = SpaceToken | TextToken | RadiusToken | ColorToken | ShadowToken | ZToken\n\n' +
    '/** Resolve a spacing token to its `var(--space-n)` expression. */\n' +
    'export function spaceVar(token: SpaceToken): string {\n  return `var(${token})`\n}\n\n' +
    '/** Resolve any design token to its `var(--token)` expression. */\n' +
    'export function tokenVar(token: DesignToken): string {\n  return `var(${token})`\n}\n'
  )
}

/** Emit @custom-media definitions for the 4-step breakpoint ladder.
 *  Includes mobile-first (min-width) names + desktop-first parity aliases (below-*).
 *  RFC Phase D-1 §2.2. */
export function generateBreakpointsCss() {
  const { sm, md, lg, xl } = BP_SOURCE
  // .98px upper bounds prevent the classic overlap at integer boundaries:
  // a value at exactly 640px belongs to --sm, not both --sm and --below-sm.
  return (
    [
      `/* ${BP_WARN} */`,
      `@custom-media --sm (min-width: ${sm}px);`,
      `@custom-media --md (min-width: ${md}px);`,
      `@custom-media --lg (min-width: ${lg}px);`,
      `@custom-media --xl (min-width: ${xl}px);`,
      '',
      '/* Desktop-first parity aliases used by the D-2 mechanical rename (range syntax) */',
      `@custom-media --below-sm (max-width: ${sm - 0.02}px);`,
      `@custom-media --below-md (max-width: ${md - 0.02}px);`,
      `@custom-media --below-lg (max-width: ${lg - 0.02}px);`,
      `@custom-media --sm-only (min-width: ${sm}px) and (max-width: ${lg - 0.02}px);`,
    ].join('\n') + '\n'
  )
}

function main() {
  writeFileSync(CSS_OUT, generateCss())
  writeFileSync(TS_OUT, generateTs())
  writeFileSync(BP_OUT, generateBreakpointsCss())
  // eslint-disable-next-line no-console
  console.log(
    'build-tokens: wrote index.tokens.generated.css + tokens.generated.ts + index.breakpoints.generated.css',
  )
}

// Only write when run as a script — importing for the drift test must be side-effect-free.
if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) main()
