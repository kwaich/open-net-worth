---
title: Bound an agent/scheduled run's inline tool offer the same way interactive chat's CORE offer is bounded — lazy discovery, not full inline schemas
status: Accepted
date: 2026-07-19
deciders: repo owner (task directive, 2026-07-19); recorded by the implementing agent
related: docs/adr/2026-07-18-remove-layered-ai-permission-system.md
---

# Bound an agent/scheduled run's inline tool offer via lazy discovery

## Context

Interactive chat got lazy tool loading this week (a ~20-tool `CORE_TOOL_NAMES` offer +
`find_tools` + a dispatch-loop discovery gate that auto-loads a known-but-unloaded tool
and bounces the model to retry). Agent and scheduled runs never got any of it: an agent's
`allowed_tools` was expanded to FULL inline schemas on every single model round trip of
every run.

Measured (2026-07-19, live `agents.json`, this branch's registry state — 272 tools,
`CORE_TOOL_NAMES` = 20):

| Agent | `allowed_tools` listed | resolved schemas | OLD inline chars | NEW inline chars (cold start) |
|---|---|---|---|---|
| Monitor | 10 | 10 | 8,382 | 8,382 (unchanged — small) |
| Fraud Detection Agent | 64 | 60 | 70,562 | 22,135 (−68.6%) |
| Transaction & Trade Logger | 143 | 135 | 156,466 | 34,297 (−78.1%) |

The interactive CORE offer itself measures 20 tools / 37,263 chars on this branch (not
the ~19/25,885 the task brief quoted — that number was measured earlier and the registry
has grown since via concurrent lanes; re-measured here rather than trusted). The
Trade-Logger agent's 156K-char inline payload was still ~4.2x the interactive CORE offer,
confirming the premise's direction even though the absolute figures had drifted.

Only 3 agents exist in the live data dir (no code-seeded "starter" agents exist to widen
the sample) — the distribution used to pick a threshold is necessarily this small.

## The load-bearing constraint (from ADR 2026-07-18)

`allowed_tools` on an agent is **CONFIGURATION — what this agent is pointed at — NOT an
enforced ceiling.** Nothing refuses a tool by caller class. So this change could NOT be a
permission change: it changes what gets offered **inline** vs. **discovered**, never what
is **reachable**. If it made any tool unreachable to an agent that could reach it before,
that would be a regression of the ADR, not a feature of this one.

## Investigation: was the interactive discovery gate even reachable from an agent turn?

