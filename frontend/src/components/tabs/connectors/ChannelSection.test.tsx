// @vitest-environment jsdom
//
// The webhook URL is a credential (a Slack/Discord incoming-webhook URL is itself a bearer
// token): the form never receives it. It shows `has_url` + the URL's host, the field is
// write-only like the secret, a blank field keeps the saved value, and the eye reads the
// saved one back through the connector-secret endpoint.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { WebhookSection } from './ChannelSection'
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
      <WebhookSection />
    </host.Provider>
  )
}

function webhook(overrides: Record<string, unknown> = {}) {
  return {
    name: 'webhook' as const,
    enabled: true,
    secret_header_name: 'X-Webhook-Secret',
    has_url: true,
    url_display: 'hooks.slack.com',
    has_secret: false,
    configured: true,
    ...overrides,
  }
}

function mockFetch(channel: ReturnType<typeof webhook>) {
  authFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (url === '/api/channels' && !init?.method) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ channels: [channel] }) })
    }
    if (url === '/api/connectors/webhook/secret/url') {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ value: 'https://hooks.slack.com/services/T0/B0/xyz' }),
      })
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve(channel) })
  })
}

function putBodies(): Record<string, unknown>[] {
  return authFetch.mock.calls
    .filter((c) => c[0] === '/api/channels/webhook' && (c[1] as RequestInit)?.method === 'PUT')
    .map((c) => JSON.parse((c[1] as RequestInit).body as string) as Record<string, unknown>)
}

async function typeInto(field: HTMLInputElement, value: string) {
  await act(async () => {
    fireEvent.change(field, { target: { value } })
  })
}

describe('WebhookSection — the URL is write-only', () => {
  it('shows the saved URL’s host, never the URL, and the field starts empty', async () => {
    mockFetch(webhook())
    render(<Harness hostRef={{ current: null }} />)
    const field = (await screen.findByLabelText(/^URL/)) as HTMLInputElement
    expect(field.value).toBe('')
    expect(field.placeholder).toContain('hooks.slack.com')
    expect(document.body.textContent).not.toContain('xyz')
  })

  it('saving without touching the URL sends no url, so the saved one is kept', async () => {
    mockFetch(webhook())
    const hostRef: { current: ConnectorSaveHost | null } = { current: null }
    render(<Harness hostRef={hostRef} />)
    await typeInto(
      (await screen.findByLabelText(/^Secret header name/)) as HTMLInputElement,
      'X-Sig',
    )

    await act(async () => {
      await hostRef.current!.run()
    })

    expect(putBodies()).toEqual([{ secret_header_name: 'X-Sig', enabled: true }])
  })

  it('a typed URL is sent, validated as http(s)', async () => {
    mockFetch(webhook())
    const hostRef: { current: ConnectorSaveHost | null } = { current: null }
    render(<Harness hostRef={hostRef} />)
    const field = (await screen.findByLabelText(/^URL/)) as HTMLInputElement

    // The footer's Save is disabled (not dirty) while the typed URL is not http(s).
    await typeInto(field, 'not a url')
    expect(hostRef.current!.state?.dirty).toBe(false)

    await typeInto(field, 'https://hooks.example.com/new')
    expect(hostRef.current!.state?.dirty).toBe(true)
    await act(async () => {
      await hostRef.current!.run()
    })
    expect(putBodies()[0]).toMatchObject({ url: 'https://hooks.example.com/new' })
  })

  it('a first save needs a URL', async () => {
    mockFetch(webhook({ has_url: false, url_display: '', configured: false, enabled: false }))
    const hostRef: { current: ConnectorSaveHost | null } = { current: null }
    render(<Harness hostRef={hostRef} />)
    const header = (await screen.findByLabelText(/^Secret header name/)) as HTMLInputElement

    await typeInto(header, 'X-Sig')
    expect(hostRef.current!.state?.dirty).toBe(false)

    await typeInto(screen.getByLabelText(/^URL/) as HTMLInputElement, 'https://hooks.example.com/a')
    expect(hostRef.current!.state?.dirty).toBe(true)
  })

  it('the eye reads the saved URL back through the connector-secret endpoint', async () => {
    mockFetch(webhook())
    render(<Harness hostRef={{ current: null }} />)
    const field = (await screen.findByLabelText(/^URL/)) as HTMLInputElement

    fireEvent.click(screen.getAllByRole('button', { name: /show|reveal/i })[0]!)

    await waitFor(() => expect(field.value).toBe('https://hooks.slack.com/services/T0/B0/xyz'))
    expect(authFetch).toHaveBeenCalledWith('/api/connectors/webhook/secret/url')
  })

  it('a revealed-but-unedited URL is not re-sent on save', async () => {
    mockFetch(webhook())
    const hostRef: { current: ConnectorSaveHost | null } = { current: null }
    render(<Harness hostRef={hostRef} />)
    const field = (await screen.findByLabelText(/^URL/)) as HTMLInputElement
    fireEvent.click(screen.getAllByRole('button', { name: /show|reveal/i })[0]!)
    await waitFor(() => expect(field.value).not.toBe(''))
    await typeInto(screen.getByLabelText(/^Secret header name/) as HTMLInputElement, 'X-Sig')

    await act(async () => {
      await hostRef.current!.run()
    })

    expect(putBodies()[0]).not.toHaveProperty('url')
  })
})
