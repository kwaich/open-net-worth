// Optimistic Ready to Assign moves by what a write does to the IDENTITY's terms (ADR
// 2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category):
//
//   Ready to Assign = cash − baseline − held in envelopes − card spending not covered
//
// `held in envelopes` is the SIGNED Σ of every envelope's Available, so assigning `delta` moves
// it by exactly `delta` and Ready to Assign by exactly `−delta` — no clamp, no per-row case,
// whether the row was in the black, in a hole, or crossing zero.
//
// THIS IS A DELIBERATE REVERSAL of the one-day 2026-09-08 identity, whose `set aside` term was
// Σ max(available, 0): under that rule, assigning into a hole reserved nothing and Ready to
// Assign did not move at all. Under the restored YNAB rule an envelope HOLDS its own overspend
// until month-end, so covering it is a real payment and genuinely COSTS Ready to Assign, dollar
// for dollar up to the size of the hole. The pins marked REVERSAL below are exactly the cases
// that flipped; they carry that history so the reversal cannot be "fixed" back by someone
// reading only the older tests.
//
// `card_spend_not_covered` has no client-side offset: it is in-month card-spend ACTIVITY, and no
// assignment creates or retires a swipe. (The server DOES re-cover past swipes out of a newly
// funded spending envelope — that shifts money from this term into the payment envelope's own
// Available, equal and opposite, so the identity still closes; only the split between the two
// terms is stale until the next payload.)
import { describe, it, expect } from 'vitest'
import { rtaDeltaForAssign, heldDeltaForAssign, buildClientAutoSteps } from './useBudgetModel'
import type { LocalCat } from './useBudgetModel'
import { buildRtaLines } from './rtaLines'
import { statusOf } from './planData'

const CASH = 5000
const r2 = (n: number) => Math.round(n * 100) / 100

function mkCat(key: string, assigned: number, available: number): LocalCat {
  return {
    key,
    envelopeId: `env-${key}`,
    categoryIds: [key],
    label: key,
    color: '#34b08a',
    assigned,
    activity: 0,
    available,
    target: 0,
    tt: null,
    cad: null,
    ...statusOf(assigned, 0, available, 0, null),
  }
}

/** A payment-envelope row as the server sends it: `owed` is the card's real debt, on the wire in
 *  its own right; `available_for_payment` IS the row's own signed available; and
 *  `underfunded = max(0, owed − max(available, 0))` — derived from those two, never the reverse.
 *  None of those fields enters the RTA arithmetic any more: a payment envelope is an envelope
 *  row like any other, and assigning into it moves what's held by exactly what was assigned. */
function mkPaymentCat(key: string, owed: number, available: number): LocalCat {
  return {
    ...mkCat(key, 0, available),
    isPaymentEnvelope: true,
    paymentOwed: owed,
    availableForPayment: available,
    paymentUnderfunded: Math.max(0, owed - Math.max(0, available)),
  }
}

const sumHeld = (rows: LocalCat[]) => r2(rows.reduce((s, c) => s + c.available, 0))

/** A book as the tab holds it: the model's rows, the payload's OWN `held_in_envelopes` and
 *  `card_spend_not_covered` figures, and the held offset our optimistic writes have accumulated
 *  since that payload landed. Held is payload+offset rather than a live re-sum of the rows:
 *  re-deriving it made it one render younger than the payload's other terms, so a healthy book
 *  fired the breakdown's defect warning on every ordinary load. */
interface Book {
  rows: LocalCat[]
  serverHeld: number
  serverCardSpend: number
  heldOffset: number
}
const book = (rows: LocalCat[], serverCardSpend = 0): Book => ({
  rows,
  serverHeld: sumHeld(rows),
  serverCardSpend,
  heldOffset: 0,
})

const heldOf = (b: Book) => r2(b.serverHeld + b.heldOffset)

/** RTA implied by the identity — what the server would send for this book. */
const rtaOf = (b: Book) => r2(CASH - heldOf(b) - b.serverCardSpend)

