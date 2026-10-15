---
title: Documents (the Vault) is a view over the existing blob stores, and Private keeps a file's contents from every model
status: Accepted
date: 2026-09-26
deciders: implementing agent (Claude) + repo owner ("just decide and build")
---

# 2026-09-26. Documents (the Vault) is a view over existing stores

> **Name.** Designed and built as "the Vault"; shipped to users as **Documents** (renamed
> 2026-09-26, before merge, by the owner's call: several banking apps use "Vault" for savings
> pots, so in a money app it could read as a place money is kept). Everything the user or Lucius
> sees says Documents — the panel, the `document` @-mention type, and the tools
> `list_documents` / `read_documents` / `save_to_documents`. The code keeps the internal name:
> `services/vault.py`, `services/vault_changes.py`, `/api/vault`, the `vault` payload key and
> data-changed domain. Below, "the Vault" means that internal design.

## Context

Users had no single place to see the files they have given the app — chat uploads, imported
statements, transaction receipts, reports Lucius drew — let alone ask Lucius about one later. The
owner asked for a "Vault": documents the user puts in (tax forms, policies, deeds), plus what the
app already files (statements, receipts), plus artifacts the user chooses to keep. Two answers
were settled with the owner up front: **one copy** (a statement in the Vault is the same statement
Accounts lists) and a **per-file Private switch**. The entry point is a button in the Lucius tab
that opens a side panel (full screen on a phone), not a new tab.

ADR `2026-08-15-separate-attachments-receipts-and-statements` split raw files into separate stores
with separate lifecycles on purpose: an attachment never implies a statement or a receipt, and each
store has its own delete semantics. A Vault that owned its own copies would undo that split and
create a second copy to keep in sync, delete, export and back up.

Artifacts had one more lifecycle to respect: automation cleanup deletes a run's artifacts with its
session (`ArtifactStore.delete_for_session`), so an artifact a user saved could vanish.

## Decision

We will build the Vault as a **read model over the four existing stores**, with no store, table or
migration of its own:

| kind | source | how it gets in |
|---|---|---|
| file | `attachments` | added in the Vault (`source_channel="vault"`) or a chat upload kept on purpose — `vault` metadata `{saved, saved_at, title, year, private, note}` on the row's payload (a hand-set `doc_type` — tax/insurance/property/other — was retired 2026-09-28, see below) |
| artifact | `artifacts` | "Save to Vault" — `saved`/`saved_at` on the row's payload |
| statement | `statements` | every imported statement, automatically |
| receipt | `receipts` | every receipt, automatically |
| asset | owned-asset `docs[]` → `attachments` | every file attached to an asset, automatically (2026-09-27, `services/vault_assets.py`) |

`services/vault.py` is the read model (`list_items`, `get_item`, `read_source`);
`services/vault_changes.py` holds the only writes. A Vault id is `<kind>-<source id>`.

- **Removing** depends on where the thing lives: a file added in the Vault is deleted (the Vault is
  its only home); a kept chat upload and a saved artifact are only taken out (they stay in their
  chat); statements and receipts are managed where they live and refuse removal here. A kept chat
  upload can also be deleted for good from here (`delete_file=true`, 2026-09-27) — the same
  delete as Settings → Uploaded files, offered beside "Remove" so it takes one place, not two.
  The user's action only: no tool can delete.
