// The collapsed thinking preview is a raw string in a clamped span — only the EXPANDED body
// goes through ReactMarkdown. A Codex summary arrives wrapped as `**Planning onboarding
// profile saving**`, so the reader saw the asterisks (owner report, 2026-08-24).

import { describe, expect, it } from 'vitest'
import { previewText } from './toolActivity/previewText'

describe('previewText', () => {
  it('unwraps the bold a Codex summary always arrives in', () => {
    expect(previewText('**Planning onboarding profile saving**')).toBe(
      'Planning onboarding profile saving',
    )
    expect(previewText('**Preparing to update profile**')).toBe('Preparing to update profile')
  })

  it('flattens to ONE line, so the two-line clamp is not spent on blank space', () => {
    expect(previewText('**Title**\n\nThen the body.')).toBe('Title Then the body.')
  })

  it('strips headings, inline code and single-mark emphasis', () => {
    expect(previewText('## Heading')).toBe('Heading')
    expect(previewText('call `get_transactions` first')).toBe('call get_transactions first')
    expect(previewText('this is *important* here')).toBe('this is important here')
    expect(previewText('an _emphasised_ word')).toBe('an emphasised word')
  })

  it('leaves a stray asterisk alone — a preview should not invent structure', () => {
    expect(previewText('2 * 3 = 6')).toBe('2 * 3 = 6')
    expect(previewText('a snake_case_name stays whole')).toBe('a snake_case_name stays whole')
  })

  it('handles the empty and the unmarked case unchanged', () => {
    expect(previewText('')).toBe('')
    expect(previewText('Just plain reasoning.')).toBe('Just plain reasoning.')
  })
})
