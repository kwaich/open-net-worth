// @vitest-environment jsdom
//
// Email is ONE connector with ONE save (RFC connectors-table): the form reads a single
// `GET /api/email/config` and writes the whole flat shape with a single
// `PUT /api/email/config`. There used to be two requests (IMAP + SMTP) with partial-failure
// reporting between them; with one request a failure leaves nothing half-saved, so the only
// failure modes left are "the server said no" and "the server was unreachable".
//
// One shared identity drives IMAP, SMTP and the alert recipient — no per-leg override for
// username/password/from/to/TLS, no status badges, no per-connector test-connection UI. TLS
// is always sent on.
//
// This file exercises the real component, not just pure helper functions, mirroring
// connectorSectionSave.test.tsx's harness shape.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { EmailUnifiedSection } from './EmailUnifiedSection'
import { useConnectorSaveHost, type ConnectorSaveHost } from './formSave'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

beforeEach(() => {
  authFetch.mockReset()
})
afterEach(cleanup)

function Harness({ hostRef }: { hostRef: { current: ConnectorSaveHost | null } }) {
  const host = useConnectorSaveHost()
  hostRef.current = host
  return (
    <host.Provider>
      <EmailUnifiedSection />
    </host.Provider>
  )
}

function putBodies(): Record<string, unknown>[] {
  return authFetch.mock.calls
    .filter((c) => (c[1] as RequestInit | undefined)?.method === 'PUT')
    .map((c) => JSON.parse((c[1] as RequestInit).body as string) as Record<string, unknown>)
}

function putUrls(): string[] {
  return authFetch.mock.calls
    .filter((c) => (c[1] as RequestInit | undefined)?.method === 'PUT')
    .map((c) => c[0] as string)
}

function getCallCount(url: string): number {
  return authFetch.mock.calls.filter(
    (c) => c[0] === url && !(c[1] as RequestInit | undefined)?.method,
  ).length
}

function emailConfig(overrides: Record<string, unknown> = {}) {
  return {
    configured: true,
    enabled: true,
    has_password: true,
    provider: null,
    address: 'me@example.com',
    imap_host: 'imap.example.com',
    imap_port: 993,
    imap_folder: 'INBOX',
    lookback_days: 7,
    smtp_host: 'smtp.example.com',
    smtp_port: 587,
    use_tls: true,
    last_status: 'never_run',
    last_error: null,
    ...overrides,
  }
}

function mockFetch(
  cfg: ReturnType<typeof emailConfig>,
  put: (() => Promise<unknown>) | null = null,
) {
  authFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (url === '/api/email/config' && init?.method === 'PUT') {
      return put ? put() : Promise.resolve({ ok: true, json: () => Promise.resolve(cfg) })
    }
    if (url === '/api/email/config') {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(cfg) })
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
  })
}

