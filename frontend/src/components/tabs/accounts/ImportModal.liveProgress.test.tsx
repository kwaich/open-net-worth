// @vitest-environment jsdom
//
// B20 — live statement-preview progress. The processing step polls
// GET /api/statements/preview-progress/{token} while the preview POST is still in flight and
// renders the REAL stage instead of the fake READ_PHASES timer. Feature-detected: a payload
// that never arrives (an older server, a network that never answers) must fall back to
// exactly today's rotating-phrase behaviour — pinned already by
// ImportModal.chooseRead.test.tsx's "holds on the last one" test, which never mocks
// `authFetch` at all and still passes with these changes in place.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import type { Account } from './types'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

const ACCOUNTS = [
  {
    id: 'rvb',
    name: 'Riverside Bank',
    currency: 'VND',
    color: '#3b86dd',
    kind: 'asset',
    type: 'cash',
    archived: false,
  },
] as unknown as Account[]

const CSV = 'Date,Description,Amount\n2026-07-01,COFFEE,-12.50\n'

function jsonResponse(body: unknown, ok = true) {
  return Promise.resolve({ ok, json: async () => body })
}

function open(onPreview = vi.fn().mockReturnValue(new Promise(() => {}))) {
  const r = render(
    <ImportModal
      open
      accounts={ACCOUNTS}
      onClose={() => {}}
      onPreview={onPreview as never}
      onDecisionCommit={
        vi.fn().mockResolvedValue({ created: 1, merged: 0, duplicates: 0, skipped: 0 }) as never
      }
    />,
  )
  return { ...r, onPreview }
}

function pick(name = 'sao-ke.csv') {
  const input = document.body.querySelector('input[type="file"]') as HTMLInputElement
  const file = new File([CSV], name, { type: 'text/csv' })
  Object.defineProperty(file, 'text', { value: async () => CSV })
  Object.defineProperty(file, 'size', { value: 41 * 1024 })
  fireEvent.change(input, { target: { files: [file] } })
}

async function startReading() {
  pick()
  await act(async () => {
    await Promise.resolve()
  })
  fireEvent.click(screen.getByRole('button', { name: 'Select account' }))
  fireEvent.click(screen.getByRole('option', { name: /Riverside Bank/ }))
  fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
}

const READING_STAGE = {
  stage: 'read',
  label: 'Reading page 2 of 4',
  started_at: new Date().toISOString(),
  done_at: null,
  page: 2,
  pages: 4,
  retry: null,
}

describe('ImportModal — live preview progress (B20)', () => {
  it('renders the real stage, the 7-segment bar and an elapsed clock from a fixture payload', async () => {
    authFetch.mockImplementation((url: string) =>
      url.includes('/preview-progress/')
        ? jsonResponse({
            stages: [
              {
                stage: 'render',
                label: 'Rendering pages',
                started_at: '2026-01-01T00:00:00Z',
                done_at: '2026-01-01T00:00:01Z',
                page: null,
                pages: null,
                retry: null,
              },
              READING_STAGE,
            ],
            started_at: '2026-01-01T00:00:00Z',
            elapsed_s: 12,
            done: false,
            error: null,
          })
        : jsonResponse({}),
    )
    open()
    await startReading()
    await waitFor(() =>
      expect(document.body.querySelector('.imp3-phase')!.textContent).toBe('Reading page 2 of 4'),
    )
    const bar = document.body.querySelector('.imp3-stagebar') as HTMLElement
    expect(bar).toBeTruthy()
    expect(bar.querySelectorAll('.seg')).toHaveLength(7)
    expect(bar.querySelectorAll('.seg.done')).toHaveLength(1) // render, before the current stage
    expect(bar.querySelectorAll('.seg.on')).toHaveLength(1) // read
    expect(document.body.querySelector('.imp3-elapsed')!.textContent).toBe('0:12 elapsed')
    expect(document.body.querySelector('.imp3-retry')).toBeNull()
  })

  it('shows the retry line only while the current read stage carries a retry count', async () => {
    authFetch.mockImplementation((url: string) =>
      url.includes('/preview-progress/')
        ? jsonResponse({
            stages: [{ ...READING_STAGE, page: 3, retry: 1 }],
            started_at: '2026-01-01T00:00:00Z',
            elapsed_s: 5,
            done: false,
            error: null,
          })
        : jsonResponse({}),
    )
    open()
    await startReading()
    await waitFor(() =>
      expect(document.body.querySelector('.imp3-retry')!.textContent).toBe(
        'Retrying page 3 (attempt 2 of 3)',
      ),
    )
  })

  it('a poll failure reads as "still working", never as the preview erroring', async () => {
    authFetch.mockImplementation(() => Promise.reject(new Error('network blip')))
    open()
    await startReading()
    await waitFor(() =>
      expect(document.body.querySelector('.imp3-phase')!.classList.contains('imp3-phase--stale')),
    )
    // Still the processing step, not the error path — the rejected POST is the only thing
    // that reaches `.imp-error`, and this one never resolves.
    expect(document.body.querySelector('.imp3-reading')).toBeTruthy()
    expect(document.body.querySelector('.imp-error')).toBeNull()
  })

  it('falls back to the fake-timer phrasing when the payload never arrives (old server)', async () => {
    vi.useFakeTimers()
    try {
      authFetch.mockImplementation(() => Promise.reject(new Error('404')))
      open()
      pick()
      await act(async () => {
        await Promise.resolve()
      })
      fireEvent.click(screen.getByRole('button', { name: 'Select account' }))
      fireEvent.click(screen.getByRole('option', { name: /Riverside Bank/ }))
      fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
      expect(document.body.querySelector('.imp3-phase')!.textContent).toBe('Reading the statement')
      expect(document.body.querySelector('.imp3-stagebar')).toBeNull()
      expect(document.body.querySelector('.imp3-elapsed')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })
})