No. `assistant.py`'s dispatch loop only ran the discovery-gate branch when
`_skill_scope.offer_tool_names() is None` — i.e. only on a turn with NO explicit offer.
An agent/scheduled run always sets an explicit offer (`_skill_scope.set_offer_scope`,
`main._scheduled_agent_runner`), so the gate was **structurally skipped** for every agent
turn, and `_active_tools()`'s offer branch returned `[t for t in TOOL_SCHEMAS if name in
allowed]` verbatim — no way for `add_extra_names` to shrink or lazily grow it. Building a
bounded/lazy OFFER without first fixing this would have been "a trap" exactly as the task
warned: the model would bounce forever on any tool outside the bounded slice, because
nothing would ever widen the offer back.

`extra_tool_names`'s session-meta persistence is chat-session-specific
(`SESSIONS_DIR/index.json`), but the loading mechanism that actually matters mid-turn is
the `ai/tools/_extra_groups_scope` **ContextVar**, which persists for the lifetime of one
`asyncio` Task — exactly the lifetime of one `assistant.run()` call, i.e. one full agent
run (`_scheduled_agent_runner` calls `assistant.run` once; its own internal `while True`
tool-round-trip loop runs inside that same Task/Context). So a tool loaded via the gate
mid-run DOES survive for the rest of that run's rounds without re-discovery — verified in
`tests/test_agent_offer.py::test_large_agent_offer_honors_extra_names_loaded_this_turn`
and the bounce-then-persists tests in `tests/test_dispatch_gate.py`. It does NOT survive
across separate scheduled fires (each mints a fresh `scheduled-<uuid>` session and is
`clear_session()`-ed at the end) — by design: a fresh run re-measures its own bounded
offer from the agent's current `allowed_tools`, so an edited config takes effect
immediately rather than being masked by a stale loaded-set.

## Decision

1. **Threshold, not a guess:** an agent whose tool count is at or under
   `len(CORE_TOOL_NAMES)` (currently 20 — the interactive cold-start offer's own size) is
   delivered in full, unchanged from before this change — a short list is already cheap
   and IS the point of a narrowly-scoped agent (Monitor, 10 tools, is untouched). Anchoring
   the threshold to CORE's own size gives a principled invariant ("an agent's cold-start
   payload is never bigger than a chat turn's") instead of a picked-from-air number, and
   it cleanly separates the measured distribution: Monitor (10) stays inline; Fraud (60)
   and Trade-Logger (143) — the two agents actually causing the bloat — get bounded.

2. **Bounded set, when large** (`ai/tools/_agent_offer.split_agent_offer`): this agent's
   own tools that overlap the interactive `CORE_TOOL_NAMES` set (measured overlap: 14/20
   for Fraud, 17/20 for Trade-Logger — a large agent's hot path is very likely CORE's own
   hot path too, so this is close to "free" prioritization) + `find_tools` (added even
   when the agent never listed it — the mechanism this ADR exists to enable) + enough
   additional tools (alphabetical, for a deterministic/cache-stable prompt) to fill the
   same budget. Everything else in the agent's own list is reachable the instant the
   model names it.

3. **The discovery gate now fires for an explicit-offer (agent) turn too**
   (`assistant.py`'s dispatch loop), restructured from "skip entirely when an offer is
   active" to "skip only when a skill scope is active; for an explicit-offer turn with no
   skill scope, auto-load + validate exactly like the interactive path, but ONLY for a
   name inside the caller's own offer." A name outside the offer is left untouched
   (falls through to normal dispatch unchanged) — this gate has never been the thing that
   refuses an out-of-offer call, and it still isn't; adding that refusal now would
   reintroduce the ceiling ADR 2026-07-18 removed.

4. **`find_tools` itself is scoped to the active offer**
   (`ai/tools/_groups._reachable_universe`, used by `_candidate_schemas`): when an
   explicit offer is active (and no skill scope), `find_tools(query=…)` /
   `find_tools(names=[…])` can only ever surface a tool inside that offer — never the
   wider registry. Without this, giving every bounded agent implicit `find_tools` access
   would have been a genuine widening (an agent could discover and load a tool its author
   never configured); with it, `find_tools` is purely a delivery mechanism for the SAME
   set `services.agents.effective_scope` already decided is reachable.

5. **The agent system prompt gets a scoped catalog** (`ai/prompts._fmt_agent_tool_catalog`,
   `build_agent_system_prompt(allowed_tools=...)`, threaded from
   `main._scheduled_agent_runner`'s existing `allowed_tools` parameter): names (not
   describes) the agent's own not-yet-inlined tools, mirroring interactive chat's "TOOL
   CATALOG" block but scoped to this agent's own list. Without this the model has no way
   to know a lazy tool exists unless the agent's own hand-written `prompt` happens to name
   it — the catalog is what makes "just call it, it auto-loads" actually discoverable.
   Appended to the STATIC (cacheable) half of the agent prompt, not the volatile half — an
   agent's own tool list doesn't change turn to turn, so this is now the ONE thing that
   makes the static block agent-specific (previously identical across every agent); a
   small/unbounded agent's prompt is untouched (the helper returns `""`).

## Consequences

- Reach is unchanged: the loadable universe for an explicit-offer turn stays exactly
  `services.agents.effective_scope(agent)`'s output — a tool that was reachable before
  this change (given enough rounds) is still reachable, at the identical cost (one bounce
  round trip for a write / an invalid-args read; zero extra round trips for a valid-args
  read, since the discovery gate executes those immediately just like the interactive
  path). Verified end-to-end in `tests/test_agent_lazy_run_e2e.py`, which drives a
  271-tool agent through the REAL `services.agents.run_agent` → the REAL
  `main._scheduled_agent_runner` (registered on the production seam) → the REAL
  `assistant.run` dispatch loop, and asserts `ok: True` plus a persisted finding.
- The prompt-caching shape changes for a bounded agent: its static system-prompt block is
  now agent-specific (carries its own scoped catalog) instead of byte-identical across
  every agent. This trades "one shared cache entry for all agents" for "one cache entry
  per bounded agent, stable across that agent's own repeated scheduled fires" — a better
  trade given each agent's tool list is itself stable run to run.
- Known imprecision (accepted, matches an existing pattern): `find_tools`'s
  `already_available`/`already_core` reporting still checks global `CORE_TOOL_NAMES`
  membership, not "is this literally in this round's bounded inline set" — a
  non-core-but-already-inlined tool (one of the alphabetical fill slots) would be
  reported as freshly "loaded" if the model calls `find_tools(names=[...])` for it again.
  Harmless (the load is idempotent, a no-op union) and mirrors the SAME imprecision the
  interactive path already has for a previously `find_tools`-loaded non-core name.
- Not addressed here (explicitly out of scope): skill-scoped turns (`run_skill`) are
  untouched — their allowlists are small by construction and were never the measured
  problem. `mcp/adapter.py` and `api/agents.py` still see the full `TOOL_SCHEMAS`
  registry unfiltered, as before (they bypass `_active_tools()` entirely).

## Alternatives considered

- **Char-budget threshold instead of tool-count.** Rejected for this pass: a tool-count
  threshold anchored to `len(CORE_TOOL_NAMES)` is simpler, self-adjusting if CORE grows,
  and directly answers "is this agent's cold-start offer as big as a chat turn's" — the
  question that actually motivated the change. A char-budget would need its own
  independently-chosen number.
- **Preserve the agent's own `allowed_tools` list ORDER for the inline slice.** Not
  possible without a larger change: `_skill_scope.set_offer_scope` stores the offer as a
  `frozenset` (order-losing) for its set-algebra with the skill-scope intersection. Given
  only 3 real agents (no observed intentional ordering to preserve), alphabetical fill +
  CORE-overlap-first was judged sufficient; revisit if agents start being authored with a
  meaningful tool-priority order.
