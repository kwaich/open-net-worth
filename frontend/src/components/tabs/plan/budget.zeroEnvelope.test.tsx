// @vitest-environment jsdom
//
// RFC envelope⊥category full split §Frontend: "Zero-category envelope row: renders like any
// row; a muted hint chip '0 categories'". Regression coverage for CategoryRow's
// rendering of an envelope with categoryIds: [] (created via "Add envelope", nothing linked
// yet) — the hint chip must appear ONLY for that case, never for a normal 1:1 or merged row.
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { CategoryRow } from './budget'
import { fSmart } from './planData'
import type { LocalCat, LocalGroup } from './useBudgetModel'

afterEach(() => cleanup())

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

function noopDnd() {
  return {
    catStart: () => () => {},
    groupStart: () => () => {},
    rowOver: () => () => {},
    rowDrop: () => () => {},
    groupOver: () => () => {},
    groupDrop: () => () => {},
    end: () => {},
  }
}

function renderRow(c: LocalCat, allGroups: LocalGroup[] = [], locked = false) {
  return render(
    <CategoryRow
      c={c}
      gid="g-a"
      dnd={noopDnd()}
      over={null}
      otherGroups={[]}
      locked={locked}
      month="2026-07"
      allGroups={allGroups}
      catLabel={(k) => k}
      catColor={() => '#34b08a'}
      rta={0}
      onOpenCommitment={vi.fn()}
      onOpenCardPayment={vi.fn()}
      onAssign={vi.fn()}
      onAction={vi.fn()}
      onRename={vi.fn()}
      onRecolor={vi.fn()}
      onMoveTo={vi.fn()}
      onToggleHide={vi.fn()}
      onDeleteEnvelope={vi.fn()}
      onEditTarget={vi.fn()}
      onTransfer={vi.fn()}
      onMerge={vi.fn()}
      onSplit={vi.fn()}
      onAddCats={vi.fn()}
    />,
  )
}

// The chip shows the tag glyph + the count; the noun rides along for screen readers, so the
// chip's own text still reads "2 categories" (owner, 2026-09-25).
const chip = (text: string) =>
  screen.queryByText(
    (_, el) => !!el?.matches('.pl-envcats, .pl-gcount--empty') && el.textContent === text,
  )

describe('CategoryRow — zero-category envelope row', () => {
  it('renders the "0 categories" hint chip', () => {
    const c = mkCat({
      key: 'env-empty',
      label: 'Emergency fund',
      envelopeId: 'env-empty',
      categoryIds: [],
    })
    renderRow(c)
    expect(chip('0 categories')).toBeTruthy()
  })

  it('the hint chip carries a linking hint in its title', () => {
    const c = mkCat({
      key: 'env-empty',
      label: 'Emergency fund',
      envelopeId: 'env-empty',
      categoryIds: [],
    })
    renderRow(c)
    expect(chip('0 categories')?.getAttribute('title')).toMatch(/link/i)
  })

  it('renders normally otherwise (label, no crash) alongside the hint', () => {
    const c = mkCat({
      key: 'env-empty',
      label: 'Emergency fund',
      envelopeId: 'env-empty',
      categoryIds: [],
    })
    renderRow(c)
    expect(screen.getByText('Emergency fund')).toBeTruthy()
  })

  it('does not render the hint chip for a normal 1:1 row', () => {
    const c = mkCat({
      key: 'dining',
      label: 'Dining',
      envelopeId: 'env-dining',
      categoryIds: ['dining'],
    })
    renderRow(c)
    expect(chip('0 categories')).toBeNull()
  })

  it('does not render the hint chip for a merged (N>1) row — it shows the EnvCatsBadge chip instead', () => {
    const c = mkCat({
      key: 'fun',
      label: 'Fun',
      envelopeId: 'env-fun',
      categoryIds: ['fun', 'hobby'],
    })
    renderRow(c)
    expect(chip('0 categories')).toBeNull()
    // "Fun" isn't named after either member ("fun"/"hobby", case-sensitive) → independently
    // named, N>1 → "N categories" (envCatsChipText — handoff item 15).
    expect(chip('2 categories')).toBeTruthy()
  })
})

