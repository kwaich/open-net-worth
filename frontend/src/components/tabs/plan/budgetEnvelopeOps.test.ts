import { describe, it, expect, vi } from 'vitest'
import {
  addCategoryCandidatesFor,
  allEnvelopeTargets,
  categoryKebabItems,
  mergeTargetsFor,
  deriveEnvelopeName,
  describeUnlinkedCategoriesLine,
  unlinkedCandidateCategories,
} from './budgetEnvelopeOps'
import type { EnvelopeKebabHandlers } from './budgetEnvelopeOps'
import type { KebabItem } from './budgetParts'
import type { CategoryMeta } from '../../../hooks/useBudget'
import type { LocalCat, LocalGroup } from './useBudgetModel'

function mkCat(
  overrides: Partial<LocalCat> & Pick<LocalCat, 'key' | 'label' | 'envelopeId' | 'categoryIds'>,
): LocalCat {
  return {
    color: '#34b08a',
    assigned: 0,
    activity: 0,
    available: 0,
    target: 0,
    tt: null,
    cad: null,
    status: 'none',
    needed: 0,
    ...overrides,
  }
}

function mkGroup(
  overrides: Partial<LocalGroup> & Pick<LocalGroup, 'id' | 'name' | 'cats'>,
): LocalGroup {
  return { collapsed: false, subtotal: { assigned: 0, activity: 0, available: 0 }, ...overrides }
}

describe('mergeTargetsFor — merge-target picker rules', () => {
  const source = mkCat({
    key: 'groceries',
    label: 'Groceries',
    envelopeId: 'env-groceries',
    categoryIds: ['groceries'],
  })
  const sibling = mkCat({
    key: 'dining',
    label: 'Dining',
    envelopeId: 'env-dining',
    categoryIds: ['dining'],
  })
  const merged = mkCat({
    key: 'fun',
    label: 'Fun',
    envelopeId: 'env-fun',
    categoryIds: ['fun', 'hobby'],
  })
  const noEnvelope = mkCat({ key: 'misc', label: 'Misc', envelopeId: '', categoryIds: ['misc'] })
  const commitmentCat = mkCat({
    key: 'rent',
    label: 'Rent',
    envelopeId: 'env-rent',
    categoryIds: ['rent'],
  })

  const groups: LocalGroup[] = [
    mkGroup({ id: 'g-source', name: 'Everyday', cats: [source, sibling, noEnvelope] }),
    mkGroup({ id: 'g-other', name: 'Fun money', cats: [merged] }),
    mkGroup({ id: 'g-synced', name: 'Commitments', synced: true, cats: [commitmentCat] }),
  ]

  it('excludes the source envelope itself', () => {
    const targets = mergeTargetsFor(source, groups)
    const keys = targets.flatMap((g) => g.cats.map((c) => c.key))
    expect(keys).not.toContain('env-groceries')
  })

  it('excludes any row from a synced (Commitments) group — never a merge target', () => {
    const targets = mergeTargetsFor(source, groups)
    expect(targets.some((g) => g.name === 'Commitments')).toBe(false)
    const keys = targets.flatMap((g) => g.cats.map((c) => c.key))
    expect(keys).not.toContain('env-rent')
  })

  it('excludes envelope-less pseudo-rows (envelopeId === "") — a guaranteed 422 as a target', () => {
    const targets = mergeTargetsFor(source, groups)
    const keys = targets.flatMap((g) => g.cats.map((c) => c.key))
    expect(keys).not.toContain('')
  })

  it('includes ordinary sibling envelopes, and labels an N>1 target with its category count', () => {
    const targets = mergeTargetsFor(source, groups)
    const flat = targets.flatMap((g) => g.cats)
    expect(flat.find((c) => c.key === 'env-dining')?.label).toBe('Dining')
    expect(flat.find((c) => c.key === 'env-fun')?.label).toBe('Fun (2 categories)')
  })

  it('a source with no valid target anywhere yields an empty list (Manage-categories close to the last real envelope)', () => {
    const onlySelf: LocalGroup[] = [mkGroup({ id: 'g1', name: 'Everyday', cats: [source] })]
    expect(mergeTargetsFor(source, onlySelf)).toEqual([])
  })
})

