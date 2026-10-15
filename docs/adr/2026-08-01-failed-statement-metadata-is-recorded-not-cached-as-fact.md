# 2026-08-01 — A failed statement-metadata read is RECORDED as a failure, never cached as "this statement prints none"

**Status:** Accepted
**Owner:** the maintainer ("why are these not being shown as near cut off date or something?")
**Touches:** `backend/services/statements/canonical.py`, `backend/services/statements/tiers.py`,
`backend/services/statements/decisions.py`,
`backend/scripts/migrate_statement_meta_unavailable.py`,
`tests/test_statement_meta_unavailable.py`

## Context

The owner reported that ledger-only entries dated `07-04`, `07-04`, `07-05` were not showing the
near-cut-off chip on their July Meridian Bank eStatement.

The chip was the symptom. The **root cause is in the parser**, and the diagnosis is exact:

| Record | `period_start` | `period_end` | `statement_date` | `account_id` | `llm_calls` |
|---|---|---|---|---|---|
| `eStatement_Jun2026 2.pdf` | None | None | `2026-06-05` | `acc_088074960000` | 2 |
| `eStatement_Jul2026.pdf` | None | None | **None** | **None** | 1 |

July's `import_meta` is **entirely null** and carries one warning:
`"statement metadata unavailable (reconciliation unknown)"` — emitted from exactly one place,
`tiers.py`'s metadata `except`.

The text extraction was never the problem. Both PDFs print `Statement Date <Month> 05, 2026` at
**the same character offset (1,660)**, well inside the 5,000-char metadata head slice:

```
JUL  @1660  'Statement Date July 05, 2026 | Credit Limit $180,000.00 …'
JUN  @1660  'Statement Date June 05, 2026 | Credit Limit $180,000.00 …'
```

So: the metadata **LLM call failed**. `app.log` (2026-07-31) has the class of failure in the
clear — `metadata extraction failed — API Error: 529 Overloaded`. `extract_meta_from_text`
caught it, returned an empty `StatementMeta`, and that empty meta was **cached on the record as
fact**. The parse is never re-billed, so it was stuck there permanently.

The same file, same tier, is loudly correct about rows and silently wrong about metadata:

| | provider failure |
|---|---|
| row extraction (`tier2_text`) | **RAISES** `StatementParseError` — parse fails, nothing cached, user retries |
| metadata extraction | **swallowed** — empty meta, parse "succeeds", empty meta cached forever |

**A failure then read as a fact everywhere downstream**, because null-means-absent:

- `ledger_diff._window` found no period *and* no statement date, so it fell through to the
  rows-derived fallback — **the ledger-check window itself was wrong**, and that window decides
  which ledger entries are collected as "not on this statement" at all. This is the panel the
  owner was staring at.
- `_window_is_self_referential` was therefore true, `boundary_days` collapsed to `0`, and the
  near-cut-off chip could never fire — the reported symptom, and the least of it.
- `_suggest_account` got `account_last4s == []`, matched nothing, and the record bound to **no
  account**. Not a second bug: the same one.

Note the corrected fact: **Meridian Bank prints a Statement Date, not a period range.** June's own record
proves it. So a correct July parse yields `statement_date=2026-07-05`, `_window` takes its
statement-date branch (trailing month → `2026-06-03 … 2026-07-06` after slack), which is *not*
self-referential — `boundary_days = 3`, and the chip fires.

## Decision

**Fix the extraction. Change nothing about the boundary rule.**

### 1. Bounded retry (`_META_ATTEMPTS = 3`)

`complete()` does not retry transport/provider errors — it raises. Rows don't need a retry
because they fail loudly; **meta does**, because for meta a transient blip is the difference
between a correct record and a permanently wrong one. One bounded re-ask over an ≤8k-char slice
is cheap next to a whole statement stuck with no period, no account digits and no balances.

### 2. `StatementMeta.meta_unavailable: bool`

On exhaustion the failure is recorded **as a failure**. This is the distinction the app could not
previously make: a statement that genuinely prints no period and one whose header we never read
cached as the *same* all-null record. Under the flag, every null field is **UNKNOWN, not absent**.

### 3. `decisions._heal_cached_meta` — a flagged record repairs itself

The third heal in the `_heal_cached_last4s` family, and the only one that spends a call. It
re-extracts the document text **deterministically** from the retained file and re-runs the ONE
bounded metadata call. **The rows are never re-read** — a metadata failure says nothing about
them, and re-billing a 49-row statement to recover its header is the reason nobody would ever
repair one. It lands before `_suggest_account`, so the recovered printed digits also re-detect
the account the failure left unbound.

Gated on the **flag, never on emptiness**: a statement that legitimately prints no metadata must
not buy a call on every preview, forever. A heal that fails again leaves the flag up for next
time rather than downgrading "unknown" to "absent". The call is billed onto the file like the
hint batch and the Lucius pass.

### 4. Migrate the data, don't shim the code

Records written before the flag existed carry only the legacy warning string. Rather than teach
the heal to sniff for that string (a back-compat shim, and string-matching as control flow),
`migrate_statement_meta_unavailable.py` rewrites the old shape into the new one, once. Each
stamped record then repairs itself through the normal write path on its next preview.

Dry-run against a copy of a representative book: **81 records, exactly 1 to stamp** —
`stmt_2771fef20000 / eStatement_Jul2026.pdf`. Verified idempotent.

## The secondary question, answered: the boundary rule is UNCHANGED

`_BOUNDARY_DAYS` and `_window_is_self_referential` are untouched. The reasoning behind
`boundary_days = 0` on a rows-derived window remains correct *and* is not what was hurting the
owner — their window should never have been rows-derived in the first place.

**The `near_cutoff` ⟺ `boundary` equivalence therefore still holds by construction**, and
`test_near_cutoff_is_the_boundary_bucket_not_a_second_threshold` is untouched and still passing.
No pin was weakened or deleted.

Whether a rows-derived window should *ever* carry a boundary bucket for ledger entries is now
genuinely moot for this report, and is deliberately left open rather than settled speculatively:
once statements carry real periods again, the population that would exercise it is small and
un-evidenced. Revisit with a real case.

## Consequences

- A transient provider blip no longer permanently poisons a statement record.
- The owner's July record repairs itself on its next preview: real window, real account binding,
  real balances — and the chip appears as a side effect of the window being correct.
- One new bounded LLM call per preview of a *flagged* record, until it heals.
- `meta_unavailable` rides the wire on `meta`, so the AI tool surface sees the same field the UI
  does (the owner's standing server-side requirement).