async function typeInto(field: HTMLInputElement, value: string) {
  await act(async () => {
    field.focus()
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!
    setter.call(field, value)
    field.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

// ── one request, the whole flat shape ────────────────────────────────────────────────

describe('EmailUnifiedSection — one save carries the whole account', () => {
  it('an unrelated-field-only save (Folder) sends the full account in ONE request', async () => {
    mockFetch(emailConfig())
    const hostRef: { current: ConnectorSaveHost | null } = { current: null }
    render(<Harness hostRef={hostRef} />)
    await typeInto((await screen.findByLabelText('Mail folder')) as HTMLInputElement, 'Statements')

    await act(async () => {
      await hostRef.current!.run()
    })

    expect(putUrls()).toEqual(['/api/email/config'])
    expect(putBodies()[0]).toEqual({
      address: 'me@example.com',
      provider: 'custom',
      imap_host: 'imap.example.com',
      imap_port: 993,
      imap_folder: 'Statements',
      smtp_host: 'smtp.example.com',
      smtp_port: 587,
      use_tls: true,
      enabled: true,
    })
  })

  it('editing the email field changes the one address, and a typed password rides along', async () => {
    mockFetch(emailConfig())
    const hostRef: { current: ConnectorSaveHost | null } = { current: null }
    render(<Harness hostRef={hostRef} />)
    await typeInto(
      (await screen.findByLabelText(/^Email \/ username/)) as HTMLInputElement,
      'newme@example.com',
    )
    await typeInto(screen.getByLabelText(/^App password/) as HTMLInputElement, 'new-app-pw')

    await act(async () => {
      await hostRef.current!.run()
    })

    expect(putBodies()).toHaveLength(1)
    expect(putBodies()[0]).toMatchObject({ address: 'newme@example.com', password: 'new-app-pw' })
    // No retired split field is ever sent.
    for (const retired of ['imap_user', 'imap_password', 'smtp_user', 'from_addr', 'to_addr']) {
      expect(putBodies()[0]).not.toHaveProperty(retired)
    }
  })

  it('a blank password is not sent, so the saved one is kept', async () => {
    mockFetch(emailConfig())
    const hostRef: { current: ConnectorSaveHost | null } = { current: null }
    render(<Harness hostRef={hostRef} />)
    await typeInto((await screen.findByLabelText('Mail folder')) as HTMLInputElement, 'Receipts')

    await act(async () => {
      await hostRef.current!.run()
    })

    expect(putBodies()[0]).not.toHaveProperty('password')
  })

  it('a first save turns the connector on; a later save keeps a Paused one Paused', async () => {
    mockFetch(emailConfig({ configured: false, enabled: false, has_password: false, address: '' }))
    const hostRef: { current: ConnectorSaveHost | null } = { current: null }
    render(<Harness hostRef={hostRef} />)
    await typeInto(
      (await screen.findByLabelText(/^Email \/ username/)) as HTMLInputElement,
      'a@b.c',
    )
    await typeInto(screen.getByLabelText(/^App password/) as HTMLInputElement, 'pw')
    await act(async () => {
      await hostRef.current!.run()
    })
    expect(putBodies()[0]).toMatchObject({ enabled: true })

    cleanup()
    authFetch.mockReset()
    mockFetch(emailConfig({ enabled: false }))
    render(<Harness hostRef={hostRef} />)
    await typeInto((await screen.findByLabelText('Mail folder')) as HTMLInputElement, 'Receipts')
    await act(async () => {
      await hostRef.current!.run()
    })
    expect(putBodies()[0]).toMatchObject({ enabled: false })
  })

  it('shows the last error the server recorded', async () => {
    mockFetch(emailConfig({ last_error: 'authentication failed' }))
    render(<Harness hostRef={{ current: null }} />)
    expect(await screen.findByText(/authentication failed/)).toBeTruthy()
  })
})

// ── failure: nothing is half-saved, one message ──────────────────────────────────────

describe('EmailUnifiedSection — failure reporting on the single save', () => {
  it('surfaces the server’s reason and keeps the form', async () => {
    mockFetch(emailConfig(), () =>
      Promise.resolve({ ok: false, json: () => Promise.resolve({ detail: 'bad port' }) }),
    )
    const hostRef: { current: ConnectorSaveHost | null } = { current: null }
    render(<Harness hostRef={hostRef} />)
    await typeInto((await screen.findByLabelText('Mail folder')) as HTMLInputElement, 'Receipts')

    await act(async () => {
      expect(await hostRef.current!.run()).toBe(false)
    })

    expect(screen.getByText('bad port')).toBeTruthy()
    expect((screen.getByLabelText('Mail folder') as HTMLInputElement).value).toBe('Receipts')
    expect(putUrls()).toEqual(['/api/email/config'])
  })

  it('a network-level rejection reports the server as unreachable', async () => {
    mockFetch(emailConfig(), () => Promise.reject(new Error('network down')))
    const hostRef: { current: ConnectorSaveHost | null } = { current: null }
    render(<Harness hostRef={hostRef} />)
    await typeInto((await screen.findByLabelText('Mail folder')) as HTMLInputElement, 'Receipts')

    await act(async () => {
      expect(await hostRef.current!.run()).toBe(false)
    })

    expect(screen.getByText(/could not reach the server/i)).toBeTruthy()
  })

  it('reloads from the server after a successful save', async () => {
    mockFetch(emailConfig())
    const hostRef: { current: ConnectorSaveHost | null } = { current: null }
    render(<Harness hostRef={hostRef} />)
    await typeInto((await screen.findByLabelText('Mail folder')) as HTMLInputElement, 'Receipts')
    const getsBefore = getCallCount('/api/email/config')

    await act(async () => {
      expect(await hostRef.current!.run()).toBe(true)
    })

    expect(getCallCount('/api/email/config')).toBeGreaterThan(getsBefore)
  })
})

// ── the provider chip reflects the account, and one tap never flips it to Custom ─────

describe('EmailUnifiedSection — provider chip', () => {
  const gmail = {
    imap_host: 'imap.gmail.com',
    imap_port: 993,
    smtp_host: 'smtp.gmail.com',
    smtp_port: 587,
    address: 'me@gmail.com',
  }
  const chip = (name: string) => screen.getByRole('button', { name })

  it("shows Gmail for Gmail's own servers even when the saved choice says custom", async () => {
    mockFetch(emailConfig({ ...gmail, provider: 'custom' }))
    render(<Harness hostRef={{ current: null }} />)
    await screen.findByRole('button', { name: 'Gmail' })
    expect(chip('Gmail').className).toContain('preset-chip--active')
    expect(chip('Custom').className).not.toContain('preset-chip--active')
  })

  it('tapping the chip that is already selected keeps it selected', async () => {
    mockFetch(emailConfig({ ...gmail, provider: 'gmail' }))
    render(<Harness hostRef={{ current: null }} />)
    await screen.findByRole('button', { name: 'Gmail' })
    await act(async () => chip('Gmail').click())
    expect(chip('Gmail').className).toContain('preset-chip--active')
  })

  it('a genuinely custom server still shows Custom', async () => {
    mockFetch(emailConfig({ provider: 'custom' }))
    render(<Harness hostRef={{ current: null }} />)
    await screen.findByRole('button', { name: 'Custom' })
    expect(chip('Custom').className).toContain('preset-chip--active')
  })
})

describe('EmailUnifiedSection — a fresh connector', () => {
  it('opens on Gmail with its servers filled in and hidden, not on Custom', async () => {
    mockFetch(
      emailConfig({
        configured: false,
        has_password: false,
        address: '',
        imap_host: '',
        smtp_host: '',
      }),
    )
    const hostRef: { current: ConnectorSaveHost | null } = { current: null }
    render(<Harness hostRef={hostRef} />)
    await screen.findByLabelText('Mail folder')
    const gmail = screen.getByRole('button', { name: 'Gmail' })
    expect(gmail.className).toContain('preset-chip--active')
    expect(screen.queryByLabelText(/IMAP host/)).toBeNull()
  })
})