describe('CategoryRow — CONF-1: the EnvCatsBadge is unreachable on a LOCKED (Commitments-synced) row', () => {
  it('a merged row that would normally show the badge suppresses it once locked', () => {
    const c = mkCat({
      key: 'fun',
      label: 'Fun',
      envelopeId: 'env-fun',
      categoryIds: ['fun', 'hobby'],
    })
    renderRow(c, [], true)
    expect(chip('2 categories')).toBeNull()
  })

  it('the same row shows the badge once UNLOCKED (control — proves the gate is `locked`, not something else)', () => {
    const c = mkCat({
      key: 'fun',
      label: 'Fun',
      envelopeId: 'env-fun',
      categoryIds: ['fun', 'hobby'],
    })
    renderRow(c, [], false)
    expect(chip('2 categories')).toBeTruthy()
  })
})

// Owner, 2026-09-25: an envelope with no target that's been fully used read as blank — it now
// shows YNAB's "Fully Spent" with a full hatched bar. Display-only: the status stays 'none'.
describe('CategoryRow — Fully Spent without a target', () => {
  const dining = (o: Partial<LocalCat>) =>
    mkCat({ key: 'e1', label: 'Dining out', envelopeId: 'e1', categoryIds: ['c1'], ...o })

  it('assigned and all spent, no target → "Fully Spent" and a full spent bar', () => {
    const { container } = renderRow(dining({ assigned: 2874.08, activity: -2874.08, available: 0 }))
    expect(screen.getByText('Fully Spent')).toBeTruthy()
    const fill = container.querySelector('.pl-tbar-fill.spent.funded') as HTMLElement
    expect(fill).toBeTruthy()
    expect(fill.style.width).toBe('100%')
  })

  it('money still available, or nothing spent → no "Fully Spent"', () => {
    renderRow(dining({ assigned: 100, activity: -40, available: 60 }))
    expect(screen.queryByText('Fully Spent')).toBeNull()
    cleanup()
    renderRow(dining({ assigned: 0, activity: 0, available: 0 }))
    expect(screen.queryByText('Fully Spent')).toBeNull()
  })
})

describe('CategoryRow — Fully Spent with a met target', () => {
  const loan = (o: Partial<LocalCat>) =>
    mkCat({ key: 'e2', label: 'Car Loan', envelopeId: 'e2', categoryIds: ['c2'], ...o })

  it('target met and every dollar spent → "Fully Spent", not "Funded"', () => {
    renderRow(
      loan({
        assigned: 3867.75,
        activity: -3867.75,
        available: 0,
        target: 3867.75,
        status: 'funded',
      }),
    )
    expect(screen.getByText('Fully Spent')).toBeTruthy()
    expect(screen.queryByText('Funded')).toBeNull()
  })

  it('target met with money left → still "Funded"', () => {
    renderRow(
      loan({ assigned: 850, activity: -831.15, available: 18.85, target: 850, status: 'funded' }),
    )
    expect(screen.getByText('Funded')).toBeTruthy()
    expect(screen.queryByText('Fully Spent')).toBeNull()
  })
})

// Owner, 2026-09-25 (YNAB): a funded envelope says how much of its money is spent —
// "Funded. Spent S$5.50 of S$200.00" — and reads just "Funded" until something is.
describe('CategoryRow — Funded says how much of it is spent', () => {
  const groceries = (o: Partial<LocalCat>) =>
    mkCat({
      key: 'e3',
      label: 'Groceries',
      envelopeId: 'e3',
      categoryIds: ['c3'],
      target: 200,
      status: 'funded',
      ...o,
    })
  const statusText = (container: HTMLElement) =>
    container.querySelector('.pl-cstatus.funded')?.textContent

  it('part of it spent → "Funded. Spent X of Y"', () => {
    const { container } = renderRow(groceries({ assigned: 200, activity: -5.5, available: 194.5 }))
    expect(statusText(container)).toBe(`Funded. Spent ${fSmart(5.5)} of ${fSmart(200)}`)
  })

  it('nothing spent yet → just "Funded"', () => {
    const { container } = renderRow(groceries({ assigned: 200, activity: 0, available: 200 }))
    expect(statusText(container)).toBe('Funded')
  })
})
