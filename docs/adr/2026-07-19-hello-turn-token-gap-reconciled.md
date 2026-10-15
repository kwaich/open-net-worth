---
title: The "hello" turn's 33,881 cache tokens — full reconciliation, and why the prior lane's accounting fell 10,500 tokens short
status: Accepted
date: 2026-07-19
deciders: repo owner (task directive, 2026-07-19); recorded by the investigating agent
related: docs/adr/2026-07-19-core-tool-offer-diet.md (superseded in part — see "What the prior lane got wrong")
---

# The "hello" turn's 33,881 cache tokens — full reconciliation

## Context

The owner typed "hello" and paid $0.194 (`2 in · 33,881 cache↑ · 14 out`). A prior
lane (`docs/adr/2026-07-19-core-tool-offer-diet.md`, commit `5a105c15`) reproduced
the turn with Anthropic's `messages.count_tokens` against the owner's real data and
accounted for only 23,337 of the 33,881+2=33,883 observed input tokens — a ~10,500
token gap — naming two unverified candidates: (a) the frontend per-screen context
snapshot, (b) the possibility that the real turn ran through OpenRouter/xAI rather
than Anthropic, making the token counts incomparable. This task's brief was to find
the rest, not repeat the same reasoning-from-code that produced the original gap.

## Method: reproduce against ground truth, not the codebase

1. **Found the exact incident row** in `~/.open-net-worth/data/cost_log.json`:
   `{"ts": "2026-07-18T21:40:05.44Z", "session_id": "95fd85f1-...", "model":
   "claude-sonnet-5", "input_tokens": 2, "cache_creation_tokens": 33881,
   "cache_read_tokens": 0, "output_tokens": 14, "cost_usd": 0.19376625,
   "surface": "web"}` — an exact match on every figure the owner quoted.
2. **Read the real session transcript**
   (`~/.open-net-worth/data/sessions/95fd85f1-....json`) to see literally what was
   sent, instead of re-deriving it.
3. **Located the exact commit that was live** when the turn fired, by timestamp
   arithmetic (turn at `2026-07-18T21:40:05Z` = `2026-07-19 05:40:05 +08:00`,
   the app's own SGD/local time) against `git log` timestamps on
   `backend/ai/prompts.py` across all branches, then reconstructed the full
   request (system blocks, tool schemas, cache_control placement — mirroring
   `anthropic_provider.stream_chat` exactly) from that commit's tree, fed with the
   owner's real data, and measured it with the real (free) `messages.count_tokens`
   endpoint. No `messages.create` call was made — no additional API cost was
   incurred by this investigation.

## Finding 1: both of the prior lane's candidates are refuted by direct evidence

- **(b) Tokenizer mismatch — REFUTED.** The incident row's `model` field is
  `claude-sonnet-5` and its cost matches Anthropic's cache-write pricing exactly
  (`backend/ai/models/pricing.py`'s `claude-` prefix table). This turn ran through
  the `anthropic` provider directly, not `openai_compatible`/OpenRouter. (The
  instance's CURRENT default provider is now OpenRouter/`z-ai/glm-5.2` — plausibly
  the owner switched away from Anthropic sometime after this exact $0.194 turn, but
  that is the config as of today, not as of the incident.)
- **(a) Frontend per-screen context snapshot — REFUTED.** The real transcript's
  first user message has exactly two content blocks: `"hello"` and a
  `<turn_context>` block (802 raw chars — date + portfolio snapshot + response-style
  line). There is no `«screen»...«/screen»` block. The dock was evidently invoked
  from a screen with no active contract (or none at all) for this turn — the
  screen-context mechanism contributed zero tokens here.

## Finding 2: the real root cause — the prior lane measured against a moving target

`backend/ai/prompts.py` was touched by a rapid sequence of commits, all on
`harness/reliability-program`, in the ~50 minutes surrounding the incident (all
times `+08:00`, the app's local zone):

| time | commit | what |
|---|---|---|
| 04:35:07 | `2ed1e79d` | (unrelated) — **this is the exact pre-incident state** |
| **05:40:05** | *(no commit)* | **the $0.194 "hello" turn fires** |
| 05:41:14 | `2dfd1d68` | `perf(ai): trim the Lucius system prompt from ~11.3K to ~4.2K tokens` |
| 06:24:24 | `5a105c15` | the prior lane's own ADR — re-measures the "hello" turn |

**The prior lane's re-measurement ran 44 minutes *after* the system prompt had
already been trimmed to ~4.2K tokens by a sibling commit — 69 seconds after the
incident itself.** Calling `build_system_prompt()` "against the owner's real data"
at that point necessarily measured the *already-shrunk* prose, not what was
actually sent when the $0.194 charge was incurred. Their own table's other four
system-prompt rows (tool catalog, memory, skills, profile/FIRE) were NOT
contaminated this way, because the catalog's own trim (`50bc2b21`, `names-only`,
2026-07-18 22:57:59) landed *before* the incident — so those four figures were
already correct. Only the hand-authored static prose was measured post-fix.

To get the true pre-incident number, this investigation checked out
`2dfd1d68^` (`2ed1e79d`, timestamp 04:35:07 — the last commit before the trim, and
confirmed via `git log --all` that no other commit touched `prompts.py` in
between) into a throwaway `git worktree`, pointed `DATA_DIR`/`CACHE_DIR` at a
read-only copy of the owner's real data, and re-ran `build_system_prompt()` there:

| component | prior lane's figure (post-fix, wrong) | this investigation (pre-incident commit, real data) |
|---|---:|---:|
| static prose (no catalog) | 3,396 | **13,543** |
| tool_catalog_block (names-only) | 2,140 | 2,311 |
| memory_block | 2,876 | 2,910 |
| skills_block | 515 | 621 |
| profile/FIRE prose | 327 | 375 |
| **system-prompt subtotal** | **9,462 (wrong)** | **19,760** |
| 20 CORE tool schemas | 13,758 | 13,706 |
| user turn (hello + turn_context, byte-exact from the real transcript) | ~208 (portfolio snapshot only) | 346 |
| **RECONSTRUCTED TOTAL** | **23,337** | **33,812** |
| **OBSERVED (cost_log)** | | **33,883** (2 input + 33,881 cache-write) |
| **residual** | **10,546 unaccounted** | **71 tokens (0.2%)** — same-day memory/skills drift, not a new unknown |

The four components that were NOT time-contaminated (catalog, memory, skills, CORE
tool schemas) reproduce the prior lane's own figures closely (within normal
day-to-day data drift), which cross-validates both measurements. The entire
~10,500-token gap is explained by the one component that WAS time-contaminated:
static system-prompt prose, undercounted by exactly the amount its own trim
commit removed.

## Consequences

- **No further fix is needed or proposed here.** The correct remedy for the real
  root cause — the static prose being ~13.5K tokens — is exactly what `2dfd1d68`
  already did (trim it to ~4.2K) and is already merged into
  `harness/reliability-program`'s history, alongside `50bc2b21` (catalog
  names-only) and `5a105c15` (CORE tool offer 20→13). Post-fix, the prior lane's
  own arithmetic (`docs/adr/2026-07-19-core-tool-offer-diet.md`) already projects a
  fully-trimmed "hello" turn at ~20,925 tokens (system 9,462 + new 13-tool CORE
  11,346 + turn-context ~117) — a genuine ~38% reduction from the original 33,883,
  achieved by sibling lanes this investigation did not need to duplicate.