describe('addCategoryCandidatesFor — "Add categories…" candidate rules', () => {
  const target = mkCat({
    key: 'groceries',
    label: 'Groceries',
    envelopeId: 'env-groceries',
    categoryIds: ['groceries'],
  })
  const sibling = mkCat({
    key: 'dining',
    label: 'Dining',
    envelopeId: 'env-dining',
    categoryIds: ['dining'],
  })
  const merged = mkCat({
    key: 'fun',
    label: 'Fun stuff',
    envelopeId: 'env-fun',
    categoryIds: ['fun', 'hobby'],
  })
  const noEnvelope = mkCat({ key: 'misc', label: 'Misc', envelopeId: '', categoryIds: ['misc'] })
  const commitmentCat = mkCat({
    key: 'rent',
    label: 'Rent',
    envelopeId: 'env-rent',
    categoryIds: ['rent'],
  })
  const groups: LocalGroup[] = [
    mkGroup({ id: 'g-a', name: 'Everyday', cats: [target, sibling, noEnvelope] }),
    mkGroup({ id: 'g-b', name: 'Fun money', cats: [merged] }),
    mkGroup({ id: 'g-synced', name: 'Commitments', synced: true, cats: [commitmentCat] }),
  ]
  const catLabel = (k: string) => ({ fun: 'Fun', hobby: 'Hobby', dining: 'Dining' })[k] ?? k
  const flat = () => addCategoryCandidatesFor(target, groups, catLabel).flatMap((g) => g.cats)

  it('excludes the target envelope itself, pseudo-rows, and synced groups', () => {
    const keys = flat().map((c) => c.categoryId)
    expect(keys).not.toContain('groceries')
    expect(keys).not.toContain('misc')
    expect(keys).not.toContain('rent')
  })

  it('lists an N>1 envelope MEMBER-BY-MEMBER with split semantics flagged', () => {
    const fun = flat().find((c) => c.categoryId === 'fun')!
    const hobby = flat().find((c) => c.categoryId === 'hobby')!
    expect(fun.soleMember).toBe(false)
    expect(fun.fromEnvelope).toBe('Fun stuff')
    expect(hobby.sourceEnvelopeId).toBe('env-fun')
    expect(fun.label).toBe('Fun')
  })

  it('a 1:1 row is a sole member (its money folds in when added)', () => {
    const dining = flat().find((c) => c.categoryId === 'dining')!
    expect(dining.soleMember).toBe(true)
    expect(dining.fromEnvelope).toBeNull()
  })
})

// ── allEnvelopeTargets — "Add to envelope…" target picker (Unbudgeted line) ────────
describe('allEnvelopeTargets — Unbudgeted "Add to envelope…" target rules', () => {
  const dining = mkCat({
    key: 'dining',
    label: 'Dining',
    envelopeId: 'env-dining',
    categoryIds: ['dining'],
  })
  const merged = mkCat({
    key: 'fun',
    label: 'Fun',
    envelopeId: 'env-fun',
    categoryIds: ['fun', 'hobby'],
  })
  const noEnvelope = mkCat({ key: 'misc', label: 'Misc', envelopeId: '', categoryIds: ['misc'] })
  const commitmentCat = mkCat({
    key: 'rent',
    label: 'Rent',
    envelopeId: 'env-rent',
    categoryIds: ['rent'],
  })
  const groups: LocalGroup[] = [
    mkGroup({ id: 'g-a', name: 'Everyday', cats: [dining, noEnvelope] }),
    mkGroup({ id: 'g-b', name: 'Fun money', cats: [merged] }),
    mkGroup({ id: 'g-synced', name: 'Commitments', synced: true, cats: [commitmentCat] }),
  ]

  it('lists every real envelope — unlike mergeTargetsFor, there is no "source" to exclude', () => {
    const keys = allEnvelopeTargets(groups).flatMap((g) => g.cats.map((c) => c.key))
    expect(keys).toEqual(['env-dining', 'env-fun'])
  })

  it('excludes synced (Commitments) groups and envelope-less rows', () => {
    const targets = allEnvelopeTargets(groups)
    expect(targets.some((g) => g.name === 'Commitments')).toBe(false)
    const keys = targets.flatMap((g) => g.cats.map((c) => c.key))
    expect(keys).not.toContain('env-rent')
    expect(keys).not.toContain('')
  })

  it('labels an N>1 target with its category count', () => {
    const fun = allEnvelopeTargets(groups)
      .flatMap((g) => g.cats)
      .find((c) => c.key === 'env-fun')!
    expect(fun.label).toBe('Fun (2 categories)')
  })
})

