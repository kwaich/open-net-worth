// @vitest-environment jsdom
//
// Tests for the P2 slash trigger integration:
//   - detectSlashToken (exported from ChatPanel)
//   - pendingSkills state in chatComposer (ordered plural selection + reset)

import { describe, it, expect, afterEach } from 'vitest'
import { act, cleanup, render } from '@testing-library/react'

import { detectSlashToken } from './ChatPanel'
import { ChatComposerProvider, useChatComposer } from '../../state/chatComposer'

afterEach(cleanup)

// ── detectSlashToken ──────────────────────────────────────────────────────────

describe('detectSlashToken', () => {
  it('returns null for an empty string', () => {
    expect(detectSlashToken('', 0)).toBeNull()
  })

  it('detects "/" at position 0 (start of string)', () => {
    const val = '/budget'
    const result = detectSlashToken(val, val.length)
    expect(result).not.toBeNull()
    expect(result!.slashPos).toBe(0)
    expect(result!.query).toBe('budget')
  })

  it('detects "/" after whitespace', () => {
    const val = 'run /budget'
    const result = detectSlashToken(val, val.length)
    expect(result).not.toBeNull()
    expect(result!.query).toBe('budget')
  })

  it('detects "/" after newline', () => {
    const val = 'hello\n/close'
    const result = detectSlashToken(val, val.length)
    expect(result).not.toBeNull()
    expect(result!.query).toBe('close')
  })

  it('returns empty query when "/" is the last character', () => {
    const val = '/'
    const result = detectSlashToken(val, val.length)
    expect(result).not.toBeNull()
    expect(result!.query).toBe('')
  })

  it('does NOT trigger on mid-word "/" (e.g. URLs)', () => {
    const val = 'http://example'
    expect(detectSlashToken(val, val.length)).toBeNull()
  })

  it('does NOT trigger when caret is past the slash token (space after)', () => {
    // "run /budget " — caret at end after the space; the /token is closed
    const val = 'run /budget '
    const result = detectSlashToken(val, val.length)
    // After the space the scan hits the space boundary before reaching "/", so null.
    expect(result).toBeNull()
  })

  it('respects the caret position — only looks backward from caretPos', () => {
    const val = '/budget more text'
    // Caret at position 7 (end of "/budget") — should find the token.
    const result = detectSlashToken(val, 7)
    expect(result).not.toBeNull()
    expect(result!.query).toBe('budget')
  })
})

// ── chatComposer pendingSkills state ─────────────────────────────────────────

type PendingSkill = { value: string; label: string }

function SkillStager({
  onReady,
}: {
  onReady: (api: {
    set: (skills: PendingSkill[]) => void
    clear: () => void
    get: () => PendingSkill[]
  }) => void
}) {
  const { pendingSkills, setPendingSkills, clearMentions } = useChatComposer()
  onReady({
    set: setPendingSkills,
    clear: clearMentions,
    get: () => pendingSkills,
  })
  return (
    <span data-testid="skill-label">{pendingSkills.map((skill) => skill.label).join(',')}</span>
  )
}

describe('chatComposer pendingSkills', () => {
  it('is empty by default', () => {
    let api: { get: () => PendingSkill[] } | null = null
    render(
      <ChatComposerProvider>
        <SkillStager
          onReady={(a) => {
            api = a
          }}
        />
      </ChatComposerProvider>,
    )
    expect(api!.get()).toEqual([])
  })

  it('setPendingSkills preserves ordered multiple selections', () => {
    let api: { set: (skills: PendingSkill[]) => void; get: () => PendingSkill[] } | null = null
    const { getByTestId } = render(
      <ChatComposerProvider>
        <SkillStager
          onReady={(a) => {
            api = a
          }}
        />
      </ChatComposerProvider>,
    )
    act(() => {
      api!.set([
        { value: 'budget-review', label: 'budget-review' },
        { value: 'monthly-close', label: 'monthly-close' },
      ])
    })
    expect(api!.get().map((skill) => skill.value)).toEqual(['budget-review', 'monthly-close'])
    expect(getByTestId('skill-label').textContent).toBe('budget-review,monthly-close')
  })

  it('clearMentions resets pendingSkills', () => {
    let api: {
      set: (skills: PendingSkill[]) => void
      clear: () => void
      get: () => PendingSkill[]
    } | null = null
    render(
      <ChatComposerProvider>
        <SkillStager
          onReady={(a) => {
            api = a
          }}
        />
      </ChatComposerProvider>,
    )
    act(() => {
      api!.set([{ value: 'monthly-close', label: 'monthly-close' }])
    })
    expect(api!.get()[0]?.value).toBe('monthly-close')
    act(() => {
      api!.clear()
    })
    expect(api!.get()).toEqual([])
  })

  it('setPendingSkills([]) clears the skills', () => {
    let api: { set: (skills: PendingSkill[]) => void; get: () => PendingSkill[] } | null = null
    render(
      <ChatComposerProvider>
        <SkillStager
          onReady={(a) => {
            api = a as typeof api
          }}
        />
      </ChatComposerProvider>,
    )
    act(() => {
      api!.set([{ value: 'budget-review', label: 'budget-review' }])
    })
    act(() => {
      api!.set([])
    })
    expect(api!.get()).toEqual([])
  })
})
