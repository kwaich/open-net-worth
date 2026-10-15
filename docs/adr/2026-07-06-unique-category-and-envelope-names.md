# 2026-07-06 — Category labels and active-envelope names are unique

**Status:** accepted (owner request, 2026-07-06 — "ensure no categories have same name and no
envelopes have same names") · reverses the previously-documented "labels are NOT unique" stance
in `services.categories.resolve_category_ref` and its pinned tests
**Where:** `backend/services/categories.py` (`_assert_unique_label`, wired into `create_category`
+ `rename_category` relabel), `backend/services/envelopes.py` (`_assert_unique_name`, wired into
`create_envelope` + `rename_envelope` + `restore_envelope`), `backend/services/planning.py`
(`sync_commitment_envelopes` passes `enforce_unique=False`)

## Decision

Two write-choke guards refuse a duplicate name, raising `ConflictError` (HTTP 409):

- **Categories** — a category **label** must be unique across the whole store, **case-insensitively
  and trimmed**, **GLOBAL across kinds** (an expense "Interest" blocks an income "Interest"). Hidden
  rows still occupy their name (hidden ≠ deleted). Enforced at `create_category` and the relabel
  branch of `rename_category` (which excludes the row being relabeled, so a self-relabel to the same
  name — even different case — is a no-op, not a self-clash). The `merge_into` branch is unaffected
  (it deletes a row, never creates a name).
- **Envelopes** — an envelope **name** must be unique among **NON-ARCHIVED** envelopes,
  case-insensitively and trimmed. Enforced at `create_envelope`, `rename_envelope`, and
  `restore_envelope` (restoring re-activates an envelope, so it must not collide with a live one).

`create_envelope` gains a keyword-only `enforce_unique: bool = True`. The bot-managed commitment
sync (`planning.sync_commitment_envelopes`) passes `enforce_unique=False`.

## Why

- Names are the flat, user-facing namespace for both entities; two same-named rows are ambiguous
  to a human and, for categories, make `resolve_category_ref` **unresolvable** — it returns `None`
  on a label matching >1 row (to avoid booking a txn/import under the wrong-KIND category), so a
  duplicate label silently degrades label-based resolution across the ledger write choke and CSV/QIF
  import. Enforcing uniqueness at creation closes that at the source; the resolver's >1-match guard
  stays as defense for legacy/direct-store writes.
- **Global (not per-kind)** for categories matches the owner's absolute phrasing ("no categories
  have same name") and keeps the resolver deterministic (it does not filter by kind). Easily
  relaxed to per-kind later if a real need appears.
- **Archived envelopes are exempt** because they are tombstones: deleting "Utilities" and later
  creating a fresh "Utilities" is legitimate and common — the archived row must not block it. This
  matched the live book, which already had an archived "Utilities" beside the active one and two
  archived "Test" envelopes (all fine; zero active-name collisions).
- **The commitment-sync bypass** is required for safety, not convenience: `sync_commitment_envelopes`
  runs on every page load and must never throw. A commitment envelope's identity is its
  `commitment.envelope_id` foreign key, not its name, so two commitments sharing a name — or a
  commitment name colliding with a user envelope — must not break the idempotent sync.

## Consequences

- **No data migration.** The live book was already clean: zero duplicate category labels (global or
  per-kind), and the only duplicate envelope names involved archived rows. This is pure go-forward
  enforcement.
- Four tests that asserted the OLD "duplicates allowed" behavior were rewritten to assert the new
  409/`ConflictError` contract: `test_categories_api::test_create_duplicate_label_rejected`,
  `test_category_opaque_ids::test_create_category_rejects_duplicate_labels`,
  `test_char_tool_dispatch::test_add_category_rejects_duplicate_labels` (a characterization test —
  updated deliberately for this owner-requested behavior change),
  `test_tools_categories_parity::test_add_category_duplicate_label_rejected`. New regression tests
  cover create/rename/case-insensitivity/self-rename for categories and
  create/rename/restore/archived-exemption/sync-bypass for envelopes.
- The frontend needs no change: the optimistic envelope-create path (`useBudgetModel.addEnvelopeLocal`)
  already rolls back via `refresh()` and surfaces the server message on failure, and
  `renameCat`/`renameEnvelope`/the AccountsTab `AddCategoryModal` all toast `errMsg(err)`. (Minor,
  pre-existing: the Manage-categories modal's rename path toasts a generic "Failed to rename category"
  on any non-2xx rather than the specific server message — unchanged by this ADR.)
- The AI tools inherit enforcement for free (they catch `OpenNetWorthError` → `{ok: False, error}`),
  so Lucius cannot mint a duplicate-named category/envelope either.
