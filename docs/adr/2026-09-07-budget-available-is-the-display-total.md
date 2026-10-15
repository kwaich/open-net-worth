---
title: The Budget summary's "available" is the wire display total — every envelope's Available plus all spending outside envelopes — not the RTA reserve; RTA treats unlinked-category spending as already gone
status: Superseded
date: 2026-09-07
deciders: implementing agent (fix/budget-outside-envelopes lane) + repo owner (design approved before implementation; the "reserve" framing corrected by owner ruling after code review, 2026-09-08)
---

> **Superseded 2026-09-08 by ADR `2026-09-08-ready-to-assign-is-unassigned-cash`.** That ADR
> retires the RTA `reserved` sum this ADR carefully distinguished the display total FROM —
> under the new identity there is no second "RTA reserve" number for the header to disagree
> with, so the header total collapses back to the plain envelope-pure sum (`Σ envelope
> available`, no Unsorted/Unbudgeted fold) and the display-total tooltip this ADR introduced is
> REMOVED. The banner this ADR added above the table (naming unlinked-category spending +
> "Add ‹category› to an envelope →") is UNTOUCHED — it is still true and useful information,
> it just no longer needs to explain a header/Total-row mismatch that no longer exists.

# 2026-09-07. The Budget summary's "available" is the wire display total

## Context

The backend computes two *different* sums that both look like "total available", and the Budget
tab had been showing a third.

**The RTA reserve** (`_ready_to_assign`, `backend/services/planning.py:1307-1312`):

```python
reserved  = sum(_available(ctx, month, spec["envelope_id"]) for spec in ctx["_rows"])
reserved += _unsorted_activity(ctx, month)          # the blank-category bucket only
reserved += _activity_map(ctx, month).get(_CARD_UNCOVERED_KEY, 0.0)
```

Envelopes + Unsorted + the card-uncovered term. It deliberately **omits Unbudgeted** — a
labeled-but-unlinked category's spending *drains RTA directly* rather than reserving into an
envelope (`planning.py:1798-1804`, `docs/PLANNING_MODEL.md`). That is a standing owner decision,
not an oversight.

**The wire display total** (`planning.py:1805-1811`):

```python
total_available = round(sum(r["available"] for r in rows.values())
                        + unsorted_activity + unbudgeted_total, 2)
```

Envelopes + Unsorted + **Unbudgeted**. It is what `GET /api/planning/month` returns as
`totals.available`, what `budget_health` reports, and what the AI's `get_budget_month` tool sees
(the 2026-07-04 `BE-TOTALS-UNBUDGETED-CONTRACT` fix aligned all three).

The two differ by the unbudgeted amount, less any card-uncovered term `reserved` carries and `totals.available` does not — identical whenever no card spending is uncovered.

The Budget tab showed neither consistently. The table's Total row folded both derived groups
(`foldExtraActivity`), matching the wire total. The Summary's "Total available" stat, the mini
"… available" line and the Lucius screen fact showed the **envelope-pure** sum, on the strength
of `ENV-RES-07/CONF-5` comments that also asserted the backend total "excludes Unbudgeted" —
false since 2026-07-04. So one screen printed two different numbers under one word.

Separately, only half the difference was visible anywhere. The yellow banner named uncategorized
spending only; a labeled-but-unlinked category (illustrative: `Gifts`, ~$200 of spending, no
envelope) appeared solely as a row far down the read-only "Outside envelopes" section, with
nothing tying it to the header's number. Real spending sat outside the plan silently.

The owner's standing principle: **one word = one number; no hidden spending.**

## Decision

We will show **one** "available" on the Budget tab — the **wire display total**: every
envelope's Available *plus* all spending that sits outside every envelope (Unsorted +
Unbudgeted). The Summary stat, the Summary mini line, the table's Total row and the Lucius
screen fact all read one `displayAvailable = foldExtraActivity(totals, extraAct)`. This is the
value the table already printed; the header now agrees with it instead of contradicting it.

We will **not** claim it is the Ready-to-Assign reserve. It is not: RTA holds back
envelopes + Unsorted + card-uncovered, and treats unlinked-category spending as money already
gone. Nothing in this change touches that math, and no code or doc may describe the displayed
figure as "what RTA subtracts".

We will compute it from the **live client model** rather than reading `m.data.totals.available`.
The two are equal to the cent at rest; the model is the one that also tracks an optimistic
assign (`assignTo` mutates the model and RTA without refetching), so reading the server field
would freeze the header the moment a user assigned money.

Because the figure is no longer envelope-scoped, the label alone cannot carry it: the stat and
the mini line get a `title` — *"Every envelope's Available, plus spending outside any
envelope."* The one genuinely envelope-scoped sentence — the coaching line "… sitting available
across your envelopes" — keeps the **envelope-pure** sum (`envelopeAvailable`), because its
wording promises that and nothing else.

