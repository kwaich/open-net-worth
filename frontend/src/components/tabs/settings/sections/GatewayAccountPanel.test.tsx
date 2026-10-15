// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as client from '../../../../api/client'
import { GatewayAccountPanel } from './GatewayAccountPanel'

let submitted: { action: string; method: string; scope: string } | null = null

beforeEach(() => {
  submitted = null
  vi.spyOn(client, 'authFetch').mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({
      mode: 'gateway',
      gateway: { email: 'a@example.com', portal_url: 'https://cloud.example.com' },
    }),
  } as Response)
  vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(function (
    this: HTMLFormElement,
  ) {
    submitted = {
      action: this.getAttribute('action') ?? '',
      method: this.method,
      scope: (this.elements.namedItem('scope') as HTMLInputElement).value,
    }
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('GatewayAccountPanel', () => {
  it('shows who is signed in and links to the portal', async () => {
    render(<GatewayAccountPanel />)
    expect(await screen.findByText(/a@example\.com/)).toBeTruthy()
    const link = screen.getByRole('link', { name: 'Private Cloud portal' })
    expect(link.getAttribute('href')).toBe('https://cloud.example.com')
    expect(link.getAttribute('rel')).toContain('noopener')
  })

  it('signs out of this app with a POST scope=app after confirming', async () => {
    render(<GatewayAccountPanel />)
    await screen.findByText(/a@example\.com/)
    fireEvent.click(screen.getByRole('button', { name: 'Sign out of this app' }))
    expect(submitted).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }))
    await waitFor(() =>
      expect(submitted).toEqual({ action: '/_gateway/logout', method: 'post', scope: 'app' }),
    )
  })

  it('signs out everywhere with scope=everywhere', async () => {
    render(<GatewayAccountPanel />)
    await screen.findByText(/a@example\.com/)
    fireEvent.click(screen.getByRole('button', { name: 'Sign out everywhere' }))
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }))
    await waitFor(() => expect(submitted?.scope).toBe('everywhere'))
  })

  it('cancelling signs nobody out', async () => {
    render(<GatewayAccountPanel />)
    await screen.findByText(/a@example\.com/)
    fireEvent.click(screen.getByRole('button', { name: 'Sign out everywhere' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(submitted).toBeNull()
  })
})
