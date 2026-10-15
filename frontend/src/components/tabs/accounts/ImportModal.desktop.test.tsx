// @vitest-environment jsdom
import { StrictMode, type ComponentProps } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, fireEvent } from '@testing-library/react'
import { ImportModal } from './ImportModal'

afterEach(cleanup)

it('opens a native chosen file in the existing choose step exactly once', async () => {
  const file = new File(['Date,Description,Amount\n2026-10-01,Coffee,-12\n'], 'desktop.csv', {
    type: 'text/csv',
  })
  const onSourcePeek = vi.fn().mockResolvedValue({ known: false, instructions: '', accountIds: [] })
  render(
    <StrictMode>
      <ImportModal
        open
        initialFile={file}
        accounts={[]}
        onClose={() => {}}
        onPreview={vi.fn()}
        onSourcePeek={onSourcePeek}
        onDecisionCommit={vi.fn()}
        baseCurrency="USD"
        rates={{ USD: 1 }}
      />
    </StrictMode>,
  )
  await waitFor(() => expect(onSourcePeek).toHaveBeenCalledTimes(1))
  expect(onSourcePeek).toHaveBeenCalledWith(file)
  expect(screen.getAllByText(/desktop\.csv/).length).toBeGreaterThan(0)
})

it('reports the file actually committed when the native choice is replaced', async () => {
  const makeFile = (name: string) => {
    const file = new File(['Date,Description,Amount\n2026-10-01,Coffee,-12\n'], name, {
      type: 'text/csv',
    })
    Object.defineProperty(file, 'text', {
      value: async () => 'Date,Description,Amount\n2026-10-01,Coffee,-12\n',
    })
    return file
  }
  const original = makeFile('original.csv')
  const replacement = makeFile('replacement.csv')
  const commit = vi
    .fn<ComponentProps<typeof ImportModal>['onDecisionCommit']>()
    .mockResolvedValue({ created: 1, merged: 0, duplicates: 0, skipped: 0 })
  render(
    <ImportModal
      open
      initialFile={original}
      defaultAccountId="cash"
      accounts={[
        {
          id: 'cash',
          name: 'Cash',
          currency: 'USD',
          type: 'cash',
          kind: 'asset',
          archived: false,
        } as never,
      ]}
      onClose={() => {}}
      onSourcePeek={vi.fn().mockResolvedValue({ known: false, instructions: '' })}
      onPreview={vi.fn().mockResolvedValue({
        statementId: 'st_replacement',
        drafts: [
          {
            date: '2026-10-01',
            description: 'Coffee',
            amount: -12,
            currency: 'USD',
            kind: 'expense',
            category: null,
            dup: false,
            rowIndex: 0,
            external_refs: [],
            flags: [],
          },
        ],
        importable: 1,
        duplicates: 0,
        skipped: [],
        total: 1,
        anomaly: {
          near_duplicate: 0,
          transfer_pair: 0,
          intra_batch_duplicate: 0,
          amount_anomaly: 0,
          flagged_rows: 0,
        },
      })}
      onDecisionCommit={commit}
      baseCurrency="USD"
      rates={{ USD: 1 }}
    />,
  )
  await screen.findByText('original.csv')
  fireEvent.click(screen.getByRole('button', { name: 'Change' }))
  fireEvent.change(document.body.querySelector('input[type="file"]')!, {
    target: { files: [replacement] },
  })
  await screen.findByText('replacement.csv')
  fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
  fireEvent.click(await screen.findByRole('button', { name: 'Confirm' }, { timeout: 5000 }))
  await waitFor(() => expect(commit).toHaveBeenCalledTimes(1))
  expect(commit.mock.calls[0]?.[4]).toBe(replacement)
})