- **The document itself is shown**: a PDF item's pages are drawn server-side as PNGs, one per
  request (`services/document_preview.py`, `GET /api/vault/items/{id}/pages/{n}`) — the
  statement route only ever downloads, and an embedded browser PDF viewer is unreliable in a
  side panel and on a phone. It is the user's own view, so Private does not apply to it. A
  saved artifact (2026-09-28) is drawn in place in the chat's own frame — `sandbox`
  `allow-scripts` without `allow-same-origin`, the egress-blocking CSP of
  `utils/artifactFrame.ts` — laid out at a report's width, scaled to fit, `pointer-events:
  none`; a tap opens the full viewer (`ArtifactPreview.tsx`).
- **Asset files** (2026-09-27) are listed like statements and managed on the asset: removal is
  refused here, and `delete_upload` and Settings → "Delete all" leave them alone. The asset's
  "Lucius can read" switch IS their Private — `vault.is_private_attachment` also asks
  `vault_assets.is_private_asset_doc` — so every model read refuses one through the same single
  guard, and there is one switch for it, not two.
- **Renaming** (2026-09-27) writes the record the item lives on (`vault_changes.rename_item`,
  `PUT /api/vault/items/{id}/name`): a file's `vault.title` (empty = back to its file name), a
  saved artifact's `title`, an asset file's doc name through the owned-assets choke (so the asset
  page shows the same name), a receipt's `title` through the receipts choke (`rename`), and a
  statement's `title` — PROMOTED to its own `statements_meta.title` column (revision
  `202609271900`) so the list never reads the payload for it, and kept in the payload so
  export/import carry it. Empty = back to the automatic name. (First shipped refusing statements
  and receipts; the owner asked for rename and delete on every file the same day.)
- **Tags** (2026-09-28, owner: "atm, user can't … organize them into folder, right?" → "maybe
  tags?"). Not folders: most items already live somewhere else (a statement on its account, a
  receipt on its transaction, a file on its asset), so a folder could only hold uploads, and a
  move would fight the one-copy rule. Tags group across every kind instead, many per item:
  `tags` (≤10, ≤32 characters each; tidied, a leading "#" dropped, case-insensitively unique —
  `vault.clean_tags`) kept on the record like the note — a file's `vault.tags`, an artifact's
  and a receipt's `tags` (the receipts choke's `tags`), an asset file's `docs[].tags`
  (owned-assets `update_doc`), and a statement's PROMOTED `statements_meta.tags` JSON column
  (revision `202609281435`, kept in the payload too). `vault_changes.set_tags` /
  `PUT /api/vault/items/{id}/tags`; notes and tags share one write path (`_write_on_record`).
  The list filters by a tag (`list_items(tag=)`, the panel's `#Tag` chips) and search matches
  tags; Lucius sees them (`list_documents` projects `tags` and filters by `tag`; the mention
  carries them). Like the note, they are the user's metadata, not contents — shown Private or not.
- **A file's hand-set type retired** (2026-09-28, owner: "why would type be used for? vs
  Document as kind?"). An upload carried a `doc_type` (tax / insurance / property / other) the
  user picked by hand; its only uses were the row's label (so the page's "Kind" row just
  repeated it — "Document" for other) and a `list_documents` filter, and Lucius can tell a tax
  form from a policy by reading it. Removed from the panel, the API, `list_documents` and
  `save_to_documents`; an upload's kind now reads "Upload", like its filter chip. Rows that
  carry an old `doc_type` keep it, inert — nothing reads or writes it.
- **Notes** (2026-09-28, owner: "worth adding a note field for the file?"): ONE optional
  free-text note per item (≤1000 characters, line breaks kept), written like a name — on the
  record the item lives on (`vault_changes.set_note`, `PUT /api/vault/items/{id}/note`): a file's
  `vault.note`, an artifact's `note`, a receipt's `note` through the receipts choke (`note`), an
  asset file's `docs[].note` through the owned-assets choke (`update_doc`), and a statement's
  `note` — PROMOTED to `statements_meta.note` (revision `202609281200`) for the same reason as
  its title, and kept in the payload. One note, not a thread: a timestamped list is a comment
  system this personal hub doesn't need. The note is the user's words ABOUT the file, not its
  contents, so Lucius reads it (`list_documents` cut to 300 characters, `read_documents` and the
  `document` mention whole) — Private or not. Hiding it for a Private file would be a promise the
  app can't keep: it rides on rows `run_sql_query` can read, exactly like the title. The note
  dialog and the Private line say so instead ("Lucius can see this file's name and note, never
  what is in it"). Search (the list and `list_documents`' `query`) matches it.
- **Deleting** a statement, receipt or asset file here (2026-09-27, `vault_changes._delete_filed`)
  goes through the path its own screen uses: the statement store's delete (record + file; the
  imported transactions stay — the import batch rolls those back), the receipts choke's forced
  delete (the links go, the transaction stays), the owned-assets `remove_doc` (the upload is
  deleted once nothing holds it). Still the user's action only: no tool deletes an item.
- **Spreadsheet previews** (2026-09-27, `services/document_tables.py`,
  `GET /api/vault/items/{id}/table?offset=`): one sheet of a CSV or Excel item a page of 200
  rows at a time (the grid loads the next page as it scrolls), up to 40 columns, as strings,
  never interpreted. A password-protected workbook is not opened.
- **A saved artifact outlives its session's cleanup**: `delete_for_session` detaches it
  (`session_id=None`, `detached_from_session=<id>`) instead of deleting it. Removing a detached
  artifact from the Vault deletes it — nothing else lists it or would ever free it.
