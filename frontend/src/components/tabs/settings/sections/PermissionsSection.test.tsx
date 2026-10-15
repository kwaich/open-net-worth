// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearQueryCache } from '../../../../state/queryCache'
import { PermissionsSection } from './PermissionsSection'

vi.mock('../../../../api/client', () => ({ authFetch: vi.fn() }))

import { authFetch } from '../../../../api/client'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

describe('PermissionsSection', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clearQueryCache()
    mockAuthFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        permissions: [
          {
            tool: 'add_transaction',
            connector_id: 'core',
            level: 'always_allow',
            updated_at: '2026-09-21T10:00:00Z',
            tool_exists: true,
          },
          {
            tool: 'plaid_fetch_transactions',
            connector_id: 'plaid',
            level: 'always_allow',
            updated_at: '2026-09-21T09:00:00Z',
            tool_exists: true,
          },
          {
            tool: 'browser_click',
            connector_id: 'mcp:playwright',
            level: 'always_allow',
            updated_at: '2026-09-21T08:00:00Z',
            tool_exists: true,
          },
          {
            tool: 'web_search',
            connector_id: 'web_search:tavily',
            level: 'blocked',
            updated_at: '2026-09-21T07:00:00Z',
            tool_exists: true,
          },
        ],
        total: 4,
        yolo: false,
      }),
    } as Response)
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('shows only built-in permission decisions and counts only visible rows', async () => {
    const { container } = render(<PermissionsSection toast={vi.fn()} />)

    await screen.findByText('add_transaction')

    expect(screen.getByText('Built-in')).toBeTruthy()
    expect(screen.queryByText('plaid_fetch_transactions')).toBeNull()
    expect(screen.queryByText('browser_click')).toBeNull()
    expect(screen.queryByText('web_search')).toBeNull()

    await waitFor(() => {
      expect(container.querySelector('.rd-panel-title .rd-badge')?.textContent).toBe('1')
    })
  })
})
