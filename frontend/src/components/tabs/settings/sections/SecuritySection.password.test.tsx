// @vitest-environment jsdom
//
// The Password panel has three shapes, chosen by what the SERVER says about this very request:
// no password yet (Set), a password and the desktop app's own session (Reset — no Current),
// and everyone else (Change — Current + New + Confirm).

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as client from '../../../../api/client'
import { clearQueryCache } from '../../../../state/queryCache'
import { SecuritySection } from './SecuritySection'

type PasswordState = {
  password_set: boolean
  current_password_required: boolean
  trust_loopback?: boolean
}

let posts: { path: string; body: Record<string, unknown> }[] = []

function serve(
  state: PasswordState,
  refuseWrites?: { status: number; detail: string; retryAfter?: string },
) {
  vi.spyOn(client, 'authFetch').mockImplementation(async (input, init) => {
    const path = String(input)
    if (init?.method === 'POST') {
      posts.push({ path, body: JSON.parse(String(init.body)) })
      if (refuseWrites) {
        return {
          ok: false,
          status: refuseWrites.status,
          headers: new Headers(
            refuseWrites.retryAfter ? { 'Retry-After': refuseWrites.retryAfter } : {},
          ),
          json: async () => ({ detail: refuseWrites.detail }),
        } as Response
      }
      return { ok: true, status: 200, json: async () => ({ ok: true }) } as Response
    }
    const body = path.includes('api-token')
      ? { token: 'synthetic-token', configured: true }
      : { session_ttl: 2592000, trust_loopback: true, auth_disabled: false, ...state }
    return { ok: true, status: 200, json: async () => body } as Response
  })
}