// ── categoryKebabItems — zero-category envelope row ordering (RFC envelope⊥category
// full split §Frontend: "its kebab leads with Add categories…") ──────────────────
describe('categoryKebabItems — zero-category envelope row', () => {
  const noopHandlers = (): EnvelopeKebabHandlers => ({
    onRename: vi.fn(),
    onEditTarget: vi.fn(),
    onMoveTo: vi.fn(),
    onToggleHide: vi.fn(),
    onDeleteEnvelope: vi.fn(),
    onMerge: vi.fn(),
    onSplit: vi.fn(),
    onAddCats: vi.fn(),
  })
  const catLabel = (k: string) => k
  const empty = mkCat({
    key: 'env-empty',
    label: 'Emergency fund',
    envelopeId: 'env-empty',
    categoryIds: [],
  })
  const sibling = mkCat({
    key: 'dining',
    label: 'Dining',
    envelopeId: 'env-dining',
    categoryIds: ['dining'],
  })
  const merged = mkCat({
    key: 'fun',
    label: 'Fun',
    envelopeId: 'env-fun',
    categoryIds: ['fun', 'hobby'],
  })
  // A third real category so BOTH `empty` and `sibling` have a genuine "Add categories…"
  // candidate (a fixture with only `empty` + `sibling` gives `sibling` none — its only would-be
  // candidate, `empty`, contributes zero rows since categoryIds is [] — flatMap of nothing).
  const groceries = mkCat({
    key: 'groceries',
    label: 'Groceries',
    envelopeId: 'env-groceries',
    categoryIds: ['groceries'],
  })
  const groups: LocalGroup[] = [
    mkGroup({ id: 'g-a', name: 'Everyday', cats: [empty, sibling, groceries, merged] }),
  ]
  const labelsOf = (items: KebabItem[]) =>
    items.map((it) => ('label' in it ? it.label : 'header' in it ? `#${it.header}` : '—'))

  it('leads with "Add categories…" when a linkable candidate exists', () => {
    const items = categoryKebabItems(empty, [], groups, catLabel, noopHandlers())
    expect(labelsOf(items)[0]).toBe('Add categories…')
  })

  it('does not duplicate "Add categories…" further down the menu', () => {
    const items = categoryKebabItems(empty, [], groups, catLabel, noopHandlers())
    const labels = labelsOf(items)
    expect(labels.filter((l) => l === 'Add categories…')).toHaveLength(1)
  })

  it('suppresses Hide/Unhide (no backing category) but still offers "Delete envelope…"', () => {
    const items = categoryKebabItems(empty, [], groups, catLabel, noopHandlers())
    const labels = labelsOf(items)
    expect(labels).not.toContain('Hide')
    expect(labels).not.toContain('Unhide')
    expect(labels).toContain('Delete envelope…')
  })

  it('still offers Rename and target editing (route to the envelope, not a category)', () => {
    const items = categoryKebabItems(empty, [], groups, catLabel, noopHandlers())
    const labels = labelsOf(items)
    expect(labels).toContain('Rename')
    expect(labels).toContain('Add target…')
  })

  it('a normal 1:1 row is unaffected — Hide stays, Add categories… stays after Merge', () => {
    const items = categoryKebabItems(sibling, [], groups, catLabel, noopHandlers())
    const labels = labelsOf(items)
    expect(labels).toContain('Hide')
    expect(labels[0]).not.toBe('Add categories…')
  })

  // ── handoff item 1 — "Remove from envelope" lists on EVERY linked row, 1:1 included ──
  it('a 1:1 row ALSO gets "Remove from envelope" (handoff delta vs the P4 baseline)', () => {
    const items = categoryKebabItems(sibling, [], groups, catLabel, noopHandlers())
    const labels = labelsOf(items)
    expect(labels).toContain('#Remove from envelope')
    expect(labels).toContain('dining')
  })

  it('a merged (N>1) row lists every member under "Remove from envelope"', () => {
    const items = categoryKebabItems(merged, [], groups, catLabel, noopHandlers())
    const labels = labelsOf(items)
    expect(labels).toContain('#Remove from envelope')
    expect(labels).toContain('fun')
    expect(labels).toContain('hobby')
  })

  it('a zero-category row has no "Remove from envelope" section (nothing to remove)', () => {
    const items = categoryKebabItems(empty, [], groups, catLabel, noopHandlers())
    expect(labelsOf(items)).not.toContain('#Remove from envelope')
  })

  // ── handoff item 2 — "Delete envelope…" replaces the old 1:1-only category "Delete" ──
  it('"Delete envelope…" appears on every non-locked row — 1:1, merged, and empty alike', () => {
    for (const c of [sibling, merged, empty]) {
      const labels = labelsOf(categoryKebabItems(c, [], groups, catLabel, noopHandlers()))
      expect(labels).toContain('Delete envelope…')
    }
  })

  it('the old plain "Delete" verb is gone — category deletion moved to the manager', () => {
    const labels = labelsOf(categoryKebabItems(sibling, [], groups, catLabel, noopHandlers()))
    expect(labels).not.toContain('Delete')
  })

  it('"Move to group" now offers on a merged (N>1) row too — no longer 1:1-only', () => {
    const otherGroups = [mkGroup({ id: 'g-b', name: 'Wants', cats: [] })]
    const labels = labelsOf(
      categoryKebabItems(merged, otherGroups, groups, catLabel, noopHandlers()),
    )
    expect(labels).toContain('#Move to group')
    expect(labels).toContain('Wants')
  })

  it('a merged (N>1) row renames the ENVELOPE, not "Rename"', () => {
    const labels = labelsOf(categoryKebabItems(merged, [], groups, catLabel, noopHandlers()))
    expect(labels).toContain('Rename envelope')
    expect(labels).not.toContain('Rename')
  })
})

