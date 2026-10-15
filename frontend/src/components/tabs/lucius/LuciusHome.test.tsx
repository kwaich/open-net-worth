// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, act } from '@testing-library/react'
import { LuciusHome } from './LuciusHome'

const mockChat = {
  conversations: [
    {
      id: 'a',
      title: 'Chat A',
      createdAt: '2026-08-12T03:08:00Z',
      updatedAt: '2026-08-12T03:08:00Z',
      messageCount: 2,
      model: '',
    },
  ],
  activeId: 'a',
  createConversation: vi.fn(),
  switchConversation: vi.fn(),
  deleteConversation: vi.fn(),
  renameConversation: vi.fn(),
  statusFor: () => 'idle' as const,
  stopConversation: vi.fn(),
}

vi.mock('../../../state/luciusChat', () => ({
  useLuciusChat: () => mockChat,
}))

const mockChatPanelProps = vi.fn()

vi.mock('../../vault/VaultPanel', () => ({
  VaultPanel: () => <div data-testid="vault-panel" />,
}))

vi.mock('../../chat/ChatPanel', () => ({
  ChatPanel: (props: { onOpenChange?: (open: boolean) => void }) => {
    mockChatPanelProps(props)
    return <div data-testid="chat-panel" />
  },
  conversationGroups: (actual: import('../../../hooks/useConversations').ConversationMeta[]) => {
    const chats = actual.filter((c) => c.source !== 'automation')
    const runs = actual.filter((c) => c.source === 'automation')
    return [
      ...(chats.length ? [{ key: 'chat' as const, items: chats, total: chats.length }] : []),
      ...(runs.length ? [{ key: 'automation' as const, items: runs, total: runs.length }] : []),
    ]
  },
  conversationWhen: () => '2:00 PM',
}))

afterEach(() => {
  cleanup()
  localStorage.clear()
  sessionStorage.clear()
  mockChat.createConversation.mockClear()
  mockChatPanelProps.mockClear()
})

// Everything below simulates being PAST the first paint of a new session (the fresh-session
// flag already set) — these tests are about collapse/expand interaction and the localStorage
// preference, independent of the new-session default covered separately below.
function markSessionAlreadyStarted() {
  sessionStorage.setItem('luciusFreshSessionStarted', '1')
}

