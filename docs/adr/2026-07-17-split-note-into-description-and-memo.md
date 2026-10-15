---
title: Split the transaction `note` into `description` (bank/matching) + `memo` (personal)
status: Accepted
date: 2026-07-17
deciders: Repo owner + Claude (data-model)
amends: 2026-06-29-merge-txn-description-into-note.md
---

# 2026-07-17. Split the transaction `note` into `description` + `memo`

## Context

ADR 2026-06-29 collapsed the two freeform transaction fields (`description` + `note`) into a
single `note`, on the grounds that "there was never a principled separation between them." That was
correct *for the axis it considered*: two freeform fields for **the same job** (a label the user
reads) had no semantic difference and just bred "which one do I read/write?" bugs.

Since then a **different, principled axis** has surfaced in real use — **provenance**, not "label vs
annotation of the same thing":

- The owner uses `note` for **personal tracking** ("what I ordered"), editing it freely after a
  transaction lands.
- But `note` is **also the identity/matching string** that four downstream systems key on. Tracing
  the code (`services/matching.py`, `services/rules.py`, `services/payee_registry.py`,
  `services/import_csv.py`) confirms a single field cannot serve both without collateral damage:

  | System | Reads `note` | Effect of a personal edit |
  |---|---|---|
  | Import/sync dedup (`external_id`) | Frozen at import — id is content-hashed once | **Safe** — re-import still dedups |
  | Near-duplicate detection (`matching.py` L2) | **Yes** — hard gate at token-Jaccard ≥ `DESC_SIMILARITY` 0.6, + 25% of `_score` | **Breaks** — an annotation pushes similarity below the gate, so a genuine cross-source duplicate stops being flagged |
  | Spend rules (`description_contains`/`keywords`/`description_regex`) | **Yes**, re-run on every edit-triggered `create_transaction` | **Breaks** — can flip a non-user-pinned category on re-run |
  | Payee re-resolution (`apply_payee_rules*`) | **Yes** (bulk re-scan reads the current `note`) | **Breaks** if the owner later re-runs the bulk payee tools |

So the collision is real and load-bearing: personalizing the one field silently degrades duplicate
detection and rules. This is not the axis 2026-06-29 rejected — it is a new one that ADR did not
weigh.

## Decision

**Re-introduce two fields, split on provenance, with clearly distinct consumers:**

- **`description`** — the bank-origin / matching string. The **only** freeform field read by any
  identity/matching system: `matching.py` (L2 similarity, `pair_signature`, the L3 `_judge_payload`),
  `rules.py` (`description_contains`/`keywords`/`description_regex`), `payee_registry.resolve_payee_id`,
  and the CSV/QIF `_external_id` content hash. This is the value 2026-06-29's `note` already held; the
  rename is chosen so **every matcher/rule keeps reading the exact same string** (see Migration).
- **`memo`** — the user's personal annotation. **Display-only.** Read by **none** of the systems
  above. Free-text, edit at will, never affects matching, dedup, rules, or payee resolution.

This is the "keep two fields, define a convention" alternative that 2026-06-29 rejected — but the
convention now has a **real, enforceable semantic**: `description` is the machine-read identity,
`memo` is human-only. It is enforced structurally (only `description` is wired into the matching
seams), not by discipline.

### Edit policy — `description` stays editable everywhere (owner decision, 2026-07-17)

`description` is **not** locked/immutable, even on imported rows. Rationale: (1) manually-entered
transactions have no bank string — the user *is* the source and must be able to type it; (2) `memo`
now absorbs the personalization pressure, so the owner rarely needs to touch `description` at all;
(3) a hard immutable field is more machinery than the problem needs. The residual risk — editing
`description` can still perturb matching — is accepted and mitigated by `memo` existing. (The
"lock imported rows" alternative was considered and rejected as premature; it can be layered on
later without a data-model change if the risk proves real.)

### Migration — `note` → `description`, `memo` empty, behavior byte-identical

