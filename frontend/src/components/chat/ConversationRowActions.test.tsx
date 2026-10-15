// @vitest-environment jsdom
//
// Rename was lost when the header dropdown became the sidebar (owner, 2026-08-06: "after
// moving to the new panel, we lose the capability of rename a conversation"). A chat is
// titled from its first message, so without rename a bad title was permanent — the only
// repair was to start the conversation over.
//
// What is pinned here:
//   1. every row in BOTH surfaces (sidebar + full list) carries a kebab
//   2. Rename edits in place and commits on Enter; Escape leaves the title alone
//   3. Delete is offered for chats and NOT for Automation runs — the server refuses those
//      with a 409, so the item would be a button that silently does nothing
//   4. opening the kebab does not switch conversation behind it

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react'
import { ConversationMenu } from './ConversationMenu'
import { ConversationBrowser } from './ConversationBrowser'
import type { ConversationMeta } from '../../hooks/useConversations'
import type { ConvRowStatus } from '../../types/chat'

function conv(over: Partial<ConversationMeta> & { id: string }): ConversationMeta {
  return {
    title: 'Rebalancing the taxable account',
    createdAt: '2026-08-06T09:00:00.000Z',
    updatedAt: '2026-08-06T09:00:00.000Z',
    messageCount: 4,
    model: '',
    ...over,
  }
}

const CHAT = conv({ id: 'c1' })
const RUN = conv({ id: 'r1', title: 'Daily Transactions Summary', source: 'automation' })

// Every row is 'idle' by default — individual tests override an id to exercise the
// Quick-Stop wiring (§5.1.3) without dragging in the real luciusChat provider.
const statusMap: Partial<Record<string, ConvRowStatus>> = {}
function statusFor(id: string): ConvRowStatus {
  return statusMap[id] ?? 'idle'
}

const handlers = {
  onSelect: vi.fn(),
  onRename: vi.fn(),
  onDelete: vi.fn(),
  onClose: vi.fn(),
  onNewChat: vi.fn(),
  onViewAll: vi.fn(),
  onStop: vi.fn(),
}

function renderMenu() {
  return render(
    <ConversationMenu
      groups={[
        { key: 'automation', items: [RUN], total: 1 },
        { key: 'chat', items: [CHAT], total: 1 },
      ]}
      activeId="c1"
      statusFor={statusFor}
      formatWhen={() => 'Aug 6'}
      {...handlers}
    />,
  )
}

function renderBrowser() {
  return render(
    <ConversationBrowser
      conversations={[CHAT, RUN]}
      activeId="c1"
      statusFor={statusFor}
      formatWhen={() => 'Aug 6'}
      onSelect={handlers.onSelect}
      onClose={handlers.onClose}
      onRename={handlers.onRename}
      onDelete={handlers.onDelete}
      onStop={handlers.onStop}
    />,
  )
}

/** Open the kebab for a conversation. Both it and the row button are named after the
 *  conversation — the kebab is the one that owns a menu. A predicate `name` matcher
 *  (not a plain string, which `getByRole` treats as an EXACT match) because a non-idle
 *  row's accessible name also carries its status word ("… Working…") — the row button's
 *  name CONTAINS the title, it is no longer equal to it. */
function openKebab(title: string) {
  const trigger = screen
    .getAllByRole('button', { name: (accessibleName) => accessibleName.includes(title) })
    .find((button) => button.getAttribute('aria-haspopup') === 'menu')
  fireEvent.click(trigger!)
  return screen.getByRole('menu', { name: title })
}

beforeEach(() => {
  vi.clearAllMocks()
  cleanup()
  delete statusMap.c1
  delete statusMap.r1
})

