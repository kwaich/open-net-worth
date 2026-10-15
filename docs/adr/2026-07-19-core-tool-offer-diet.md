---
title: Cut the interactive CORE tool offer on measured per-tool session-spread, not intuition
status: Accepted
date: 2026-07-19
deciders: repo owner (task directive, 2026-07-19); recorded by the implementing agent
related: docs/adr/2026-07-19-agent-runs-lazy-tool-offer.md, docs/adr/2026-07-18-remove-layered-ai-permission-system.md
---

# Cut the interactive CORE tool offer on measured per-tool session-spread

## Context

The owner typed "hello" into the app and it cost $0.194 (`2 in · 33,881 cache↑ · 14
out`) — a ~33,881-token prefix written to cache before the model produced a single
useful token. Verbatim: "too expensive that i really avoid using, so i'm not sure if
anyone will use it." The system prompt had already been cut the night before (11,338
-> 4,161 tokens per that lane's own measurement, on a near-empty fixture). The
remaining, then-unexamined bulk was assumed to be the CORE tool offer
(`CORE_TOOL_NAMES` in `ai/tools/_groups.py`, 20 tools / 37,263 chars ≈ 9,300 tokens by
a `chars÷4` heuristic).

## Re-measurement before cutting anything

Re-measured `build_tool_schemas()` for the interactive path (`ai/tools/_groups.py`,
current branch state) rather than trusting the task brief's number:

| | chars | tokens (`chars÷4` estimate) | tokens (real Anthropic `count_tokens`) |
|---|---|---|---|
| Old CORE (20 tools) | 37,374 | ~9,343 | **13,758** |
| New CORE (13 tools, this ADR) | 30,663 | ~7,666 | **11,346** |

The real tokenizer costs **~48% more than the `chars÷4` heuristic** for tool-schema
JSON specifically (punctuation, enum arrays, nested `items`/`properties` tokenize
worse than prose). This matters: every prior estimate in this program that used
`chars÷4` for schema content understated the true cost by roughly half.

### Where the REST of the 33,881 tokens actually goes (Lever 3)