describe('LuciusHome', () => {
  it('renders the sidebar and the chat panel side by side once the session preference says expanded', () => {
    markSessionAlreadyStarted()
    localStorage.setItem('luciusSideCollapsed', '0')
    render(<LuciusHome />)
    expect(screen.getByText('Chat A')).toBeTruthy()
    expect(screen.getByTestId('chat-panel')).toBeTruthy()
  })

  it('collapsing the sidebar hides it and persists the choice', () => {
    markSessionAlreadyStarted()
    localStorage.setItem('luciusSideCollapsed', '0')
    render(<LuciusHome />)
    fireEvent.click(screen.getByLabelText('Hide chat history'))
    expect(screen.queryByText('Chat A')).toBeNull()
    expect(localStorage.getItem('luciusSideCollapsed')).toBe('1')
  })

  it('reopening the sidebar restores it', () => {
    markSessionAlreadyStarted()
    localStorage.setItem('luciusSideCollapsed', '0')
    render(<LuciusHome />)
    fireEvent.click(screen.getByLabelText('Hide chat history'))
    fireEvent.click(screen.getByLabelText('Show chat history'))
    expect(screen.getByText('Chat A')).toBeTruthy()
    expect(localStorage.getItem('luciusSideCollapsed')).toBe('0')
  })

  it('starts collapsed if the last session left it collapsed', () => {
    markSessionAlreadyStarted()
    localStorage.setItem('luciusSideCollapsed', '1')
    render(<LuciusHome />)
    expect(screen.queryByText('Chat A')).toBeNull()
  })

  it('starts collapsed on a brand new session even if a previous session left it expanded', () => {
    // No markSessionAlreadyStarted() — this IS a fresh session, which forces collapsed
    // regardless of the persisted "expanded" preference from a previous session.
    localStorage.setItem('luciusSideCollapsed', '0')
    render(<LuciusHome />)
    expect(screen.queryByText('Chat A')).toBeNull()
    expect(screen.getByLabelText('Show chat history')).toBeTruthy()
  })

  it('dragging the resize handle changes the sidebar width', () => {
    markSessionAlreadyStarted()
    localStorage.setItem('luciusSideCollapsed', '0')
    render(<LuciusHome />)
    const resizer = screen.getByTitle('Drag to resize (double-click to reset)')
    fireEvent.pointerDown(resizer, {
      clientX: 300,
      pointerId: 1,
      pointerType: 'mouse',
      isPrimary: true,
      button: 0,
    })
    fireEvent.pointerMove(resizer, { clientX: 350, pointerId: 1, pointerType: 'mouse' })
    fireEvent.pointerUp(resizer, { clientX: 350, pointerId: 1, pointerType: 'mouse' })
    expect(screen.getByText('Chat A')).toBeTruthy()
    expect(localStorage.getItem('luciusSideCollapsed')).toBe('0')
    expect(localStorage.getItem('luciusSideWidth')).toBe('350')
  })

  it('double-clicking the resize handle resets the width', () => {
    markSessionAlreadyStarted()
    localStorage.setItem('luciusSideCollapsed', '0')
    localStorage.setItem('luciusSideWidth', '400')
    render(<LuciusHome />)
    const resizer = screen.getByTitle('Drag to resize (double-click to reset)')
    fireEvent.doubleClick(resizer)
    expect(localStorage.getItem('luciusSideWidth')).toBe('280')
  })

  it('selecting a conversation calls switchConversation', () => {
    markSessionAlreadyStarted()
    localStorage.setItem('luciusSideCollapsed', '0')
    render(<LuciusHome />)
    fireEvent.click(screen.getByText('Chat A'))
    expect(mockChat.switchConversation).toHaveBeenCalledWith('a')
  })

  // Regression: tapping an automation from the Lucius tab navigated to the automation's detail
  // but silently dropped the Lucius panel — the embedded ChatPanel's onOpenChange was hardcoded
  // to a no-op, so ChatPanel's own navigate-away call had nothing to open the docked side panel
  // with. LuciusHome must forward a real onOpenChange through so navigating away can request it.
  it('forwards onOpenChange through to the embedded ChatPanel instead of swallowing it', () => {
    const onOpenChange = vi.fn()
    render(<LuciusHome onOpenChange={onOpenChange} />)
    expect(mockChatPanelProps).toHaveBeenCalled()
    const lastProps = mockChatPanelProps.mock.calls.at(-1)![0]
    expect(lastProps.onOpenChange).toBe(onOpenChange)
  })

  it('starts a fresh conversation the first time a new browser session opens the tab', () => {
    render(<LuciusHome />)
    expect(mockChat.createConversation).toHaveBeenCalledTimes(1)
    expect(sessionStorage.getItem('luciusFreshSessionStarted')).toBe('1')
  })

  it('Documents stays open across a refresh (remount) within the browser session', () => {
    markSessionAlreadyStarted()
    const { unmount } = render(<LuciusHome />)
    fireEvent.click(screen.getByRole('button', { name: 'Documents' }))
    expect(screen.getByTestId('vault-panel')).toBeTruthy()
    unmount()
    render(<LuciusHome />)
    expect(screen.getByTestId('vault-panel')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Documents' }))
    expect(screen.queryByTestId('vault-panel')).toBeNull()
    expect(sessionStorage.getItem('luciusDocumentsOpen')).toBeNull()
  })

  it('opening Documents keeps the history open when there is room for all three', () => {
    markSessionAlreadyStarted()
    localStorage.setItem('luciusSideCollapsed', '0')
    const wide = render(<LuciusHome />)
    const shell = wide.container.querySelector('.lucius-home-shell') as HTMLElement
    Object.defineProperty(shell, 'clientWidth', { value: 1600, configurable: true })
    fireEvent.click(screen.getByRole('button', { name: 'Documents' }))
    expect(screen.getByTestId('vault-panel')).toBeTruthy()
    expect(screen.getByText('Chat A')).toBeTruthy() // the history is still there
    cleanup()
    sessionStorage.clear()
    markSessionAlreadyStarted()
    const narrow = render(<LuciusHome />)
    const tight = narrow.container.querySelector('.lucius-home-shell') as HTMLElement
    Object.defineProperty(tight, 'clientWidth', { value: 1100, configurable: true })
    fireEvent.click(screen.getByRole('button', { name: 'Documents' }))
    expect(screen.queryByText('Chat A')).toBeNull() // made room: 1100 - 280 - 480 < 440
  })

  it('does not start another fresh conversation on a later mount within the same session', () => {
    const { unmount } = render(<LuciusHome />)
    expect(mockChat.createConversation).toHaveBeenCalledTimes(1)
    unmount()
    // Same tab, same session — sessionStorage survives the remount (a route change away
    // from /lucius and back, for instance), unlike localStorage.clear() in afterEach which
    // only ever runs between TESTS, not between renders within one.
    render(<LuciusHome />)
    expect(mockChat.createConversation).toHaveBeenCalledTimes(1)
  })

  // Regression: the history popover is far wider than its trigger button and sits a few px
  // below it, so a cursor moving from the button toward any row but the popover's own
  // leftmost sliver crosses a dead zone outside both elements' DOM subtrees. That used to
  // fire mouseleave (and close the popover) before the cursor ever arrived — owner report,
  // real device: "hover to show it, move toward it to click, and it disappears."
  it('keeps the hover popover open through a mouseleave, closing only after the grace period', () => {
    vi.useFakeTimers()
    try {
      markSessionAlreadyStarted()
      localStorage.setItem('luciusSideCollapsed', '1')
      render(<LuciusHome />)
      const wrap = screen
        .getByLabelText('Show chat history')
        .closest('.lucius-home-reopen-wrap') as HTMLElement

      fireEvent.mouseEnter(wrap)
      expect(screen.getByText('Chat A')).toBeTruthy()

      // The cursor leaving the button on its way to a row in the popover — must NOT close
      // immediately.
      fireEvent.mouseLeave(wrap)
      expect(screen.getByText('Chat A')).toBeTruthy()

      // Re-entering (landing on the popover itself) before the grace period elapses cancels
      // the pending close.
      fireEvent.mouseEnter(wrap)
      act(() => {
        vi.advanceTimersByTime(1000)
      })
      expect(screen.getByText('Chat A')).toBeTruthy()

      // A genuine, sustained leave still closes it.
      fireEvent.mouseLeave(wrap)
      act(() => {
        vi.advanceTimersByTime(300)
      })
      expect(screen.queryByText('Chat A')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  // Regression: a row's Rename/Delete kebab (ActionMenu) portals its dropdown to
  // document.body — outside the hover wrap's own DOM subtree — so moving the cursor from the
  // kebab trigger into the dropdown fires mouseleave on the wrap exactly like a genuine "moved
  // away" would. That used to close the whole flyout (menu included) out from under the user
  // mid-click — owner report: "tapping Rename or Delete is closing the floating panel", and
  // (after a first fix only covered the mouse/debounce path) "tapping on rename still close
  // the floating one though" — the SAME click also blurs the kebab trigger to the portaled
  // menu item, and the wrap's separate, UNDEBOUNCED onBlur handler closed on that immediately.
  it('keeps the hover popover open through opening AND selecting a row menu item, closing only on a later genuine mouseleave', () => {
    vi.useFakeTimers()
    try {
      markSessionAlreadyStarted()
      localStorage.setItem('luciusSideCollapsed', '1')
      render(<LuciusHome />)
      const wrap = screen
        .getByLabelText('Show chat history')
        .closest('.lucius-home-reopen-wrap') as HTMLElement

      fireEvent.mouseEnter(wrap)
      expect(document.querySelector('.lucius-home-reopen-preview')).toBeTruthy()

      // Open the row's kebab menu — ActionMenu's trigger carries aria-label={conv.title}, same
      // as the row's own select button, so target it by its distinct wrapper class instead.
      const kebabTrigger = document.querySelector('.conv-row-kebab button') as HTMLElement
      fireEvent.click(kebabTrigger)
      expect(screen.getByRole('menuitem', { name: 'Rename' })).toBeTruthy()

      // A real browser moves focus onto whatever was clicked — jsdom's fireEvent.click does
      // not, so simulate that explicitly: focus leaving the (real, in-DOM) kebab trigger for
      // the (portaled, out-of-DOM) menu item fires the wrap's UNDEBOUNCED onBlur immediately.
      // This is the exact path the mouseleave-debounce fix alone didn't cover.
      const portaledMenuItem = document.createElement('button')
      document.body.appendChild(portaledMenuItem)
      fireEvent.blur(kebabTrigger, { relatedTarget: portaledMenuItem })
      expect(document.querySelector('.lucius-home-reopen-preview')).toBeTruthy()
      document.body.removeChild(portaledMenuItem)

      // The cursor leaving the wrap on its way into the portaled menu — must NOT close the
      // flyout while the menu is still open, even well past the normal grace period.
      fireEvent.mouseLeave(wrap)
      act(() => {
        vi.advanceTimersByTime(1000)
      })
      expect(document.querySelector('.lucius-home-reopen-preview')).toBeTruthy()
      expect(screen.getByRole('menuitem', { name: 'Rename' })).toBeTruthy()

      // Selecting Rename: the click both fires ActionMenu's onSelect AND blurs the (real,
      // in-DOM) kebab trigger toward the (portaled, out-of-DOM) menu item — the flyout must
      // survive both, staying open with the row now showing its inline rename input, not
      // auto-closing a moment later out from under it.
      fireEvent.click(screen.getByRole('menuitem', { name: 'Rename' }))
      act(() => {
        vi.advanceTimersByTime(1000)
      })
      expect(document.querySelector('.lucius-home-reopen-preview')).toBeTruthy()
      expect(screen.getByRole('textbox', { name: 'Conversation title' })).toBeTruthy()

      // A later, genuine mouseleave of the wrap still closes it as normal — selecting a menu
      // item doesn't pin the flyout open forever.
      fireEvent.mouseLeave(wrap)
      act(() => {
        vi.advanceTimersByTime(300)
      })
      expect(document.querySelector('.lucius-home-reopen-preview')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })
})
