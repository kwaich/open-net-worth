// @vitest-environment jsdom
//
// ChatPanel.handoff.test.tsx — regression tests for the dock→panel handoff race.
//
// Bug: when the dock had BOTH text AND @-mention chips, "Open in side panel" dropped
// the chips. seed([], draftText) called mc.clear() first, racing with the pending-
// mentions useEffect that drains chips via insertChip. Fix: setText() appends text
// without clearing so chips drained concurrently survive.

import { afterEach, describe, it, expect, vi, beforeEach } from 'vitest'
import { render, act, cleanup } from '@testing-library/react'
import { createRef } from 'react'
import type { ReactNode } from 'react'

// ── Mocks setup ───────────────────────────────────────────────────────────────

// We test the LuciusComposerHandle directly (the unit under test is the
// setText / seed contract, not the full ChatPanel DOM tree which would require
// all providers). The ChatPanel open-effect calls composerRef.current.setText();
// we verify that path preserves existing chips.

// Mock chatComposer state
const mockPendingMentions: import('../../types/mentions').MentionRef[] = []
const mockClearPendingMentions = vi.fn()

vi.mock('../../state/chatComposer', () => ({
  useChatComposer: () => ({
    pendingMentions: mockPendingMentions,
    addMention: vi.fn(),
    removeMention: vi.fn(),
    clearPendingMentions: mockClearPendingMentions,
    clearMentions: vi.fn(),
    respondingAgent: null,
    setRespondingAgent: vi.fn(),
    pendingSkills: [],
    setPendingSkills: vi.fn(),
    requestOpen: vi.fn(),
    subscribeOpen: vi.fn(() => () => {}),
    composerDraft: { text: '', mentions: [] },
    setComposerDraft: vi.fn(),
    clearComposerDraft: vi.fn(),
  }),
}))

vi.mock('../../state/screenSnapshot', () => ({
  useScreenSnapshot: () => ({
    enabled: false,
    setEnabled: vi.fn(),
    pinnedHandleIds: [],
    togglePin: vi.fn(),
    snapshot: null,
    setSnapshot: vi.fn(),
    clearPins: vi.fn(),
  }),
  ScreenSnapshotProvider: ({ children }: { children: ReactNode }) => children,
  serializeSnapshot: vi.fn(),
}))

vi.mock('../../lucius/screenContract', () => ({
  getScreenSnapshot: () => ({ title: 'Test', facts: [], raw: '', handles: [] }),
  captureAndSerialize: () => null,
  allHandles: () => [],
  screenSuggestions: () => [],
}))

vi.mock('../../lucius/identity', () => ({
  GLYPH_SVG: {},
  TYPE_MARKER_SVG: {},
  hSvg: () => '',
  Spark: () => null,
  Identity: () => null,
}))

vi.mock('../../hooks/useMentionables', () => ({
  useMentionables: () => ({ items: [], loading: false }),
}))

