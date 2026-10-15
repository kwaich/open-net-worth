---
title: An operator may read a stored connector secret; a model may not — one auth-gated endpoint, one eye, and a structural split
status: Accepted
date: 2026-08-14
deciders: owner (explicit instruction) + implementing Claude agent (reveal-stored-keys lane)
---

# An operator may read a stored connector secret; a model may not

## Context

Every connector credential was **write-only for its whole life**. A store's `public()` answered
`has_key` / `has_token` / `has_password` and never the value, the forms said "leave blank to keep
it" and "values are never shown", and the eye on a password field unmasked only what you had just
typed. Once saved, the key you pasted in was unreachable from anywhere in the product.

That is wrong for a self-hosted app whose operator owns the box, the database and the `.env`
file the key was probably seeded from. The value is sitting in plaintext in `secret_config`; the
only thing the write-only rule achieved was making the operator open SQLite to answer "which of
my two Tavily accounts is this?" or "what token is this bot running on?".

Three ad-hoc reveal endpoints had already grown in response, on three different routers with
three different shapes — `GET /api/email/config/reveal-password`,
`GET /api/channels/{name}/reveal-secret`, `GET /api/telegram/config/reveal-token` — each wired to
a **separate "Reveal saved …" button** sitting next to a masked field that already had an eye,
each swapping the input for a read-only echo row with its own Copy and Hide. Two controls for one
job, on three of thirteen credential fields; the other ten had nothing.

The owner's instruction: *"the eye should be there, that button should be gone."*

## Decision

### 1. One auth-gated endpoint for every family

`GET /api/connectors/{connector_id}/secret/{field}` → `{connector_id, field, value}`, with
`Cache-Control: no-store`. The three bespoke routes are **deleted**, not left alongside it.

Consolidating onto one route rather than extending the existing three was the choice with the
most weight behind it, and the reasoning is worth recording: there was no single route to unify
*onto*. The three covered 3 of 13 fields and disagreed on path shape, response key
(`imap_password` / `smtp_password` / `bot_token`) and 404 wording. Finishing the job their way
meant seven more bespoke routes across seven more routers — `plaid`, `yodlee` (×2), `alpaca`
(×2), `enable_banking`, `ibkr`, `web_search`, `mcp` (×2) — each with its own field-name response
key that the frontend would then need a per-provider adapter for. One route addressed by the
connector id the UI already holds is the same surface the permission ladder uses
(`/api/connectors/{id}/permissions`), and it makes "which fields are revealable" a single
allowlist instead of a property distributed across ten routers.

**Route order is load-bearing.** `connectors_api` owns `/api/connectors/{connector_id:path}` and
`:path` matches slashes, so this router is included BEFORE it in `backend/main.py`, exactly as
`connector_permissions` is. Pinned by a test, because the failure mode is a silent 404 on every
reveal.

### 2. A separate service module, not a wider `public()`

`backend/services/connector_secrets.py` is the ONLY module that reads a credential value out of a
connector store. `services/connectors.py`, `ConnectorView` and every store's `public()` are
**unchanged** — no flag, no `include_secrets=True`, no conditional field.

This is the direct lesson of ADR `2026-08-13-what-a-model-may-see-of-a-connector`. That leak
(`list_mcp_servers` returning a working DSN) was not caused by anyone deciding to show a model a
credential; it was caused by a projection built for the browser being reused for the model. A
field that is *usually* stripped is one forgotten call site from being a leak. So the split is
structural: a secret cannot appear in a tool result because the code path that produces one is
not reachable from `backend/ai` or `backend/mcp` at all.

**No shared serializer was widened, so none had to be split.** The reveal path reads each store's
raw `get()` directly and returns one string.

### 3. The boundary, tested rather than asserted

`tests/test_connector_secrets_model_isolation.py` seeds a unique sentinel into all fourteen
connector credentials and then sweeps the *whole serialized output* of every model-facing
surface — `list_mcp_servers`, `get_connector_permissions`, `set_connector_enabled`,
`connector_status`, and `ConnectorView` itself — for any of them. It sweeps for forbidden values
rather than asserting expected fields, so a new store column or a new tool that dumps a view
fails without anyone remembering to update an allowlist. A static AST pass pins that nothing
under `backend/ai` or `backend/mcp` imports the reveal service, and a control test asserts the
sentinels really are stored — without it, every sweep would be a test that cannot fail.

Verified by mutation: adding `out["token"] = config.token` to `McpClientStore._public_one` fails
the sweep.

### 4. Stored values only — but a first-boot env seed IS the stored value

Nothing in the reveal path reads `os.environ`. Each value comes from the store that owns it, via
that store's own `get()`.