Measured the full "hello" turn prefix end to end — system prompt (via
`_split_turn_context`) + CORE tool schemas + the injected per-turn context, exactly
as `assistant.py`'s dispatch loop assembles it — against the owner's OWN real data
(`~/.open-net-worth/data`, read-only; the Anthropic key was read in-process from
`model_config.json` and never written to a file, log, or this document), using
`messages.count_tokens` (the authoritative measurement per the task brief, and what
produced the owner's original number):

| Component | Chars | Real tokens | Notes |
|---|---|---|---|
| `static_block` prose (hand-authored, `ai/prompts.py`) | 9,713 | 3,396 | The system-prompt-cut lane's own target; close to its quoted ~4,161 (measured on near-empty data — see below) |
| `tool_catalog_block` (flat names, ALL ~272 registered tools) | 5,464 | 2,140 | Embedded in `static_block` by `_fmt_tool_catalog` — a SEPARATE mechanism from the `tools=` schema offer, unaffected by CORE membership |
| `memory_block` (`_fmt_memory`) | 6,474 | 2,876 | Real accumulated memory facts on this account, near its 30-fact/8K-char cap |
| `skills_block` (`_fmt_skills_catalog`) | 1,476 | 515 | Real enabled skills on this account |
| profile/goals/liabilities/FIRE prose | 684 | 327 | Real profile data |
| `per_turn_block` (date + portfolio snapshot) | 426 | 208 | Injected into the user turn, not the system param |
| **System prompt total** | **24,284** | **9,462** | Measured as one block — matches the component sum almost exactly |
| CORE tool schemas (old, 20 tools) | 37,374 | 13,758 | |
| CORE tool schemas (new, 13 tools) | 30,663 | 11,346 | |
| **Full "hello" turn, OLD CORE** | | **23,337** | system + turn-context + old tools |
| **Full "hello" turn, NEW CORE** | | **20,925** | system + turn-context + new tools (**−10.3%**) |

**Finding: the "~4,161 tokens" figure quoted for the system-prompt cut was almost
certainly measured against a near-empty account** (exactly like this same program's
own tool-surface-eval baseline documents doing for the PRE-cut prompt — see
`backend/evals/tool_surface/baselines/README.md`: "12,416 tokens ... Fresh throwaway
DATA_DIR, essentially empty profile/accounts"). On THIS real account the system
prompt costs 9,462 tokens — more than double — because three of its five components
(the tool catalog, the memory block, the skills catalog) are registry-size- or
data-dependent and were never touched by the prose-only cut. This is a bigger,
previously unexamined cost than the CORE tool offer itself, and it lives entirely in
`ai/prompts.py::build_system_prompt`, which is **out of scope for this lane** (owned
by the system-prompt lane). Reported here per the task brief's explicit instruction
to surface this even if it cannot be fixed in this change.

**Residual gap.** This lane's full real-tokenizer reproduction (23,337 / 20,925
tokens) still falls short of the owner's reported 33,881 by ~10,500 tokens even
using the OLD 20-tool CORE set. Two candidates, neither reproducible from this
lane's scope: (1) the frontend's per-screen context snapshot (ADR 0028) rides the
user turn whenever the LuciusDock is invoked from an instrumented screen (Cash,
Investments, Asset Detail, Overview, Plan, Tax) — `frontend/` is out of scope for
this lane and a real production screen state at capture time is unknown; (2) the
ACTIVE configured chat provider in `model_config.json` is `openai_compatible` via
OpenRouter (`x-ai/grok-4.5`), not Anthropic directly — a different tokenizer very
plausibly counts the same content as more tokens than Anthropic's `count_tokens`
(which is what this ADR, and the task brief, uses as the authoritative measurement).
The relative reduction (−10.3% on this lane's changes) should transfer across
tokenizers; the absolute counts may not.

## Decision: cut CORE_TOOL_NAMES from 20 to 13 tools on measured session-spread

Located the real session-transcript corpus the 2026-07-18 harness audit used
(`~/.open-net-worth/data/sessions`, ~293 real Lucius session transcripts — the
same corpus `.claude/rfcs/harness-audit-2026-07-18/HANDOFF.md`'s "transcript
forensics" addendum measured) and computed, for every CORE tool, **in how many
DISTINCT sessions it was called** (session-spread, not raw call count — a tool
called 100 times in one session must not look like broad usage). Old/legacy tool
names present in the corpus (`add_transaction`/`bulk_add_transactions`, both merged
into today's `add_transactions` since this corpus was captured) were canonicalized
via the registry's own `aliases=` table before counting, so the merge doesn't
manufacture a false "unused" reading.

| Tool | Session-spread | Verdict |
|---|---|---|
| `list_accounts` | 36.2% | KEEP |
| `get_transactions` | 34.1% | KEEP |
| `get_portfolio` | 24.9% | KEEP |
| `add_transactions` | 20.1% | KEEP |
| `calculate` | 19.5% | KEEP |
| `update_transaction` | 18.8% | KEEP |
| `get_live_prices` | 14.7% | KEEP |
| `find_tools` | 11.9% | KEEP (mechanism) |
| `list_categories` | 7.5% | KEEP |
| `get_payees` | 7.2% | KEEP |
| `get_net_worth` | 6.8% | KEEP |
| `get_profile` | 6.8% | KEEP |
| `fetch_emails` | 3.8% | **DEMOTE** |
| `get_ledger_summary` | 3.8% | **DEMOTE** |
| `get_spending_summary` | 3.1% | **DEMOTE** |
| `create_payee` | 2.4% | **DEMOTE** |
| `list_checkpoints` | 1.7% | **DEMOTE** |
| `import_transactions` | 1.0% | KEEP (structural — see below) |
| `restore_checkpoint` | 1.0% | **DEMOTE** |
| `notify` | 0.3% | **DEMOTE** |

There is a clean break between the ~7–36% tier (12 tools, kept) and the ~0.3–3.8%
tier (8 tools) — a defensible, evidence-visible cutline, not an arbitrary threshold.

**`import_transactions` is the one deliberate exception to the frequency rule.**
Its own measured spread is also low (~1%), but the corpus predates its CORE
placement: it was added to `CORE_TOOL_NAMES` specifically to fix the corpus's #1
measured waste event (per the harness-audit forensics: 24 of 291 sessions hand-booked
≥5 transactions with zero `import_transactions` calls — 1,597 rows booked one at a
time — because the tool was `find_tools`-gated and undiscoverable at the moment a
statement-shaped session started). Low PRE-core usage is not evidence against a fix
whose whole point is to change FUTURE discoverability; demoting it now would
reintroduce the exact bug it was added to close. It stays core pending a fresh
measurement once real usage under CORE placement exists.

**`notify` and `list_checkpoints`/`restore_checkpoint` deserve explicit callout**
since they were previously hand-justified as "Operational must-stays" in
`_groups.py`'s own comments — a judgment call, not evidence. Cross-checked against
`~/.open-net-worth/data/agents.json` and the corpus's aggregated `scheduled.json`
transcript: `notify`'s only real usage in the entire corpus is inside the
scheduled/agent-run blob (all three live agents — Monitor, Fraud Detection, and
Transaction & Trade Logger — call it), never in a genuinely typed interactive
session. For an agent whose own tool list is small (≤13 after this change, e.g.
Monitor at 10 tools), `_agent_offer.split_agent_offer` delivers its FULL list inline
regardless of CORE membership, so demoting `notify` costs it nothing. For the two
large agents (Fraud Detection 64 tools, Trade-Logger 143 tools), `notify` no longer
lands in the guaranteed CORE-overlap slice and would need one auto-load bounce the
first time a run actually calls it — a real but small, internal-to-the-turn cost
(never a broken feature), consistent with the tradeoff `_agent_offer.py` already
accepts for every other non-CORE tool those same agents call. `list_checkpoints`/
`restore_checkpoint` (the undo safety net) were kept loaded on the theory that a
rare-but-critical tool deserves a permanent slot; re-examined here under the same
evidence bar the owner already applied when rejecting the inverse move (promoting
the five most-bounced tools: "just web search is fine, get screen detail is fine,
but the rest, i don't think so"). At 1.0–1.7% session-spread they fail "called on a
large fraction of turns" cleanly, and demotion does not remove the feature or make
it unreliable — every tool name (demoted or not) is still listed in the system
prompt's flat tool catalog (`ai/prompts.py::_fmt_tool_catalog`, unaffected by this
change), so the model still knows `restore_checkpoint` exists and can call it
directly; the dispatch loop's discovery gate auto-loads it and bounces the model to
retry ONCE, invisibly to the end user (one extra internal round trip inside the same
assistant turn, not a second user-visible message). This is flagged explicitly, per
the task brief's own instruction to surface consequential trade-offs rather than
bury them in a diff — it is the most re-litigable call in this change.

## What was NOT cut, and why

**Schema prose (Lever 1).** The transaction-tool field table (`ai/tools/
_txn_schema.py`) was already aggressively de-duplicated by an earlier lane (its own
docstring documents `add_transactions 10,707 -> 7,868 chars`, `-14.8%` on the whole
CORE offer at the time). The ledger-confirm-gate lane subsequently added new fields
(`force`, `decisions`, `import_description`, `import_session_id`, `statement_ref_id`,
`confirm_token`) that grew `add_transactions` back to 8,518 chars — but every
remaining paragraph of prose in the kept tools (`get_transactions`'s "FILTER
SERVER-SIDE" / "FILTER ONCE NOT N TIMES" guidance, `list_accounts`'s "filter rather
than fetch everything" guidance, `add_transactions`'s statement-import routing
paragraph) is directly cited in this program's own forensics as the fix for a
specific measured bug (63 byte-identical repeat `list_accounts` calls; the 1,597-row
statement-import lockout). Cutting it for a few hundred more chars would risk
reintroducing exactly the failures this program already spent effort fixing, so it
was left alone. One concrete ~250-char redundancy was found (`import_transactions`'s
four mutually-exclusive source fields each restate "mutually exclusive with the
other three" individually, on top of the top-level description already stating it
once) but `imports.py` is called out as owned by a separate lane in
`tests/test_tool_schema_budget.py`'s own `lane_excluded` comment, so it was left for
that lane to avoid a merge collision.

**Files explicitly off-limits for this lane** (`ai/tools/misc.py`, `ai/tools/
screen.py`, `ai/tools/email_ingest.py`, `ai/prompts.py`, `frontend/`) were not
touched. `fetch_emails`' tool definition lives in `email_ingest.py`; only its
*membership* in `CORE_TOOL_NAMES` (a different file, `_groups.py`) changed.

## Consequences

- Every demoted tool remains fully reachable: it is still a registered tool, still
  named in the system prompt's flat catalog, and still auto-loaded (with one bounce)
  by the dispatch loop's discovery gate the instant the model calls it by name —
  demotion changes the DEFAULT OFFER, never REACH.
- `_agent_offer.split_agent_offer`'s CORE-overlap heuristic re-measures smaller
  against the new 13-tool set (9 of Fraud Detection's 64 tools, 10 of
  Trade-Logger's 143, down from 14/16 against the old 20-tool set) — unaffected in
  mechanism, just a smaller guaranteed-inline slice for those two large agents.
- Tests updated to match the new deliberate target (10–17 tools, not 17–24):
  `tests/test_find_tools.py`, `tests/test_r3_tool_scoping.py`,
  `tests/test_tool_offer_size.py`, `tests/test_tool_schema_budget.py`. The stale
  "must-stay-core" parametrization in `test_r3_tool_scoping.py` was inverted into an
  explicit regression test asserting the demoted tools are rare-but-reachable, not
  silently dropped.
- `ONW_FULL_TOOL_OFFER=1` still restores the full registry, unaffected.

## Verification

- `backend/evals/tool_surface` (the cost+correctness harness this program built for
  exactly this decision): `arm A-real` imports `CORE_TOOL_NAMES` live from the
  registry, so it automatically reflects this change with no harness edits needed.
  Re-ran the 4-task discriminating subset (`near_miss_no_account`,
  `near_miss_oversell`, `near_miss_unknown_account`, `read_lots_lazy`) against the
  committed baseline (`baselines/real-A-real-discriminating-subset-6e416457.json`)
  via `make tool-surface-baseline-compare` — see the commit message / final report
  for the pass/fail result and cost delta.
- `.venv/bin/python3 -m pytest -q -m "not integration"` — full suite, output
  captured to a file and read; failures (if any) re-run in isolation per the
  program's known contention-flake caveat.
- `make lint` (ruff + import-linter).

## Agent provenance

- **Decided by:** the repo owner, via task directive, 2026-07-19 ("Cut the CORE offer
  hard, without making the assistant worse" — measure first, then decide). Recorded by
  the implementing agent on a worktree branched from `harness/reliability-program`.
- **Lane / branch:** worktree lane, files scoped to `ai/tools/_groups.py`,
  `ai/assistant.py`, `ai/tools/_agent_offer.py` and tests — explicitly barred from
  `ai/tools/misc.py`, `ai/tools/screen.py`, `ai/tools/email_ingest.py`, `ai/prompts.py`,
  `frontend/` (other lanes' files).
- **Alternatives weighed:** shrinking tool-schema prose further (already heavily done
  by an earlier lane; the remaining prose is cited by this program's own forensics as
  the fix for specific measured bugs — rejected as too risky for too little); demoting
  `import_transactions` on the same frequency rule (rejected — its low spread reflects
  PRE-core discoverability, not post-core value, and it fixes the corpus's #1 measured
  waste event); leaving `notify`/`list_checkpoints`/`restore_checkpoint` core on their
  prior "operational must-stay" judgment (rejected in favor of the same measured-
  evidence bar the owner already applied to reject tool promotion).
- **Driving artifacts:** `.claude/rfcs/harness-audit-2026-07-18/HANDOFF.md` (the
  transcript-forensics addendum, 416 files / ~293 real sessions / 3,507 tool calls);
  `~/.open-net-worth/data/sessions` (the corpus itself, read-only, no message content
  extracted — only per-tool session membership); `~/.open-net-worth/data/agents.json`
  (live agent tool lists, for the `_agent_offer` overlap re-measurement);
  `backend/evals/tool_surface/baselines/README.md` (the empty-fixture system-prompt
  measurement this ADR's Lever-3 finding builds on).
