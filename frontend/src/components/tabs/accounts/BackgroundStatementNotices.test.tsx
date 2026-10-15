// @vitest-environment jsdom
//
// Owner, 2026-10-06: a statement left reading in the background must say when it is done — "at
// least in app as a popup". The popup is app-wide (mounted beside the drawer surfaces), driven by
// the backgroundStatements store, and its "Review" hands the statement to the Accounts tab.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor } from '@testing-library/react'
import { BackgroundStatementNotices } from './BackgroundStatementNotices'
import {
  backgroundStatements,
  takeStatementReview,
  trackBackgroundStatement,
} from './backgroundStatements'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

function status(parse_status: string) {
  authFetch.mockImplementation(() =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: async () => ({ parse_status, progress: null, error: null }),
    }),
  )
}

beforeEach(() => {
  localStorage.clear()
  takeStatementReview()
})
afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

describe('BackgroundStatementNotices', () => {
  it('pops up when a background read is ready, and Review hands it to the Accounts tab', async () => {
    trackBackgroundStatement({ id: 's1', accountId: 'acc1', filename: 'oct.pdf' })
    status('ready')
    const onOpenAccounts = vi.fn()
    render(<BackgroundStatementNotices onOpenAccounts={onOpenAccounts} />)

    expect(await screen.findByText('Ready to review')).toBeTruthy()
    expect(screen.getByText('oct.pdf')).toBeTruthy()
    expect(backgroundStatements()).toEqual([]) // reported once, no longer watched

    fireEvent.click(screen.getByRole('button', { name: 'Review' }))
    expect(onOpenAccounts).toHaveBeenCalledTimes(1)
    expect(takeStatementReview()).toEqual({ id: 's1', accountId: 'acc1', filename: 'oct.pdf' })
    await waitFor(() => expect(screen.queryByText('Ready to review')).toBeNull())
  })

  it('a failed read says so, with no Review', async () => {
    trackBackgroundStatement({ id: 's2', accountId: 'acc1', filename: 'bad.csv' })
    status('failed')
    render(<BackgroundStatementNotices />)

    expect(await screen.findByText('Couldn’t read')).toBeTruthy()
    expect(screen.getByText('bad.csv')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Review' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    await waitFor(() => expect(screen.queryByText('Couldn’t read')).toBeNull())
  })

  it('stays quiet and keeps watching while the read is still going', async () => {
    trackBackgroundStatement({ id: 's3', accountId: null, filename: 'slow.pdf' })
    status('parsing')
    render(<BackgroundStatementNotices />)

    await waitFor(() => expect(authFetch).toHaveBeenCalled())
    expect(screen.queryByRole('status')).toBeNull()
    expect(backgroundStatements().map((s) => s.id)).toEqual(['s3'])
  })
})
