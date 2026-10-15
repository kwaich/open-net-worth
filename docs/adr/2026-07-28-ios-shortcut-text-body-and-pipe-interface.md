---
title: iOS tap-to-log shortcuts send a text JSON body and take a pipe-separated input
status: Partially superseded by 2026-07-28-capture-endpoint
date: 2026-07-28
deciders: Claude, on owner direction ("let's build it for iOS")
---

# iOS tap-to-log shortcuts send a text JSON body and take a pipe-separated input

> **Superseded in part, same day, by
> [`2026-07-28-capture-endpoint`](2026-07-28-capture-endpoint.md).** Moving the shortcuts off
> `POST /api/transactions` and onto `POST /api/capture` removed the problems Decisions 2 and 3
> existed to solve:
>
> - **Decision 2 (pipe-separated input) is obsolete.** The shortcut no longer parses its input at
>   all — it forwards a sentence. There is no field order to get wrong.
> - **Decision 3 (omit `from_currency`) is moot.** The client sends no ledger fields whatsoever, so
>   there is nothing to omit. The underlying finding still holds and is worth keeping: a static
>   per-install currency is either a no-op or an FX bug, because `_normalise_leg` overwrites a
>   leg's currency with the account's own in every branch.
> - **Decision 1 (text body over a JSON dictionary field) still stands**, and its main
>   justification is actually stronger now: the body is flat, but a text template remains the form
>   this repo can validate offline with no device in the loop.
> - **Decision 4 (deterministic action ids) still stands**, unchanged.
>
> Kept rather than rewritten because the reasoning — especially the `TransactionCreate`
> `extra="ignore"` trap that silently drops a misnamed field — is why the capture endpoint exists.

## Context

`docs/prd/tap-to-log-transactions.md` scoped tap-to-log as "a recipe, not a feature": a
downloadable `.shortcut` plus docs, no code changes. Building the artifact surfaced three
constraints the PRD hadn't priced in, each of which forced a design choice that is **not** what a
reader would assume from the generated plist alone.

Two facts frame everything below: the generator (`scripts/shortcuts/`) runs in CI on Linux, and
**no device is available to test against**. Every choice here favours a construction that can be
*proved correct offline* over one that merely looks idiomatic in the Shortcuts editor.

## Decision 1 — the request body is text, not a Shortcuts JSON dictionary field

Shortcuts' *Get Contents of URL* has a native JSON body mode (`WFHTTPBodyType: "JSON"` +
`WFJSONValues`), which handles string escaping for you. We use `WFHTTPBodyType: "File"` with a
Text action's output instead.

**Why:** the payload's idempotency key is `external_refs: [{"source": …, "ref": …}]` — an array of
objects. The plist encoding for a nested array-of-dictionaries inside a `WFDictionaryFieldValue`
is not something we can verify without a device, and getting it subtly wrong yields a shortcut
that imports fine and posts a malformed body. A text template is unambiguous, and
`tests/test_ios_shortcuts.py` reconstructs it, `json.loads` it, and feeds it through the real
`TransactionCreate` model — a proof the dictionary-field route could not give us offline.

**The cost we accepted:** we now own escaping. A merchant named `Say "Cheese" Pizza` would
terminate the JSON string early, so the payee and card strings pass through a Replace Text action
that swaps `"` for `'` before interpolation. That is lossy by one character class, and it is a
deliberate trade against a 422 on a real purchase. Newlines in a merchant name remain unhandled.

**Revisit when** a device is available to confirm the nested-array encoding, or if the API grows a
flat idempotency field — either removes the reason for the text template.

## Decision 2 — the shortcut's interface is one `amount|payee|card` string

The shortcut does not read the Apple Pay trigger's variables directly. It takes a single text
input in a documented pipe-separated form and splits it.