beforeEach(() => {
  clearQueryCache()
  posts = []
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('SecuritySection password panel', () => {
  it('no password set: "Set a password" with New + Confirm and no Current field', async () => {
    serve({ password_set: false, current_password_required: false })
    render(<SecuritySection toast={vi.fn()} />)

    expect(await screen.findByText('Set a password')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Set password' })).toBeTruthy()
    expect(screen.getByText('New password')).toBeTruthy()
    expect(screen.getByText('Confirm password')).toBeTruthy()
    expect(screen.queryByText('Current password')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Remove password' })).toBeNull()
  })

  it("desktop's own book: no Current field, the note, and a Reset button that sends no old password", async () => {
    serve({ password_set: true, current_password_required: false })
    const toast = vi.fn()
    const { container } = render(<SecuritySection toast={toast} />)

    const reset = await screen.findByRole('button', { name: 'Reset password' })
    expect(screen.queryByText('Current password')).toBeNull()
    expect(
      screen.getByText(
        /You're on this computer's own book, so the current password isn't needed\./,
      ),
    ).toBeTruthy()

    const [fresh, confirm] = Array.from(
      container.querySelectorAll<HTMLInputElement>('input[autocomplete="new-password"]'),
    )
    fireEvent.change(fresh!, { target: { value: 'brand-new-pw' } })
    fireEvent.change(confirm!, { target: { value: 'brand-new-pw' } })
    fireEvent.click(reset)

    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0]!.path).toBe('/api/auth/change-password')
    expect(posts[0]!.body).toEqual({ new_password: 'brand-new-pw' })
  })

  it('anywhere else: Current + New + Confirm, Change button, and the recovery pointer', async () => {
    serve({ password_set: true, current_password_required: true })
    render(<SecuritySection toast={vi.fn()} />)

    expect(await screen.findByRole('button', { name: 'Change password' })).toBeTruthy()
    expect(screen.getByText('Current password')).toBeTruthy()
    expect(screen.getByText('New password')).toBeTruthy()
    expect(screen.getByText('Confirm password')).toBeTruthy()
    expect(screen.getByText(/Forgot it\? In the desktop app, set a new one here\./)).toBeTruthy()
    expect(screen.getByText('onw reset-password')).toBeTruthy()
    expect(screen.queryByText(/auth\.json/)).toBeNull()
  })

  it('enforces the same 8-character minimum as setup before it calls the server', async () => {
    serve({ password_set: false, current_password_required: false })
    const toast = vi.fn()
    const { container } = render(<SecuritySection toast={toast} />)

    const button = await screen.findByRole('button', { name: 'Set password' })
    const [fresh, confirm] = Array.from(
      container.querySelectorAll<HTMLInputElement>('input[autocomplete="new-password"]'),
    )
    fireEvent.change(fresh!, { target: { value: 'seven77' } })
    fireEvent.change(confirm!, { target: { value: 'seven77' } })
    fireEvent.click(button)

    expect(toast).toHaveBeenCalledWith('New password must be at least 8 characters')
    expect(posts).toHaveLength(0)
  })

  it('refuses a password of only spaces before it calls the server', async () => {
    serve({ password_set: false, current_password_required: false })
    const toast = vi.fn()
    const { container } = render(<SecuritySection toast={toast} />)

    const button = await screen.findByRole('button', { name: 'Set password' })
    const [fresh, confirm] = Array.from(
      container.querySelectorAll<HTMLInputElement>('input[autocomplete="new-password"]'),
    )
    fireEvent.change(fresh!, { target: { value: '          ' } })
    fireEvent.change(confirm!, { target: { value: '          ' } })
    fireEvent.click(button)

    expect(toast).toHaveBeenCalledWith('Enter a new password')
    expect(posts).toHaveLength(0)
  })

  it.each([
    ['900', 'Too many attempts. Try again in 15 minutes.'],
    ['61', 'Too many attempts. Try again in 2 minutes.'],
    ['30', 'Too many attempts. Try again in 1 minute.'],
    [undefined, 'Too many attempts. Try again in 15 minutes.'],
  ])(
    'a 429 (Retry-After %s) reads like the login screen, not the raw detail',
    async (retryAfter, message) => {
      serve(
        { password_set: true, current_password_required: true },
        { status: 429, detail: 'too many attempts, try again later', retryAfter },
      )
      const toast = vi.fn()
      const { container } = render(<SecuritySection toast={toast} />)

      await screen.findByRole('button', { name: 'Change password' })
      const field = (autocomplete: string) =>
        Array.from(
          container.querySelectorAll<HTMLInputElement>(`input[autocomplete="${autocomplete}"]`),
        )
      fireEvent.change(field('current-password')[0]!, { target: { value: 'nope' } })
      for (const input of field('new-password')) {
        fireEvent.change(input, { target: { value: 'brand-new-pw' } })
      }
      fireEvent.click(screen.getByRole('button', { name: 'Change password' }))

      await waitFor(() => expect(toast).toHaveBeenCalledWith(message))
    },
  )

  it('names the loopback toggle for what it does', async () => {
    serve({ password_set: true, current_password_required: true })
    render(<SecuritySection toast={vi.fn()} />)

    expect(await screen.findByText("Don't ask for the password on this computer")).toBeTruthy()
    expect(
      screen.getByText(
        'Requests from the computer running Open Net Worth skip the password. Turn off on a shared computer.',
      ),
    ).toBeTruthy()
    expect(screen.queryByText(/trust loopback/i)).toBeNull()
  })

  it('Remove password confirms inline, then sends no current password on the desktop book', async () => {
    serve({ password_set: true, current_password_required: false })
    const toast = vi.fn()
    render(<SecuritySection toast={toast} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Remove password' }))
    expect(screen.getByText(/other devices will be signed out/i)).toBeTruthy()
    expect(screen.queryByLabelText('New password')).toBeNull()
    expect(posts).toHaveLength(0)

    fireEvent.click(screen.getByRole('button', { name: 'Remove password' }))
    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0]).toEqual({ path: '/api/auth/remove-password', body: {} })
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Password removed'))
  })

  it('Remove password needs the current password everywhere else, and Cancel backs out', async () => {
    serve({ password_set: true, current_password_required: true })
    const toast = vi.fn()
    const { container } = render(<SecuritySection toast={toast} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Remove password' }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove password' }))
    expect(toast).toHaveBeenCalledWith('Enter your current password to remove it')
    expect(posts).toHaveLength(0)

    const current = container.querySelector<HTMLInputElement>(
      'input[autocomplete="current-password"]',
    )
    fireEvent.change(current!, { target: { value: 'orig-pass' } })
    fireEvent.click(screen.getByRole('button', { name: 'Remove password' }))
    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0]!.body).toEqual({ old_password: 'orig-pass' })

    fireEvent.click(await screen.findByRole('button', { name: 'Remove password' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByRole('button', { name: 'Change password' })).toBeTruthy()
  })

  it('Remove password explains and blocks itself while this computer is asked for the password', async () => {
    serve({ password_set: true, current_password_required: true, trust_loopback: false })
    render(<SecuritySection toast={vi.fn()} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Remove password' }))
    expect(
      screen.getByText(/removing it would open this book to every local request/i),
    ).toBeTruthy()
    const confirm = screen.getByRole('button', { name: 'Remove password' }) as HTMLButtonElement
    expect(confirm.disabled).toBe(true)
    fireEvent.click(confirm)
    expect(posts).toHaveLength(0)
  })

  it.each([
    ['change', 'Change password'],
    ['remove', 'Remove password'],
  ])('a wrong current password on %s toasts instead of signing out', async (kind, button) => {
    serve(
      { password_set: true, current_password_required: true },
      { status: 403, detail: 'current password incorrect' },
    )
    const toast = vi.fn()
    const { container } = render(<SecuritySection toast={toast} />)

    await screen.findByRole('button', { name: 'Change password' })
    const field = (autocomplete: string) =>
      Array.from(
        container.querySelectorAll<HTMLInputElement>(`input[autocomplete="${autocomplete}"]`),
      )
    fireEvent.change(field('current-password')[0]!, { target: { value: 'nope' } })
    if (kind === 'change') {
      for (const input of field('new-password')) {
        fireEvent.change(input, { target: { value: 'brand-new-pw' } })
      }
    } else {
      fireEvent.click(screen.getByRole('button', { name: 'Remove password' }))
    }
    fireEvent.click(screen.getByRole('button', { name: button }))

    await waitFor(() => expect(toast).toHaveBeenCalledWith('Current password is wrong'))
  })
})