/** The whole point: the optimistic RTA and the popover's terms must still be one identity
 *  mid-write, so the breakdown never grows an "Unexplained" line. */
function expectPopoverReconciles(b: Book, rta: number) {
  const lines = buildRtaLines({
    onBudgetCash: CASH,
    heldInEnvelopes: heldOf(b),
    heldOutsideEnvelopes: 0,
    cardSpendNotCovered: b.serverCardSpend,
    baseline: 0,
    assignedInFuture: 0,
    rta,
  })
  expect(lines.some((l) => l.key === 'other')).toBe(false)
}

/** Replay what the model does optimistically: `assigned`/`available` move on the touched rows
 *  (the payment fields keep their server values — an assign never pays the card), and the held
 *  offset accumulates each leg's own Δ, exactly as `applyOptimisticTerms` does. */
function applyWrite(b: Book, legs: [string, number][]): Book {
  const byKey = new Map(legs)
  const heldOffset = legs.reduce((o, [, delta]) => r2(o + heldDeltaForAssign(delta)), b.heldOffset)
  return {
    ...b,
    rows: b.rows.map((c) =>
      byKey.has(c.key)
        ? {
            ...c,
            assigned: r2(c.assigned + byKey.get(c.key)!),
            available: r2(c.available + byKey.get(c.key)!),
          }
        : c,
    ),
    heldOffset,
  }
}

/** One optimistic write, end to end: returns how RTA moved. */
function writeOptimistically(b: Book, legs: [string, number][]): number {
  const dRta = legs.reduce((sum, [, delta]) => sum + rtaDeltaForAssign(delta), 0)
  const rtaBefore = rtaOf(b)
  const rtaAfter = r2(rtaBefore + dRta)
  const after = applyWrite(b, legs)
  expect(rtaAfter).toBe(rtaOf(after)) // the optimistic figure IS what the server will send back
  expectPopoverReconciles(after, rtaAfter)
  return r2(rtaAfter - rtaBefore)
}

const assignOptimistically = (b: Book, key: string, delta: number) =>
  writeOptimistically(b, [[key, delta]])

describe('rtaDeltaForAssign — an ordinary envelope', () => {
  const hole = () => book([mkCat('groceries', 0, -50), mkCat('rent', 500, 500)])

  // REVERSAL (ADR 2026-09-09 over 2026-09-08). Yesterday's pin read "topping up part of a hole
  // moves nothing — the cash it spent already left RTA": +20 into −50 gave ΔRTA 0. The restored
  // YNAB rule holds the overspend in the envelope until month-end, so nothing has been charged
  // yet and covering it IS the payment: +20 costs a full 20.
  it('covering PART of a hole costs the full amount — the hold has not been paid yet', () => {
    expect(assignOptimistically(hole(), 'groceries', 20)).toBe(-20)
  })

  // REVERSAL. Yesterday only the part landing ABOVE zero reserved anything, so +80 into −50
  // moved RTA by −30. Now the whole 80 is a cost: 50 settles the hold, 30 is newly held for
  // later, and both are money that stopped being assignable.
  it('filling a hole past zero costs the WHOLE assignment, not just the part above zero', () => {
    expect(assignOptimistically(hole(), 'groceries', 80)).toBe(-80)
  })

  it('assigning into a funded envelope costs the whole amount', () => {
    expect(assignOptimistically(book([mkCat('gifts', 10, 10)]), 'gifts', 20)).toBe(-20)
  })

  // REVERSAL, the mirror image: releasing a hole's assignment used to give nothing back.
  it('releasing gives the whole amount back, from a hole exactly as from a funded row', () => {
    expect(assignOptimistically(hole(), 'groceries', -20)).toBe(20)
    expect(assignOptimistically(hole(), 'rent', -200)).toBe(200)
  })
})