We will also make the banner above the table cover **all** spending outside envelopes: it names
the unlinked categories (top three by size, then "+N more"), counts **spending only**
(`activity < -0.005` — a refund is money coming back, and netting it against another category's
spend produced a headline smaller than the lines beneath it), formats with `fSmart` so a small
category never renders as "S$0", and offers each named category an inline "Add ‹category› to an
envelope →" button that opens the **existing** `AddToEnvelopeModal` → `linkCategoryToEnvelope`
flow. The button only opens the picker; nothing is written until the user confirms in it.

Out of scope, deliberately: the RTA math itself, any backend endpoint, and the
`overspent`/`underfunded` chip counts.

## Consequences

### Positive

- One word, one number. The header and the Total row can no longer disagree, and the AI's screen
  fact reports the same figure the user is looking at.
- The displayed figure matches the documented wire contract (`totals.available`), so the UI, the
  health strip and `get_budget_month` all describe the book the same way.
- Spending outside every envelope is impossible to miss and one click from being fixed — the
  banner names it and offers the link action, instead of relying on the user scrolling to a
  read-only section.
- The `ENV-RES-07/CONF-5` comments, which asserted a backend contract untrue since 2026-07-04,
  are gone rather than quietly misleading the next reader.

### Negative

- The header figure gets **more negative** on books with outside spending. That is the honest
  number, but it will read as a regression to anyone who had internalised the old one.
- "Total available" no longer equals the sum of the envelope rows visible above it. The
  difference is exactly what the Outside-envelopes section lists, and the tooltip says so, but it
  is a reconciliation the user has to make.
- **Two legitimate "available" totals now coexist in the codebase** — the displayed one and the
  RTA reserve. Anyone adding a third surface has to pick deliberately; the names
  (`displayAvailable` vs `envelopeAvailable`) and this ADR are the guard.
- The banner is denser: with several unlinked categories it carries a compact list plus up to
  three link buttons. Capped at three named categories + "+N more".

### Follow-ups

- The RTA breakdown popover explains the cash side but never names the unlinked-category spending
  that drained RTA directly. That is the surface where the *reserve* distinction actually matters
  to a user; consider a line for it there.
- The `overspent` / `underfunded` chip counts still use the client's status vocabulary rather
  than the server's; reconcile them separately.
- The Unsorted figure is still passed pre-`abs()`, so a net-refunded blank-category month would
  read as "spending". Unchanged here; worth tightening with the same `activity < 0` rule.

## Alternatives considered

- **Call the displayed figure "the RTA reserve"** — rejected: factually wrong by exactly the
  unbudgeted amount. This was the original framing of this ADR and was caught in code review; the
  *value* shown was right, the justification was not.
- **Show the true RTA reserve in the header instead** — rejected: it would then disagree with the
  Total row directly beneath it, re-creating the same two-numbers-one-word problem from the other
  side, and it is not a number the envelope table can explain.
- **Read `m.data.totals.available` directly in the Summary** — rejected: correct at rest, stale
  after every optimistic assign (the model is mutated locally and never refetched on success), so
  the header would stop moving as the user assigned money.
- **Leave the header envelope-pure and only fix the banner** — rejected: the header would still
  contradict the Total row on the same screen.
- **Fold unlinked-category spending into the RTA identity's `reserved` sum** — rejected: that
  changes money math and re-litigates a settled owner decision. This ADR is about display
  honesty.
- **A new picker for "link this category"** — rejected: `AddToEnvelopeModal` +
  `linkCategoryToEnvelope` already exist and are the flow the Unbudgeted rows use. Reuse.

## Agent provenance

- **Decided by:** implementing agent on the `fix/budget-outside-envelopes` lane; design approved
  by the repo owner before implementation, and the "reserve" framing corrected by the owner's
  ruling after a five-lane review (2026-09-08).
- **Lane / branch:** `fix/budget-outside-envelopes`, worktree `worktrees/onw-budget-outside`.
- **Alternatives weighed:** display total vs. RTA reserve in the header; server field vs. live
  recomputation; banner-only fix; changing the RTA identity; building a new link picker.
- **Driving artifacts:** the owner's report that the header chip and the Total row disagreed on a
  real month; the stale `ENV-RES-07/CONF-5` comments in
  `frontend/src/components/tabs/plan/budget.tsx`; `backend/services/planning.py`'s
  `_ready_to_assign` (`reserved`) vs `total_available`; `docs/PLANNING_MODEL.md`.
- **Why this way (one line):** one word, one number — the "available" the user reads must be the
  one the table beneath it prints, and nothing it contains may be invisible.
