// @vitest-environment jsdom
//
// ChatPanel.pendingText.test.tsx — regression tests for QA D2: a prompt suggestion tapped
// while the side panel is CLOSED never reached the composer.
//
// The seam under test is the chatComposer inbox (`setPendingText` / `addMention` +
// `requestOpen`) crossing the dock→side-panel transition. Both the real ChatPanel and the
// real LuciusComposer are rendered — the bug was an ORDERING bug BETWEEN them (the panel's
// open-restore seed() vs. the composer's inbox drain), so stubbing either one out would test
// nothing. Everything else ChatPanel pulls in (chat state, models, profile, transcript
// widgets) is mocked to the smallest shape it destructures.
//
// Shell shape mirrors RouterShell: the side panel is ALWAYS mounted (active={open}); the dock
// is mounted only while the panel is closed, so tapping a chip unmounts one composer and
// activates the other in the same commit.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { useRef, useState } from 'react'

import { render, act, cleanup } from '@testing-library/react'
import { ChatComposerProvider, useChatComposer } from '../../state/chatComposer'
import type { MentionRef } from '../../types/mentions'

// ── Mocks: everything ChatPanel needs that is not part of this seam ───────────

const chatState = {
  conversations: [] as unknown[],
  activeId: 'conv-1',
  createConversation: vi.fn(),
  switchConversation: vi.fn(),
  deleteConversation: vi.fn(),
  renameConversation: vi.fn(),
  setConversationModel: vi.fn(),
  messages: [] as unknown[],
  queuedMessages: [] as unknown[],
  hydrated: true,
  streaming: false,
  multiTurnEnabled: false,
  following: false,
  sendMessage: vi.fn(),
  resolvePendingToolUse: vi.fn(),
  stopStreaming: vi.fn(),
  sending: false,
  liveOutputTokens: 0,
  compacting: null,
  retryLast: vi.fn(),
  respondToChange: vi.fn(),
  previewCheckpoint: vi.fn(),
  restoreCheckpoint: vi.fn(),
  suggestions: [] as string[],
  suggestionsLoading: false,
  fetchSuggestions: vi.fn(),
  statusFor: () => 'idle',
  stopConversation: vi.fn(),
}

vi.mock('../../state/luciusChat', () => ({
  useLuciusChat: () => chatState,
}))
vi.mock('../../state/chatSessions', () => ({
  chatSessions: { statusFor: () => 'idle' },
}))
vi.mock('../../state/chatReducer', () => ({
  loadEarlierMessages: vi.fn(async () => 0),
}))
vi.mock('../../utils/chatStore', () => ({
  positionalCounter: () => null,
}))
vi.mock('../../hooks/useChatSuggestionsEnabled', () => ({
  useChatSuggestionsEnabled: () => ({ enabled: false, loaded: true }),
}))
vi.mock('../../hooks/useModels', () => ({
  useModels: () => ({ models: [], defaultModel: '', imageReader: '' }),
  resolveModelOverride: () => '',
  effortChoicesFor: () => [],
  clampEffortChoice: (e: string) => e,
  effortChoiceLabel: (e: string) => e,
  noVisionLabel: () => null,
}))
vi.mock('../../hooks/serverState', () => ({
  useProfile: () => ({ data: null }),
}))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
}))
vi.mock('../tabs/overview/attentionFlags', () => ({
  useAttentionFlags: () => ({ flags: [], loading: false }),
}))
vi.mock('../../lucius/visibleIds', () => ({
  useVisibleIdsValue: () => ({ visibleIds: new Set<string>() }),
}))
vi.mock('../../state/screenSnapshot', () => ({
  useScreenSnapshot: () => ({
    enabled: false,
    setEnabled: vi.fn(),
    pinnedHandleIds: [],
    togglePin: vi.fn(),
    clearPins: vi.fn(),
    snapshot: null,
  }),
}))
vi.mock('../../lucius/screenContract', () => ({
  getScreenSnapshot: () => ({ title: 'T', facts: [], raw: '', handles: [] }),
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
  IdentityInner: () => null,
}))
vi.mock('../../hooks/useMentionables', () => ({
  useMentionables: () => ({ items: [], loading: false }),
}))
vi.mock('./MessageBubble', () => ({ MessageBubble: () => null }))
vi.mock('./TurnActivity', () => ({ TurnActivity: () => null }))
vi.mock('./ConversationBrowser', () => ({ ConversationBrowser: () => null }))
vi.mock('./ConversationMenu', () => ({
  ConversationMenu: () => null,
  shortWhen: () => '',
}))
vi.mock('./lucius/Autocomplete', () => ({ Autocomplete: () => null }))
vi.mock('./lucius/ScreenContextBar', () => ({ ScreenContextBar: () => null }))
// PermissionModeControl joined this mock when the composer gained the per-conversation
// approvals picker (2026-08-27) — ChatPanel now reaches ../ui through
// ConversationApprovalPicker as well as directly.
vi.mock('../ui', () => ({ ModelPicker: () => null, PermissionModeControl: () => null }))
vi.mock('./lucius/ModelEffortRow', () => ({ EffortChoices: () => null }))

