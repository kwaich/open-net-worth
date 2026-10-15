// @vitest-environment jsdom
//
// ChatPanel.landingScroll.test.tsx — owner report (2026-09-20, screenshot): the Lucius home
// tab opened with "Good afternoon, …" sliced in half under the ticker and the Scheduled list's
// "Show 5 more" sitting at the bottom edge — the landing screen had been scrolled to its
// bottom with no user action.
//
// The transcript's stick-to-bottom machinery (the conversation-switch jump, the
// `[messages, streaming]` effect, useChatScrollPin's observers) exists so the NEWEST message —
// at the bottom — stays in view. On the empty landing screen the thing that matters is at the
// TOP (greeting → composer), and what grows the container is the Activity panel loading
// underneath; pinning to the bottom scrolls the composer away. Same mocks as
// ChatPanel.tabSwitch.test.tsx; the real ChatPanel is the seam under test.

import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { render, act, cleanup } from '@testing-library/react'
import { ChatComposerProvider } from '../../state/chatComposer'
import type { Message } from '../../types/chat'

// ── Mocks: everything ChatPanel needs that is not part of this seam ───────────

const chatState = {
  conversations: [] as unknown[],
  activeId: 'conv-1',
  createConversation: vi.fn(),
  switchConversation: vi.fn(),
  deleteConversation: vi.fn(),
  renameConversation: vi.fn(),
  setConversationModel: vi.fn(),
  messages: [] as Message[],
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
  restoreSuggestions: vi.fn(),
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
vi.mock('../ui', () => ({ ModelPicker: () => null, PermissionModeControl: () => null }))
vi.mock('./lucius/ModelEffortRow', () => ({ EffortChoices: () => null }))

// The Activity panel stands in for "content that arrives under the composer after first
// paint" — its rows are what grow the landing screen past the viewport in the real app.
let setActivityRows: (n: number) => void = () => {}
vi.mock('../tabs/lucius/ActivityPanel', async () => {
  const { useEffect, useState } = await import('react')
  return {
    ActivityPanel: () => {
      const [rows, setRows] = useState(0)
      useEffect(() => {
        setActivityRows = setRows
      }, [])
      return (
        <div className="lx-automation-activity">
          {Array.from({ length: rows }, (_, i) => (
            <div key={i}>scheduled row {i}</div>
          ))}
        </div>
      )
    },
  }
})

import { ChatPanel } from './ChatPanel'

// ── Scroll geometry ───────────────────────────────────────────────────────────
// jsdom does no layout: every scrollHeight/clientHeight is 0 and nothing ever overflows. Give
// the transcript scroller (.lx-side-scroll) a real viewport and a settable content height so
// the pin logic sees exactly what a browser would.

const viewport = { scrollHeight: 0, clientHeight: 600 }
const scrollTops = new WeakMap<Element, number>()
const isScroller = (el: Element) => el.classList.contains('lx-side-scroll')
const original = {
  scrollHeight: Object.getOwnPropertyDescriptor(Element.prototype, 'scrollHeight'),
  clientHeight: Object.getOwnPropertyDescriptor(Element.prototype, 'clientHeight'),
  scrollTop: Object.getOwnPropertyDescriptor(Element.prototype, 'scrollTop'),
}

beforeAll(() => {
  Object.defineProperty(Element.prototype, 'scrollHeight', {
    configurable: true,
    get(this: Element) {
      return isScroller(this) ? viewport.scrollHeight : 0
    },
  })
  Object.defineProperty(Element.prototype, 'clientHeight', {
    configurable: true,
    get(this: Element) {
      return isScroller(this) ? viewport.clientHeight : 0
    },
  })
  Object.defineProperty(Element.prototype, 'scrollTop', {
    configurable: true,
    get(this: Element) {
      return scrollTops.get(this) ?? 0
    },
    set(this: Element, v: number) {
      scrollTops.set(this, Math.max(0, Math.min(v, this.scrollHeight - this.clientHeight)))
    },
  })
})

afterAll(() => {
  for (const [key, desc] of Object.entries(original)) {
    if (desc) Object.defineProperty(Element.prototype, key, desc)
    else delete (Element.prototype as unknown as Record<string, unknown>)[key]
  }
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  chatState.messages = []
  viewport.scrollHeight = 0
})

async function settle() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 30))
  })
}

function scroller(): HTMLElement {
  return document.querySelector<HTMLElement>('.lx-side-scroll')!
}

function mount() {
  return render(
    <ChatComposerProvider>
      <ChatPanel open onOpenChange={() => {}} fillWidth autoFocusComposer={false} homeMode />
    </ChatComposerProvider>,
  )
}

describe('the empty landing screen is top-anchored — the greeting and composer stay in view', () => {
  it('stays at the top when the Activity panel loads its rows after first paint', async () => {
    // Fits at first paint …
    viewport.scrollHeight = 400
    mount()
    await settle()
    expect(scroller().scrollTop).toBe(0)

    // … then the Scheduled list arrives and the group no longer fits.
    viewport.scrollHeight = 1400
    act(() => setActivityRows(10))
    await settle()

    expect(scroller().scrollTop).toBe(0)
  })

  it('stays at the top when the group already overflows at mount', async () => {
    viewport.scrollHeight = 1400
    mount()
    await settle()

    expect(scroller().scrollTop).toBe(0)
  })

  it('a real conversation on the same tab still jumps to its latest message', async () => {
    // Positive control: only the LANDING screen is exempt — the transcript keeps its jump.
    chatState.messages = [
      { id: 'm1', role: 'user', text: 'hi' },
      { id: 'm2', role: 'assistant', text: 'hello' },
    ] as Message[]
    viewport.scrollHeight = 1400
    mount()
    await settle()

    expect(scroller().scrollTop).toBe(800)
  })
})
