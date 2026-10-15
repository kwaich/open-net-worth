// @vitest-environment jsdom
//
// The per-conversation approval picker that rides in the composer toolbar
// (RFC 2026-08-27-per-conversation-approval-mode).
//
// It renders `ui/PermissionModeControl` — the SAME control the Automation editor uses. One
// three-way choice should not look like two different controls depending on where you meet
// it, which is why that component was promoted out of components/tabs/automations/ rather
// than a second one being written here.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { ConversationApprovalPicker } from './ConversationApprovalPicker'

vi.mock('../../api/permissionMode', () => ({
  getPermissionMode: vi.fn(),
  setPermissionMode: vi.fn(),
}))

import { getPermissionMode, setPermissionMode } from '../../api/permissionMode'

const mockGet = getPermissionMode as ReturnType<typeof vi.fn>
const mockSet = setPermissionMode as ReturnType<typeof vi.fn>

beforeEach(() => vi.clearAllMocks())
afterEach(cleanup)

function renderCluster(sessionId = 'conv-1') {
  return render(<ConversationApprovalPicker sessionId={sessionId} />)
}

const trigger = () => screen.getByRole('button', { name: /^Approvals in this conversation:/ })

describe('composer — per-conversation approvals', () => {
  it('shows the stored mode for this conversation', async () => {
    mockGet.mockResolvedValue({ mode: 'skip', editable: true })
    renderCluster()
    await waitFor(() => expect(trigger().textContent).toContain('Skip'))
  })

  it('defaults to Manual before the read resolves, never to Skip', () => {
    // The mode that ASKS is what a user must see while we do not yet know. A control that
    // flashed "Skip" during load would be telling them approvals are off when they are not.
    mockGet.mockReturnValue(new Promise(() => {}))
    const { container } = renderCluster('conv-never-read')
    expect(trigger().textContent).toContain('Manual')
    // ...and that placeholder is not SHOWN: painting "Manual" and flipping to the real mode a
    // moment later was a visible change on every open.
    expect(container.querySelector('.lx-approval-slot--pending')).toBeTruthy()
  })

  it('a conversation read once paints its real mode on the first frame of a revisit', async () => {
    mockGet.mockResolvedValue({ mode: 'auto', editable: true })
    const first = renderCluster('conv-revisit')
    await waitFor(() => expect(trigger().textContent).toContain('Auto'))
    first.unmount()

    mockGet.mockReturnValue(new Promise(() => {}))
    const { container } = renderCluster('conv-revisit')
    expect(trigger().textContent).toContain('Auto')
    expect(container.querySelector('.lx-approval-slot--pending')).toBeNull()
  })

  it('a FAILED read leaves Manual on screen', async () => {
    mockGet.mockRejectedValue(new Error('offline'))
    const { container } = renderCluster('conv-offline')
    await waitFor(() => expect(mockGet).toHaveBeenCalled())
    expect(trigger().textContent).toContain('Manual')
    await waitFor(() => expect(container.querySelector('.lx-approval-slot--pending')).toBeNull())
  })

  it('picking a mode writes it for THIS conversation', async () => {
    mockGet.mockResolvedValue({ mode: 'manual', editable: true })
    mockSet.mockResolvedValue('auto')
    renderCluster('conv-42')
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith('conv-42'))

    fireEvent.click(trigger())
    const menu = screen.getByRole('menu')
    fireEvent.click(within(menu).getByRole('menuitemradio', { name: 'Automatically approve' }))

    await waitFor(() => expect(mockSet).toHaveBeenCalledWith('conv-42', 'auto'))
    await waitFor(() => expect(trigger().textContent).toContain('Auto'))
  })

  it('a FAILED write REVERTS — the control never claims a mode the server did not store', async () => {
    // The failure that matters. Leaving "Skip" on screen after a rejected write costs the
    // user a belief about their own permissions that is not true.
    mockGet.mockResolvedValue({ mode: 'manual', editable: true })
    mockSet.mockRejectedValue(new Error('boom'))
    renderCluster()
    await waitFor(() => expect(mockGet).toHaveBeenCalled())

    fireEvent.click(trigger())
    fireEvent.click(
      within(screen.getByRole('menu')).getByRole('menuitemradio', { name: 'Skip all approvals' }),
    )

    await waitFor(() => expect(mockSet).toHaveBeenCalled())
    await waitFor(() => expect(trigger().textContent).toContain('Manual'))
  })

  it('renders nothing when there is no conversation yet', () => {
    renderCluster('')
    expect(screen.queryByRole('button', { name: /^Approvals in this conversation:/ })).toBeNull()
    expect(mockGet).not.toHaveBeenCalled()
  })

  it("an Automation run shows the AUTOMATION's mode, READ-ONLY", async () => {
    // The bug the owner caught on 2026-08-27: a run's conversation has a session row like any
    // other, so the picker read `manual` (never set) while the run was really executing in
    // `auto` — and the gate ignores the conversation's mode inside a run anyway, so changing
    // it did nothing. The endpoint now reports the AUTOMATION's mode with editable:false.
    mockGet.mockResolvedValue({ mode: 'auto', editable: false })
    renderCluster('automation-run-1')

    await waitFor(() => expect(trigger().textContent).toContain('Auto'))
    expect(trigger().hasAttribute('disabled')).toBe(true)

    fireEvent.click(trigger())
    expect(screen.queryByRole('menu')).toBeNull()
    expect(mockSet).not.toHaveBeenCalled()
  })
})