// ── Shell ─────────────────────────────────────────────────────────────────────

import { ChatPanel } from './ChatPanel'
import { LuciusComposer } from './lucius/Composer'
import type { LuciusComposerHandle } from './lucius/Composer'

const originalMatchMedia = Object.getOwnPropertyDescriptor(window, 'matchMedia')

function useCoarsePrimaryPointer() {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: ((query: string) => ({
      matches: query === '(pointer: coarse)',
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as typeof window.matchMedia,
  })
}

/** Stand-in for LuciusDock's composer surface: mounted only while the panel is closed, and
 *  active by default — the instance the panel replaces when a chip opens the panel. */
function Dock() {
  const composerRef = useRef<LuciusComposerHandle>(null)
  return (
    <div data-testid="dock">
      <LuciusComposer ref={composerRef} onSend={vi.fn()} visibleIds={new Set<string>()} />
    </div>
  )
}

// Shell controls, assigned on every render so the tests can drive the app-level actions
// (tap a chip, close/open the panel, hand a draft over from the dock) directly.
let stagePrompt: (text: string) => void
let stageMention: (m: MentionRef) => void
let closePanel: () => void
let openPanel: () => void
let handoffDraft: (text: string) => void

function Shell() {
  const [open, setOpen] = useState(false)
  const { setPendingText, requestOpen, addMention, setComposerDraft } = useChatComposer()
  // Exactly what a Connectors prompt chip does (ConnectorsTab onUsePrompt), and what a
  // row's ✦ button does (addMention → requestOpen), in one synchronous handler.
  stagePrompt = (text: string) => {
    setPendingText(text)
    requestOpen()
  }
  stageMention = (m: MentionRef) => addMention(m)
  closePanel = () => setOpen(false)
  openPanel = () => setOpen(true)
  handoffDraft = (text: string) => setComposerDraft({ text, mentions: [] })
  return (
    <>
      <div data-testid="panel">
        <ChatPanel open={open} onOpenChange={setOpen} />
      </div>
      {!open && <Dock />}
    </>
  )
}

function mountShell() {
  return render(
    <ChatComposerProvider>
      <Shell />
    </ChatComposerProvider>,
  )
}

/** Let the commit's passive effects, any RAF and any timer settle. */
async function settle() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 30))
  })
}

function composerText(testid: string): string {
  const el = document.querySelector(`[data-testid="${testid}"] .chat-composer`)
  return el?.textContent ?? ''
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  if (originalMatchMedia) Object.defineProperty(window, 'matchMedia', originalMatchMedia)
})

const PROMPT = 'Notify me if any account drops below $500'

