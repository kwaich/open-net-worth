/**
 * Breakpoint codegen drift guard.
 *
 * The checked-in generated file (index.breakpoints.generated.css) MUST be
 * byte-identical to what the codegen produces from src/design/breakpoints.source.mjs.
 * If this test turns red, someone edited the generated file by hand, or edited
 * breakpoints.source.mjs without running `npm run build:tokens`.
 * Fix: run the codegen and commit the result.
 *
 * Also guards that the typed runtime constant (src/components/ui/breakpoints.ts)
 * stays in sync with the source of truth — so the "MUST match" comment in that
 * file is enforced by CI rather than convention.
 *
 * RFC: .claude/rfcs/phaseD-responsive.md (Phase D — D-1 drift guard)
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, it, expect } from 'vitest'
import { generateBreakpointsCss } from '../../scripts/build-tokens.mjs'
import { BP_SOURCE } from '../design/breakpoints.source.mjs'
import { BP } from '../components/ui/breakpoints'

describe('breakpoint codegen', () => {
  it('checked-in generated breakpoints CSS matches the source of truth', () => {
    const onDisk = readFileSync(resolve(__dirname, '../index.breakpoints.generated.css'), 'utf8')
    expect(onDisk).toBe(generateBreakpointsCss())
  })

  it('breakpoints.ts runtime constant matches breakpoints.source.mjs values', () => {
    // The four semantic names must equal the codegen source — no silent drift.
    expect(BP.sm).toBe(BP_SOURCE.sm)
    expect(BP.md).toBe(BP_SOURCE.md)
    expect(BP.lg).toBe(BP_SOURCE.lg)
    expect(BP.xl).toBe(BP_SOURCE.xl)
  })
})
