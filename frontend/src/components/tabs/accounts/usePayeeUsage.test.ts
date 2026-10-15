// @vitest-environment jsdom
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

import { usePayeeUsage } from './usePayeeUsage'
import type { Payee } from './types'

const base = (id: string, name: string): Payee =>
  ({
    id,
    name,
    aliases: [],
    defaultCategories: {},
    autocategorize: false,
    hidden: false,
    txnCount: 0,
    lastUsed: null,
    rules: [],
  }) as Payee

describe('usePayeeUsage', () => {
  it("asks for usage only here and merges each payee's count and last-used date", async () => {
    authFetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          payees: [{ id: 'pay_a', name: 'Grab', txn_count: 7, last_used: '2026-10-01' }],
        }),
    } as Response)
    const payees = [base('pay_a', 'Grab'), base('pay_b', 'Unused')]
    const { result } = renderHook(() => usePayeeUsage(payees))
    await waitFor(() => expect(result.current[0]!.txnCount).toBe(7))
    expect(authFetch).toHaveBeenCalledWith('/api/payees?usage=true', undefined)
    expect(result.current[0]!.lastUsed).toBe('2026-10-01')
    expect(result.current[1]!.txnCount).toBe(0)
  })
})
