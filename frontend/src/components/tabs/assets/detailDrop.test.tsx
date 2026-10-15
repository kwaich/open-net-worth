// @vitest-environment jsdom
//
// Files dropped on the asset page attach to it (owner 2026-09-28: "why does this not support
// drag and drop?") — anywhere on the page, not only on the Documents tab, which may not even
// be open. Against a mocked authFetch; synthetic data only.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { OwnedAsset } from '../../../types/assets'
import { AssetDetail } from './detail'

const { authFetch } = vi.hoisted(() => ({ authFetch: vi.fn() }))
vi.mock('../../../api/client', () => ({ authFetch }))

function asset(): OwnedAsset {
  return {
    id: 'oa_test000001',
    name: 'Test Condo',
    category: 'property',
    description: '',
    photo_ref: null,
    facts: [],
    notes: '',
    cost: null,
    cost_date: null,
    share_pct: 100,
    share_note: '',
    loan_account_id: null,
    insured: false,
    maturity_date: null,
    currency: 'SGD',
    value: 850000,
    valued_on: '2026-09-26',
    share_value: 850000,
    marks: [],
    docs: [],
    archived: false,
    disposed_on: null,
    disposal_proceeds: null,
    disposal_txn_id: null,
    disposal_account_id: null,
    created_at: '2026-09-26T00:00:00Z',
    updated_at: '2026-09-26T00:00:00Z',
  }
}

function ok(data: unknown): Response {
  return { ok: true, status: 200, json: async () => data } as Response
}

function renderPage() {
  const onToast = vi.fn()
  const utils = render(
    <AssetDetail
      a={asset()}
      cats={[]}
      loans={[]}
      onBack={vi.fn()}
      onDispose={vi.fn()}
      onDelete={vi.fn()}
      onPhoto={vi.fn()}
      onNav={vi.fn()}
      onToast={onToast}
    />,
  )
  return { onToast, body: utils.container.querySelector('.as-dbody') as HTMLElement }
}

const overlay = () => screen.queryByText('Drop to add to Test Condo’s documents')

beforeEach(() => {
  authFetch.mockReset()
})
afterEach(cleanup)

describe('AssetDetail — dropping files', () => {
  it('takes files dropped anywhere on the page: says where they go, opens Documents, adds each', async () => {
    authFetch.mockResolvedValue(ok(asset()))
    const { onToast, body } = renderPage()
    const files = [new File(['%PDF'], 'spa.pdf'), new File(['jpg'], 'deed.jpg')]
    fireEvent.dragOver(body, { dataTransfer: { types: ['Files'], files } })
    expect(overlay()).toBeTruthy()
    fireEvent.drop(body, { dataTransfer: { types: ['Files'], files } })
    expect(overlay()).toBeNull()
    await waitFor(() => expect(onToast).toHaveBeenCalledWith('2 files added.'))
    const posted = authFetch.mock.calls.map(([, init]) =>
      ((init as RequestInit).body as FormData).get('file'),
    )
    expect(posted).toEqual(files)
    expect(screen.getByRole('tab', { name: 'Documents' }).getAttribute('aria-selected')).toBe(
      'true',
    )
  })

  it('stops saying so when the files leave the page without being dropped', () => {
    const { body } = renderPage()
    fireEvent.dragOver(body, { dataTransfer: { types: ['Files'], files: [] } })
    expect(overlay()).toBeTruthy()
    fireEvent.dragLeave(body, { relatedTarget: null })
    expect(overlay()).toBeNull()
  })

  it('ignores a drag that carries no files — a Lucius chip, some selected text', () => {
    const { body } = renderPage()
    fireEvent.dragOver(body, { dataTransfer: { types: ['text/plain'] } })
    expect(overlay()).toBeNull()
    fireEvent.drop(body, { dataTransfer: { types: ['text/plain'], files: [] } })
    expect(authFetch).not.toHaveBeenCalled()
  })
})