- **This task's own worktree predates all three fixes** (branched from an earlier
  point on `harness/reliability-program` than where they landed), so none of them
  are present in this branch's tree. No code was changed by this investigation —
  it is a closing verification of an already-fixed incident, not a new change.
- The measurement-timing bug this ADR documents is itself a process lesson: a
  parallel-lanes-on-one-integration-branch workflow means a "re-measure against
  real data" step can silently measure a sibling lane's already-merged fix instead
  of the state that produced the incident being investigated. Pinning the exact
  commit by timestamp (not just "current HEAD") was required to get a trustworthy
  number.

## Verification

- Real Anthropic `messages.count_tokens` (free, no `messages.create` call — zero
  additional API cost) against the exact request shape
  `anthropic_provider.stream_chat` builds (system cache blocks, `tools=` with the
  trailing `cache_control`, `_cache_last_message`), fed the owner's real
  `profile.json`/`accounts.json`/`positions.json`/`memory.json`/`skills/` (copied
  read-only into an isolated scratch `DATA_DIR`/`CACHE_DIR` — the live tree was
  never written to) and the byte-exact real user message from
  `~/.open-net-worth/data/sessions/95fd85f1-....json`.
- Full reconstructed total: 33,812 vs. observed 33,883 — 99.8% match.
- No repository code was modified; `git worktree add --detach` was used to inspect
  `2dfd1d68^` and removed afterward. `.venv/bin/python3 -m pytest -q -m "not
  integration"` was run on this branch's actual (unmodified) tree to confirm the
  investigation left it green (see commit).

## Agent provenance

- **Investigated by:** an agent on a worktree branched from
  `harness/reliability-program`, per task directive 2026-07-19 ("Find them. This
  is the whole job.").
- **Alternatives ruled out:** re-deriving the answer from the codebase alone
  (explicitly the failure mode that produced the original 10,500-token gap —
  rejected in favor of reproducing against `cost_log.json` + the real session
  transcript + a real, timestamp-pinned commit checkout); trusting the prior
  lane's system-prompt figure at face value (rejected once the commit-timestamp
  arithmetic showed it was measured 44 minutes after a sibling trim landed).
- **Driving artifacts:** `~/.open-net-worth/data/cost_log.json` (ground truth),
  `~/.open-net-worth/data/sessions/95fd85f1-204d-4bd2-a462-eb95be6bcb41.json` (the
  real transcript), `docs/adr/2026-07-19-core-tool-offer-diet.md` (the prior
  lane's own reconstruction and named-but-unverified candidates), commits
  `2dfd1d68`/`50bc2b21`/`5a105c15` (the already-merged fixes on
  `harness/reliability-program`).