describe('rtaDeltaForAssign — a card payment envelope is an envelope like any other', () => {
  // REVERSAL: funding an unfunded card used to be RTA-neutral (set-aside up, netted card debt
  // down). A card's debt no longer reaches Ready to Assign at all — it lives on the card — so
  // assigning toward it is an ordinary cost, which is exactly what "pay the card off over time"
  // has to feel like.
  it('funding a card costs Ready to Assign, whatever the card owes', () => {
    expect(assignOptimistically(book([mkPaymentCat('cc', 100, 0)]), 'cc', 20)).toBe(-20)
    expect(assignOptimistically(book([mkPaymentCat('cc', 10, 0)]), 'cc', 20)).toBe(-20)
  })

  it('releasing from a paid-off card envelope gives the money back', () => {
    expect(assignOptimistically(book([mkPaymentCat('cc', 0, 40)]), 'cc', -30)).toBe(30)
  })

  // The offset is the plain signed sum of what we assigned — no card field enters it.
  it('the held offset carries exactly what was assigned', () => {
    const b = book([mkPaymentCat('cc', 100, 0)])
    expect(applyWrite(b, [['cc', 20]]).heldOffset).toBe(20)
    expect(applyWrite(b, [['cc', 150]]).heldOffset).toBe(150)
  })
})

// `card_spend_not_covered` is a server figure our writes never move — a book carrying one still
// reconciles at rest and after an ordinary assign, with the offset tracking only what WE did.
describe('a book with uncovered card spending this month', () => {
  const withCardSpend = () => book([mkCat('rent', 500, 500)], 300)

  it('reconciles at rest', () => {
    const b = withCardSpend()
    expect(rtaOf(b)).toBe(4200) // 5000 cash − 500 held − 300 uncovered card spend
    expectPopoverReconciles(b, rtaOf(b))
  })

  it('still reconciles after an ordinary assign — the offset tracks only what WE changed', () => {
    const b = withCardSpend()
    expect(assignOptimistically(b, 'rent', 20)).toBe(-20)
    expect(applyWrite(b, [['rent', 20]]).heldOffset).toBe(20)
  })
})

describe('"Top up overspent envelopes" — the whole preview now COSTS Ready to Assign', () => {
  // REVERSAL: under 2026-09-08 this preview was RTA-neutral by construction, which is why
  // `_auto_cover_overspending` shipped uncapped for a day. Under the restored identity every
  // step is a real payment, so the backend's cap at the displayed RTA is load-bearing again.
  it('costs exactly the sum of the holes it fills', () => {
    const b = book([
      mkCat('groceries', 0, -50),
      mkCat('transport', 10, -20),
      mkCat('rent', 500, 500),
    ])
    const steps = buildClientAutoSteps(b.rows, 'Top up overspent envelopes')
    expect(steps.map((s) => s.key)).toEqual(['groceries', 'transport'])
    expect(
      writeOptimistically(
        b,
        steps.map((s) => [s.key, s.delta] as [string, number]),
      ),
    ).toBe(-70)
  })
})

describe('moveBetweenEnvelopes — both legs, one at a time', () => {
  // REVERSAL: yesterday this handed 30 back to Ready to Assign, because a hole reserved nothing.
  // Now both legs are ordinary signed moves that cancel — money merely changed pocket.
  it('moving a funded balance into a hole is RTA-neutral, like any envelope→envelope move', () => {
    const b = book([mkCat('rent', 500, 50), mkCat('groceries', 0, -50)])
    expect(
      writeOptimistically(b, [
        ['rent', -30],
        ['groceries', 30],
      ]),
    ).toBe(0)
  })

  it('a move between two funded envelopes is still RTA-neutral', () => {
    const b = book([mkCat('rent', 500, 500), mkCat('gifts', 10, 10)])
    expect(
      writeOptimistically(b, [
        ['rent', -30],
        ['gifts', 30],
      ]),
    ).toBe(0)
  })

  // An `__rta` end contributes no envelope term at all, so the single envelope leg moves the
  // headline on its own — exactly the shape the hook passes to `applyOptimisticTerms`.
  it('a leg to or from Ready to Assign moves the headline by the whole amount', () => {
    const b = book([mkCat('rent', 500, 500)])
    expect(writeOptimistically(b, [['rent', 40]])).toBe(-40)
    expect(writeOptimistically(b, [['rent', -40]])).toBe(40)
  })
})