**Why:** **a personal automation cannot be exported or imported** — only the shortcut it runs can.
This is an Apple limitation with no workaround in any app, so some manual setup is unavoidable.
The design question is therefore *how much* stays manual. Putting a text-formatting step in the
user's automation (`[Amount]|[Merchant]|[Card]`) keeps their manual work at two actions while all
the real logic — JSON, headers, date freezing, idempotency — lives in the shareable file.

**Alternatives weighed:** having the automation call the API directly (nothing shareable at all);
passing a dictionary as input (Run Shortcut's input is a single value, and dictionary passing is
another unverifiable encoding); one shortcut per card (multiplies the reinstall problem the task
set out to solve).

## Decision 3 — the payload omits `from_currency` entirely

The shortcut initially asked for a currency at install time and sent it. It no longer asks, and
sends no currency field at all.

**Why:** `_normalise_leg` (`services/ledger.py`) overwrites a leg's currency with the account's own
on every path — `cur = acc_cur` runs unconditionally once the account resolves. So a per-install
currency equal to the account's is a no-op. One that *differs* is worse than useless: it is read as
a foreign charge, captured into `original_amount`/`original_currency`, and the amount is
FX-converted into the account currency. A user with an SGD card who answered `USD` at install would
have had every single transaction silently multiplied by an FX rate.

The deeper point is that currency is a **per-transaction** fact and an install question is a
**static** one. No static answer can be right for a card that is sometimes used abroad, so the
field does not belong in the setup at all. Omitting it delegates to the account, which is the
only party that actually knows.

The confirmation notification now reads `from_currency` back off the response rather than echoing
the phone's assumption — the ping reflects what was booked, which is the same principle the PRD
argued for when it preferred a server-side `notify()` over a client-side confirmation.

**Consequence accepted:** a genuine foreign-currency purchase is booked at the account's currency
with no `original_*` capture, exactly as if it had been typed in without a currency. Recovering
that would need the trigger to expose the transaction's own currency, which it does not.

## Decision 4 — action UUIDs are deterministic

Built artifacts are committed under `docs/shortcuts/dist/` so users can download them from the
repo. Random per-build UUIDs would rewrite every byte of both files on every run.

Ids are derived from a fixed namespace plus a per-shortcut counter, and a test asserts the
committed files match the generator. A stale artifact is a broken download, and this is the
cheapest mechanism that makes drift a test failure rather than a user-facing surprise.

## Consequences

### Positive
- The payload is pinned against the live model. This immediately caught that the PRD's sketched
  body named three fields that do not exist (`external_id`, `note`, and a string `from_amount`) —
  and because `TransactionCreate` carries no `model_config`, Pydantic's default `extra="ignore"`
  would have accepted that body with a 200 and silently dropped the idempotency key.
- Users get a real download; reinstall onto a second device is an iCloud link away once configured.

### Negative
- Double quotes in merchant names are substituted, not preserved.
- The pipe interface is undocumented inside iOS itself — a user who edits their automation and
  changes the field order gets a wrong-looking transaction, not an error.
- **The files remain unverified on a device.** The tests prove structure and payload validity; they
  cannot prove the Shortcuts app accepts our plist. The docs page carries a build-by-hand recipe so
  the feature degrades to the PRD's original "recipe" scope rather than to nothing.

## Agent provenance

- **Decided by:** the implementing Claude agent, on the owner's "let's build it for iOS".
- **Lane / branch:** `claude/mobile-shortcuts-copy-reinstall-0bvd91`.
- **Alternatives weighed:** native JSON dictionary field vs. text body (chosen); direct trigger-
  variable access vs. pipe-separated input (chosen); a static install-time currency vs. omitting
  the field so the account decides (chosen); random vs. deterministic action ids (chosen).
- **Driving artifacts:** `docs/prd/tap-to-log-transactions.md`; `scripts/shortcuts/build.py`;
  `tests/test_ios_shortcuts.py`; `docs/shortcuts/ios-tap-to-log.md`.
- **Why this way (one line):** with no device in the loop, prefer the construction whose
  correctness a test can actually establish.