On a fresh instance that config may itself be env-seeded, because every connector store's
documented contract is "env vars seed the row the first time it is created; after the first save
the row is authoritative". Revealing it is correct and useful — an operator who configured
Telegram with `TELEGRAM_BOT_TOKEN` sees the token their bot is actually running on, rather than a
dead eye above a form that says a token is set.

What reveal does NOT consult is `SearchConfigStore.provider_runtime_config`'s **permanent** env
fallback, which returns a value the store never adopted. **Accepted consequence:** a web-search
provider keyed only by env, on an instance whose `search_config` row already exists, reports
`has_key: true` and reveals nothing. That is the honest answer — there is no saved key — and the
field shows "Could not read the saved value" rather than pretending.

### 5. Only fields a form renders

The allowlist is the thirteen credential inputs plus the MCP `env` overlay. Machine-minted
session artifacts — Plaid's `access_token`, Yodlee's two session tokens, Enable Banking's
`session_id` — are deliberately absent even though they are credentials: no form shows them,
nobody can paste one anywhere useful, and the sync path refreshes them anyway. **Being sensitive
is not what makes a field revealable; being an input the operator typed is.** A test pins that a
stored-but-unlisted field 404s.

### 6. No rate limit — deliberate, with an audit trail instead

Rate limiting defends a surface where guessing pays. Login is capped at 5 failures / 15 min
because an attacker learns something by trying again. Nothing here is guessable: a caller either
presents a valid session/bearer or gets a 401, and a caller that has one can already read the
ledger, rewrite the config, and rotate these same credentials through endpoints that exist today.
A cap would add a real failure mode — a detail page revealing several fields, or an operator
comparing two keys, hitting a 429 — to buy nothing.

What ships instead is `logger.info("connector secret revealed: %s/%s", connector_id, field)` —
the EVENT, never the value — so reveals are visible in the log viewer. This mirrors
`api.models.reveal_provider_key`, which made the same call for model-provider keys.

### 7. The eye is the only control

`ui/useSecretReveal` puts the reveal on the eye that was already there: an empty box over a saved
value → the eye fetches it and shows it **editable in place**; anything typed → the eye is the
ordinary local mask toggle; hide an unedited reveal → back to blank, which is what every one of
these stores reads as "keep the saved value". The "Reveal saved …" buttons, their read-only echo
rows and their Copy/Hide pairs are deleted.

The hook is presentation-agnostic, which is what let the MCP edit form use it over raw
design-handoff `rd-input`/`rd-textarea` elements — including an env box that is never masked at
all, where the same state machine means "fill it with the saved set" / "empty it again".

**Looking is not editing.** A revealed-but-unedited field reports `pristine`, which the form folds
back to blank before its dirty comparison (`formSave.unrevealed`), so merely opening the eye does
not arm the modal's Save. Without it, every glance at a key would light up a write button.

## What this ADR deliberately does NOT change

- **Storage.** Values still live in `secret_config` / `mcp_clients`
  (`core.sensitive.SECRET_TABLES`), still checkpoint-EXCLUDED, still `NON_RESTORABLE_TABLES`, and
  `services.backup.write_sanitised_copy` still strips them from a downloaded archive. This is a
  read path for a logged-in operator, not a change to how secrets are kept or exported.
- **`/api/model-config/providers/{key}/reveal-key`** stays where it is. Model-provider keys are a
  Settings → Models resource, not a connector; folding them in would widen this lane for no gain.
  Its `ProviderRow` UI is where the one-input-one-eye pattern was first built, and this ADR
  generalises it rather than replacing it.
- **Every model-facing projection.** `redact_endpoint`, `sanitize_untrusted` and the "no
  `ConnectorView` from a tool" rule are untouched and remain exactly as strict.

## Consequences

- `GET /api/connectors` and the detail payload stay secret-free, which is the point: they are
  cached by the browser, logged by a proxy and screenshotted. A secret returned there would be
  exposed a hundred times to be read once.
- Thirteen credential fields plus the MCP env overlay gained a reveal; three gained one that
  works through the same control as the other ten instead of a bespoke button.
- Five `_LONG_DESC` connector descriptions no longer say "write-only". That phrasing was internal
  storage vocabulary leaking into user-facing text, where it read as a claim about the key's
  permissions at the vendor.
- An `.env`-only web-search key now shows an error on reveal where previously there was no reveal
  at all. Net new information, not a regression, but it is a visible "no" the operator did not
  see before.

## Agent provenance

Implemented in one lane on `fix/reveal-stored-keys`. The consolidation of the three pre-existing
reveal routes (rather than extending them) was raised by the coordinating agent as an open
question and is argued in §1 above. Two designs were rejected during implementation and are
recorded because the reasoning is not visible in the diff: a `public(include_secrets=True)` flag
(§2), and returning an env-only fallback value (§4). The model-isolation test was verified by
deliberately introducing a leak and confirming it fails.
