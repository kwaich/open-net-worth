// Regression test for a real crash: opening a commitment whose funding plan includes an
// asset_sale source with a resolved position renders <AssetLink>, which calls useAssetNav()
// unconditionally (components/intel/AssetLink.tsx). RouterShell originally wrapped only the
// Intel tab in <AssetNavProvider>, so navigating to a commitment like this threw
// "useAssetNav must be used inside <AssetNavProvider>" and the whole Commitments tab
// error-boundaried. Fixed by also wrapping the Commitments tab render in RouterShell.tsx.
// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { Funding } from './detailFunding'
import { AssetNavProvider } from '../../../state/assetNav'
import type { Commitment } from '../../../types/commitments'
import type { DetailMutations } from './detailTypes'
import type { PositionMeta } from './fundingValue'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))

// Funding pulls in useNavigate (via <Funding>) and AssetNavProvider pulls in
// useNavigate + useRouterState — mock both, mirroring AssetLink.test.tsx.
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useRouterState: () => ({ matches: [] }),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const commitment: Commitment = {
  id: 'riverside-heights',
  name: 'Parkview Residences',
  kind: 'purchase',
  currency: 'VND',
  status: 'active',
  linked_position_id: null,
  linked_account_id: null,
  note: '',
  created_at: '2026-01-01T00:00:00Z',
  installments: [],
  funding_plan: [
    {
      id: 'fs-asset-sale',
      type: 'asset_sale',
      label: 'Sell ONWAI (5th)',
      amount: null,
      currency: 'EUR',
      ref_position_id: 'pos_onwai',
      sell_quantity: 244,
      available_date: null,
      ref_account_id: null,
      repayment_commitment_id: null,
      note: '',
      disabled: false,
      cadence: null,
      status: 'planned',
      realized_txn_id: null,
      value_base: 13598.32,
    },
  ],
}

const positions: Record<string, PositionMeta> = {
  pos_onwai: {
    instrumentId: 'inst-onwai',
    symbol: 'ONWAI',
    quantity: 1242,
    price: 38,
    priceCurrency: 'EUR',
  },
}

const m = {
  onAskLucius: vi.fn(),
  onOpenCommitment: vi.fn(),
  onOpenTxn: vi.fn(),
  updateInstallment: vi.fn(),
  removeInstallment: vi.fn(),
  payInstallment: vi.fn(),
  linkTxn: vi.fn(),
  updateFunding: vi.fn(),
  removeFunding: vi.fn(),
  addFunding: vi.fn(),
  updateAction: vi.fn(),
  addAction: vi.fn(),
  setActionMissed: vi.fn(),
  rename: vi.fn(),
  updateNote: vi.fn(),
  setStatus: vi.fn(),
  deleteCommitment: vi.fn(),
} as unknown as DetailMutations

describe('Funding tab — asset-sale source deep link', () => {
  it('throws without an AssetNavProvider ancestor (documents the real crash)', () => {
    expect(() =>
      render(
        <Funding
          commitment={commitment}
          fx={{ VND: 1, EUR: 1.4666 }}
          envelope={null}
          positions={positions}
          m={m}
        />,
      ),
    ).toThrow('useAssetNav must be used inside <AssetNavProvider>')
  })

  it('renders the position deep link fine when wrapped in AssetNavProvider', () => {
    const { container } = render(
      <AssetNavProvider>
        <Funding
          commitment={commitment}
          fx={{ VND: 1, EUR: 1.4666 }}
          envelope={null}
          positions={positions}
          m={m}
        />
      </AssetNavProvider>,
    )
    const link = container.querySelector('.asset-link--clickable')
    expect(link?.getAttribute('title')).toBe('Open ONWAI')
  })
})
