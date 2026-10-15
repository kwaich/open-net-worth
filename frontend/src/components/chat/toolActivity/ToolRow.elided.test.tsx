// @vitest-environment jsdom
//
// An oversized tool result is stripped from the transcript list payload and fetched when the
// row is opened. These tests render inside <StrictMode>, because the app does (`main.tsx`)
// and the bug this covers only exists there.
//
// React 18 StrictMode mounts, runs cleanup, then mounts again. An `alive` ref that was only
// ever set to false IN CLEANUP therefore stayed false for the component's whole life, so
// every response was discarded as "component is gone" and every elided row spun "Loading
// full result…" forever (owner report, 2026-08-23). A test rendering without StrictMode
// could not see it — which is why these render with it.

import { StrictMode } from 'react'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ToolRow } from './ToolRow'
import type { Cluster, ToolItem } from './cluster'

const fetchToolDetail = vi.fn()
vi.mock('../../../utils/chatStore', () => ({
  fetchToolDetail: (...args: unknown[]) => fetchToolDetail(...args),
}))

afterEach(() => {
  cleanup()
  fetchToolDetail.mockReset()
})

function elidedRow(): Cluster {
  const item: ToolItem = {
    type: 'tool',
    name: 'email_get_messages',
    id: 'tu_1',
    status: 'done',
    // The list payload carried neither — only the sizes it stripped.
    resultElided: 91_000,
    display: {
      label: 'Get messages',
      verb: 'read',
      noun: 'messages',
      owner_id: 'email',
      owner_name: 'Email',
      detail: '',
    },
  }
  return {
    key: 'tu_1',
    items: [item],
    label: 'Get messages',
    verb: 'read',
    noun: 'messages',
    ownerId: 'email',
    ownerName: 'Email',
    detail: '',
    count: 1,
    failed: false,
    errorText: '',
    busy: false,
  } as Cluster
}

function open() {
  const { container } = render(
    <StrictMode>
      <ToolRow cluster={elidedRow()} sessionId="sess-1" look={{}} />
    </StrictMode>,
  )
  ;(container.querySelector('.msg-toolact__head') as HTMLElement).click()
  return container
}

describe('an elided result under StrictMode', () => {
  it('renders the fetched result instead of spinning forever', async () => {
    fetchToolDetail.mockResolvedValue({ input: { limit: 5 }, result: '{"ok":true}' })
    open()
    await waitFor(() => expect(screen.getByText('Result')).toBeTruthy())
    expect(screen.getByText('Request')).toBeTruthy()
    expect(screen.queryByText(/Loading full result/)).toBeNull()
  })

  it('says it could not load when the fetch comes back empty', async () => {
    // `fetchToolDetail` never throws — it answers null. Treating that as "still loading" is
    // the same forever-spin by another route.
    fetchToolDetail.mockResolvedValue(null)
    open()
    await waitFor(() => expect(screen.getByText(/Couldn’t load the full result/)).toBeTruthy())
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
  })

  it('offers no Retry when there is no session to fetch against', async () => {
    const { container } = render(
      <StrictMode>
        <ToolRow cluster={elidedRow()} look={{}} />
      </StrictMode>,
    )
    ;(container.querySelector('.msg-toolact__head') as HTMLElement).click()
    await waitFor(() => expect(screen.getByText(/Couldn’t load the full result/)).toBeTruthy())
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull()
    expect(fetchToolDetail).not.toHaveBeenCalled()
  })
})
