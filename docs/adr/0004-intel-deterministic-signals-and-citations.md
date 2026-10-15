---
number: 0004
title: Intel real-data — deterministic risk/macro/digest signals + cited provenance
status: Accepted
date: 2026-06-17
deciders: Intel-redesign orchestration (Claude) + repo owner (async)
---

# 0004. Intel real-data: deterministic signals + cited provenance

## Context

Users reported Intel "doesn't create real new data" — macro/risk/opportunity signals felt
weak. Root cause from the codebase map: the pipeline runs template news queries and asks one
LLM to paraphrase; macro is a set of curated central-bank query **strings** with no mapping to
the user's holdings; there is **no risk lane**; and sources are stored as **bare URL strings**
(no quote, no date), so nothing reads as verified. An LLM asked to invent portfolio numbers
would be both wrong-prone and a token cost — and the repo already cut `INTEL_MAX_TOKENS`
4000→2000 after burning 284K tokens in 4 days.

## Decision

The fix is **differently-trusted lanes**: exact numbers come from deterministic `services/`
code; the LLM is reduced to narration and thesis prose, always with citations.

1. **Risk lane is deterministic** (`services/risk_signals.py`, no LLM): single-name
   concentration, asset-class/sector overweight, unhedged FX mismatch, drawdown-from-cost,
   thresholds scaled by `profile.risk_tolerance`. Emits `type="risk"` story arcs with a stable
   synthetic key (idempotent upsert; breach→clear flips status, never leaves a stale
   `action_needed`). **`type="risk"` is deliberately NOT added to the LLM `_OUTPUT_TOOL` enum**
   so the deterministic writer is the sole author. Runs at the top of `run_intel_scan` (cheap,
   no search budget) + a compute-only `get_risk_signals` tool (headless-allowed).
2. **Macro carries computed exposure** (`services/macro_exposure.py`, no LLM): a macro story's
   `affected_holdings[]` is computed by us — exact per-currency FX base value, directional sign;
   sector/theme is tag-only (`impact="directional"`). The LLM writes the narrative
   `portfolio_impact`; it never authors the structured exposure. Missing FX rate →
   `direction="unknown"`, never a fabricated magnitude.
3. **Digest is deterministic + optional narration** (`services/intel_digest.py`): market
   backdrop (benchmark proxy always; crypto proxy only if crypto held), return drivers (top
   positions by signed base-currency value change; degrade to weight-ranked when no prior
   price), top movers — all from `returns`/`positions.hydrated()`. The one-line LLM narration
   lives in `monitoring/intel_digest_narration.py`, capped ~120 tokens via the batch seam, and
   is **interactive-only**: a headless/agent caller always gets `narrate=false`.
4. **Citations carry provenance** — source entries become `{url,title,domain,published_at,quote}`.
   No bulk data migration: briefs are a checkpoint-excluded derived cache, so `read_asset_detail`
   **coerces bare-string sources on read** (`{url,title:"",domain:_hostname(url),...}`) and the
   next refresh self-heals to the rich shape. The LLM supplies `url`+`quote`; we fill
   `domain`/`published_at`/`title` ourselves (anti-fabrication, mirrors peer-ticker handling).
5. **`_hostname` moves to `core/urls.py`** (stdlib only) so `services/asset_detail.py` can derive
   a domain without an illegal `services → monitoring` import.

## Consequences

### Positive
- Risk/macro/digest numbers are exact, reproducible, and cost nothing (no LLM, no search budget).
- Citations make the thesis verifiable in-app (hover-quote) — the #1 trust lever — and expose
  thin sourcing instead of hiding it.
- All monetary values recomputed via `core.fx.to_base` from native legs; nothing trades or moves money.

### Negative / known edges
- Per-name "period contribution" we cannot truly compute is labeled day/period value-change and
  degrades to weight-ranked — honest, not a true per-name TWR.
- Adding a short `quote` per source nudges the brief object toward the 4000-token brief cap
  (separate from the 2000-token scan cap); verified no truncation.
- A stored bare-string source shows an empty quote until the next refresh.

## Alternatives considered
- **LLM-narrated risk/macro numbers** — rejected (wrong-prone + token cost; violates "financial
  math only in services").
- **One-shot bulk migration of all stored sources** — rejected (briefs are a derived cache;
  coerce-on-read is free and self-healing).
- **A beta/factor exposure model for macro** — rejected as dishonest without real factor data;
  degrade to exact-FX + directional tags instead.

## Agent provenance
- **Decided by:** the Intel-redesign orchestration agent from a `solution-architect` RFC; owner async.
- **Lane / branch:** `feature/intel-redesign`.
- **Driving artifacts:** `backend/services/{risk_signals,macro_exposure,intel_digest}.py`,
  `backend/monitoring/{asset_brief,intel_agent,intel_digest_narration}.py`,
  `backend/services/asset_detail.py`, `backend/core/urls.py`, `backend/core/headless_scope.py`.
- **Why this way (one line):** exact numbers from deterministic services, LLM only for cited
  prose, citations verifiable in-app — real data, honest degradation, no cost blowup.
