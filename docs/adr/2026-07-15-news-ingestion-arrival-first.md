---
title: Arrival-first news ingestion — user-supplied custom feed URLs as a new cron-fetched external-I/O class; arrival-clock spike detection OR-combined onto the shipped publish-clock detector
status: Accepted
date: 2026-07-15
deciders: orchestrating Claude agent (INT5), owner-directed mid-build amendment; ratified by code review
---

# Arrival-first news ingestion: custom feeds' SSRF posture + arrival-clock OR-semantics

## Context

Full design + rationale: `.claude/rfcs/news-ingestion-latency.md` (INT5 — low-latency news
ingestion, driven by the measured Jul-14 Uber/Kestrel Dash incident: 4.3h alert lag). This
ADR records two decisions from that build that are cross-cutting or hard-to-reverse enough to
warrant their own record, separate from the RFC's full narrative.

## Decision 1 — `custom_feed_urls`: a new class of user-supplied, cron-fetched external I/O

Mid-build owner amendment (open-source generalization: the project must not hardcode the
German/US markets the incident happened to involve): any watchlist item (ticker or topic) may
carry up to **5** `custom_feed_urls` — arbitrary RSS/Atom URLs the user supplies, fetched
every ~90s by the unattended hot-tier scheduler job (`rss_poll_hot`), with **zero interactive
confirmation** at fetch time (the confirmation happens once, at save time). A sibling field,
`linked_tickers` (topic-to-ticker linkage, not a URL — no SSRF surface), is capped at 10 for
the same "don't let one item's list grow unbounded" discipline, but is a separate, lower-risk
decision not covered further here.

This is a genuinely new risk shape for this codebase: every other outbound-URL surface
(`web_search`, `fetch_page`) is a **per-turn, model-invoked** fetch of a URL that appears in
that turn's context — a human (or the model, turn-scoped) is in the loop. `custom_feed_urls`
is **stored, then fetched unattended, forever, on a cron** — closer in shape to a scheduled
task than a chat tool call.

**Decided posture:**

- **Validate once, at the single write choke, not at fetch time.** `db/watchlist_store.py`'s
  `_validate_custom_feed_urls` runs inside `WatchlistStore.insert`/`update_by_id` — the ONE
  path both REST (`api/watchlist.py`) and every AI-tool write (`ai/tools/watchlist.py`) pass
  through, so no caller (interactive or headless) can bypass it. `core.net_guard.assert_public_
  url` (http(s)-only, rejects private/loopback/link-local/reserved targets) is reused as-is —
  the SAME guard `monitoring/collect.py`'s `_fetch_feed` re-validates on every hop redirect.
  **Deliberately NOT re-validated on every fetch** beyond what `_fetch_feed` already does for
  every feed URL (its own SSRF guard + per-hop redirect re-check) — a second bespoke gate at
  cron time would be redundant with the existing collector-wide guard, not a stronger one.
- **Bounded, not confirmed.** ≤5 URLs per item is a resource cap (bounds worst-case fetch
  volume per item), not a security control — the SSRF guard is the security control. A user
  who wants to point their own watchlist at a hostile URL already controls their own instance
  and their own network egress; the guard's job is preventing an LLM-coerced or injected value
  from reaching an internal/metadata host, not preventing a deliberate self-inflicted action.
- **No DNS-rebinding IP-pinning** (a TOCTOU where the guard's `assert_public_url` resolves a
  hostname to a public IP, but the actual connect resolves it differently to a private one) —
  explicitly OUT of scope for this lane; it is a codebase-wide `net_guard` hardening item
  tracked separately, not specific to this new surface.
- **Failure isolation stays per-source, not per-URL-confirmed**: a custom feed that starts
  timing out or erroring is swallowed exactly like a first-party feed (`_fetch_feed`'s
  existing catch-all), with per-URL fetch-failure state now surfaced via
  `collect.fetch_failed(url)` (added for the hot-tier's own source backoff, decision 2's
  sibling fix — see the SF-3 finding in the code-review record). Custom feeds are NOT yet
  wired into the backoff tracker (scoped to the two FIXED shared sources — the regulatory
  registry + GDELT — since those are what a persistent failure risks an IP ban on); a broken
  custom feed simply gets no fresh content each tick until the user fixes/removes it. Follow-up,
  not blocking.

