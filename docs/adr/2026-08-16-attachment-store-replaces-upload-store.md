---
title: AttachmentStore replaces UploadStore; durable content splits from dispatch content
status: Accepted
date: 2026-08-16
deciders: orchestrating Claude agent + repo owner
---

# AttachmentStore replaces UploadStore; durable content splits from dispatch content

## Context

`.codex/rfcs/cross-channel-attachments-vision-and-statement-evidence.md` Phase 2 (items
10-14) and `docs/adr/2026-08-15-separate-attachments-receipts-and-statements.md` require
one durable, cross-channel attachment record: every chat channel — web upload AND
Telegram photo/document/album — must persist an inbound file into `AttachmentStore`
before the turn, and the conversation transcript must store a stable `attachment_ref`,
never expanded base64/text.

Two implementation calls were open going in:

1. Whether to rename `UploadStore`/`uploads` cleanly to `AttachmentStore`/`attachments`,
   or layer the RFC's new fields onto the existing schema/service names.
2. Where "resolve a ref into real provider content" should live, given that today's
   `build_user_content` does resolution AND returns the value that becomes the durable
   transcript in one step — the exact thing the RFC says must stop happening.

## Decision

### 1. Clean rename, not a payload-only extension

`UploadStore` → `AttachmentStore` (table `uploads` → `attachments`, Alembic revision
0041, id prefix `up_` → `att_` for new rows), `services/uploads.py` →
`services/attachments.py`. `ATTACHMENTS_JSON`/`ATTACHMENTS_DIR` replace
`UPLOADS_JSON`/`UPLOADS_DIR` in `core/paths.py`; the physical blob directory is renamed
(not left mismatched with its constant) via `shutil.move` in the migration's `upgrade()`,
best-effort (a fresh install has no such directory yet).

Rationale: the RFC explicitly frames this as the store's semantic role changing ("replaces
the semantic role of the current UploadStore"), `PayloadTableStore`'s payload is
schemaless JSON (so the six new fields cost no column migration — the whole revision is a
table rename + a light per-row backfill), and this repo's own precedent (revision
0040, `connector_tool_permissions` → `tool_permissions`) is a clean rename with a frozen
local `sa.table()` in every migration that predates it, not a parallel/compat name.

**Minimum record** (RFC section 2.1), keeping every existing `UploadStore` field:
`id`, `created_at`, `filename`, `kind`, `media_type`, `summary`, `size_bytes`, `format`,
`content_hash`, `blob_locator`, `source_channel`, `source_message_id`,
`source_conversation_id`, `statement_id` (unchanged, Phase 4 removes the coupling — not
this change), `last_used_at`, `last_used_session`, `metadata`.

**Not renamed**: the AI tool surface (`get_uploads`/`delete_upload`) and the REST paths
(`/api/upload(s)`) — CLAUDE.md requires a tool rename to carry a persisted alias (tool
names are persisted data — automations, skill frontmatter, in-flight transcripts), and a
REST path rename is a separate, frontend-touching decision the RFC does not ask for in
Phase 2. Both now call the renamed store/service underneath.

**Legacy rows**: migration backfills the six new fields with honest not-known defaults —
`source_channel="legacy:unknown"`, everything else `None`/`{}` — never a guessed channel.
Deep migration diagnostics/receipts (RFC item 31) are explicitly Phase 6 scope.

**Collateral this rename touched**: `db/portable_bridge.py`'s bundle key ("uploads.json"
→ "attachments.json" — no dual-key import shim, matching "no backward-compat shims"),
`core/portable.py`, `ai/checkpoint/_policy.py`'s exclusion sets, `db/misc_io.py`'s current-
state `MISC_PAYLOAD_SPECS`, and — because revision 0018 and `db/misc_io.py` used to import
the live `uploads` Table object straight out of `db/tables.py` — revision 0018 was fixed
to define its own frozen local `sa.table("uploads", ...)` instead (mirroring 0040's own
precedent: a historical migration must not depend on a name `db/tables.py` no longer
carries once a later revision renames it).

### 2. Durable content vs. dispatch content: resolve eagerly, once, in `_run_turn`

`build_user_content` (message construction time, `connectors/processor.py`/`api/chat.py`)
now returns ONLY the durable shape: `attachment_ref` blocks (+ caption text). It no longer
takes a `model` parameter and no longer runs any vision-capability/reader logic — that
logic (`vision_gap`, `read_images_for_blind_model`, the count/byte admission ladder) moved
into a new function, `resolve_attachment_refs_for_dispatch`.

The question was WHERE that resolver runs. Two designs were weighed:

