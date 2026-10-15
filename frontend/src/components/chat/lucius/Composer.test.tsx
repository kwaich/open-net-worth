// @vitest-environment jsdom
//
// Composer.test.tsx — render smoke tests for LuciusComposer (W2A).
//
// We focus on:
//   1. The composer mounts without crashing.
//   2. The send button starts disabled (empty input).
//   3. onSend fires after typing and clicking.
//   4. The imperative handle exposes expected methods.
//   5. The ScreenContextBar is rendered.
//
// These tests run in jsdom; full keyboard/contenteditable interactions are
// tested in the existing MentionComposer tests.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRef } from 'react'
import { render, fireEvent, cleanup } from '@testing-library/react'
import { LuciusComposer } from './Composer'
import type { LuciusComposerHandle, LuciusComposerProps } from './Composer'

afterEach(cleanup)

// ── Provider stubs ────────────────────────────────────────────────────────────
// LuciusComposer needs ChatComposerProvider + ScreenSnapshotProvider.

vi.mock('../../../state/chatComposer', () => ({
  useChatComposer: () => ({
    pendingMentions: [],
    respondingAgent: null,
    pendingSkills: [],
    setPendingSkills: vi.fn(),
    clearPendingMentions: vi.fn(),
  }),
}))

vi.mock('../../../state/screenSnapshot', () => ({
  useScreenSnapshot: () => ({
    enabled: false,
    setEnabled: vi.fn(),
    pinnedHandleIds: [],
    togglePin: vi.fn(),
    clearPins: vi.fn(),
    snapshot: null,
  }),
}))

// ScreenContextBar calls getScreenSnapshot — return a safe empty result.
vi.mock('../../../lucius/screenContract', () => ({
  getScreenSnapshot: () => ({ title: 'Test', facts: [], raw: '', handles: [] }),
  captureAndSerialize: () => null,
  allHandles: () => [],
}))

vi.mock('../../../lucius/identity', () => ({
  GLYPH_SVG: {},
  TYPE_MARKER_SVG: {},
  hSvg: () => '',
  Spark: () => null,
  Identity: () => null,
  IdentityInner: () => null,
}))

vi.mock('../../../hooks/useMentionables', () => ({
  useMentionables: () => ({ items: [], loading: false }),
}))

const EMPTY_VISIBLE_IDS = new Set<string>()
const onSend = vi.fn()

