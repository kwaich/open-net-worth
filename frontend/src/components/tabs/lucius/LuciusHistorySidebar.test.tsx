// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { LuciusHistorySidebar } from './LuciusHistorySidebar'
import type { ConversationMeta } from '../../../hooks/useConversations'

afterEach(cleanup)

const conv = (over: Partial<ConversationMeta>): ConversationMeta => ({
  id: 'c1',
  title: 'Rebalancing after the NVDA rally',
  createdAt: '2026-08-12T03:08:00Z',
  updatedAt: '2026-08-12T03:08:00Z',
  messageCount: 4,
  model: '',
  ...over,
})

function baseProps() {
  return {
    groups: [
      {
        key: 'chat',
        items: [conv({ id: 'a', title: 'Chat A' }), conv({ id: 'b', title: 'Chat B' })],
        total: 2,
      },
    ] as { key: 'chat' | 'automation'; items: ConversationMeta[]; total: number }[],
    activeId: 'a',
    statusFor: () => 'idle' as const,
    onNewChat: vi.fn(),
    onSelect: vi.fn(),
    onRename: vi.fn(),
    onDelete: vi.fn(),
    onStop: vi.fn(),
    onViewAll: vi.fn(),
    onMarkAllRead: vi.fn(),
    onCollapse: vi.fn(),
  }
}

describe('LuciusHistorySidebar', () => {
  it('renders a New button that fires onNewChat', () => {
    const props = baseProps()
    render(<LuciusHistorySidebar {...props} />)
    fireEvent.click(screen.getByText('New'))
    expect(props.onNewChat).toHaveBeenCalledTimes(1)
  })

  it('renders every chat row from the groups prop', () => {
    render(<LuciusHistorySidebar {...baseProps()} />)
    expect(screen.getByText('Chat A')).toBeTruthy()
    expect(screen.getByText('Chat B')).toBeTruthy()
  })

  it('selecting a row fires onSelect with its id', () => {
    const props = baseProps()
    render(<LuciusHistorySidebar {...props} />)
    fireEvent.click(screen.getByText('Chat B'))
    expect(props.onSelect).toHaveBeenCalledWith('b')
  })

  it('the collapse control fires onCollapse', () => {
    const props = baseProps()
    render(<LuciusHistorySidebar {...props} />)
    fireEvent.click(screen.getByLabelText('Hide chat history'))
    expect(props.onCollapse).toHaveBeenCalledTimes(1)
  })

  it('shows an empty state with no chats', () => {
    const props = baseProps()
    props.groups = []
    render(<LuciusHistorySidebar {...props} />)
    expect(screen.getByText('No chats yet.')).toBeTruthy()
  })

  it('a non-empty Automation runs group renders its own section with a View all runs link', () => {
    const props = baseProps()
    props.groups = [
      ...props.groups,
      {
        key: 'automation',
        items: [conv({ id: 'r1', source: 'automation', title: 'Monthly digest' })],
        total: 1,
      },
    ]
    render(<LuciusHistorySidebar {...props} />)
    expect(screen.getByText('Monthly digest')).toBeTruthy()
    fireEvent.click(screen.getByText('View all runs'))
    expect(props.onViewAll).toHaveBeenCalledWith('automation')
  })

  describe('Mark all read', () => {
    function withRuns() {
      const props = baseProps()
      props.groups = [
        ...props.groups,
        {
          key: 'automation',
          // Seven runs: the rail shows five, so the two it hides must be cleared too.
          items: Array.from({ length: 7 }, (_, i) =>
            conv({ id: `r${i}`, source: 'automation', title: `Run ${i}` }),
          ),
          total: 7,
        },
      ]
      return props
    }

    it('is absent when nothing in the section is unread', () => {
      render(<LuciusHistorySidebar {...withRuns()} />)
      expect(screen.queryByLabelText('Mark all read')).toBeNull()
    })

    it('marks every unread run read — including rows the rail does not show', () => {
      const props = withRuns()
      props.statusFor = ((id: string) =>
        ['r0', 'r5', 'r6', 'a'].includes(id) ? 'done' : 'idle') as never
      render(<LuciusHistorySidebar {...props} />)

      // The runs section header carries the only run-scoped button; chats has its own.
      const buttons = screen.getAllByLabelText('Mark all read')
      expect(buttons).toHaveLength(2)
      fireEvent.click(buttons[0]!)

      expect(props.onMarkAllRead).toHaveBeenCalledWith(['r0', 'r5', 'r6'])
    })

    it('counts a failed run as unread, but never a working or needs-you one', () => {
      const props = withRuns()
      props.statusFor = ((id: string) =>
        ({ r0: 'failed', r1: 'working', r2: 'needs_you' })[id] ?? 'idle') as never
      render(<LuciusHistorySidebar {...props} />)

      fireEvent.click(screen.getByLabelText('Mark all read'))

      expect(props.onMarkAllRead).toHaveBeenCalledWith(['r0'])
    })
  })
})
