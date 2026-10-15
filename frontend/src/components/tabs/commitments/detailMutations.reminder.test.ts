// Regression test for the real "remind me" feature (owner: "Build it for real") — arming a
// reminder creates a real one-shot Lucius Automation and links its id onto the action;
// disarming deletes that Automation and clears the link. Both writes go through the unified
// Automation REST surface + the existing commitments action PATCH choke.
// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook, cleanup } from '@testing-library/react'
import { useDetailMutations } from './detailMutations'
import type { Commitment, CommitmentAction } from '../../../types/commitments'

const mockAuthFetch = vi.fn()
vi.mock('../../../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const commitment: Commitment = {
  id: 'c1',
  name: 'Parkview Residences',
  kind: 'purchase',
  currency: 'SGD',
  status: 'active',
  linked_position_id: null,
  linked_account_id: null,
  note: '',
  created_at: '2026-01-01T00:00:00Z',
  installments: [],
  funding_plan: [],
  actions: [],
}

const action: CommitmentAction = {
  id: 'act-1',
  label: 'Sign SPA addendum',
  category: 'legal',
  due_date: '2026-09-01',
  start_by: null,
  amount: null,
  currency: 'SGD',
  status: 'pending',
  counterparty: '',
  note: '',
  funding_id: null,
  reminder_automation_id: null,
}

function ok(data: unknown): Response {
  return { ok: true, json: async () => data, text: async () => '' } as Response
}

describe('useDetailMutations.setActionReminder', () => {
  it('arming: creates a one-shot Lucius Automation and links its id onto the action', async () => {
    mockAuthFetch
      .mockResolvedValueOnce(
        ok({
          id: 'auto-99',
          name: 'Reminder — Sign SPA addendum',
          trigger: { kind: 'schedule', type: 'once', at: '2026-09-01T09:00:00' },
          execution: { kind: 'lucius' },
          status: 'active',
          run_count: 0,
        }),
      )
      .mockResolvedValueOnce(ok({ ok: true, commitment }))

    const onChanged = vi.fn()
    const { result } = renderHook(() => useDetailMutations(commitment, onChanged))
    await result.current.setActionReminder(action, true)

    expect(mockAuthFetch).toHaveBeenCalledTimes(2)
    const [createUrl, createOpts] = mockAuthFetch.mock.calls[0]!
    expect(createUrl).toBe('/api/automations')
    const createBody = JSON.parse((createOpts as RequestInit).body as string)
    expect(createBody).toMatchObject({
      name: 'Reminder — Sign SPA addendum',
      trigger: { kind: 'schedule', type: 'once', at: '2026-09-01T09:00:00' },
      permission_mode: 'auto',
      always_allowed: [
        {
          src: 'Open Net Worth',
          desc: 'Send the reminder for Sign SPA addendum.',
          capability: 'notify',
          tools: ['notify'],
        },
      ],
      max_runs: 1,
      status: 'active',
    })
    expect(createBody.instructions).toContain('Sign SPA addendum')
    expect(createBody.instructions).toContain('all registered notification channels')

    const [patchUrl, patchOpts] = mockAuthFetch.mock.calls[1]!
    expect(patchUrl).toBe('/api/commitments/c1/actions/act-1')
    expect((patchOpts as RequestInit).method).toBe('PATCH')
    expect(JSON.parse((patchOpts as RequestInit).body as string)).toEqual({
      changes: { reminder_automation_id: 'auto-99' },
    })
    expect(onChanged).toHaveBeenCalledTimes(1)
  })

  it('disarming: deletes the linked Automation and clears reminder_automation_id', async () => {
    mockAuthFetch
      .mockResolvedValueOnce(ok({ ok: true }))
      .mockResolvedValueOnce(ok({ ok: true, commitment }))

    const onChanged = vi.fn()
    const { result } = renderHook(() => useDetailMutations(commitment, onChanged))
    await result.current.setActionReminder({ ...action, reminder_automation_id: 'auto-99' }, false)

    const [cancelUrl, cancelOpts] = mockAuthFetch.mock.calls[0]!
    expect(cancelUrl).toBe('/api/automations/auto-99')
    expect((cancelOpts as RequestInit).method).toBe('DELETE')

    const [patchUrl, patchOpts] = mockAuthFetch.mock.calls[1]!
    expect(patchUrl).toBe('/api/commitments/c1/actions/act-1')
    expect(JSON.parse((patchOpts as RequestInit).body as string)).toEqual({
      changes: { reminder_automation_id: null },
    })
  })

  it('disarming when no task was ever linked skips the cancel call but still clears the field', async () => {
    mockAuthFetch.mockResolvedValueOnce(ok({ ok: true, commitment }))

    const onChanged = vi.fn()
    const { result } = renderHook(() => useDetailMutations(commitment, onChanged))
    await result.current.setActionReminder(action, false)

    expect(mockAuthFetch).toHaveBeenCalledTimes(1)
    const [patchUrl] = mockAuthFetch.mock.calls[0]!
    expect(patchUrl).toBe('/api/commitments/c1/actions/act-1')
  })
})
