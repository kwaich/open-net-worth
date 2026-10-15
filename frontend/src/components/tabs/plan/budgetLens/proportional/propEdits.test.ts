// The Edit envelope save (propEdits.ts): its writes run in the one order where a combined edit
// lands whole — the server refuses to move an envelope holding more than one category.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { applyEnvelopeEdit } from './propEdits'
import type { PropEnvelope } from './propApi'

const calls: string[] = []
vi.mock('./envelopeOps', () => ({
  updateEnvelope: async () => void calls.push('rename'),
  removeCategory: async (_e: string, k: string) => void calls.push('remove ' + k),
  moveToGroup: async (_e: string, g: string) => void calls.push('move ' + g),
  addCategory: async (_e: string, k: string) => void calls.push('add ' + k),
  createCategory: async (label: string) => (calls.push('create ' + label), 'cat_new'),
}))
vi.mock('./propApi', () => ({
  setCap: async () => void calls.push('cap'),
  setEnvelopeExcluded: async (_e: string, out: boolean) => void calls.push('excluded ' + out),
}))

const env: PropEnvelope = {
  id: 'env_food',
  label: 'Food',
  color: '#3b86dd',
  group: 'Living',
  category_ids: ['cat_groceries', 'cat_dining'],
  group_id: 'grp_bills',
  cap: 500,
  activity: 0,
  history: { last: 0, avg3: 0, last_by_day: 0, avg3_by_day: 0 },
}

beforeEach(() => void calls.splice(0))

describe('applyEnvelopeEdit', () => {
  it('removes categories BEFORE moving the envelope, and adds them after', async () => {
    await applyEnvelopeEdit(
      env,
      {
        label: 'Groceries',
        color: env.color,
        group: 'Home',
        cats: ['cat_groceries', 'cat_snacks'],
        cap: 600,
        counted: true,
      },
      { ym: '2026-09', capsLocked: false, groups: [], envs: [env] },
    )
    expect(calls).toEqual(['rename', 'remove cat_dining', 'move Home', 'add cat_snacks', 'cap'])
  })
})
