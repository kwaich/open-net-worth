// @vitest-environment jsdom
//
// Recently added used to fetch the WHOLE payee list (a request that counted every
// transaction in the book) just to turn payee ids into names, showing raw bank descriptions
// until it landed. Rows now arrive with `payee_name`; the widget makes no payee request.

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))

import { transactionsWidgetComponents } from './transactions'
import { WIDGET_DEFINITIONS } from '../definitions'
import type { WidgetInstance } from '../types'

function jsonOk(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as Response)
}

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

describe('Recently added rows', () => {
  it('show the payee name the row carries, falling back to the description, and never fetch the payee list', async () => {
    authFetch.mockImplementation(() =>
      jsonOk({
        transactions: [
          {
            id: 't1',
            date: '2026-10-12',
            kind: 'expense',
            payee_id: 'pay_seng',
            payee_name: 'Seng Kee Herbal Soup',
            description: 'SENG KEE HERBAL SOUP PayLah! Scan & Pay Ref: PLPE4628417034468859',
            base_amount: 17,
          },
          {
            id: 't2',
            date: '2026-10-11',
            kind: 'expense',
            payee_id: null,
            description: 'BUS/MRT',
            base_amount: 3.5,
          },
        ],
      }),
    )
    const Comp = transactionsWidgetComponents['recent-added-txns']!
    const def = WIDGET_DEFINITIONS.find((d) => d.defId === 'recent-added-txns')!
    const instance: WidgetInstance = {
      instanceId: 'w-recent',
      defId: 'recent-added-txns',
      x: 0,
      y: 0,
      w: 4,
      h: 240,
      config: {},
    }
    render(<Comp instance={instance} def={def} onPatch={vi.fn()} />)
    expect(await screen.findByText('Seng Kee Herbal Soup')).toBeTruthy()
    expect(screen.getByText('BUS/MRT')).toBeTruthy()
    expect(screen.queryByText(/PayLah!/)).toBeNull()
    expect(authFetch.mock.calls.some((c) => String(c[0]).startsWith('/api/payees'))).toBe(false)
  })
})