describe('chatComposer inbox survives the dock → side-panel transition (QA D2)', () => {
  it('Lucius tab → another tab: restoring the iPad side panel draft does not focus it', async () => {
    useCoarsePrimaryPointer()
    const focusSpy = vi.spyOn(HTMLElement.prototype, 'focus')
    const composerWasFocused = () =>
      focusSpy.mock.contexts.some(
        (el) => el instanceof HTMLElement && el.classList.contains('chat-composer'),
      )

    try {
      mountShell()
      await settle()
      act(() => handoffDraft('typed on the Lucius tab'))
      focusSpy.mockClear()

      act(() => openPanel())
      await settle()

      expect(composerText('panel')).toBe('typed on the Lucius tab')
      expect(composerWasFocused()).toBe(false)
    } finally {
      focusSpy.mockRestore()
    }
  })

  it('panel CLOSED: a prompt chip lands in the composer that ends up visible', async () => {
    mountShell()
    await settle()
    expect(document.querySelector('[data-testid="dock"]')).not.toBeNull()

    act(() => stagePrompt(PROMPT))
    await settle()

    // The dock is gone; the panel's composer holds the text — and still holds it after the
    // open transition has fully settled (the bug wiped it one frame after the open).
    expect(document.querySelector('[data-testid="dock"]')).toBeNull()
    expect(composerText('panel')).toContain(PROMPT)
  })

  it('panel ALREADY OPEN: a prompt chip still lands (the case that always worked)', async () => {
    mountShell()
    await settle()
    act(() => stagePrompt('first prompt'))
    await settle()

    act(() => stagePrompt(PROMPT))
    await settle()

    // A staged prompt REPLACES the composer's text (MentionComposer.setText), it does not
    // append — so the second chip leaves only its own prompt behind. Unchanged behavior;
    // pinned here because the closed-panel path above must agree with it.
    expect(composerText('panel')).toBe(PROMPT)
  })

  it('consumes the text EXACTLY once — no double insert, no leak into a later open', async () => {
    mountShell()
    await settle()
    act(() => stagePrompt(PROMPT))
    await settle()

    // Once, in one composer.
    const panel = composerText('panel')
    expect(panel.split(PROMPT).length - 1).toBe(1)

    // The queue is drained: closing and reopening the panel must not replay it. Clear the
    // composer first so a replay would be visible rather than hidden behind the draft.
    const editor = document.querySelector('[data-testid="panel"] .chat-composer') as HTMLElement
    act(() => {
      editor.replaceChildren()
      editor.dispatchEvent(new Event('input', { bubbles: true }))
    })
    act(() => closePanel())
    await settle()
    act(() => openPanel())
    await settle()

    expect(composerText('panel')).not.toContain(PROMPT)
  })

  it('panel CLOSED: an @-mention chip staged by the same tap survives too', async () => {
    mountShell()
    await settle()

    act(() => stageMention({ type: 'account', ref: 'acc_1', label: 'Checking' }))
    await settle()

    expect(composerText('panel')).toContain('Checking')
  })

  it('still restores a draft handed over from the dock when the panel opens', async () => {
    mountShell()
    await settle()

    // The explicit dock→side handoff (LuciusDock's "Open in side panel") writes the draft into
    // the shared store and then asks the panel to open. Moving the restore into a layout effect
    // must not break it: the draft is written in the same handler, i.e. before the commit.
    act(() => {
      handoffDraft('typed in the dock')
      openPanel()
    })
    await settle()

    expect(composerText('panel')).toBe('typed in the dock')
  })

  it('mobile: the tab mounts its panel already open and still gets the staged prompt', async () => {
    // The phone has no dock and no side panel — LuciusMobile mounts ChatPanel with open={true}
    // only once the Lucius tab is showing, so the prompt is staged BEFORE that instance exists
    // and its composer drains on mount. `justOpened` never fires there (open is true from the
    // first render), so the restore-seed this fix reorders is not even in play — asserted here
    // so the mobile surface stays covered if that ever changes.
    function MobileShell() {
      const [mounted, setMounted] = useState(false)
      const { setPendingText } = useChatComposer()
      stagePrompt = (text: string) => {
        setPendingText(text)
        setMounted(true)
      }
      return mounted ? (
        <div data-testid="panel">
          <ChatPanel open onOpenChange={() => {}} fillWidth autoFocusComposer={false} homeMode />
        </div>
      ) : null
    }
    render(
      <ChatComposerProvider>
        <MobileShell />
      </ChatComposerProvider>,
    )
    await settle()

    act(() => stagePrompt(PROMPT))
    await settle()

    expect(composerText('panel')).toBe(PROMPT)
  })

  it('a draft and a staged prompt in ONE transition: restore first, prompt wins', async () => {
    mountShell()
    await settle()

    act(() => {
      handoffDraft('typed in the dock')
      stagePrompt(PROMPT)
    })
    await settle()

    // The restore runs first and the drain replaces it — the same outcome as tapping a chip
    // with text already sitting in an OPEN panel. What must never happen is the reverse order,
    // where the restore lands last and wipes the prompt (D2).
    expect(composerText('panel')).toBe(PROMPT)
  })
})
