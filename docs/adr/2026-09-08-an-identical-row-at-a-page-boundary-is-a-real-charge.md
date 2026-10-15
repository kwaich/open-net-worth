---
title: An identical row at a vision page boundary is a real charge — never dedup across pages
status: Accepted
date: 2026-09-08
deciders: implementing agent (lane/B10-vision-page-boundary-drop) + repo owner
---

# 2026-09-08. An identical row at a page boundary is a real charge

## Context

`backend/services/statements/guards.py::stitch_pages` concatenated the vision tier's per-page
extractions and dropped the first row of page N+1 whenever it matched (date, direction, amount
within 0.005, stripped description) the last row of page N — on the theory it was catching a
model re-reading the previous page's last row as its first. Its only caller was
`tier3_vision` (`backend/services/statements/tiers.py`), which renders each PDF page as a
**separate image** and sends each as its own model call: pages cannot overlap, so the premise
the guard was built on never held for its one caller. It shipped in the initial commit with no
test exercising it directly.

It cost a real import: an owner's card statement printed three identical small vending-machine
charges at the bottom of page 1 and a fourth identical one at the top of page 2. The guard
silently kept 4 of 5, and nothing in the reconcile/balance guards caught the loss because the
statement still (almost) balanced.

## Decision

We will never dedup rows across a page boundary anywhere in the parse. `stitch_pages` is
deleted; `tier3_vision` flattens each page's rows in order
(`rows = [row for page in pages for row in page]`). Duplicate detection across an import stays
where it already lives for every other tier: the ledger-level dedup / reconcile guard, which
looks at the emitter identity, not row-shape adjacency.

## Consequences

### Positive

- A real repeated charge at a page boundary is never silently discarded again.
- One less deterministic guard whose only premise (page overlap) was never true for its caller —
  less code, no behavior it was meant to protect.

### Negative

- If a vision model genuinely re-reads a row across two page images (the artifact this guard was
  originally written for, even though it can't occur through today's one-page-per-image caller),
  it now surfaces as a duplicate row for the ledger-level dedup / reconcile guard to catch and
  flag — an honest failure mode (visible, correctable) in place of a silent, wrong one.

### Follow-ups

- None — `docs/INVARIANTS.md` gets one line recording the rule.

## Alternatives considered

- **Keep the guard but scope it to a real overlap signal** (e.g. only fire when the caller
  explicitly renders overlapping page images) — rejected: `tier3_vision` is the only caller and
  never produces overlapping pages, so there is no live case for the guard to protect against.
- **Lower the match tolerance instead of deleting the guard** — rejected: any threshold still
  drops a genuine identical charge; the bug is the premise, not the tuning.

## Agent provenance

- **Decided by:** implementing agent, junior-engineer lane
- **Lane / branch:** `lane/B10-vision-page-boundary-drop`
- **Alternatives weighed:** scoping the guard to a real overlap signal · tightening the match
  tolerance
- **Driving artifacts:** owner bug report (vending-machine charges dropped on a card statement
  import); `backend/services/statements/guards.py::stitch_pages`;
  `backend/services/statements/tiers.py::tier3_vision`
- **Why this way (one line):** the guard's only caller renders one page per image and can never
  overlap, so the "boundary artifact" it protected against was never real — deleting it is safer
  than tuning it.
