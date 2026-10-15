# Acceptance criteria — Source email snapshot (EM1)

A snapshot of the email content the email-transaction-logger agent read at fetch time,
surfaced in the transaction detail drawer's "Source email" section (between Provenance and
Activity), so the owner can cross-check a booking against its source. Snapshots are stashed
unreferenced at fetch time and promoted to referenced when a transaction actually books off
them (an `external_refs` entry `{"source": "email", "ref": <message_id>}` — ADR 2026-07-21);
an unreferenced snapshot is pruned beyond ~200.
Backend: `backend/db/email_snapshot_store.py`, `backend/services/email_snapshots.py`,
`GET /api/transactions/{txn_id}/source-emails`. Frontend:
`frontend/src/components/tabs/accounts/TxnDetail.tsx`.

Deliberately cut (do not test): mail-client deep links, attachments/MIME, claim-vs-evidence
highlighting, a Lucius re-verify tool, retention GC for promoted snapshots, any new modal/tab.

### EM1-01 — non-email transaction shows no section
- GIVEN I open the detail drawer for a transaction that was NOT booked from email (no
  `external_id`, or an `external_id` that doesn't start with `email:`)
- WHEN the drawer renders
- THEN there is no "Source email" section anywhere in the drawer (no header, no box, no
  loading/empty state — nothing renders for this transaction)

### EM1-02 — email transaction with a stashed snapshot renders the section
- GIVEN I open the detail drawer for a transaction booked from email whose source snapshot
  exists
- WHEN the drawer finishes loading
- THEN a "Source email" section appears BELOW the "Provenance" section (and above "Activity")
  showing real Subject / From / Received rows, and a collapsed "Show full email ▾" toggle

### EM1-03 — expanding the toggle reveals the full body
- GIVEN the "Source email" section is showing (collapsed) for an email transaction
- WHEN I click "Show full email ▾"
- THEN the button label flips to "Hide full email ▴", a scrollable plain-text body appears
  below it, the email's line breaks are preserved, and no part of the body renders as a
  clickable link

### EM1-04 — collapsing reverts
- GIVEN the full email body is expanded (EM1-03)
- WHEN I click "Hide full email ▴"
- THEN the body disappears and the button label reverts to "Show full email ▾"

### EM1-05 — email transaction with no stashed snapshot (404) shows the unavailable message
- GIVEN I open the detail drawer for a transaction booked from email whose source snapshot
  was never stashed (or has since been pruned) — the endpoint returns 404
- WHEN the drawer finishes loading
- THEN the "Source email" header appears with the verbatim line "Snapshot unavailable — this
  transaction was booked from email before snapshots were kept (or the original message is no
  longer stored)." and NO Subject/From/Received rows and NO show/hide toggle

### EM1-06 — in-flight fetch shows a loading skeleton
- GIVEN I open the detail drawer for an email transaction and the source-email fetch has not
  yet resolved
- WHEN the drawer renders during that window
- THEN the "Source email" section shows 3 skeleton rows (Subject/From/Received placeholders),
  no real data, no toggle

### EM1-07 — a non-404 failure shows the error state, and Retry re-issues the fetch
- GIVEN I open the detail drawer for an email transaction and the source-email request fails
  with a non-404 error (e.g. network failure, 500)
- WHEN the drawer finishes loading
- THEN the "Source email" section shows "Couldn't load the source email. " followed by a
  "Retry" button
- WHEN I click "Retry"
- THEN the section transitions back to the loading skeleton (EM1-06) and re-issues the fetch

### EM1-08 — switching transactions resets the disclosure and re-fetches
- GIVEN the drawer is open on email transaction A with "Show full email ▾" expanded
  (EM1-03)
- WHEN I close the drawer and open it on a DIFFERENT email transaction B
- THEN transaction B's "Source email" section starts COLLAPSED ("Show full email ▾") and
  shows B's OWN Subject/From/Received/body data, never A's stale data or expanded state

### EM1-09 — a body containing HTML-looking characters renders escaped, never as markup
- GIVEN an email transaction's stashed snapshot body_text contains raw HTML-looking text
  (e.g. `<a href="...">click</a>` or `<script>alert(1)</script>`)
- WHEN I expand "Show full email ▾"
- THEN the body renders as literal visible text (the angle brackets are visible characters)
  and the DOM contains NO `<a>` or `<script>` element produced from that content — it is never
  interpreted as markup

### EM1-10 — switching rows WITHOUT closing the drawer never lets a stale response win
- GIVEN the drawer is open on email transaction A and its source-email fetch is still in
  flight (slow network)
- WHEN I switch the drawer to a DIFFERENT email transaction B before A's fetch resolves, and
  B's own fetch resolves first
- THEN B's Subject/From/Received/body render normally; WHEN A's now-stale fetch resolves
  afterward, the drawer's displayed data stays B's — A's late response never overwrites it
  (this covers both the automatic re-fetch-on-switch path and a manual "Retry" click that
  outlives a subsequent row switch)

## Known limitations (v1 — deliberate, per EM1 scope)

- No deep link into a mail client, no attachment/MIME rendering.
- No highlighting of which part of the email backed which claim on the transaction.
- No Lucius tool to re-verify a booking against its source email.
- No retention GC for a PROMOTED (referenced=True) snapshot — it is kept forever once a
  transaction books off it, mirroring the artifacts precedent.
- The unreferenced-row prune bound (~200) is not directly observable from the UI and is not
  covered by this acceptance file — it's covered by the backend store test instead.


## Multi-source emails (ADR 2026-07-21 — one `external_refs` list)

A transaction may carry SEVERAL email provenance pairs (the merge/dedup-review flow absorbs a
second source email onto the canonical row). The drawer renders one repeatable
Subject/From/Received/full-body block per email. Vitest:
`frontend/src/components/tabs/accounts/TxnDetail.sourceEmail.test.tsx` ("multi-source-email
drawer" describe block); endpoint: `tests/test_email_snapshot_api.py::test_multi_email_txn_returns_every_snapshot_oldest_first`.

### EM2-01 — single-email case renders exactly as before
- GIVEN a transaction with exactly ONE email provenance pair and a stashed snapshot
- WHEN the drawer renders
- THEN the section header reads "Source email" (no count) and the block is visually identical
  to the pre-multi-email drawer (no added chrome, no divider)

### EM2-02 — multi-email header and blocks
- GIVEN a transaction whose `external_refs` carries N > 1 email pairs
- WHEN the drawer renders
- THEN the header reads "Source emails (N)" and N blocks render oldest-received-first with a
  thin divider between consecutive blocks (N−1 dividers)

### EM2-03 — per-email independent expansion
- GIVEN a multi-email section is showing
- WHEN I expand one email's "Show full email ▾"
- THEN only THAT email's body opens (its toggle flips to "Hide full email ▴"); the other
  emails stay collapsed, and each toggle operates independently (keyed by message id)

### EM2-04 — a pruned snapshot stays visible as an unavailable slot
- GIVEN one of the N email pairs has no surviving snapshot (never stashed / pruned)
- WHEN the drawer renders
- THEN that email's slot shows the verbatim "Snapshot unavailable — …" copy while the other
  emails render their full blocks — missing evidence is visible, never silently dropped