// ── deriveEnvelopeName — NewEnvelopeModal's name-prefill rule (handoff item 4) ────────────
describe('deriveEnvelopeName', () => {
  it('prefills from the first picked category while untouched', () => {
    expect(deriveEnvelopeName(['Groceries', 'Dining'], false, '')).toBe('Groceries')
  })

  it('is empty when nothing is picked and the user has not typed', () => {
    expect(deriveEnvelopeName([], false, '')).toBe('')
  })

  it('once the user has typed, the manual value wins even if picks change', () => {
    expect(deriveEnvelopeName(['Groceries'], true, 'Everyday food')).toBe('Everyday food')
  })

  it('a typed EMPTY string still wins over the prefill (explicit clear)', () => {
    expect(deriveEnvelopeName(['Groceries'], true, '')).toBe('')
  })
})

// ── unlinkedCandidateCategories — NewEnvelopeModal's checklist source ─────────────────────
describe('unlinkedCandidateCategories', () => {
  function mkMeta(
    overrides: Partial<CategoryMeta> & Pick<CategoryMeta, 'id' | 'label' | 'kind'>,
  ): CategoryMeta {
    return { color: '#3b86dd', ...overrides }
  }
  const donations = mkMeta({ id: 'donations', label: 'Donations', kind: 'expense' })
  const salary = mkMeta({ id: 'salary', label: 'Salary', kind: 'income' })
  const ccpay = mkMeta({ id: 'ccpay', label: 'Credit card payment', kind: 'transfer' })
  const split = mkMeta({ id: 'split', label: 'Split', kind: 'expense' })
  const groceries = mkMeta({ id: 'groceries', label: 'Groceries', kind: 'expense' })
  const hiddenCat = mkMeta({ id: 'hidden_one', label: 'Hidden one', kind: 'expense' })
  const categories = [donations, salary, ccpay, split, groceries, hiddenCat]
  const groups: LocalGroup[] = [
    mkGroup({
      id: 'g-a',
      name: 'Everyday',
      cats: [
        mkCat({
          key: 'groceries',
          label: 'Groceries',
          envelopeId: 'env-groceries',
          categoryIds: ['groceries'],
        }),
      ],
    }),
  ]

  it('excludes income-kind categories (inflows never link to envelopes)', () => {
    const keys = unlinkedCandidateCategories(categories, groups, new Set()).map((c) => c.id)
    expect(keys).not.toContain('salary')
  })

  it('excludes the structural "split" sentinel', () => {
    const keys = unlinkedCandidateCategories(categories, groups, new Set()).map((c) => c.id)
    expect(keys).not.toContain('split')
  })

  it('excludes hidden categories', () => {
    const keys = unlinkedCandidateCategories(categories, groups, new Set(['hidden_one'])).map(
      (c) => c.id,
    )
    expect(keys).not.toContain('hidden_one')
  })

  it('excludes categories already linked to an envelope', () => {
    const keys = unlinkedCandidateCategories(categories, groups, new Set()).map((c) => c.id)
    expect(keys).not.toContain('groceries')
  })

  it('includes transfer-kind categories (categorized transfers are intentionally included)', () => {
    const keys = unlinkedCandidateCategories(categories, groups, new Set()).map((c) => c.id)
    expect(keys).toContain('ccpay')
  })

  it('includes an ordinary unlinked expense category', () => {
    const keys = unlinkedCandidateCategories(categories, groups, new Set()).map((c) => c.id)
    expect(keys).toContain('donations')
  })
})

