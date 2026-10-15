# 2026-07-19 — Account references resolve by NAME at the ledger write choke

**Status:** accepted (owner-directed, 2026-07-19)
**Where:** `backend/services/accounts.py` (NEW — the resolver),
`backend/services/ledger.py` (`_resolve_account_refs` at the create/bulk chokes,
`_missing_amount_message`, the self-transfer + `move_to` messages),
`backend/services/import_common.py` (`resolve_account`),
`backend/ai/tools/_shared.py` (`_validate_account_id`, `_account_currency`),
`backend/ai/tools/imports.py` (`_resolve_account`), `backend/ai/tools/statements.py`
(`_resolve_account_or_error`), `backend/ai/tools/_txn_schema.py` + `backend/ai/prompts.py`
(descriptions), `tests/test_account_ref_resolution.py`

## Context — this was measured, not inspected

A forensic pass over 289 real chat sessions (3,507 tool calls) found **account-reference
resolution to be ~20% of every error in the corpus** — the single largest recurring failure
class. It appeared on no code-audit list, because nothing about the code looks wrong when you
read it. Measured error templates:

```
account_id '<X>' not found. Call list_accounts to get valid account IDs.       7
account_id is required. Call list_accounts to get account IDs, then re-submit.  6
to_account_id '<X>' does not exist — call list_accounts to get valid IDs        6
from_account_id '<X>' does not exist ...                                        3
to_account_id is set but to_amount is missing for transfer transaction          3
from_account_id is set but from_amount is missing ...                           3
from_account_id and to_account_id must be different accounts                    2
No account with id '<X>' (import path)                                          2
```

`add_transactions` errored at 14% (19/132 calls); `update_transaction` at 8% (19/231),
dominated by leg-id failures. `list_accounts` was the 5th most-called tool — 169 calls,
1.23 MB of results — and **63 of those were byte-identical repeats inside a single session**:
the model paying repeatedly to re-learn ids it had already been given.

**The asymmetry was the bug.** `services.categories.resolve_category_ref` has long been wired
into the write choke so a caller-supplied LABEL resolves to the stored id. Accounts had no
equivalent, so a name-shaped account reference was a hard refusal.

## Decision

### 1. `services.accounts.resolve_account_ref(ref, *, rows=None) -> str | None`

Modelled on `resolve_category_ref`, including the `rows` pre-fetch escape hatch for bulk
callers. Matching ladder:

1. **exact id → itself** (fast path; an id is authoritative, no name scan at all)
2. **exact name, case-insensitive**, among NON-ARCHIVED accounts
3. **exact name, case-insensitive**, among archived accounts — reached only when no live
   account claims the name

Matching is EXACT after strip + casefold. **Never fuzzy.** Guessing that "DBS" means "DBS
Savings" rather than "DBS Multiplier" is not a call this layer gets to make silently.

### 2. Ambiguity RAISES — the one deliberate divergence from the category resolver

`resolve_category_ref` returns `None` on an ambiguous label and lets the caller treat it as
unknown. `resolve_account_ref` **raises `ValidationError` naming every candidate** (with the
fields that actually disambiguate them: currency, platform, archived status, and each id).

Why diverge: for a category, "unresolved" degrades to a rejection or to Unsorted — both safe.
For an account, returning `None` would surface as *"no such account"* for a name that in fact
matches two, sending the caller hunting for a typo that isn't there. And any future caller
tempted to "just pick the first" would be booking money against the wrong balance. **This is
a money app; a wrong account is a wrong balance.** Ambiguity is a distinct, actionable failure
and is reported as one.

### 3. Archived accounts match, but never shadow a live namesake

Archived accounts **do** resolve by name. `_validate_legs` has always accepted an archived
account BY ID (a closed account still owns its transaction history, and a correction against
it must stay possible), so refusing the same account by name would be an arbitrary asymmetry
between two ways of naming the same row.

An archived account never makes a live one ambiguous — step 2 wins outright. Archiving is an
explicit "this one is out of use" signal from the user, so resolving a bare name to the live
account is a *reading*, not a coin flip. Two LIVE namesakes, or two ARCHIVED ones with no live
claimant, are genuinely ambiguous and raise. When an archived account IS the resolved match,
that fact is logged, and it is spelled out in any ambiguity message so a surprising match is
explained rather than mysterious.

### 4. Resolution at the choke was NOT sufficient on its own

The tool-layer validators refused a name-shaped ref **before the choke ever saw it**, so
wiring the service alone would have been invisible. Both halves had to land together. Every
such validator, now delegating to the one resolver rather than reimplementing it:

| Validator | File:line (pre-change) | Path it gated |
|---|---|---|
| `_validate_account_id` | `backend/ai/tools/_shared.py:37` | `add_transactions` / `bulk_add_transactions` — the 14%-error tool |
| `_account_exists` → `_resolve_account` | `backend/ai/tools/imports.py:38`, refusal at `:177` | `import_transactions` |
| inline `find_by_id` gate | `backend/ai/tools/statements.py:475` | `diff_statement` |
| inline `find_by_id` gate | `backend/ai/tools/statements.py:294` | `verify_statement_balance` |
| private name-match loop | `backend/services/import_common.py:408` | structured OFX/QIF import |