- **(Rejected) Resolve inside `_prepare_messages`, per round-trip.** This is the seam that
  already knows "current turn vs. older turn" (`turn_start`/`in_current_turn`), so it looks
  like the natural home. But making it `async def` (required for the reader's LLM call)
  breaks its ~40 existing synchronous call sites across
  `tests/test_chat_cost_levers.py`, `test_history_compaction.py`, `test_cost_caching.py`,
  `test_telegram_proactive_context.py`, `test_context_folding.py`, and
  `test_skill_host_runtime.py` — a disproportionate blast radius for a Phase 2 change that
  does not otherwise touch compaction/folding.
- **(Chosen) Resolve eagerly, once, right after `history.extend(new_messages)`, before the
  provider-call loop starts.** `_run_turn` computes the resolved (real image/text) content
  for the current turn's message ONE time and mutates `history` in place with it — every
  subsequent round-trip this turn reuses it via the ordinary sliding-window machinery,
  UNCHANGED. `_prepare_messages` stays fully synchronous; its only new responsibility is
  rendering any OLDER turn's leftover `attachment_ref` block as a short stub
  (`stub_attachment_refs`, sync, no reader call) so a raw non-standard block type never
  reaches a provider.

The unresolved problem this raises: `history` now briefly holds RESOLVED content for the
current message, but the value written to the SESSION STORE must stay ref-shaped (the
RFC's whole point). `_run_turn` keeps the original ref-shaped content in a local
`_attachment_durable_override = (turn_ctx_idx, original_content)` and a `_durable_history()`
helper that returns a **same-length COPY** of `history` with that one message swapped back
to its original ref-shaped content — never a mutation of the live, still-in-use `history`
object. Every `save_session_durable`/`_save_session` call inside `_run_turn` (8 call
sites) goes through `_durable_history()` instead of the bare `history` list.

### Consequences

- Positive: `_prepare_messages` and its 40+ existing tests are untouched in shape; the
  resolution ladder (vision-capable / known-blind / unknown / count-refusal) is exercised
  by ONE function or its stub twin, not duplicated at two call depths.
- Positive: the durable/dispatch split is enforced structurally — nothing HAS to remember
  to downgrade content before saving; `_durable_history()` is the only path every save call
  goes through.
- Negative: `_run_turn` now carries one more piece of turn-scoped state
  (`_attachment_durable_override`) and a small closure; documented inline at its
  declaration site with the reasoning above.
- Negative: resolution happens exactly once per turn regardless of how many provider
  round-trips follow — correct for this phase (matches prior `build_user_content` timing
  exactly, which also resolved once), but Phase 3's first-refusal recovery (a mid-turn
  retry after a typed `UnsupportedModalityError`) will need to invalidate/re-resolve rather
  than assume the eager resolution is final. Left for that phase, which already owns the
  retry-loop design.

## Alternatives considered

- Keep `UploadStore`/`uploads` and layer the RFC's fields into the existing payload dict —
  rejected per the RFC's own "replaces the semantic role" framing and this repo's rename
  precedent (revision 0040).
- Resolve `attachment_ref` blocks inside `_prepare_messages` (async) — rejected: ~40 broken
  synchronous test call sites for zero behavioral gain over resolving eagerly once in
  `_run_turn`.
- Tag resolved provider content blocks with an extra internal key (e.g.
  `"_attachment_id"`) and strip it before the wire call, instead of tracking the durable
  override out-of-band — rejected: an extra key riding all the way to a provider's actual
  request body is one call site away from leaking a non-standard field into strict
  JSON-schema-validated API request, for a savings of one small closure.

## Agent provenance

- **Decided by:** orchestrating Claude agent + repo owner (Codex-authored RFC/prior ADR)
- **Lane / branch:** `feature/cross-channel-attachments-phase2`, based on
  `feature/cross-channel-attachments-epic`
- **Alternatives weighed:** payload-only extension vs. clean rename; async
  `_prepare_messages` resolution vs. eager once-per-turn resolution vs. block-tagging
- **Driving artifacts:** `.codex/rfcs/cross-channel-attachments-vision-and-statement-evidence.md`
  Phase 2 (items 10-14), `docs/adr/2026-08-15-separate-attachments-receipts-and-statements.md`
- **Why this way (one line):** the RFC's own framing plus this repo's rename precedent make
  a clean store rename the lower-risk choice, and resolving attachments once, eagerly, in
  `_run_turn` gets the exact same durable/dispatch split as an async `_prepare_messages`
  without breaking the ~40 tests that already depend on it staying synchronous.
