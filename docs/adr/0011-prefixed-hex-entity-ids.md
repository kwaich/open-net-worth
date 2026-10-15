---
number: 0011
title: One prefixed-hex entity-id scheme (token-cheap, self-describing) via core.ids.new_id
status: Accepted
date: 2026-06-20
deciders: Repo owner (interactive) + Claude (design discussion → implementation)
---

# 0011. One prefixed-hex entity-id scheme via `core.ids.new_id`

## Context

Entity ids were minted four different ways, with no shared helper:

1. **Full uuid4** — `str(uuid.uuid4())` (36 chars, dashed) — the default for almost every store (transactions, accounts, positions, lots, instruments, rules, events, …).
2. **Prefixed short hex** — `pay_<12 hex>`, `mr_<12 hex>` (payees, match reviews).
3. **Prefixed full hex** — `evt_<32 hex>` (audit events).
4. **Slug + 8 hex** — `grp-`, `task-`, `tgt-` (category groups, scheduled tasks, targets), plus pure human slugs (commitments, agents).

This is an **LLM-first app**: ids are returned to the model in `get_transactions`/portfolio listings and echoed back in `update_/delete_/add_` tool calls. A dashed uuid tokenizes to **~23 tokens** (hex+dashes shatter into tiny BPE pieces) and is **error-prone for the model to transcribe**, causing failed/incorrect tool calls. Measured (o200k_base, a proxy for the production tokenizer): `pay_<12 hex>` is **~8 tokens** — **~65% fewer per id**, and on a 30-row listing where each id appears in the output and again in a follow-up call, ~**900 tokens saved per turn**. Crockford base32 was considered and measured **worse** (random base32 runs tokenize near 1 char/token, ~13 tokens; hex packs ~2 chars/token because tokenizers have memorized hex pairs) — and hex's alphabet (`0-9a-f`) already has **no ambiguous characters**, so base32's human-legibility win doesn't apply.

The short hex *is* a uuid — `uuid.uuid4().hex[:12]` is the first 12 hex chars (48 fully-random bits; uuid4's fixed version/variant nibbles sit at hex index 12+, just past the slice). So the token win comes purely from **dropping the dashes** and **keeping 12 hex chars** — not from a different entropy source.

Re-id-ing existing rows was explicitly rejected: ids are foreign keys everywhere (txn→acct, lot→pos, audit `evt_`→txn, `external_id` dedup, run_history→agent), so a re-id is a high-risk, largely-irreversible cross-store migration with near-zero payoff for a single-user app. A pre-change audit confirmed **nothing parses or length-validates an id as a uuid** (no `uuid.UUID(id)`, no `len(id)==36`, no uuid-shaped regex) — ids are opaque strings everywhere, so changing the mint format for **new** rows is safe.

## Decision

We will mint every **persisted entity id** through one helper and standardize the format.

1. **One minter.** `core.ids.new_id(prefix, *, exists=None)` → `f"{prefix}_{uuid4().hex[:12]}"` — a `{prefix}_` tag + 12 hex chars (48 bits), no dashes. `prefix` must be lowercase-alnum starting with a letter. The optional `exists(candidate) -> bool` drives a check-and-retry so the effective collision rate is **zero** regardless of volume (48 bits alone is ~1-in-5.6M at 10k ids of one type, and collisions are namespaced per prefix). It lives in `core/` (stdlib-only leaf — importable from every layer).
2. **Adopt it for every persisted entity's `"id"`**, with a stable per-type prefix: `acc txn inst pos lot grant inc vest rlz rule find lsn hsug imp run sig pan wl wli mcpp mcpw corr dis pay mr art stmt grp task tgt evt mem ckpt`. Distinct entities get distinct prefixes even when related — a watchlist *collection* is `wl`, a watchlist *item* is `wli`. The existing `pay_`/`mr_` are routed through `new_id` unchanged; `grp-`/`task-`/`tgt-`/`evt_` change format (dash→underscore, 8/32→12 hex) for new rows only.
3. **Forward-only.** Existing rows keep their ids; no migration; foreign keys untouched. Stores mix old (uuid) and new (prefixed) ids — fine, since ids are opaque.
4. **Leave non-entity values alone.** `idempotency_key`s, security nonces, chat session ids, batch handles, temp-file suffixes, and MCP nonce tokens keep raw uuid (they are dedup/auth tokens, not ids — changing them would be a regression). Intentional **human slugs** (commitments `_slugify`, agent keys) and **nested sub-entity** ids (equity tranches inside a grant doc, intel timeline/story events inside a signal) keep their current scheme.

## Consequences

- **~65% fewer tokens per id** on model-facing surfaces, and **fewer wrong-id tool calls** (short prefixed ids are far easier for the model to echo exactly). The `txn_`/`acc_`/… prefix is self-describing, so the model rarely confuses entity types.
- **Better debuggability** — a prefixed id in a log or error instantly says what it is.
- **Mixed formats within a store during the transition** (old `…uuid…` rows beside new `txn_…` rows). Acceptable — ids are opaque and unique either way — but it looks inconsistent until old rows age out.
- **The entity-vs-token boundary is a judgment call to maintain.** A new mint must decide: persisted entity id (→ `new_id`) or transient token/nonce/slug (→ leave). This ADR's classification is the reference.
- **DB-migration-friendly** — a future move off JSON files inherits short, namespaced, self-describing keys.
- **Collision math** is comfortable: 48 bits, namespaced per prefix, with `exists` available for any store that wants belt-and-suspenders (the existing `pay_`/`mr_` set the no-check precedent and have been fine).

## Alternatives considered

- **Keep full uuids everywhere** — rejected: ~23 tokens/id and transcription errors in an LLM-first app; the whole motivation.
- **Crockford base32 suffix** — rejected: *measured* worse for tokens than hex (~13 vs ~8), uppercase in an otherwise-lowercase id space, extra encoding code — and hex is already human-unambiguous, so base32's only edge (no `I/L/O/U` confusion) buys nothing here.
- **Re-id existing data to one scheme** — rejected: high-risk, largely-irreversible cross-store foreign-key migration for near-zero payoff on a single-user app.
- **Expose a short handle to the model while storing uuids** (decouple LLM-facing id from stored id) — rejected for now: adds a resolution/mapping layer + ambiguity risk; the prefixed-hex stored id is simpler and captures the same win. Revisit only if measurement shows the residual cost matters.
- **Do nothing / status quo** — rejected: four inconsistent schemes, the worst (full uuid) being the most common and most token-expensive.

## Agent provenance

- **Decided by:** Repo owner (interactive) + Claude — a whiteboard design discussion (collision math + tokenizer measurement) that settled on prefixed hex over base32 and forward-only over migration.
- **Lane / branch:** `feature/intel-redesign` (no worktree).
- **Alternatives weighed:** full uuid; Crockford base32; full re-id migration; LLM-facing short-handle indirection; status quo — see above.
- **Driving artifacts:** in-session tokenizer measurement (uuid ≈ 23 tok, `pay_`-hex ≈ 8 tok, base32 ≈ 13 tok); collision birthday-bound table; pre-change audit proving ids are opaque (no uuid parsing/length checks); the 36-file adoption sweep, each store gated on its own tests.
- **Why this way (one line):** the short hex *is* a sliced uuid — dropping the dashes and keeping 12 hex chars gives a token-cheap, self-describing, collision-safe id with no new dependency and no migration.