function renderComposer(
  screenAwarenessDisabled = false,
  extraProps: Partial<Pick<LuciusComposerProps, 'dismissKeyboardOnSend'>> = {},
) {
  const ref = createRef<LuciusComposerHandle>()
  const utils = render(
    <LuciusComposer
      ref={ref}
      onSend={onSend}
      visibleIds={EMPTY_VISIBLE_IDS}
      screenAwarenessDisabled={screenAwarenessDisabled}
      {...extraProps}
    />,
  )
  return { ...utils, ref }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('LuciusComposer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders without crashing', () => {
    const { container } = renderComposer()
    expect(container.querySelector('.lx-compose')).not.toBeNull()
  })

  it('renders the ScreenContextBar', () => {
    renderComposer()
    // ScreenContextBar renders an lx-scope element.
    expect(document.querySelector('.lx-scope')).not.toBeNull()
  })

  it('closes the ScreenContextBar Peek when the composer becomes inactive', () => {
    const ref = createRef<LuciusComposerHandle>()
    const { container, rerender } = render(
      <LuciusComposer ref={ref} onSend={onSend} visibleIds={EMPTY_VISIBLE_IDS} active />,
    )
    fireEvent.click(container.querySelector<HTMLButtonElement>('.lx-scope-peek')!)
    expect(document.body.querySelector('.lx-scope-peekbox')).not.toBeNull()

    rerender(
      <LuciusComposer ref={ref} onSend={onSend} visibleIds={EMPTY_VISIBLE_IDS} active={false} />,
    )
    expect(document.body.querySelector('.lx-scope-peekbox')).toBeNull()
  })

  it('hides the ScreenContextBar when screenAwarenessDisabled (mobile Lucius tab)', () => {
    renderComposer(true)
    expect(document.querySelector('.lx-scope')).toBeNull()
  })

  it('serialize() carries only the parked command catalog when screenAwarenessDisabled', () => {
    // The phone's Lucius tab replaces the screen: no screen content, but the catalog still
    // rides so run_screen_command parks + offers the chip instead of "available: none".
    const { ref } = renderComposer(true)
    const screen = ref.current!.serialize().screen!
    expect(screen).toMatchObject({ title: '', facts: {}, visible_text: '', handles: [] })
    expect(screen.commands.map((c) => c.name)).toContain('plan.stage_whatif')
    expect(screen.commands.every((c) => c.live === false)).toBe(true)
  })

  it('renders the editor (MentionComposer)', () => {
    renderComposer()
    expect(document.querySelector('.chat-composer')).not.toBeNull()
  })

  it('send button is disabled when empty', () => {
    renderComposer()
    const sendBtn = document.querySelector<HTMLButtonElement>('.lx-send')
    expect(sendBtn).not.toBeNull()
    expect(sendBtn!.disabled).toBe(true)
  })

  it('dismisses the keyboard after a valid send when requested by the mobile surface', () => {
    const { getByRole } = renderComposer(true, { dismissKeyboardOnSend: true })
    const editor = getByRole('textbox')
    editor.textContent = 'Sent from mobile'
    fireEvent.input(editor)
    editor.focus()

    fireEvent.click(getByRole('button', { name: 'Send' }))

    expect(document.activeElement).not.toBe(editor)
  })

  it('keeps the desktop composer focused after sending', () => {
    const { getByRole } = renderComposer()
    const editor = getByRole('textbox')
    editor.textContent = 'Sent from desktop'
    fireEvent.input(editor)
    editor.focus()

    fireEvent.click(getByRole('button', { name: 'Send' }))

    expect(document.activeElement).toBe(editor)
  })

  // A busy conversation stays sendable (acceptance C1/C2) — the send button is now ALWAYS
  // "Send" and is never disabled by streaming state; see doSend's removed `busy` gate.
  // Stop lives in TurnStatusBar now, scoped to the conversation on screen (§5.2).

  it('attach / @ / / tool buttons render', () => {
    renderComposer()
    const tools = document.querySelectorAll('.lx-tool')
    expect(tools.length).toBeGreaterThanOrEqual(3)
  })

  it('exposes imperative handle with expected methods', () => {
    const { ref } = renderComposer()
    const handle = ref.current
    expect(handle).not.toBeNull()
    expect(typeof handle!.focus).toBe('function')
    expect(typeof handle!.clear).toBe('function')
    expect(typeof handle!.serialize).toBe('function')
    expect(typeof handle!.insertChip).toBe('function')
    expect(typeof handle!.seed).toBe('function')
    expect(typeof handle!.isEmpty).toBe('function')
  })

  it('isEmpty() returns true on empty editor', () => {
    const { ref } = renderComposer()
    expect(ref.current!.isEmpty()).toBe(true)
  })

  it('seed() rebuilds a chip from a transcript token — no raw [token] text', () => {
    // Regression (dock↔side draft sync): seed() used to insert the chip AND append the raw
    // serialized transcript, so "[transaction: …]" rendered as text alongside the chip on the
    // receiving surface. It must now parse the transcript and emit a single chip.
    const { container, ref } = renderComposer()
    const mention = {
      type: 'transaction' as const,
      ref: 't-1',
      label: 'PHAU BUY 3 @ 389',
      chipDate: 'Jun 12',
      chipAmt: '+đ30,000,000',
      chipAmtNeg: false,
    }
    ref.current!.seed([{ mention }], '[transaction: t-1|PHAU BUY 3 @ 389]')

    const editor = container.querySelector('.chat-composer')!
    // Exactly one chip rebuilt from the token.
    expect(editor.querySelectorAll('.mention-chip').length).toBe(1)
    // The serialized token must NOT survive as raw text.
    expect(editor.textContent ?? '').not.toContain('[transaction:')
    // The chip is enriched from `items` (amount survives the hand-off, not just type/ref/label).
    expect(editor.querySelector('.chip-amt')?.textContent).toBe('+đ30,000,000')
  })

  it('serialize() returns empty ComposerParsed when empty', () => {
    const { ref } = renderComposer()
    const parsed = ref.current!.serialize()
    expect(parsed.text).toBe('')
    expect(parsed.mentions).toEqual([])
    expect(parsed.attachments).toEqual([])
    expect(parsed.respondingAgent).toBeNull()
    expect(parsed.skills).toEqual([])
  })

  it('clicking @ tool opens autocomplete', () => {
    renderComposer()
    const atBtn = document.querySelectorAll<HTMLButtonElement>('.lx-tool')[1]!
    fireEvent.click(atBtn)
    // Autocomplete renders with class .ac
    expect(document.querySelector('.ac')).not.toBeNull()
  })

  it('clicking / tool opens autocomplete in skill mode', () => {
    renderComposer()
    const slashBtn = document.querySelectorAll<HTMLButtonElement>('.lx-tool')[2]!
    fireEvent.click(slashBtn)
    expect(document.querySelector('.ac')).not.toBeNull()
  })
})