## Decision 2 — Arrival-clock spike detection is OR-combined onto the shipped detector, never a replacement

INT3 shipped a **publish-clock** news-spike detector (`_is_spike`, counts `published_at` in a
90-min window vs. a trailing baseline) with a load-bearing cold-start guard: a first-ever
backlog fetch (many rows, all `first_seen=now`, `published_at` spread over days) must NOT fire
(`tests/test_rss_poller.py::test_cold_start_regression_backlog_does_not_fire`, ADR-adjacent
regression from a real 2026-07 incident).

The Jul-14 incident's root cause was the OPPOSITE failure mode: a story whose `published_at`
predates the 90-min window even though it just landed in the cache (a late-indexed syndicate
copy) can never trip a publish-only counter — "born stale." The RFC's literal step 8 read as
"wire topics + hot assets to `clock=seen`," which — read as a wholesale REPLACEMENT of the
publish-clock check — would reopen the INT3 cold-start regression (a fresh backlog fetch has
recent `first_seen` for every row too, so a first_seen-only counter fires on exactly the
scenario INT3 fixed).

**Decided: `_is_spike_any_clock` OR-combines both clocks**, never replacing the shipped
detector: `_is_spike(rows, clock="published")` first (unchanged, INT3's exact behavior); if
that doesn't fire, `_is_spike(rows, clock="seen")` (reads `first_seen` instead of
`published_at` for both the recent-window count AND the baseline). `rss_poller.py`'s fast
(held assets) and slow (topics) paths both call `_is_spike_any_clock` instead of `_is_spike`
directly. This is provably safe against the cold-start regression: the arrival clock's OWN
cold-start guard (`_SPIKE_MIN_HISTORY_DAYS` applied to `first_seen` spread, not `published_at`
spread) blocks a first-ever backlog fetch exactly the way the publish-clock guard always has —
a backlog dumped in ONE poll has near-zero `first_seen` SPREAD regardless of how old the
articles are, so `history_span_days < _SPIKE_MIN_HISTORY_DAYS` on the seen clock too.
Regression-tested both directions: `tests/test_spike_detection.py::
test_is_spike_any_clock_cold_start_backlog_still_blocked_on_both_clocks` (guard holds) and
`test_is_spike_any_clock_july14_replay_late_indexed_wave` / `tests/test_rss_poller.py::
test_fast_tier_arrival_clock_catches_late_indexed_wave_publish_clock_misses` (the incident
scenario now fires).

**Consequence accepted**: a genuinely established subject can now spike on volume alone via
EITHER clock — this is strictly additive recall (more true positives), not a precision
trade-off, since both clocks share the identical floor/factor/cold-start-guard math, just
reading a different timestamp field.

## Alternatives considered

- **Confirm every custom-feed fetch interactively** — defeats the point (it's a background
  cron source); rejected.
- **A separate, stricter SSRF re-check at cron time** — redundant with `_fetch_feed`'s existing
  guard, which already applies to every feed URL uniformly regardless of source; rejected as
  needless duplication.
- **Replace `_is_spike` with the arrival clock outright** (the RFC's literal step 8 wording) —
  reopens the INT3 cold-start regression; rejected, caught in code review before merge.
- **A configurable per-user clock choice** (published-only / seen-only / both) — deferred;
  the OR-combination is a strict improvement over publish-only with no observed downside, so
  a toggle adds complexity with no clear user-facing benefit yet.

## References

- RFC: `.claude/rfcs/news-ingestion-latency.md`
- ADR `2026-07-15-batch-seam-capability-routing.md` (sibling INT-lane ADR, same day)
- Code review findings this ADR responds to: SF-2 (cross-subject arrival gap, a related but
  DISTINCT fix — see `rss_poller.py::_augment_with_linked_topics`), SF-3 (hot-tier per-source
  failure backoff)
