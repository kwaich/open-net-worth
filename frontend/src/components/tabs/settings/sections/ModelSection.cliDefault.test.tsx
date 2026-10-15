// @vitest-environment jsdom
//
// The Settings default-model picker tags the row a CLI would run on its own, so choosing the
// CLI's real default is a recognised option rather than one id among many.

import { describe, it, expect } from 'vitest'
import { cliDefaultNote } from './ModelSection'

const defaults = { codex_cli: 'synthetic-default', claude_cli: 'opus' }

describe('cliDefaultNote', () => {
  it('names the Codex default and the Claude Code default', () => {
    expect(cliDefaultNote({ id: 'synthetic-default', provider_key: 'codex_cli' }, defaults)).toBe(
      'Default in Codex',
    )
    expect(cliDefaultNote({ id: 'opus', provider_key: 'claude_cli' }, defaults)).toBe(
      'Claude Code default',
    )
  })

  it('leaves every other row untagged', () => {
    expect(
      cliDefaultNote({ id: 'gpt-5.6-sol', provider_key: 'codex_cli' }, defaults),
    ).toBeUndefined()
    expect(cliDefaultNote({ id: 'opus', provider_key: 'anthropic' }, defaults)).toBeUndefined()
    expect(cliDefaultNote({ id: 'opus', provider_key: 'claude_cli' }, undefined)).toBeUndefined()
  })
})
