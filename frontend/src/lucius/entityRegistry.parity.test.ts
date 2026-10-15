// entityRegistry.parity.test.ts — render-identical guarantee.
//
// Asserts that the registered descriptors — the backend's own, seeded in tests from
// entityTypes.fixture.json (pinned to the backend by tests/test_entity_types_fixture.py) —
// produce the SAME icon, label, and color output as the original TYPE_LABEL /
// MENTION_ICONS / TYPE_COLOR maps they replaced. Zero-visual-change, by contract.
//
// When this test is green, it is safe to ship entityRegistry.ts as the single source
// for icon/label/tone across all surfaces (chips, autocomplete, detail popover).

import { describe, it, expect } from 'vitest'
import { allDescriptors, iconFor, labelFor, toneFor } from './entityRegistry'
import { SEMANTIC, TYPE_COLOR } from './mentionColors'

// ── Today's maps (verbatim copies, deleted from their source files by the migration) ─

// Icons that were in types/mentions.ts MENTION_ICONS before the migration.
const LEGACY_MENTION_ICONS: Record<string, string> = {
  net_worth: '▲',
  // DELIBERATE divergence from the legacy pin (owner, 2026-08-23): holding/instrument and
  // category/budget_month each shared one glyph, so the two types were indistinguishable in
  // the picker and on a chip. Split; tests/test_mention_type_frontend_coverage.py now fails
  // on ANY duplicate icon, which is what keeps this honest going forward.
  holding: '◆',
  instrument: '◈',
  account: '⬡',
  transaction: '↕',
  category: '▣',
  budget_month: '◱',
  target: '◎',
  allocation_slice: '◭',
  returns: '↗',
  payee: '◉',
  watchlist: '☆',
  automation: '◷',
  skill: '⚡',
}

// Labels that were in TYPE_LABEL maps in MentionDetail.tsx, Chip.tsx, Autocomplete.tsx,
// and matchEntity.ts. All four maps were identical.
const LEGACY_TYPE_LABEL: Record<string, string> = {
  net_worth: 'Net Worth',
  holding: 'Holding',
  instrument: 'Security',
  account: 'Account',
  transaction: 'Transaction',
  category: 'Category',
  budget_month: 'Budget Month',
  target: 'Target',
  allocation_slice: 'Allocation',
  returns: 'Returns',
  payee: 'Payee',
  watchlist: 'Watchlist',
  automation: 'Automation',
  skill: 'Skill',
}

// ── Parity checks ────────────────────────────────────────────────────────────

describe('entity registry — render-identical parity', () => {
  // All current core types are present in the bundled registry.
  it('all current core types have a registered descriptor', () => {
    const registeredTypes = new Set(allDescriptors().map((d) => d.type))
    for (const type of Object.keys(LEGACY_MENTION_ICONS)) {
      expect(registeredTypes.has(type), `"${type}" must be a registered descriptor`).toBe(true)
    }
  })

  // iconFor() == MENTION_ICONS for every type.
  it('iconFor matches the expected core icons', () => {
    for (const [type, expectedIcon] of Object.entries(LEGACY_MENTION_ICONS)) {
      expect(iconFor(type), `icon mismatch for "${type}"`).toBe(expectedIcon)
    }
  })

  // labelFor() == TYPE_LABEL for every type.
  it('labelFor matches the expected core labels', () => {
    for (const [type, expectedLabel] of Object.entries(LEGACY_TYPE_LABEL)) {
      expect(labelFor(type), `label mismatch for "${type}"`).toBe(expectedLabel)
    }
  })

  // toneFor() maps to a color that matches TYPE_COLOR for every type.
  // colorFor() precedence: dot → SEMANTIC[tone] → TYPE_COLOR[type] → neutral.
  //
  // The parity guarantee requires two sub-cases:
  //
  // (A) Non-neutral tone: SEMANTIC[toneFor(type)] must equal TYPE_COLOR[type].
  //     If they diverge, a caller passing {tone: toneFor(type)} to colorFor() would
  //     produce a different color than a caller passing {type} alone.
  //
  // (B) Neutral tone: TYPE_COLOR[type] must equal SEMANTIC.neutral.
  //     If they diverge, a caller passing
  //     {tone: 'neutral'} to colorFor() would silently change that type's color.
  //     Any type with a custom hex that differs from SEMANTIC.neutral MUST have its
  //     own named tone (not 'neutral') so the divergence is explicit and testable.
  //
  // In production, toneFor() has zero call sites that pass its result to colorFor()
  // directly — colorFor() is always called with {type, dot} only.  This test exists
  // to catch any future regression if that invariant is violated.
  it('toneFor produces colors consistent with legacy TYPE_COLOR', () => {
    const SEMANTIC_MAP: Record<string, string> = SEMANTIC
    const TYPE_COLOR_MAP: Record<string, string> = TYPE_COLOR

    for (const [type] of Object.entries(LEGACY_MENTION_ICONS)) {
      const tone = toneFor(type)
      const toneColor = SEMANTIC_MAP[tone]
      const typeColor = TYPE_COLOR_MAP[type]

      if (!typeColor) continue // type not in TYPE_COLOR — skip

      if (tone !== 'neutral') {
        // Case A: non-neutral tone.  SEMANTIC[tone] must be defined AND equal TYPE_COLOR[type].
        expect(toneColor, `SEMANTIC["${tone}"] should be defined for type "${type}"`).toBeDefined()
        expect(toneColor, `SEMANTIC["${tone}"] should equal TYPE_COLOR["${type}"]`).toBe(typeColor)
      } else {
        // Case B: neutral tone.  TYPE_COLOR[type] must equal SEMANTIC.neutral.
        // Any type whose custom hex differs from SEMANTIC.neutral MUST have a named tone key
        // (not 'neutral') — otherwise colorFor({tone:'neutral'}) would silently change its color.
        expect(
          typeColor,
          `TYPE_COLOR["${type}"] (${typeColor}) must equal SEMANTIC.neutral (${SEMANTIC.neutral}) ` +
            `when tone is 'neutral'.  If this type has a custom hex, give it a dedicated tone key.`,
        ).toBe(SEMANTIC.neutral)
      }
    }
  })

  // No unknown tones (all tones must be in SEMANTIC keys or 'neutral').
  it('all descriptor tones are valid SEMANTIC keys', () => {
    const validTones = new Set(Object.keys(SEMANTIC))
    for (const d of allDescriptors()) {
      expect(
        validTones.has(d.tone),
        `tone "${d.tone}" for type "${d.type}" is not in SEMANTIC`,
      ).toBe(true)
    }
  })
})

// ── Fallback behaviour ────────────────────────────────────────────────────────

describe('entityRegistry — fallback for unknown types', () => {
  it('iconFor returns ◆ for unknown type', () => {
    expect(iconFor('totally_unknown_future_type')).toBe('◆')
  })

  it('labelFor returns the raw type string for unknown type', () => {
    expect(labelFor('my_custom_entity')).toBe('my_custom_entity')
  })

  it('toneFor returns "neutral" for unknown type', () => {
    expect(toneFor('unknown_type')).toBe('neutral')
  })
})