A one-shot script (`backend/scripts/migrate_note_into_description.py`, same guardrails as
`migrate_txn_description_into_note.py`: dry-run default, `--apply`, timestamped backup, atomic write,
idempotent) rewrites `transactions.json`:

- each row's existing `note` → `description`
- `memo` → `""` (empty)
- the `note` key is deleted

Because every matcher/rule/payee/import-hash consumer is repointed from `note` to `description` in
the same change, and the migration moves the identical string into `description`, **the day of the
migration nothing re-triggers**: L2 duplicates that matched still match, rules that fired still fire,
`external_id`s are untouched (they were frozen at import). This is the same byte-identical-behavior
property 2026-06-29 relied on, in reverse. Per-line `TxnSplit.note` is left as-is (out of scope —
it is not read by any matcher).

The owner's personal annotations currently mixed into `note` land in `description` (they are not
lost); moving them to `memo` is a going-forward manual act, not something the migration can
mechanically separate.

## Consequences

### Positive

- Personal tracking no longer degrades duplicate detection, rules, or payee resolution — the two
  jobs live in two fields with structurally distinct consumers.
- Restores the bank-standard vocabulary (`description`) that importers and users expect, while
  keeping a first-class personal `memo`.
- The migration is reversible (backup) and re-runnable (idempotent), and preserves matching behavior
  exactly on cutover.

### Negative

- Re-widens the model back to two freeform fields — the very thing 2026-06-29 narrowed. Mitigated
  by the now-real semantic split (machine-read vs human-only) and by wiring, not convention.
- Every consumer that read `note` must be repointed to `description` in one atomic change (miss one
  and a matcher reads an empty string). The touch list is enumerated in the companion spec.
- Two ADRs now describe opposite moves; this one **amends** 2026-06-29 and is the current truth. The
  earlier ADR is not deleted (append-only) but should be read as superseded on this point.

### Follow-ups

- If `description` edits on imported rows prove to hurt matching in practice, layer on the
  "lock imported rows" edit policy — no data-model change required.
- Consider whether the AI `add_transaction`/`bulk_add_transactions` tools should default a caller's
  single free-text into `description` (matching-visible) or `memo` (silent). Spec proposes
  `description` for back-compat with today's `note` behavior.

## Alternatives considered

- **Keep a single `note`** — rejected: it is the documented status quo and is actively breaking
  dedup/rules when personalized (the whole reason for this ADR).
- **Lock `description` on imported rows (immutable bank origin)** — considered; rejected for v1 as
  premature machinery. Deferred as a cheap later follow-up.
- **Auto-split existing `note` into description vs memo at migration time** — rejected: impossible to
  mechanically tell which characters were bank-origin vs personal; all existing text goes to
  `description` (the matching-safe choice) and the user re-homes memos over time.
- **A third "raw immutable import ref" alongside an editable description** — rejected as
  over-engineering; `external_id` already frozen-fingerprints the import, and `description` +
  `memo` covers the stated need.

## Agent provenance

- **Decided by:** Repo owner (chose the `description`/`memo` split, "always editable", ADR-first),
  designed by Claude (data-model).
- **Lane / branch:** `claude/ledger-note-description-lh1j10`.
- **Alternatives weighed:** single `note` / lock imported rows / auto-split at migration / a third
  raw-import field — all rejected in favour of a provenance split (`description` machine-read,
  `memo` human-only) with an editable `description` and a byte-identical `note`→`description`
  migration.
- **Driving artifacts:** this ADR + `.claude/rfcs/2026-07-17-txn-description-memo-split.md` (build
  spec); implementation lands `backend/scripts/migrate_note_into_description.py` + the consumer
  repoint enumerated there.
- **Why this way (one line):** `note` was doing two incompatible jobs (a machine-read matching
  string and a freely-edited personal annotation), so split it on provenance — `description` for
  matching, `memo` for the human — repointing every matcher to `description` so cutover changes no
  behavior.
