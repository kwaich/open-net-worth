// @vitest-environment jsdom
//
// Two security-review fixes (findings 1 + 5), both about a control that must not go unseen
// or unconfirmed:
//
//   1. A polarity CAUTION (an implausible income/expense split — the runtime signal for a
//      backwards amount sign, see backend/services/aggregation/lunch_flow.py) must render
//      even on a technically-"ok" sync — the run-log itself only shows on a non-ok status,
//      which would otherwise bury the one signal that matters most.
//   2. Disconnect fires an irreversible DELETE at the vendor — it must ask first, like every
//      other irreversible control in this app, rather than firing on a single click.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'
import { LunchFlowSection } from './LunchFlowSection'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

const BASE_STATUS = {
  configured: true,
  enabled: true,
  has_credentials: true,
  client_id: 'lf_client_1',
  email: '',
  has_link: true,
  user_id: 'u1',
  last_synced_at: '2026-08-17T00:00:00Z',
  last_status: 'ok',
  last_error: null,
  last_caution: null,
  connections: [{ connection_id: 100, institution_name: 'Example Bank', accounts: [] }],
}

function mockStatus(overrides: Record<string, unknown> = {}) {
  const status = { ...BASE_STATUS, ...overrides }
  authFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (url === '/api/lunch-flow/status' && !init?.method) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(status) })
    }
    if (url.startsWith('/api/lunch-flow/connections/') && init?.method === 'DELETE') {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) })
    }
    // The Accounts list reads the shared GET /api/accounts cache — an empty array is the
    // honest "no app accounts yet" answer.
    if (url === '/api/accounts')
      return Promise.resolve({ ok: true, json: () => Promise.resolve([]) })
    // ConnectorAccountsList's own self-fetch — `accounts` is never omitted by the real
    // backend (connector_fetch.provider_accounts always includes it), so the component
    // doesn't guard against it being absent.
    if (url === '/api/connectors/lunch_flow/accounts') {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ status: 'ok', provider_label: 'Lunch Flow', accounts: [] }),
      })
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
  })
  return status
}

beforeEach(() => {
  authFetch.mockReset()
})
afterEach(cleanup)

async function renderSection() {
  render(<LunchFlowSection />)
  // Client ID is a required field (asterisk appended to the label) since the fix in
  // EmailUnifiedSection's style — match from the start rather than exact.
  await screen.findByLabelText(/^Client ID/)
}

describe('a polarity caution is always visible, regardless of last_status', () => {
  it('renders nothing when there is no caution', async () => {
    mockStatus({ last_caution: null })
    await renderSection()
    expect(screen.queryByText(/^Caution:/)).toBeNull()
  })

  it('shows the caution text on a technically-"ok" sync', async () => {
    mockStatus({
      last_status: 'ok',
      last_caution:
        '12 of 12 imported transactions (100%) this run mapped to income — this looks like a backwards amount sign.',
    })
    await renderSection()
    expect(await screen.findByText(/^Caution:/)).toBeTruthy()
    expect(screen.getByText(/backwards amount sign/)).toBeTruthy()
    // The status line still renders normally for an "ok" status — the caution is a
    // SEPARATE signal, not a replacement for the form's own status reporting.
    expect(screen.getByText(/Last fetched/)).toBeTruthy()
  })

  it('shows the caution alongside a non-ok status too', async () => {
    mockStatus({ last_status: 'auth_error', last_caution: 'looks backwards' })
    await renderSection()
    expect(await screen.findByText(/^Caution:/)).toBeTruthy()
  })
})

describe('disconnecting a linked institution confirms first', () => {
  it('does not call DELETE until the confirm dialog is accepted', async () => {
    mockStatus()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await renderSection()

    fireEvent.click(await screen.findByRole('button', { name: 'Disconnect' }))

    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(confirmSpy.mock.calls[0]?.[0]).toContain('Example Bank')
    expect(
      authFetch.mock.calls.some(
        (c) => String(c[0]).includes('/connections/') && (c[1] as RequestInit)?.method === 'DELETE',
      ),
    ).toBe(false)
    confirmSpy.mockRestore()
  })

  it('calls DELETE once the confirm dialog is accepted', async () => {
    mockStatus()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    await renderSection()

    fireEvent.click(await screen.findByRole('button', { name: 'Disconnect' }))

    await waitFor(() =>
      expect(
        authFetch.mock.calls.some(
          (c) =>
            String(c[0]) === '/api/lunch-flow/connections/100' &&
            (c[1] as RequestInit)?.method === 'DELETE',
        ),
      ).toBe(true),
    )
    confirmSpy.mockRestore()
  })
})
