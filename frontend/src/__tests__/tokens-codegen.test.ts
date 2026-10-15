/**
 * Design-token codegen drift guard.
 *
 * The two checked-in generated artifacts (index.tokens.generated.css and
 * components/ui/tokens.generated.ts) MUST be byte-identical to what the codegen
 * produces from src/design/tokens.source.mjs. If this test turns red, someone
 * edited a generated file by hand, or edited tokens.source.mjs without running
 * `npm run build:tokens`. Fix: run the codegen and commit the result.
 *
 * RFC: .claude/rfcs/frontend-styling-pwa-ideal.md (Phase A — token pipeline)
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, it, expect } from 'vitest'
import { generateCss, generateTs } from '../../scripts/build-tokens.mjs'

describe('design token codegen', () => {
  it('checked-in generated CSS matches the source of truth', () => {
    const onDisk = readFileSync(resolve(__dirname, '../index.tokens.generated.css'), 'utf8')
    expect(onDisk).toBe(generateCss())
  })

  it('checked-in generated TS matches the source of truth', () => {
    const onDisk = readFileSync(resolve(__dirname, '../components/ui/tokens.generated.ts'), 'utf8')
    expect(onDisk).toBe(generateTs())
  })
})
