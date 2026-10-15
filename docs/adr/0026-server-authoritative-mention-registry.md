# 0026 — Server-authoritative mention registry with a type-agnostic client

- Status: Accepted
- Date: 2026-06-24
- Deciders: user (chose the comprehensive, generic design and drove it to implementation) + designing/implementing agent
- Related: `backend/services/mentions/{registry,resolve,dispatch,__init__}.py`, `backend/api/chat.py` (the `mentions[]` / `responding_agent` / `skill` turn contract); design spec `docs/superpowers/specs/2026-06-24-lucius-mention-system-design.md`; plan `docs/superpowers/plans/2026-06-24-lucius-mention-system-p0-foundation.md`; generalizes the S1 RFC `docs/rfc/lucius-data-mentions.md`

## Context

S1 shipped a **push-only, type-closed** @-mention system: a `✦` button on a few widgets pushes a typed `{type, ref, label}` reference into the chat composer, and `backend/services/mentions.py` dereferences it server-side into bounded, base-currency-correct **current** data for the turn (the client never ships numbers — avoids staleness and the amount-hidden leak). Resolution was a hardcoded `_RESOLVERS` dict + a closed `KNOWN_TYPES` set.

We are generalizing this into a **pull-based, generic, multi-agent-ready** system (the `@` picker + `/` verbs + a `/mentionables` search endpoint, landing across phases P1–P4). The design brief's load-bearing requirement is that **the client stays type-agnostic** — "adding Goals later = a new provider, no picker rewrite." That forces a decision about **where the capability registry lives** (which types are mentionable, how to search them, how to render them, what they resolve to) and **how much per-type knowledge the client holds**. Getting this wrong locks a contract that every later phase builds on, so it is settled now (P0), before any picker or search code exists.

## Decision

**One canonical, server-authoritative provider registry; the client is type-agnostic.**

`backend/services/mentions/registry.py` defines a `MentionProvider` dataclass and a `REGISTRY: dict[str, MentionProvider]` that is the single source of truth for resolution (P0) and search (P1+). `resolve_mentions` routes every ref through `REGISTRY.get(type).resolve(...)`; `KNOWN_TYPES` is derived from `REGISTRY`. The provider carries:

```python
@dataclass(frozen=True)
class MentionProvider:
    type: str
    surfaces: frozenset[str]                       # subset of {"entity","agent","verb"} — a SET
    icon: str
    resolve: Callable[[str], dict | None]          # the existing S1 resolver, lifted in
    search:  Callable[[SearchQuery], list[dict]] | None = None   # P1 attaches this
```

Load-bearing properties:

1. **`resolve` must be server-side — that is non-negotiable and pre-decided by S1.** Fresh, base-currency-correct data recomputed via `txn_base_amount()`/`net_worth()`; the client never supplies figures. So resolution lives in `services` regardless of any client registry.

2. **`search` is also a backend job.** The design's hybrid split is "on-screen/recent → client (instant, from the DOM); the full dataset → backend." Global search scans the in-memory stores server-side (`/api/mentionables`, P1). So the *searchable* half of "what's mentionable" is server-side too.

3. **The normalized row is self-describing, so the client needs no per-type code.** Search returns `{type, id, label, sublabel, value, icon}`; a single generic renderer draws any row (icon + label, sublabel in the picker). The **only** per-type knowledge the client holds is the icon glyph (`frontend/src/types/mentions.ts` `MENTION_ICONS`). Therefore a *client-side* registry is unnecessary — there is nothing per-type for it to hold beyond the glyph.

4. **`surfaces` is a SET, not a single value**, because objects are multi-surface: an agent is `{"entity","agent"}` (talk *about* + talk *to*), a skill is `{"verb","entity"}` (invoke + talk about), a transaction is `{"entity"}`. The pick's destination (context chip vs `responding_agent` vs `skill`) is the zone it was chosen from, not the type alone.

5. **The registry generalizes S1 rather than replacing it.** Each provider's `resolve` is the *existing* S1 resolver, lifted in verbatim; `search` is the new, default-`None` seam P1 fills. Adding a new mentionable type (Goals, payee, watchlist, rule, scheduled-task, …) is **one server-side provider** plus — only if it introduces a new glyph — one entry in the client `MENTION_ICONS` map. Zero picker/composer rewrite.

This is the cleanest realization of the brief's "client stays type-agnostic / adding a type = a new provider": the registry is server-authoritative; the client renders self-describing rows and harvests on-screen entities generically from the DOM (`data-entity-id`, P1).

### Alternatives rejected

- **Two parallel registries (a client registry for icon/render/local-search + a server registry for search/resolve), coupled by the type key.** Each side owns what it needs, no bootstrap round-trip — but **two places to register a type and a drift hazard** (a type added server-side but not client-side silently won't render). Rejected: the self-describing row collapses client rendering to one generic path, so the only thing a client registry would hold is the icon glyph — not worth a second registry.

- **Fully client-driven registry; server is a dumb query executor the client parameterizes.** Rejected outright: it violates S1's core invariant (`resolve` must be server-side for fresh, base-correct, un-leakable data) and the design's "search the global dataset on the backend," and would drag monetary/data-shaping logic into the frontend — the exact thing the codebase's "monetary math only in `services/`" rule forbids.

## Consequences

- **Positive:** one source of truth for mentionability; adding a type is a single server-side provider (plus an optional glyph); the client stays thin and type-agnostic; the dependency rule is held by construction (`services/mentions/` imports only `db`/`services`/`core`, never `api` — CI import-linter enforces); P1's `search` and the `responding_agent`/`skill` routing have a contract-tested seam already in place (`search is None` asserted for every P0 provider).
- **Negative / costs:** the client retains exactly one bit of per-type knowledge — the icon glyph — so a brand-new type with a brand-new glyph touches two files (the server provider + `MENTION_ICONS`); a type with no glyph falls back to a default glyph until added. This is the deliberate, minimal duplication the design accepts in exchange for a type-agnostic renderer. `resolve.py` is ~415 lines (just over the ~400 soft cap, down from S1's 508) — a per-domain split of the resolvers is a possible fast-follow.
- **Reversibility:** the registry is additive over S1's behavior — `resolve_mentions` still produces identical output (characterization-tested). Reverting is mechanical: inline the providers' `resolve` back into a flat dict. The package split (`registry`/`resolve`/`dispatch`/`__init__`) is a pure reorganization behind the unchanged public API, so no caller is affected either way.
