---
title: A seeded prev-close → live line beats a server "unavailable" state at an equity open — anchored at the session open, and disclosed
status: Accepted
date: 2026-09-03
deciders: Claude (owner-delegated: "Investigate and fix this for me … Review these codes and fix if needed") + repo owner
---

# 2026-09-03. A seeded prev-close → live line beats a server "unavailable" state at an equity open — anchored at the session open, and disclosed

## Context

Within the first minutes of a XETRA session the owner opened KSTL.DE and the 1D pane showed the
hard error "Price history unavailable — Live market data is unavailable and there is no recent
saved history for this timeframe. [Try again]" — while the header, one line up, already had a
live quote (€36.56, as of 9:01) and a previous close (€36.75). The owner's words: "I know kstl
has delayed quote. But why can't we use the latest data we have. Showing error like this when
user opens to check the open is really bad UX."

Three things were true at once:

1. The vendor's intraday tape for a delayed venue is EMPTY for the first ~15 minutes, so the
   series endpoint reports `state: "unavailable"` — the 2026-08-12 resilient-market-data spec made
   that state an unconditional veto: render the error, "no synthetic point".
2. The client already knew how to draw through that gap. `buildIntradayTape` seeds a two-point
   line from the previous close to the live quote whenever the market is open and the tape has
   fewer than two points (equities only — crypto has no prev-close convention).
3. The first fix (`4473a09b7`) let the seed override the veto — but the seed was dated at the
   VIEWER's local midnight. The 1D chart draws on a fixed 1-minute grid that starts at the
   session open and never visits an earlier bucket, so for every viewer whose midnight precedes
   the open (Berlin, Singapore, Ho Chi Minh City…) the seed was silently dropped. The error was
   gone; the pane was blank; nothing said why. The review of that fix caught it because the
   component test never passed `sessionBounds`, so it exercised the auto-scale path production
   doesn't use.

## Decision

- **The seeded line wins.** When the market is open, the series state is `unavailable`, and the
  client-built tape has ≥2 points, the chart DRAWS. `explicitUnavailable` (the error + Try again)
  is reserved for `unavailable` with nothing to draw. The 2026-08-12 spec's "no synthetic point"
  rule is superseded for the open-market equity case only; closed markets and crypto still show
  the error.
- **The seed is anchored at the session open, not local midnight.** `buildIntradayTape` takes
  `seedAt` — the same `sessionWindowRange(...).from` the fixed grid starts at — and dates the
  prev-close point there, so it lands in the first grid slot. If the open is unknown, unparseable,
  or not strictly before the first real point (a header quote stamped before the open), it falls
  back to the old ordering-only anchor rather than emit a backwards point. The regression test
  proves the fixed-grid path end to end, with real `sessionBounds`.
- **It is disclosed, in three places.** A `Delayed feed` banner (same slot and container as the
  stale "Saved data" banner) reads "Showing previous close → latest quote until intraday bars
  arrive."; the `aria-live` region announces "Intraday prices are delayed; the chart shows the
  previous close to the latest quote." instead of going silent; and the crosshair shows the
  point's `label` ("Prev close") in place of a fabricated timestamp — the field the type doc had
  promised since 2026-08 and nothing consumed.

## Consequences

- A user who opens the detail view at the bell sees the line move from the first second, and is
  told it is a seed, not the tape. Real bars replace the seed the moment two exist (or one plus a
  newer live tail); the banner goes with them.
- Try again is unreachable while the seed draws. Deliberate: with a live quote and a prev close
  there is nothing a refetch would improve that the 60s poll doesn't already do.
- Known, left alone: the `applies` rule lets a transient `unavailable` poll replace real bars
  with the seed (pre-existing, covered by a test that documents it); `points.length >= 2` is
  repeated at five sites; a cache eviction mid-session can flash the spinner. Each is a separate
  change with its own test, not a rider on this one.
