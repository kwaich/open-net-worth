---
title: Split fetch_emails into search_messages + get_messages
status: Accepted
date: 2026-08-12
deciders: repo owner + implementing Claude agent
---

# Split fetch_emails into search_messages + get_messages

## Context

`fetch_emails` fused mailbox SEARCH and body HYDRATE into one call: `imap_client.fetch_messages`
downloads and MIME-parses the full RFC822 body of every UID the `UID SEARCH` matched (bounded by
`limit`, per the earlier 2026-08-08 "bound IMAP body fetches" fix), then hands every body to the
model — whether or not the model ever reads it.

Measured against this instance's real `tool_call_log`/`sessions` data (2026-08-10/11 runs): a
45-message fetch took ~28s and returned 140,577 characters, of which 130,787 were bodies; only 8
of the 45 were ever used for `add_transactions`, whose bodies totaled 8,783 characters. ~24 of the
28s were spent downloading/parsing bodies after `UID SEARCH` returned. Google's own Gmail MCP
server (`search_threads`/`get_thread`/`get_message`) draws the same line: search returns bounded
summaries with no body; a body is a separate, explicit fetch.

The owner asked for this split directly ("i think we need to split between search_threads vs
get_thread vs get_message... atm it's too slow and wasting a lot of money") and, after discussing
message-vs-thread semantics, converged on message-level tools only (thread-level deferred).

Two invariants constrain the design:
- `core/email_ids.py` (ADR 2026-07-20): the model is NEVER shown or made to handle a raw RFC 5322
  `Message-ID` — only the short internal id (`internal_email_id`, sha256-derived). A real
  double-book incident traced to the model echoing a raw header inconsistently.
- `backend/ai/assistant.py`'s dispatch loop runs sibling `tool_use` blocks in one model turn
  SEQUENTIALLY (a plain `for` loop, each `await`ed) — so N singular per-message tool calls cost N
  round trips, including N separate IMAP connections if each opened its own.

## Decision

We will add `search_messages` (header-only: from/subject/date, no body, bounded
`showing`/`total`/`more`) and `get_messages` (`message_ids: List[str]`, up to 25/call, ALL fetched
over ONE IMAP connection) as the new RECEIVE pair, and **retire `fetch_emails` outright** — no
alias, removed from the registry, the seeded `email-transaction-logger` skill migrated to the new
pair. A softer, keep-both-registered version shipped first; the owner overrode it directly ("the
reason we built new tools is to fucking replace the old tool") on the grounds that leaving the old,
expensive tool reachable defeats the point of the change — the model can still reach for it out of
habit and burn the same cost. See Alternatives for what the softer version would have looked like
and why it was rejected once actually weighed against that.

`search_messages` fetches `BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)]` per candidate
UID instead of `(RFC822)` — never downloading a body — then stashes a body-less row per message
into the EXISTING `email_snapshots` store (`services/email_snapshots.stash_snapshots`, extended to
tolerate a missing `body_text`), keyed by the same internal id `fetch_emails` used to use. That
row's existing `raw_message_id` field is what lets `get_messages` translate an internal id back
into something searchable, without ever putting the raw value in front of the model:
`get_messages` looks up `email_snapshots.locate(message_id)`, then
`imap_client.fetch_by_message_ids` does `UID SEARCH HEADER "Message-ID" <raw>` → `UID FETCH
(RFC822)` for each, over one connection, and `services/email_snapshots.stash_or_update` fills in
the body (inserting fresh if `search_messages` never ran for that id).

**Batched header FETCH (live-verified fix, same day):** the first version of `search_headers`
issued one `UID FETCH` per candidate message — still N round trips, just smaller ones. Live
against the owner's Gmail account, a 50-message `search_messages` call took 37-57s and hit
the pre-existing 30s per-read socket timeout mid-scan; the round-trip COUNT, not the header/body
distinction, was the actual latency driver. Fixed by batching the UID set into one `UID FETCH
<uid1,uid2,...> (UID BODY.PEEK[HEADER.FIELDS (...)])` command (chunked at 200 — `search_messages`'
own hard cap on `limit`, well under any real IMAP command-line limit), cutting a 50-message search
to one round trip. `fetch_by_message_ids` (get_messages) was left un-batched: its ≤25-id cap and
one-shot nature make the added complexity of an OR-chained multi-id SEARCH not obviously worth it
yet — flagged as a follow-up if live evidence says otherwise.

## Consequences

### Positive
- A search over a wide date window costs one cheap header fetch per candidate instead of a full
  MIME download+parse; the model only pays the expensive fetch for messages it actually selects.
- No new store/table/migration — `email_snapshots` already had the right key (internal id) and
  the right field (`raw_message_id`); only its write paths gained a body-less mode and a
  merge-update. `PayloadTableStore`'s JSON `payload` column means no Alembic migration either.
- `get_messages` batches in one IMAP connection, and `search_messages` batches its header FETCH
  into one IMAP command, so the sequential-dispatch cost the assistant loop imposes is one round
  trip instead of N for a normal call.
- One RECEIVE surface, not two — the model cannot accidentally reach for the expensive path out of
  habit, which was the whole point of the change (the owner's explicit call once the softer
  keep-both version was on the table).
- The double-book-prevention identity scheme (`core/email_ids.py`) is untouched — `search_messages`
  and `get_messages` show/handle the same short internal id `fetch_emails` used to.

### Negative
- `get_messages` always relocates via `UID SEARCH HEADER Message-ID` rather than caching a direct
  UID — an extra IMAP round trip per message versus a hypothetical UID-cache fast path, traded
  deliberately for not having to reason about UID/UIDVALIDITY staleness (see Alternatives).
- A message with NO `Message-ID` header (rare, legacy/malformed mail) forces `search_messages` to
  upgrade to a full RFC822 fetch for just that message, so its identity stays a content hash of
  the same bytes any other path would see — this narrows but does not eliminate the cost win for
  that rare case.
- No tool now covers "dump every full body in a window at once" — a genuinely deliberate bulk use
  case (e.g. a one-off audit) has to do `search_messages` then `get_messages` in batches of ≤25
  instead of one call. Judged an acceptable cost: the common case dominates real usage (71 sessions
  of `fetch_emails` telemetry, only a handful looking like a deliberate full-window dump), and nothing
  else in the codebase called `fetch_emails` after the skill migration.

### Follow-ups
- If a genuine "dump every body in a window" need shows up in practice, it can be served by
  `search_messages` + repeated `get_messages` batches from the CALLER side — no new tool needed
  unless that proves to be materially worse than a dedicated one.
- `fetch_by_message_ids` (get_messages) is still one `UID SEARCH` + one `UID FETCH` per requested
  id — un-batched. Revisit (OR-chained multi-id SEARCH, matched back to requests by parsing each
  fetched body's own Message-ID header) if live evidence shows it's a real bottleneck, mirroring
  the `search_headers` batching fix above.
- `get_thread`/thread-level tools were explicitly deferred — revisit if a conversational-mail use
  case shows up.

## Alternatives considered

- **Cache a direct UID/folder/UIDVALIDITY locator at search time, fast-path `UID FETCH` on a
  repeat get_messages** — rejected for v1: adds a staleness-detection dimension (UID reuse,
  UIDVALIDITY changes, folder renames) for a small win, since `get_messages` batches are small
  (≤25) and not a hot loop; the always-correct Message-ID-header search is simpler and was chosen
  instead. Left as an optimization if batch call volume ever makes it worth the complexity.
- **A new dedicated locator-cache store/table** — rejected: `email_snapshots` already carries
  `raw_message_id` keyed by the same internal id every consumer needs; a second store duplicating
  that key namespace would be an unjustified abstraction for what turned out to be a two-method
  addition (`locate`, `stash_or_update`) to the existing one.
- **Singular `get_message(message_id)`, mirroring Gmail's MCP naming** — rejected: the assistant's
  sequential tool-dispatch loop makes N singular calls cost N IMAP connections; a batched
  `get_messages(message_ids: [...])` collapses that to one. Also matches this repo's own
  entity-set-arity naming rule (`tool_inventory.py --lint`): an array-of-entities input requires a
  plural tool name.
- **Soft-deprecate `fetch_emails` (keep it registered, steer via description + skill migration)**
  — the FIRST version shipped, on the reasoning that `fetch_emails` was actively used (71 real
  sessions in this instance's own telemetry) and had a legitimate bulk-dump use case, so an
  immediate breaking removal seemed like the higher-risk move. REJECTED once actually weighed
  against the alternative and put to the owner directly: leaving the expensive tool reachable
  undermines the entire point of the split — the model can still pick it, by habit or for an
  ill-fitting case, and burn the same cost the change exists to stop. The 71-session number also
  doesn't distinguish routine use (now served by the new pair) from genuine bulk-dump use (rare,
  and the migrated skill was the only first-party caller anyway) — see the Negative/Follow-ups
  above for what a caller with a real bulk need does instead.
- **Snippet/preview text in `search_messages`** — rejected: IMAP has no server-side snippet
  equivalent to Gmail's API; a partial `BODY[TEXT]<0,N>` peek returns raw MIME boilerplate for
  multipart/HTML mail (unreliable), and computing a real plain-text snippet requires the same MIME
  parse the split exists to avoid. `search_messages` returns from/subject/date only.
- **A smaller header-FETCH batch chunk (e.g. 25) as a defensive bound** — rejected once the actual
  constraints were checked: IMAP command-line length and response size are both non-issues at
  `search_messages`' own 200-message cap (~1.5KB request, ~40KB response), so a smaller chunk
  would only be trading round trips for nothing. The one real cost of a bigger chunk — coarser
  `should_stop()` cancellation granularity — was judged worth it; chunk size matches the 200 cap so
  a normal call is one round trip.

## Agent provenance

- **Decided by:** implementing Claude agent, in direct conversation with the repo owner
- **Lane / branch:** `main` (direct working-tree change, not a worktree lane)
- **Alternatives weighed:** UID-locator fast-path cache vs. always-correct Message-ID-header
  search; a new locator store vs. extending `email_snapshots`; singular vs. batched `get_messages`
  naming; hard-retire vs. soft-deprecate `fetch_emails`; header-only search with vs. without a
  snippet field
- **Driving artifacts:** owner's live-session request quoting Google's Gmail MCP
  (`search_threads`/`get_thread`/`get_message`) contract; this instance's own `tool_call_log`/
  `sessions` telemetry (45-message fetch, 28s, 140,577 chars, 8 messages actually booked);
  `core/email_ids.py`'s ADR 2026-07-20 identity design; the 2026-08-08 "bound IMAP body fetches"
  spec/plan (`docs/superpowers/specs/`, `docs/superpowers/plans/`), which bounded fetch COUNT but
  never split search from hydrate; the owner's explicit override of the initial soft-deprecation
  choice ("the reason we built new tools is to fucking replace the old tool"); a live app.log
  repro of `search_messages` hitting the 30s socket timeout on the owner's Gmail account (37-57s for a
  50-message one-UID-at-a-time scan), which drove the header-FETCH batching fix
- **Why this way (one line):** reuse the existing `email_snapshots` store's key and
  `raw_message_id` field instead of inventing new state, retire `fetch_emails` outright rather than
  leave a cheaper-but-still-reachable escape hatch that would undercut the whole point of the
  change, and batch the header FETCH into one IMAP round trip once live evidence showed round-trip
  count — not the header/body distinction — was the actual latency driver.