// ── describeUnlinkedCategoriesLine — DeleteEnvelopeModal's member-categories bullet ───────
describe('describeUnlinkedCategoriesLine', () => {
  it('empty envelope → empty string (the bullet is suppressed at the call site)', () => {
    expect(describeUnlinkedCategoriesLine([])).toBe('')
  })

  it('a single member, singular grammar', () => {
    expect(describeUnlinkedCategoriesLine(['Groceries'])).toBe(
      '1 category (Groceries) becomes unbudgeted — link it to another envelope any time',
    )
  })

  it('multiple members, plural grammar, every label spelled out (no truncation)', () => {
    expect(describeUnlinkedCategoriesLine(['Utilities', 'Water', 'Gas'])).toBe(
      '3 categories (Utilities, Water, Gas) become unbudgeted — link them to another envelope any time',
    )
  })
})

// Owner, 2026-09-25: "Add categories to …" never offered categories in NO envelope (Gifts,
// Household under Outside envelopes) — exactly the ones that need one.
describe('addCategoryCandidatesFor — categories in no envelope', () => {
  const target = mkCat({ key: 'gifts', label: 'Gifts', envelopeId: 'env-gifts', categoryIds: [] })
  const other = mkCat({ key: 'rent', label: 'Rent', envelopeId: 'env-rent', categoryIds: ['rent'] })
  const groups: LocalGroup[] = [mkGroup({ id: 'g-a', name: 'Bills', cats: [target, other] })]
  const unlinked = [
    { id: 'cat_gifts', label: 'Gifts', kind: 'expense' },
    { id: 'cat_household', label: 'Household', kind: 'expense' },
  ] as unknown as Parameters<typeof addCategoryCandidatesFor>[3]

  it('lists them first, as a plain add with nothing to detach', () => {
    const [first, ...rest] = addCategoryCandidatesFor(target, groups, (k) => k, unlinked)
    expect(first!.name).toBe('Not in an envelope')
    expect(first!.cats.map((c) => c.label)).toEqual(['Gifts', 'Household'])
    expect(first!.cats.every((c) => c.unlinked && c.sourceEnvelopeId === '' && !c.soleMember)).toBe(
      true,
    )
    expect(rest.flatMap((g) => g.cats).map((c) => c.categoryId)).toEqual(['rent'])
  })

  it('no unlinked categories → no such group', () => {
    expect(addCategoryCandidatesFor(target, groups, (k) => k).map((g) => g.name)).toEqual(['Bills'])
  })
})
