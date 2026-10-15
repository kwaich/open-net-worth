// @vitest-environment jsdom
//
// The briefing's prompt box edits the server-side standing instruction and regenerates the
// card — it used to send a chat message, so what the user typed never shaped the briefing.

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

import { BriefingFocusControl } from './BriefingFocusControl'
import { BRIEFING_REGENERATE_EVENT } from '../../shared/briefingFocus'

const STORED = { briefing_preferences: { fx: false, custom_focus: 'Keep it short.' } }

function jsonOk(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as Response)
}

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

describe('BriefingFocusControl', () => {
  it('saves the instruction with the other preferences intact and regenerates the briefing', async () => {
    authFetch.mockImplementation(() => jsonOk(STORED))
    const regenerated = vi.fn()
    window.addEventListener(BRIEFING_REGENERATE_EVENT, regenerated)
    const onSaved = vi.fn()
    render(<BriefingFocusControl onSaved={onSaved} />)

    const box = (await screen.findByDisplayValue('Keep it short.')) as HTMLTextAreaElement
    fireEvent.change(box, { target: { value: 'Only my mortgage, two lines.' } })
    fireEvent.click(screen.getByRole('button', { name: /Regenerate/ }))

    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    const put = authFetch.mock.calls.find((c) => c[1]?.method === 'PUT')!
    expect(JSON.parse(put[1].body)).toEqual({
      briefing_preferences: { fx: false, custom_focus: 'Only my mortgage, two lines.' },
    })
    expect(regenerated).toHaveBeenCalled()
    window.removeEventListener(BRIEFING_REGENERATE_EVENT, regenerated)
  })

  it('carries over a prompt typed into the widget before the box reached the server', async () => {
    authFetch.mockImplementation(() => jsonOk(STORED))
    render(<BriefingFocusControl legacyPrompt="keep it concise!" onSaved={vi.fn()} />)
    expect(await screen.findByDisplayValue('keep it concise!')).toBeTruthy()
  })
})
