// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest'
import type { ReactElement } from 'react'
import { render, screen, cleanup, fireEvent, waitFor, act } from '@testing-library/react'
import { StepPassword } from './StepPassword'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../api/client', () => ({ authFetch }))

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

function mockApi(ok = true, settings: Record<string, unknown> = { password_set: false }) {
  authFetch.mockImplementation((url: string) =>
    Promise.resolve({
      ok,
      status: ok ? 200 : 400,
      json: () => Promise.resolve(url === '/api/auth/settings' ? settings : {}),
    }),
  )
}

/** The step asks the server whether a password exists before it shows anything. */
async function renderStep(ui: ReactElement) {
  render(ui)
  await waitFor(() => expect(authFetch).toHaveBeenCalledWith('/api/auth/settings'))
  await act(async () => {})
}

function submitCalls() {
  return authFetch.mock.calls.filter((c) => c[0] === '/api/auth/change-password')
}

describe('StepPassword — a short password is an error, not a silent no-op', () => {
  it('says nothing while the field is empty — empty means skip', async () => {
    mockApi()
    await renderStep(<StepPassword onDone={() => {}} />)
    expect(screen.queryByText(/more to go/i)).toBeNull()
  })

  it('does not scold you mid-type, only once you leave the field short', async () => {
    mockApi()
    await renderStep(<StepPassword onDone={() => {}} />)

    fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: 'short' } })
    expect(screen.queryByText(/more to go/i)).toBeNull()

    fireEvent.blur(screen.getByLabelText(/^password$/i))
    expect(screen.getByText(/3 more to go/i)).toBeTruthy()
  })

  it('also catches a short password on Enter, even if the field was never blurred', async () => {
    // Set & finish is disabled here (confirm isn't even typeable-into yet at 5 chars), so Enter
    // is what proves the short-password check isn't only reachable via blur — that was the bug:
    // "8 characters minimum" was a sidebar note that never became visible feedback for what
    // actually went wrong.
    mockApi()
    const onDone = vi.fn()
    await renderStep(<StepPassword onDone={onDone} />)

    fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: 'short' } })
    fireEvent.keyDown(screen.getByLabelText(/^password$/i), { key: 'Enter' })

    expect(await screen.findByText(/more to go/i)).toBeTruthy()
    expect(onDone).not.toHaveBeenCalled()
  })
})

describe('StepPassword — the ordinary paths', () => {
  it('disables Set & finish while both fields are empty — Skip is the way past', async () => {
    // Set & finish and Skip doing the same thing with nothing typed was the exact ambiguity
    // someone clicking Set & finish first, out of habit, kept running into.
    mockApi()
    await renderStep(<StepPassword onDone={() => {}} />)

    expect(screen.getByRole('button', { name: /set & finish/i }).hasAttribute('disabled')).toBe(
      true,
    )
  })

  it('stays disabled while only the password field has something typed — confirm is still blank', async () => {
    // An untyped confirm is not "not yet a mismatch", it's simply not confirmed — the button
    // must not read this as ready just because password.length > 0.
    mockApi()
    await renderStep(<StepPassword onDone={() => {}} />)

    fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: 'longenough1' } })
    expect(screen.getByRole('button', { name: /set & finish/i }).hasAttribute('disabled')).toBe(
      true,
    )
  })

  it('re-enables only once both fields are typed and agree', async () => {
    mockApi()
    await renderStep(<StepPassword onDone={() => {}} />)

    fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: 'longenough1' } })
    fireEvent.change(screen.getByLabelText(/confirm/i), { target: { value: 'longenough1' } })
    expect(screen.getByRole('button', { name: /set & finish/i }).hasAttribute('disabled')).toBe(
      false,
    )
  })

  it('skip is a real button, not a smaller afterthought', async () => {
    mockApi()
    const onDone = vi.fn()
    await renderStep(<StepPassword onDone={onDone} />)

    fireEvent.click(screen.getByRole('button', { name: /skip/i }))
    expect(onDone).toHaveBeenCalled()
  })

  it('disables Set & finish the moment the two fields disagree — live, no click needed', async () => {
    // A string comparison needs no round trip to the server, so there is nothing to wait for:
    // the button goes disabled on the keystroke that creates the mismatch, before any click.
    mockApi()
    const onDone = vi.fn()
    await renderStep(<StepPassword onDone={onDone} />)

    fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: 'longenough1' } })
    fireEvent.change(screen.getByLabelText(/confirm/i), { target: { value: 'different1' } })
    const button = screen.getByRole('button', { name: /set & finish/i })

    expect(button.hasAttribute('disabled')).toBe(true)
    expect(screen.getByText(/do not match/i)).toBeTruthy()

    // A disabled button firing its click handler is exactly what "disabled" is supposed to
    // prevent — assert it holds, not just that we politely didn't click it.
    fireEvent.click(button)
    expect(submitCalls()).toHaveLength(0)
    expect(onDone).not.toHaveBeenCalled()

    // Fixing the field re-enables it immediately too — no lingering stuck-disabled state.
    fireEvent.change(screen.getByLabelText(/confirm/i), { target: { value: 'longenough1' } })
    expect(button.hasAttribute('disabled')).toBe(false)
    expect(screen.queryByText(/do not match/i)).toBeNull()
  })

  it('keeps Set & finish disabled when a password is typed but confirm is still untouched', async () => {
    // The one that actually shipped wrong: a valid password with a still-blank confirm field
    // read as no mismatch (nothing to compare against yet) and the button stayed clickable,
    // which meant "Set & finish" could submit a password nobody had confirmed at all.
    mockApi()
    const onDone = vi.fn()
    await renderStep(<StepPassword onDone={onDone} />)

    fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: 'longenough1' } })
    const button = screen.getByRole('button', { name: /set & finish/i })
    expect(button.hasAttribute('disabled')).toBe(true)

    fireEvent.click(button)
    expect(submitCalls()).toHaveLength(0)
    expect(onDone).not.toHaveBeenCalled()
  })

  it('sets a valid matching password', async () => {
    mockApi()
    const onDone = vi.fn()
    await renderStep(<StepPassword onDone={onDone} onAuthChanged={() => {}} />)

    fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: 'longenough1' } })
    fireEvent.change(screen.getByLabelText(/confirm/i), { target: { value: 'longenough1' } })
    fireEvent.click(screen.getByRole('button', { name: /set & finish/i }))

    await waitFor(() => expect(onDone).toHaveBeenCalled())
  })
})