describe.each([
  ['sidebar', renderMenu],
  ['browser', renderBrowser],
])('conversation row actions — %s', (_label, renderSurface) => {
  it('offers Rename and Delete on a chat', () => {
    renderSurface()
    const menu = openKebab(CHAT.title)
    expect(within(menu).getByRole('menuitem', { name: 'Rename' })).toBeTruthy()
    expect(within(menu).getByRole('menuitem', { name: 'Delete' })).toBeTruthy()
  })

  it('offers Rename but NOT Delete on an Automation run', () => {
    renderSurface()
    const menu = openKebab(RUN.title)
    expect(within(menu).getByRole('menuitem', { name: 'Rename' })).toBeTruthy()
    expect(within(menu).queryByRole('menuitem', { name: 'Delete' })).toBeNull()
  })

  it('renames in place, committing on Enter', () => {
    renderSurface()
    fireEvent.click(within(openKebab(CHAT.title)).getByRole('menuitem', { name: 'Rename' }))

    const input = screen.getByRole('textbox', { name: 'Conversation title' })
    expect((input as HTMLInputElement).value).toBe(CHAT.title)
    fireEvent.change(input, { target: { value: 'Tax-loss harvesting' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(handlers.onRename).toHaveBeenCalledWith('c1', 'Tax-loss harvesting')
    // Back to a row, not left in edit mode.
    expect(screen.queryByRole('textbox', { name: 'Conversation title' })).toBeNull()
  })

  it('leaves the title alone on Escape — including the blur that follows it', () => {
    renderSurface()
    fireEvent.click(within(openKebab(CHAT.title)).getByRole('menuitem', { name: 'Rename' }))

    const input = screen.getByRole('textbox', { name: 'Conversation title' })
    fireEvent.change(input, { target: { value: 'nope' } })
    fireEvent.keyDown(input, { key: 'Escape' })
    fireEvent.blur(input)

    expect(handlers.onRename).not.toHaveBeenCalled()
  })

  it('shows a confirm dialog before deleting, not the native browser confirm', () => {
    const confirmSpy = vi.spyOn(window, 'confirm')
    renderSurface()
    fireEvent.click(within(openKebab(CHAT.title)).getByRole('menuitem', { name: 'Delete' }))

    expect(confirmSpy).not.toHaveBeenCalled()
    expect(handlers.onDelete).not.toHaveBeenCalled()
    const dialog = screen.getByRole('dialog', { name: 'Delete conversation?' })
    expect(within(dialog).getByText(new RegExp(CHAT.title))).toBeTruthy()
    confirmSpy.mockRestore()
  })

  it('deletes the chat once the dialog is confirmed', () => {
    renderSurface()
    fireEvent.click(within(openKebab(CHAT.title)).getByRole('menuitem', { name: 'Delete' }))
    const dialog = screen.getByRole('dialog', { name: 'Delete conversation?' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }))

    expect(handlers.onDelete).toHaveBeenCalledWith('c1')
    expect(screen.queryByRole('dialog', { name: 'Delete conversation?' })).toBeNull()
  })

  it('does not delete when the dialog is cancelled', () => {
    renderSurface()
    fireEvent.click(within(openKebab(CHAT.title)).getByRole('menuitem', { name: 'Delete' }))
    const dialog = screen.getByRole('dialog', { name: 'Delete conversation?' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    expect(handlers.onDelete).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog', { name: 'Delete conversation?' })).toBeNull()
  })

  it('does not switch conversation when the kebab is used', () => {
    renderSurface()
    openKebab(CHAT.title)
    expect(handlers.onSelect).not.toHaveBeenCalled()
  })

  it('still switches conversation when the row itself is clicked', () => {
    renderSurface()
    fireEvent.click(screen.getByText(RUN.title))
    expect(handlers.onSelect).toHaveBeenCalledWith('r1')
  })

  // Quick-Stop on a `working` row (§5.1.3) — reuses the existing kebab, not a new button.
  it('offers Stop, above Rename, on a working row', () => {
    statusMap.c1 = 'working'
    renderSurface()
    const menu = openKebab(CHAT.title)
    const labels = within(menu)
      .getAllByRole('menuitem')
      .map((item) => item.textContent)
    expect(labels[0]).toBe('Stop')
    expect(labels).toContain('Rename')
  })

  it('calls onStop with the row id, not onDelete/onSelect', () => {
    statusMap.c1 = 'working'
    renderSurface()
    fireEvent.click(within(openKebab(CHAT.title)).getByRole('menuitem', { name: 'Stop' }))
    expect(handlers.onStop).toHaveBeenCalledWith('c1')
    expect(handlers.onDelete).not.toHaveBeenCalled()
    expect(handlers.onSelect).not.toHaveBeenCalled()
  })

  // Deliberately NOT offered on needs_you — approving/denying a money write blind, from a
  // list row without seeing the change, is exactly the shortcut this app should not offer.
  it('does NOT offer Stop on a needs_you row', () => {
    statusMap.c1 = 'needs_you'
    renderSurface()
    const menu = openKebab(CHAT.title)
    expect(within(menu).queryByRole('menuitem', { name: 'Stop' })).toBeNull()
  })

  it('does not offer Stop on an idle row', () => {
    renderSurface()
    const menu = openKebab(CHAT.title)
    expect(within(menu).queryByRole('menuitem', { name: 'Stop' })).toBeNull()
  })
})
