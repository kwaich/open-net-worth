# Acceptance — Settings › Permissions

Design of record: `.claude/rfcs/2026-07-20-approval-and-review-model.md` §15.4 (decisions are
visible and revocable), §16.1 (checkpoint-EXCLUDED), §13.3 (`email_send_message` is gated too), as
merged with the Connectors redesign's three-level ladder
(`.claude/rfcs/2026-08-10-connectors-architecture.md` §4/§10) by ADR
`2026-08-14-one-permission-store-for-every-tool`. Until 2026-08-14 this tab was backed by its
own binary store (`tool_grants`, `GET`/`DELETE /api/grants`); it now reads and writes the SAME
table the Connectors detail page's permission ladder does — a decision made either place shows
up here.

**Why this tab is load-bearing, not cosmetic:** permissions are checkpoint-EXCLUDED, so a
decision SURVIVES a checkpoint restore. This tab is the ONLY app-wide way to see and undo one —
the Connectors detail page only shows one connector's own rows. A permission system with no
visible state is one a user can only meet by being surprised — which is exactly how the
unattended-write envelope failed on 2026-07-20.

Surface: `GET /api/tool-permissions`, `PUT /api/tool-permissions/{tool}`,
`DELETE /api/tool-permissions/{tool}` (`backend/api/tool_permissions.py`);
`frontend/src/components/tabs/settings/sections/PermissionsSection.tsx`.

---

## PT-1 — The tab exists and is reachable

**GIVEN** the app is open
**WHEN** I navigate to Settings
**THEN** a "Permissions" section appears in the section list
**AND** selecting it renders the Tool permissions panel
**AND** the selection survives a reload (mirrored to `?sub=permissions` in the URL — nothing
is stored locally, so closing and reopening Settings lands at its top level again).

## PT-2 — Honest empty state

**GIVEN** no decisions have been made anywhere in the app (Settings or any connector's ladder)
**WHEN** I open Permissions
**THEN** I see "No permissions set yet"
**AND** body text stating that a read runs and a write asks by default, and that choosing
"Always allow" on a confirm prompt — or setting a level on a connector's own ladder — adds a
row here
**AND** NOT a blank panel.

Rationale: "nothing decided yet; here's what happens by default" beats a blank panel — the
empty state has to teach the model, because a user who has never hit a prompt has no other way
to learn what this screen is for.

## PT-3 — A decision lists with its tool name, owner and level

**GIVEN** a stored decision for `add_transactions` at level `always_allow`
**WHEN** I open Permissions
**THEN** a row shows the tool name **verbatim** (`add_transactions`, monospace) — the same
string the user saw on the confirm card, never a prettified label
**AND** a level badge reading "Always allow"
**AND** the last-updated date
**AND** a control to change or clear it.

**GIVEN** a decision at level `needs_approval`
**THEN** its badge reads "Needs approval".

**GIVEN** a decision at level `blocked`
**THEN** its badge reads "Blocked".

**GIVEN** a decision made through a CONNECTOR's own permission ladder (e.g. `plaid_fetch_transactions`
under owner `plaid`, or a tool under `mcp:<server_id>`)
**THEN** it appears in this list too, identified by its owner — this list is the WHOLE table,
not only the tools the confirm card ever staged.

Rationale: a decision is keyed on the canonical tool name AND its owner; showing anything else
would break the link between the prompt/ladder the user acted on and the row they are now
looking at. There is no "session" scope any more — every stored level is a standing decision
until cleared (the per-session grant tier this tab once also showed was removed on 2026-08-02,
ahead of this merge).

## PT-4 — Clearing a decision returns the tool to its default

**GIVEN** a row for `email_send_message`
**WHEN** I clear it
**THEN** the control shows a loading state
**AND** the row disappears without a full-page reload
**AND** `DELETE /api/tool-permissions/email_send_message` returned 200 (`{"cleared": "email_send_message"}`)
**AND** re-opening the tab does not show it (it is gone server-side, not just from local
state) — the tool returns to its UNDECIDED default, not to a stored `needs_approval`.

## PT-5 — A failed write is reported inline, not swallowed

**GIVEN** the clear/set request fails (server down / 404)
**WHEN** I act on a row
**THEN** an inline error appears under that row
**AND** the row stays showing its last-known-good level (no optimistic value left stranded)
**AND** other rows are unaffected.

## PT-6 — A decision for a renamed-away tool is shown, not hidden

**GIVEN** a stored decision whose tool name no longer resolves in the registry
**WHEN** I open Permissions
**THEN** the row still renders (`tool_exists: false` from the API)
**AND** carries "No longer available — this tool was renamed or removed, so this decision is
inert."
**AND** clearing it still works — the request carries the row's own `connector_id` as a query
parameter, since a stale name can no longer be resolved back to its owner.

Rationale: a decision does NOT survive a rename (RFC §15.1) and the user is silently asked
again. The RFC accepts that silence at the *prompt*; it does not require it *here*. Hiding the
row would leave a stored permission the user can neither see nor clear.

## PT-7 — Yolo mode is impossible to miss

**GIVEN** the reserved `(core, "*")` row is stored at level `always_allow`
**WHEN** I open Permissions
**THEN** a "Confirmation is off" panel renders ABOVE the list
**AND** it names this as a standing decision (not an environment variable — there is no env-var
path any more) and says every write runs without asking, whether or not it appears in the list
below
**AND** it offers a control to turn it back off, which clears that one row.

**GIVEN** that row is absent or not at `always_allow`
**THEN** no such panel renders.

Rationale: a mode that silently skips every confirmation must be visible somewhere, and this is
the somewhere. It renders above the list because while yolo is on, the list is not the whole
truth about what can happen without asking. Deliberately does NOT override a stored `blocked`
row on some other tool — yolo means "stop asking me," never "ignore what I refused."

## PT-8 — Loading and error states

**WHEN** the tab is opened and the request is in flight
**THEN** a "Loading permissions…" spinner renders (never an empty-state flash).

**WHEN** `GET /api/tool-permissions` fails
**THEN** an ErrorState renders with a working Retry
**AND** Retry re-issues the request.

## PT-9 — The level semantics are stated on screen

**GIVEN** at least one stored decision
**THEN** footer text states that a level covers the whole tool — every amount, every account,
every argument
**AND** that "Always allow" fires even when nobody is watching (an Automation, a headless run)
— there is no surface-dependent softening of it
**AND** that decisions are not reverted by restoring a checkpoint, so this list (and each
connector's own ladder) is the only place they change.

Rationale: §13.3b — a level has no bounds beyond the tool itself. That is intended and
understood, which means it must be *stated*, not left to be discovered.

---

## Verification status

**Not verified in this pass.** This file was updated to describe the merged permission model
(ADR `2026-08-14-one-permission-store-for-every-tool`) as a docs-only change; the backend
(`backend/api/tool_permissions.py`, `backend/services/tool_permissions.py`) and frontend
(`PermissionsSection.tsx` and its `api/toolPermissions.ts` client) were mid-flight in
concurrent lanes at the time of this edit. PT-1 … PT-9 must be re-run against the merged build
before this clears the UI Quality Gate: a backend REST/store test pass (CRUD, the yolo row, the
`tool_exists` join, the checkpoint exclusion) plus `make qa-e2e` / `make ui-gate` and screenshot
evidence, exactly as this file's prior verification pass required for the binary-grant version
it superseded.