vi.mock('./Autocomplete', () => ({
  Autocomplete: () => null,
}))
vi.mock('./ScreenContextBar', () => ({
  ScreenContextBar: () => null,
}))
vi.mock('./lucius/ScreenContextBar', () => ({
  ScreenContextBar: () => null,
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  mockPendingMentions.length = 0
})

// ── Tests ─────────────────────────────────────────────────────────────────────

import { LuciusComposer } from './lucius/Composer'
import type { LuciusComposerHandle } from './lucius/Composer'
import { serializeMentionRef } from '../../types/mentions'

describe('LuciusComposerHandle.setText — dock→panel handoff (regression)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('exposes setText on the handle', () => {
    const ref = createRef<LuciusComposerHandle>()
    render(<LuciusComposer ref={ref} onSend={vi.fn()} visibleIds={new Set()} />)
    expect(typeof ref.current?.setText).toBe('function')
  })

  it('setText appends text without clearing existing chips', async () => {
    const ref = createRef<LuciusComposerHandle>()
    render(<LuciusComposer ref={ref} onSend={vi.fn()} visibleIds={new Set()} />)

    const handle = ref.current!

    // Simulate the chip that pendingMentions useEffect would drain.
    act(() => {
      handle.insertChip({ type: 'account', ref: 'acc-1', label: 'Meridian Bank Multiplier' })
    })

    // After insert, the chip should be in the editor.
    let parsed = handle.serialize()
    expect(parsed.mentions).toHaveLength(1)
    expect(parsed.mentions[0]!.label).toBe('Meridian Bank Multiplier')

    // Now simulate ChatPanel's open-effect calling setText with the draft text.
    act(() => {
      handle.setText('hello')
    })

    // BOTH the chip AND the text must survive.
    parsed = handle.serialize()
    expect(parsed.mentions).toHaveLength(1)
    expect(parsed.mentions[0]!.label).toBe('Meridian Bank Multiplier')
    expect(parsed.text).toContain('hello')
  })

  it('setText with no prior chips just sets text (no crash)', () => {
    const ref = createRef<LuciusComposerHandle>()
    render(<LuciusComposer ref={ref} onSend={vi.fn()} visibleIds={new Set()} />)

    act(() => {
      ref.current!.setText('hello')
    })

    const parsed = ref.current!.serialize()
    expect(parsed.text).toContain('hello')
    expect(parsed.mentions).toHaveLength(0)
  })

  it('seed() clears old chips and rebuilds from the transcript — no raw token', () => {
    const ref = createRef<LuciusComposerHandle>()
    const { container } = render(
      <LuciusComposer ref={ref} onSend={vi.fn()} visibleIds={new Set()} />,
    )

    const handle = ref.current!

    // A stale chip that seed() must clear.
    act(() => {
      handle.insertChip({ type: 'account', ref: 'acc-2', label: 'QuickPay USD' })
    })

    // seed() the REAL serialized transcript: the chip token lives INSIDE the text (that is
    // what serialize() produces), not as a separate plain string.
    const aapl = { type: 'holding' as const, ref: 'hold-1', label: 'AAPL' }
    act(() => {
      handle.seed([{ mention: aapl }], serializeMentionRef(aapl) + ' seed text')
    })

    const parsed = handle.serialize()
    expect(parsed.mentions).toHaveLength(1)
    expect(parsed.mentions[0]!.label).toBe('AAPL')
    expect(parsed.text).toContain('seed text')
    // The token must become a single chip — not raw text alongside it.
    const editor = container.querySelector('.chat-composer')!
    expect(editor.querySelectorAll('.mention-chip').length).toBe(1)
    expect(editor.textContent ?? '').not.toContain('[holding:')
  })

  it('seed() rebuilds multiple chips + text from the transcript — no raw tokens', () => {
    const ref = createRef<LuciusComposerHandle>()
    const { container } = render(
      <LuciusComposer ref={ref} onSend={vi.fn()} visibleIds={new Set()} />,
    )

    const handle = ref.current!

    const ibkr = { type: 'account' as const, ref: 'acc-3', label: 'Vantage Brokerage' }
    const tsla = { type: 'holding' as const, ref: 'hold-2', label: 'TSLA' }
    act(() => {
      handle.seed(
        [{ mention: ibkr }, { mention: tsla }],
        serializeMentionRef(ibkr) + serializeMentionRef(tsla) + ' my draft',
      )
    })

    const parsed = handle.serialize()
    expect(parsed.mentions).toHaveLength(2)
    expect(parsed.text).toContain('my draft')
    const editor = container.querySelector('.chat-composer')!
    expect(editor.querySelectorAll('.mention-chip').length).toBe(2)
    expect(editor.textContent ?? '').not.toContain('[account:')
  })

  it('preserves chip META (amount) across serialize → seed — the holding-price regression', () => {
    // The user's bug: a holding chip showed its price in the dock but lost it after the
    // dock→side hand-off. Root cause: readSegments dropped the chip meta, so composerDraft
    // carried only type/ref/label and seed() could not restore the price. buildChipEl now
    // stamps the meta into the chip dataset and readSegments recovers it.
    const ref = createRef<LuciusComposerHandle>()
    const { container } = render(
      <LuciusComposer ref={ref} onSend={vi.fn()} visibleIds={new Set()} />,
    )

    const handle = ref.current!

    // Build a draft with a priced holding chip, then serialize it the way the dock does.
    const aapl = { type: 'holding' as const, ref: 'pos-aapl', label: 'AAPL', chipAmt: 'S$281.32' }
    act(() => {
      handle.insertChip(aapl)
    })
    const draft = handle.serialize()
    // The serialized mentions carry the price (recovered from the chip dataset).
    expect(draft.mentions[0]!.chipAmt).toBe('S$281.32')

    // Re-seed (the dock↔side hand-off) and confirm the chip STILL renders the price.
    act(() => {
      handle.clear()
      handle.seed(
        draft.mentions.map((m) => ({ mention: m })),
        draft.text,
      )
    })
    const editor = container.querySelector('.chat-composer')!
    expect(editor.querySelectorAll('.mention-chip').length).toBe(1)
    expect(editor.querySelector('.chip-amt')?.textContent).toBe('S$281.32')
  })

  it('side→dock round-trip: seed with text only (no chips) works without crash', () => {
    const ref = createRef<LuciusComposerHandle>()
    render(<LuciusComposer ref={ref} onSend={vi.fn()} visibleIds={new Set()} />)

    act(() => {
      ref.current!.seed([], 'typed text from side panel')
    })

    const parsed = ref.current!.serialize()
    expect(parsed.text).toContain('typed text from side panel')
    expect(parsed.mentions).toHaveLength(0)
  })
})