- **Private** (files only) keeps a file's CONTENTS away from every model, fail closed. Every path
  that hands attachment contents to a model reads through
  `services.attachments.get_attachment_for_model` / `get_attachment_import_source` (None for a
  Private file) and asks `private_refusal` for the reason: `read_documents`, `read_attachments`,
  a re-attached ref on the current turn (`ai/attachments.py`), the import and equity-PDF parsers,
  `diff_statement`, and receipt promotion (a receipt is readable by `read_receipts`, so copying a
  Private file into one is refused). `get_attachment` itself stays unguarded for the user's own
  views (chat thumbnails); downloads use `read_attachment_bytes`. Lucius may know a Private file
  EXISTS (it is listed, with its title and the user's note) — never what is in it. Only the user switches Private off
  (the Vault's PATCH); `keep_attachment` — what `save_to_documents` calls — refuses to, and un-keeping
  a file leaves the flag set. Private also covers what is derived from the contents or copied
  from them: the row's `summary` (a CSV's first row, readable by `get_uploads` and
  `run_sql_query`) is replaced while Private and restored from the parsed payload after, and a
  receipt promoted from the file is Private too (`read_receipts` and the Vault refuse it). A
  statement imported from the file earlier is NOT — it is part of the statement history the
  ledger was built from, and stays readable.
- **Only the user removes a Vault file.** `delete_upload` refuses one and Settings → "Delete all"
  skips them; `POST /api/vault/files` requires the same CSRF header as the import routes (a
  multipart POST is a CORS-simple request).
- **Lucius's surface** is three tools: `list_documents` (metadata, bounded), `read_documents`
  (contents, ≤5 ids, a 24k-character budget per call with `offset` paging, Private refused), and
  `save_to_documents` (keep an artifact or a chat file — only when asked). Lucius never removes an item.
  "Ask Lucius about this file" is a `document` mention: metadata plus how to read it, never the
  contents.

## Consequences

### Positive

- One copy, no migration: export/import and backups carry the Vault metadata untouched because it
  rides on rows they already carry. Each store keeps its own delete semantics.
- Private is enforced in the service layer, by default, for every model-facing read — a new tool
  that reads attachments through `get_attachment_for_model` inherits the guard.
- Statements and receipts show up in the Vault with no extra step, as the owner asked.

### Negative

- `list_items` reads five stores on every call (statements from their promoted columns only,
  never the payload or parsed rows) — fine for one household's files, but a scan, not an index.
  A single item (`get_item`: tools, mentions) is a point read of its own store.
- Private is an app-level promise, not a sandbox: a model that runs code in the opt-in code
  sandbox (not a filesystem jail — CLAUDE.md) could read files on disk; a user-configured
  fetch-style MCP server could request the loopback file route (approval-gated by default); and
  a conversation that already read a file before it was marked Private keeps what it read. A
  statement imported from the file before it was marked Private stays readable (see Decision).
  The product doc says so.
- A statement cannot be marked Private in V1 (Lucius needs statements to reconcile), and a
  receipt is Private only through the file it was promoted from.

### Follow-ups

- Report snapshots (a saved figure-in-time from the Reports tab) were deferred — artifacts cover
  "keep what Lucius made" for now.
- If a Private statement is ever wanted, the guard belongs in `StatementStore` readers the same
  way it sits in `services.attachments` today.

## Alternatives considered

- **A dedicated Vault store holding copies** — rejected: a second copy of every statement and
  receipt, with its own lifecycle to reconcile against the originals; breaks "one copy".
- **A new Vault tab** — rejected by the owner: a button in the Lucius tab opening a side panel.
- **Private as a model-side instruction only** ("don't read this") — rejected: the model can be
  steered by the very content it would read; the refusal has to happen before content is loaded.
- **Guarding `get_attachment` itself** — rejected: the chat's own image thumbnails read through it,
  so a Private image would vanish from the user's own conversation.

## Review

A security review of the first build (2026-09-26) found, and this ADR's Decision now covers:
Vault-only files deletable through `delete_upload`/Settings "Delete all"; the content-derived
`summary` leaking a Private CSV's first row; receipt copies of a Private file staying readable;
a missing CSRF check on the multipart upload; Private not being set atomically with the upload.
The code review found a saved-then-detached artifact becoming an unlisted orphan on un-save — it
is now deleted, since its conversation is gone.

## Agent provenance

- **Decided by:** orchestrating Claude agent, after two design rounds with the owner in Claude Design.
- **Lane / branch:** `feat/vault-v1` (worktree `.claude/worktrees/vault-v1`).
- **Alternatives weighed:** own store vs view; tab vs Lucius side panel; prompt-only vs service-level Private.
- **Driving artifacts:** owner requests 2026-09-26 ("Think of it as a Vault", "1 - yes 2 - yes",
  "a button … in Lucius main tab", "users can decide to save artifacts to their vault").
- **Why this way (one line):** the stores already model each file's lifecycle correctly; the Vault
  only needed to show them together and add the user's own intent (keep, type, year, Private).
