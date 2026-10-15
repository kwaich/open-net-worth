---
title: Fully retire the combined `external_id` string — split into `external_source` + `external_ref`
status: Accepted
date: 2026-07-20
deciders: Repo owner + Claude (data-model / dedup)
supersedes-recommendation: .claude/rfcs/2026-07-19-email-provenance-two-field-external-ref.md (§5/§8 phased-mirror plan)
---

# 2026-07-20. Retire `external_id`; split into `external_source` + `external_ref`

## Context

A **real production double-book**: the `email-transaction-logger` agent constructs a transaction
dedup key by hand — `external_id="email:<message_id>"` — and different runs formatted it
inconsistently (bracketed `<id>` vs bare `id`, sometimes dropping the `email:` prefix, sometimes
truncating the id). The L0 exact-idempotency floor (`TransactionStore.by_external_id`) is an exact
string compare, so the mismatch slipped the guard and the same real bank transfer booked twice.

Root cause: **one string was doing two different jobs** — provenance ("where did this row come
from") and dedup ("have I already booked this event") — and the model was *constructing the
combined string by hand*. A survey of the live ledger found 276 `external_id`-bearing rows across
`email:` (219, the only model-typed prefix, five sub-shapes of inconsistency), `xlsx:`/`pdf:` (27),
`opening:` (20), `vest:` (2), `anthropic:` (1). Every non-email prefix is built by trusted service
code and is internally consistent — the reliability hole is the model-typed `email:` path — but the
*field itself* conflates two concerns with different error tolerances (a provenance miss is a UX
papercut; a dedup miss duplicates real money).

The RFC (`.claude/rfcs/2026-07-19-email-provenance-two-field-external-ref.md`) recommended a
**phased** rollout: Phase 1 splits the *email tool surface* and keeps `external_id` as a derived
mirror + input back-compat shim so the ~7 prefix-check call sites and the matching namespace
machinery keep working unchanged; Phase 2 later migrates those and drops the mirror.

## Decision

**The owner explicitly overrode the phased plan: full retirement in ONE change, no derived mirror,
no back-compat shim, applied to EVERY flow that used `external_id` — not just the email path.**

1. **Schema.** `external_id: Optional[str]` is REMOVED from `Transaction`/`TransactionCreate`/the
   REST/eval models. Two structured fields replace it:
   - `external_source: Optional[str]` — the namespace (`email`/`opening`/`recurring`/`ibkr`/`vest`/
     `option-premium`/`ofx`/`csv`/`simplefin`/…).
   - `external_ref: Optional[str]` — the raw per-event id (Message-ID, FITID, `acct:txn` key,
     content hash). It MAY contain colons; the universal split rule is "first colon: prefix →
     `external_source`, remainder → `external_ref`."
   The absorbed-on-merge list `external_ids: List[str]` (combined strings) is redesigned to
   `external_refs: List[{"source", "ref"}]`.

2. **Store.** `by_external_id`/`has_external_id` are DELETED and replaced by
   `by_external_ref(source, ref)` / `has_external_ref(source, ref)`, which key on the
   `(source, ref)` tuple. The F13 identity contract (a user-deleted row frees the key; a
   merge-tombstoned row stays covered via the canonical's absorbed set) is preserved on the tuple
   lookup.

3. **Every writer** that built `f"{prefix}:{rest}"` now sets the two fields directly:
   `set_account_opening` (`opening`), recurring runs (`recurring`), IBKR/connector syncs, statement
   & file importers (`ofx`/`csv`/`qif`/`xlsx`/`mt940`/`camt`/`pdf`), equity vests (`vest`), option
   premium/close legs (`option-premium`/`option-close`), and the email agent tool surface.

4. **Every reader** that prefix-checked the combined string now reads `external_source` directly —
   which is strictly SIMPLER (no `split(":")`): the opening-txn sweep skips, `is_opening`, the
   Starting-Balance/planning/audit/tax classifiers, `matching._judge_payload`, and the feed-grade
   heuristic.

5. **The identity invariant (highest-risk).** `core/txn_sources.py`'s
   `id_namespace()`/`is_authoritative_namespace()` string-prefix parsers are DELETED. In their place
   `is_authoritative_source(source)` reads the source field directly, and a shared
   `ext_ref_pairs(row) -> set[(source, ref)]` (leaf, so `db` and `services` share ONE definition)
   builds a row's full pair set (primary + absorbed `external_refs[]`). `matching._full_ref_set`
   and `_distinct_same_source_event` operate on `(source, ref)` tuples grouped by source. Invariant
   1 ("two distinct authoritative refs under the SAME source are two real events, never merged") is
   preserved and pinned by a characterization test written FIRST
   (`tests/dedup/test_external_ref_split.py`).

6. **The model-facing tool** (`add_transactions`) drops `external_id` entirely and gains
   `external_source` (a narrow enum `["email"]`, structured so a future source is a one-line add)
   and `external_ref` ("copy the `message_id` shown by `fetch_emails` exactly as-is — do not add
   brackets, a prefix, or a colon"). **No back-compat acceptance of the old `external_id="email:…"`
   shape on the tool input** (the RFC wanted this kept; the owner removed it — a caller using the old
   shape gets a clean schema error, not silent tolerance).

7. **The email id the model books is a SHORT SERVER-COMPUTED HASH — the model never sees or handles
   a raw Message-ID (refined design, owner-directed mid-implementation).** The original build had the
   model copy a canonicalised raw `message_id`; that was replaced. Now:
   - `imap_client` extracts the raw `Message-ID` header (or the no-Message-ID fallback below) — in
     code, deterministic — and `core/email_ids.py::internal_email_id(raw)` hashes it to a SHORT
     12-hex id (`sha256(canon(raw))[:12]`, matching `core.ids.new_id`'s convention).
   - `fetch_emails`/`_render_emails` shows the model ONLY that short id; the model copies it verbatim
     into `external_ref`. Because the model is entirely OFF the identity path, it cannot garble the
     value — the whole class of "bracketed vs bare / prefix dropped / truncated" inconsistency is
     structurally gone, not merely canonicalised away.
   - `email_snapshots.json` is now keyed by the SAME short id (raw header kept as `raw_message_id`
     for the drawer/debuggability), so the ledger's `external_ref` looks the snapshot up directly.
   - `internal_email_id` bracket-normalises (`canon`, a private server-side helper — the model never
     sees it) BEFORE hashing. This is NOT to fix model inconsistency (the model no longer touches the
     raw id) but to keep the hash STABLE across the migration boundary: `imap_client` yields a
     usually-bracketed header while pre-migration rows carry whatever the agent historically echoed
     (often bracket-stripped) — without it, a re-fetch of an already-booked email would hash to a
     different id than its migrated row and DOUBLE-BOOK, the exact bug this change kills. A
     deliberate, documented deviation from the "no canon needed" refinement note, on money-correctness
     grounds; it never reaches the model, so it doesn't reintroduce the inconsistency the refinement
     removed.
   - The IMAP no-Message-ID fallback replaced the folder-UNSTABLE `uid-<validity>-<uid>` with
     `content_fallback_id(subject, sender, FULL-precision timestamp, full body)` (a full sha256 that
     `internal_email_id` then shortens) — a real collision involved two same-day, same-subject,
     same-sender Meridian Bank alerts differing only by time of day; and this account's bank templates carry
     300-400+ chars of identical boilerplate, so the FULL body (capped at ~2000 chars only as a
     huge-body backstop) is hashed, never a short prefix. Content-derived, so it is stable across a
     folder switch.
   - **Migration for email refs**: `external_ref` becomes `internal_email_id(<the old raw ref>)` — the
     SAME id the live path shows the model, so a migrated row is indistinguishable from a freshly
     booked one.

8. **Migration.** `backend/scripts/migrate_external_id_split.py` (dry-run default, `--apply`,
   backup, idempotent, NOT a boot migration) splits every row's `external_id` on the first colon and
   `external_ids[]` into `external_refs[]`, HASHES `email` refs to the short internal id (via
   `internal_email_id`, matching the live path), and DELETES the old keys (full retirement — no mirror). Bare no-colon values that look like a Message-ID → `email`; else
   `external_source=None`. Truncated `@domain`-less email refs are split as-is and counted for owner
   awareness. Retro-merging the already-double-booked incident pair is explicitly OUT OF SCOPE
   (L0 is forward-looking).

## Consequences

- **Deviations from the RFC** (recorded here because the owner overrode a written recommendation):
  (a) full retirement instead of the phased mirror — the ~7 prefix checks + namespace machinery +
  all-namespace migration all land in this one change; (b) the derived `external_id` mirror is NOT
  written; (c) the tool no longer accepts the legacy combined `external_id` input shape at all,
  which the RFC wanted kept for in-flight transcripts/stored skills — a stored skill/agent still
  emitting `external_id=` will have that key silently ignored and book with no dedup key, so the
  seeded skill/agent bodies + the frontend template were updated in this same change. `make_transaction`
  performs NO input normalisation of a legacy `external_id` key (no shim).
- The redesigned `external_refs[]` (list of `{source, ref}`) is threaded through every merge/absorb
  path (draft merge, canonical merge inheritance, statements `ledger_diff`, orchestrator & import
  prefilters, the REST bulk L0). The prefilter seams were renamed: `known_external_ids()` →
  `known_external_refs()` (tuples), orchestrator `_known_external_ids(prefix)` →
  `_known_external_refs(source)`, adapter `external_id_prefix` → `external_source`,
  `bulk_create_transactions(dedup_by_external_id=)` → `dedup_by_external_ref=`.
- No monetary math moved; base-amount / historical-rate logic is untouched. `by_external_ref` is a
  structured lookup (data access), keeping `db` a leaf below `services`. The new `core/email_ids.py`
  is a true stdlib-only leaf (the load-bearing reason it is in `core`, not `services`).
- The golden tool-schema file (`tests/golden/tool_input_schemas.json`) was hand-edited for
  `add_transactions` (the one tool whose emitted schema changed), reviewed field-by-field.