describe('StepPassword — a book that already has a password', () => {
  // Settings › Data › Reset keeps the password, so first run can start on a book that has one.
  // The change-password endpoint then wants the CURRENT password, which this step cannot ask for:
  // the form used to submit without it and show the server's 403 raw, leaving Skip as the only way out.
  it('shows no fields — the info text and one Finish button', async () => {
    mockApi(true, { password_set: true })
    const onDone = vi.fn()
    await renderStep(<StepPassword onDone={onDone} />)

    expect(
      screen.getByText(
        /This book already has a password\. You can change it any time in Settings . Security\./,
      ),
    ).toBeTruthy()
    expect(screen.queryByLabelText(/^password$/i)).toBeNull()
    expect(screen.queryByLabelText(/confirm/i)).toBeNull()
    expect(screen.queryByRole('button', { name: /skip/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /set & finish/i })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /^finish$/i }))
    expect(onDone).toHaveBeenCalledTimes(1)
    expect(submitCalls()).toHaveLength(0)
  })

  it('shows the form when the book has no password', async () => {
    mockApi(true, { password_set: false })
    await renderStep(<StepPassword onDone={() => {}} />)
    expect(screen.getByLabelText(/^password$/i)).toBeTruthy()
    expect(screen.queryByText(/already has a password/i)).toBeNull()
  })

  it('falls back to the form when the settings read fails', async () => {
    authFetch.mockImplementation(() => Promise.reject(new Error('offline')))
    render(<StepPassword onDone={() => {}} />)
    expect(await screen.findByLabelText(/^password$/i)).toBeTruthy()
  })
})

describe('StepPassword — spaces are part of the password', () => {
  it('sends the password exactly as typed, spaces included', async () => {
    mockApi()
    const onDone = vi.fn()
    await renderStep(<StepPassword onDone={onDone} onAuthChanged={() => {}} />)

    fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: '  spaced pw  ' } })
    fireEvent.change(screen.getByLabelText(/confirm/i), { target: { value: '  spaced pw  ' } })
    fireEvent.click(screen.getByRole('button', { name: /set & finish/i }))

    await waitFor(() => expect(onDone).toHaveBeenCalled())
    const [, init] = submitCalls()[0]!
    expect(JSON.parse(init.body)).toEqual({ new_password: '  spaced pw  ' })
  })

  it('refuses a password of only spaces without calling the server', async () => {
    mockApi()
    const onDone = vi.fn()
    await renderStep(<StepPassword onDone={onDone} />)

    fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: '          ' } })
    fireEvent.change(screen.getByLabelText(/confirm/i), { target: { value: '          ' } })
    fireEvent.click(screen.getByRole('button', { name: /set & finish/i }))

    expect(await screen.findByText(/only spaces/i)).toBeTruthy()
    expect(submitCalls()).toHaveLength(0)
    expect(onDone).not.toHaveBeenCalled()
  })
})