`import_common.resolve_account` deserves separate mention: it was a **second, private
name-matching implementation** that returned whichever namesake it hit FIRST. A statement
naming an account two of yours share (the "IBKR USD" / "IBKR SGD" custodian shape) could
import a whole file's rows against an arbitrary one of them. It now delegates to the shared
resolver and refuses instead, with the ambiguity error translated into `ImportError_` so the
import surfaces keep their error contract.

Deliberately NOT changed: `backend/ai/tools/accounts.py` (account CRUD — `update_account` /
`delete_account` take an id to modify *the account itself*, a materially riskier surface than
a leg reference), `backend/api/ledger.py` (`/accounts/{account_id}` path params are ids by
contract; the UI never sends a name), and the best-effort display lookups in
`ai/tools/lots.py` / `ai/tools/portfolio.py` (not validators — they already degrade to `None`).

### 5. Ordering: resolution runs before `matching.check`

In `create_transaction`, `_resolve_account_refs(data)` runs **before** `matching.check` —
the L0 dedup key includes the account id, so a name resolved after it would hash to a
different row and slip the idempotency guard. Pinned by
`test_name_and_id_dedupe_to_one_row_via_external_id`.

### 6. ONE store identity per path — the resolver never opens its own store

`resolve_account_ref(rows=...)` is given the caller's OWN account rows at every wired site:
`services.ledger` passes its module-level `accounts` instance, and the three tool-layer sites
pass `svc.accounts`. The resolver only falls back to building a fresh `AccountStore()` when a
caller supplies nothing.

This was caught by the characterization suite rather than by reading. The first implementation
had the resolver read its own fresh store; `tests/test_char_tool_dispatch.py` monkeypatches
`svc.accounts.find_by_id` (the documented seam in `_shared.py`'s module docstring — handlers
read through this module *at call time* so patches are observed), and nine tests broke because
resolution could no longer see what validation saw. The failure was a test artifact only in
appearance: two independently constructed stores can genuinely disagree about which accounts
exist, and resolution feeds a leg-existence check that runs microseconds later. Each wired site
also keeps an **id fast path through its own store handle**, so a known id never depends on the
resolver's read at all.

The same round exposed a cost bug in the first cut: `_resolve_account_refs` read the whole
account store on EVERY write, including the overwhelmingly common one that already supplies
ids. It now checks each field with a cheap indexed `find_by_id` first
(`_unresolved_account_fields`) and reads the store **only when a non-id ref is actually
present** — returning the caller's dict untouched otherwise. `bulk_create_transactions` makes
the same call once for the whole batch. So an id-only book pays nothing for this feature, and
a store stub that implements only `find_by_id` (which is what several existing tests inject)
is never asked for `.all()`.

### 7. Two-leg SHAPE errors improved separately (not merged into resolution)

A different failure class: these are about shape, not resolution. Per the measured finding
that a next-action hint lifts recovery from 44% to 63%, `_missing_amount_message` now names
the exact field AND the currency it must be stated in — and when both legs share a currency,
states the value outright (*"Both legs are in SGD, so add to_amount=500 — the same value as
the other leg"*), because a transfer carries a per-leg amount only so the two legs can be
denominated differently. The self-transfer error names the offending account and points at
the two real fixes (name each currency wallet separately; or use expense/income for a fee on
one account) instead of restating the rule.

## Consequences

- A name works anywhere an id does on a ledger leg — REST, AI tools, and every import path
  inherit it from one choke.
- Most of the 63 byte-identical `list_accounts` repeats lose their reason to exist. The
  remaining legitimate reason (discovering what accounts exist) is preserved: the statement-
  parsing prompt still calls it ONCE, and now says explicitly not to call it again just to
  translate a name.
- Tool descriptions were updated only AFTER the code made them true.

## Known gap (pinned, deliberately not fixed here)

`_validate_legs` existence-checks BOTH legs of a transfer but **neither leg of an
expense/income/investment** — an unresolvable single-leg account books against a dangling id.
Resolution does not change this either way (an unresolved ref passes through untouched,
exactly as before), and today the refusal comes from the tool layer, which is where the
measured errors were raised. Tightening the choke itself is a behavioural change with its own
blast radius and belongs in its own change. Pinned as characterization by
`test_unknown_single_leg_is_not_refused_at_the_choke` so the gap is visible rather than
assumed absent.

## Agent provenance

Built by an agent lane on `harness/reliability-program`. The consequential calls the agent
made, none of which were pre-specified: (a) ambiguity **raises** rather than returning `None`,
diverging from the category resolver it was told to mirror — justified in §2; (b) archived
accounts match but lose to a live namesake (§3); (c) the tool-layer validators were found to
be a second refusal point that made the choke fix invisible on its own (§4) — this reshaped
the change from one-layer to two; (d) `import_common.resolve_account`'s silent first-match
pick was found and closed as a latent wrong-account bug; (e) one-store-identity-per-path
(§6), adopted after the characterization suite exposed the two-store hazard; (f) the
non-transfer existence gap was found, judged out of scope, and pinned rather than silently
fixed.
