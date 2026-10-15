// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { VaultItem, VaultTable } from '../../api/vault'
import { TablePreview } from './TablePreview'

const fetchVaultTable =
  vi.fn<(id: string, sheet?: string, offset?: number) => Promise<VaultTable>>()
vi.mock('../../api/vault', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/vault')>()),
  fetchVaultTable: (id: string, sheet?: string, offset?: number) =>
    fetchVaultTable(id, sheet, offset),
}))

const ITEM = {
  id: 'statement-stmt_1',
  kind: 'statement',
  source_id: 'stmt_1',
  title: 'Synthetic Bank — Sep 2026',
  filename: 'history.xlsx',
  media_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  private: false,
} as VaultItem

function grid(sheet: string, rows: string[][]): VaultTable {
  return {
    sheets: ['Transactions', 'Summary'],
    sheet,
    rows,
    columns: rows[0]?.length ?? 0,
    offset: 0,
    has_more: false,
    total_rows: rows.length,
    more_columns: false,
  }
}

beforeEach(() => {
  fetchVaultTable.mockReset().mockImplementation(async (_id, sheet) =>
    sheet === 'Summary'
      ? grid('Summary', [['Closing balance', '2903.3']])
      : grid('Transactions', [
          ['Date', 'Description', 'Amount'],
          ['2026-09-02', 'Coffee', '-12.5'],
        ]),
  )
})
afterEach(cleanup)

describe('TablePreview', () => {
  it('shows the rows as the file has them, with column letters and row numbers', async () => {
    render(<TablePreview item={ITEM} />)
    expect(await screen.findByRole('cell', { name: 'Coffee' })).toBeTruthy()
    expect(screen.getAllByRole('columnheader').map((h) => h.textContent)).toEqual(['A', 'B', 'C'])
    expect(screen.getByRole('rowheader', { name: '2' })).toBeTruthy()
  })

  it('switches sheets', async () => {
    render(<TablePreview item={ITEM} />)
    await screen.findByRole('cell', { name: 'Coffee' })
    fireEvent.click(screen.getByRole('button', { name: 'Summary' }))
    expect(await screen.findByRole('cell', { name: 'Closing balance' })).toBeTruthy()
    expect(fetchVaultTable).toHaveBeenLastCalledWith('statement-stmt_1', 'Summary', undefined)
    expect(screen.getByRole('button', { name: 'Summary' }).getAttribute('aria-pressed')).toBe(
      'true',
    )
  })

  it('loads the next page of rows as you scroll to the bottom', async () => {
    const page = (from: number, n: number, more: boolean) => ({
      sheets: [],
      sheet: '',
      rows: Array.from({ length: n }, (_, i) => [`row ${from + i + 1}`]),
      columns: 1,
      offset: from,
      has_more: more,
      total_rows: 250,
      more_columns: false,
    })
    fetchVaultTable.mockImplementation(async (_id, _sheet, offset = 0) =>
      offset === 0 ? page(0, 200, true) : page(200, 50, false),
    )
    render(<TablePreview item={ITEM} />)
    expect(await screen.findByRole('cell', { name: 'row 200' })).toBeTruthy()
    expect(screen.getByText(/Rows 1–200 of 250 — scroll for more/)).toBeTruthy()
    fireEvent.scroll(screen.getByRole('region'))
    fireEvent.scroll(screen.getByRole('region')) // a second event in the same tick: one fetch
    expect(await screen.findByRole('cell', { name: 'row 250' })).toBeTruthy()
    expect(fetchVaultTable.mock.calls.filter((c) => c[2] === 200)).toHaveLength(1)
    expect(screen.getAllByRole('row')).toHaveLength(251) // header + 250, none doubled
    expect(fetchVaultTable).toHaveBeenLastCalledWith('statement-stmt_1', undefined, 200)
    expect(screen.getByRole('rowheader', { name: '250' })).toBeTruthy()
    expect(screen.queryByText(/scroll for more/)).toBeNull()
  })

  it('a file that can’t be read keeps a preview-sized place and says why', async () => {
    fetchVaultTable.mockRejectedValue(
      new Error('this spreadsheet is password-protected — download it to open it'),
    )
    render(<TablePreview item={ITEM} />)
    expect(await screen.findByText('No preview for this file')).toBeTruthy()
    expect(screen.getByText(/password-protected/)).toBeTruthy()
  })
})
