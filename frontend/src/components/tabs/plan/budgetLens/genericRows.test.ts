import { describe, expect, it } from 'vitest'
import {
  blankGenericRows,
  listParamName,
  proposedRows,
  proposedSelection,
  setupVerb,
} from './genericRows'
import type { BudgetEngineVerb } from './types'

// A made-up engine's setup verb — no shipped engine renders generically today.
const SET_TRACKED: BudgetEngineVerb = {
  id: 'set_tracked',
  label: 'Choose which categories are capped',
  params: { category_ids: 'list of category ids or labels, required' },
}

describe('setupVerb — discovering the setup verb from declared verbs, never hardcoded', () => {
  it('picks the one verb whose params are a single list-shaped field', () => {
    expect(setupVerb([SET_TRACKED])).toBe(SET_TRACKED)
  })

  it('ignores a verb with more than one param (e.g. a bucket-mapping verb like set_config)', () => {
    const setConfig: BudgetEngineVerb = {
      id: 'set_config',
      label: 'Set targets and mapping',
      params: { targets: 'object, required', mapping: 'object, required' },
    }
    expect(setupVerb([setConfig])).toBeUndefined()
  })

  it('ignores a single-param verb whose hint is not list-shaped', () => {
    const rename: BudgetEngineVerb = {
      id: 'rename',
      label: 'Rename',
      params: { name: 'string, required' },
    }
    expect(setupVerb([rename])).toBeUndefined()
  })

  it('returns undefined for an engine with no verbs at all', () => {
    expect(setupVerb([])).toBeUndefined()
  })
})

describe('listParamName', () => {
  it("reads the verb's own single param name", () => {
    expect(listParamName(SET_TRACKED)).toBe('category_ids')
  })

  it('is null when there is no verb', () => {
    expect(listParamName(undefined)).toBeNull()
  })
})

describe("proposedSelection — the draft response's own copy of the verb's list param", () => {
  it('reads the ids straight off the declared param name, no per-engine field guessing', () => {
    const selection = proposedSelection(
      { category_ids: ['cat_a', 'cat_b'], rows: [] },
      'category_ids',
    )
    expect(selection).toEqual(new Set(['cat_a', 'cat_b']))
  })

  it('degrades to an empty set when the param is absent or not a list, never throws', () => {
    expect(proposedSelection({ rows: [] }, 'category_ids')).toEqual(new Set())
    expect(proposedSelection({ category_ids: 'not-a-list', rows: [] }, 'category_ids')).toEqual(
      new Set(),
    )
  })
})

describe('proposedRows', () => {
  it('reads the rows array as-is', () => {
    const rows = [{ category_id: 'cat_a', label: 'A', low_confidence: false }]
    expect(proposedRows({ rows })).toEqual(rows)
  })

  it('degrades to an empty array when rows is missing, never throws', () => {
    expect(proposedRows({})).toEqual([])
  })
})

describe('blankGenericRows — "Start blank instead"', () => {
  it('every expense category, none selected, sorted by label, no income/hidden/structural categories', () => {
    const rows = blankGenericRows([
      { id: 'cat_b', label: 'Bravo', kind: 'expense' },
      { id: 'cat_a', label: 'Alpha', kind: 'expense' },
      { id: 'cat_income', label: 'Salary', kind: 'income' },
      { id: 'split', label: 'Split', kind: 'expense' },
    ])
    expect(rows).toEqual([
      { category_id: 'cat_a', label: 'Alpha', low_confidence: false },
      { category_id: 'cat_b', label: 'Bravo', low_confidence: false },
    ])
  })
})
