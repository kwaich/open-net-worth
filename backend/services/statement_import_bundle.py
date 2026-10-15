"""statement_import_bundle — the built-in 'statement-import' skill.

A MANAGED builtin skill (like skill-creator) that ships the universal
statement-parsing playbook as a Tier-2 on-demand resource: the model calls
activate_skill("statement-import") before processing any bank or credit card
statement, loads the playbook once into the turn, and then works.

This frees the add_transactions tool description (sent on EVERY request)
from carrying the full playbook (~5 KB of prose) and compresses it to a
reference + core invariants only.

Layering: services → db + services (reads SkillStore, calls skill_bundles).
Called from the app-startup hook in main.py — NEVER from db.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SKILL_ID = "statement-import"

_DESCRIPTION = (
    "How to drive the statement-import pipeline: preview with import_transactions, review "
    "the per-row verdicts with the user, commit with decisions, report the batch id. "
    "Optional — import_transactions is the entrance and works without this skill."
)

# The playbook body (injected into the turn by activate_skill).
_BODY = """\
You are importing a bank or credit card statement into the ledger.

`import_transactions` is the ONE entrance for every format (CSV / OFX / QFX / QIF /
MT940 / CAMT / XLSX / PDF). Do NOT hand-parse rows out of the statement.

## What the server already does — do not redo any of it

The pipeline handles all of this deterministically, server-side, before you see a row:

- **Skip rules** — opening/closing/carry-forward balance lines, declined/failed rows,
  zero-amount rows.
- **Direction** — re-derived from the statement's own sign/DR-CR convention, from the
  account's perspective (a card purchase is an outflow even though it grows a liability).
- **Transfer legs** — both legs mapped when the counterparty is one of the user's accounts.
- **FX pairing** — the same bank reference appearing in two currency sections is paired
  into ONE transfer.
- **Multi-currency routing** — each row routed to the institution-sibling wallet holding that
  row's currency; a row is never booked into an account of a different currency.
- **Categorization**, **payee resolution**, and **spend rules**.
- **external refs** — content-addressed (external_source + external_ref), so a re-import dedups to zero.
- **Duplicate matching** — every row matched against the ledger and its file-mates, banded.

You do not batch, and you do not deduplicate. Rows are returned uncapped; there is no
25-row limit to work around.

## The flow

**1. Preview.** Call `import_transactions` with `commit:false` and the `account_id`
(`list_accounts` to resolve it; if the account does not exist, propose creating it first).

**2. Read the verdicts.** The response carries:
- `statement_id` — you MUST pass this back on the commit.
- `rows[]` — each with `index`, `needs_eyes`, `duplicate`, `skip`/`skip_reason`,
  `account_id` (where it routed), and match `flags` carrying a `band` and an `existing_id`.
- `needs_eyes` (count), `reconcile` (the statement's own balance check),
  `ledger_check`, `routing`, `already_imported`.

**3. Report to the user BEFORE committing:**
```
Ready to import: X / Y rows
  Needs your eyes: N
  Likely duplicates: N
  Skipped by the parser: N (reason)
Routing: <currency> → <account>, ...
Balance check: ok | mismatch (<detail>) | not available
```
- If `reconcile` is **off**, say so plainly and ask the user to check the misread rows
  BEFORE committing — a bad parse is not something to book and fix later.
- List every `needs_eyes` and `duplicate` row (date, description, amount, index).
  Do NOT decide duplicates yourself; ask which to merge, skip, or create.
- If `already_imported` is set, say so — this file has been imported before.

**4. Commit.** Call again with `commit:true` and the SAME `statement_id`. Pass
`decisions` for the rows the user ruled on:
`[{row: <index>, action: create|merge|skip, existing_id?: <from the row's match flag>,
edits?: {category?, payee?, amount?, date?, direction?, account_id?}}]`
Omit `decisions` entirely to accept the preview as shown. Either way the commit reads the
CACHED parse — it never re-parses and never re-bills, and rows flagged `needs_eyes` or as
exact duplicates still default to skip.

**5. Report the outcome**, including the `batch_id` — that is what
`rollback_import_batch` needs if the user wants to undo it.

Never skip the report, even when everything succeeded.

## When a row genuinely cannot be parsed

In order: pass an explicit `mapping` (CSV/XLSX — it is remembered for this source);
teach the layout once with a `statement-format:` skill (below). Hand-entering rows with
`add_transactions` is the last resort, not the next step — and if you do, submit every row
and let the duplicate pre-flight decide.

## After the import

`verify_statement_balance` and `diff_statement` reconcile an ALREADY-imported statement
against the ledger. They are audits, not the parse path — reach for them when the user
disputes a balance, not as a routine step (the preview's `reconcile` already checked it).

## Teaching a statement format (statement skills)

A statement-format skill is an ORDINARY skill — create it with `create_skill`, list it with
`list_skills`, edit with `update_skill`, delete with `delete_skill`. The unified parser
automatically includes applicable skills' bodies as guidance when importing.

Workflow when the user wants to teach a format (or an import read a source badly):
1. Preview the example with `import_transactions` (commit:false) and CHECK THE GATE: the
   response's `reconcile` verdict. If status is "off", find out what was misread (compare
   preview rows against the statement with the user) BEFORE saving any skill — a skill must
   prove itself on its own example.
2. Compose the skill body from what you learned: which columns/sections mean what, the sign
   convention (charges vs credits), date order, rows to skip, plus 2–5 corrected example rows
   as worked input→output pairs. Keep it under ~3000 characters.
3. Save it with `create_skill`:
   - name: `statement-format-<bank-source>` (e.g. `statement-format-uob-visa`)
   - metadata: {"statement_format": "true", "account_id": "<account id, or omit for global>"}
   - body: the guidance text.
4. Confirm with the user, then re-run the preview — the skill's guidance now rides into the
   extraction, and every future import of this source uses it (skills outrank nothing
   deterministic: format MEMORY and the correctness guards still apply on every import).
"""


def ensure_statement_import_installed() -> bool:
    """Ensure the 'statement-import' built-in skill EXISTS and carries its body.

    Like skill-creator, this is a MANAGED builtin — always present and refreshed
    on every boot. It is read-only/non-deletable in the UI so re-seeding cannot
    fight a user deletion. Returns True if it created and/or updated the skill.
    Best-effort — never raises into the caller."""
    try:
        from backend.db.skill_store import SkillStore

        store = SkillStore()
        existing = store.find_by_id(SKILL_ID)
        if existing is None:
            store.create(
                {
                    "id": SKILL_ID,
                    "name": SKILL_ID,
                    "description": _DESCRIPTION,
                    "author": "builtin",
                    "body": _BODY,
                }
            )
            logger.info("seeded the statement-import built-in skill")
            return True
        # Refresh the canonical content fields when the managed bundle changes.
        # The description is what the model reads when deciding whether to auto-invoke this
        # skill, so a stale one ("call this BEFORE parsing any statement") keeps steering the
        # model after the body has been rewritten — refresh it
        # like any other managed field rather than only stamping it at seed time.
        changed = False
        if existing.get("body") != _BODY:
            store.update_skill(SKILL_ID, {"body": _BODY})
            changed = True
        if existing.get("description") != _DESCRIPTION:
            store.update_skill(SKILL_ID, {"description": _DESCRIPTION})
            changed = True
        if changed:
            logger.info("refreshed the statement-import built-in skill")
        return changed
    except Exception:
        logger.exception("ensure_statement_import_installed failed (non-fatal)")
        return False
