# Acceptance criteria — Connectors as data sources (UI)

Interaction-level GIVEN/WHEN/THEN for the "connectors as data sources" UI lane
(`.claude/rfcs/connectors-as-data-sources-ui-spec.md`): the 8 provider config sections,
the Automations gallery's connector-sync templates, the Add/Edit Account modal's "Linked to"
list, the transaction drawer's generalized "Source" section, and the Add Account method
step's dead-end fix. The QA agent must exercise every criterion and report PASS/FAIL per ID.

---

### CDS-UI-1
- GIVEN a configured Plaid connector with `last_synced_at` set and `last_status: "ok"`
- WHEN its config form opens
- THEN it shows "Last fetched `<relative>` · up to date" and no "Sync now" button anywhere
  in the form.

### CDS-UI-2
- GIVEN any of the 8 provider config forms (SimpleFIN, Plaid, Lunch Money, Lunch Flow, YNAB,
  Enable Banking, Alpaca, Interactive Brokers)
- WHEN inspected
- THEN no poll-interval control or copy is present and no request to a `*/sync` endpoint
  fires from that form.

### CDS-UI-3
- GIVEN a provider config form in any state (configured, unconfigured, paused, erroring)
- WHEN "Automate with a template" is clicked
- THEN the app navigates to `/automations?template=sync_<provider>` and the automation
  editor opens prefilled with that provider's template.

### CDS-UI-4
- GIVEN a provider's config form is open and `GET /api/connectors/{id}/accounts` returns
  `status: "ok"` with 2 accounts
- WHEN the Accounts section renders
- THEN it lists 2 rows, each with a link control (an unlinked row shows a Select; a linked
  row shows "Linked to \"<name>\"" and an Unlink button).

### CDS-UI-5
- GIVEN an unlinked provider-account row
- WHEN an existing app account is chosen from its Select
- THEN `PUT /api/accounts/{id}` fires with `external_ids` appended (not replaced) and the
  row re-renders as "Linked to <name>" with an Unlink button.

### CDS-UI-6
- GIVEN an unlinked provider-account row
- WHEN "+ Create account…" is chosen
- THEN `AccountModal` opens on its quick-create step, prefilled with name and currency while
  retaining the provider's institution/last-four metadata for submission, and saving both
  creates the account (`POST /api/accounts`) and links it
  (`PUT /api/accounts/{id}`).

### CDS-UI-7
- GIVEN a linked provider-account row
- WHEN "Unlink" is clicked
- THEN `PUT /api/accounts/{id}` fires with that one `{source, ref}` entry removed and no
  others, and the row returns to its unlinked (Select) state — no confirm dialog appears.

### CDS-UI-8
- GIVEN `GET /api/connectors/{id}/accounts` returns a non-ok status (`not_configured`,
  `disabled`, `auth_error`, `rate_limited`, `connect_error`, or `error`)
- WHEN the Accounts section renders
- THEN it shows the mapped empty/error copy for that status (not a blank panel, not a raw
  status string), with a Retry button for the error-shaped statuses only.

### CDS-UI-9
- GIVEN the Automations gallery
- WHEN scrolled to the bottom
- THEN a "Sync a connector" section renders below "Start from a template" with 8 cards
  (SimpleFIN, Plaid, Lunch Money, Lunch Flow, YNAB, Enable Banking, Alpaca, Interactive
  Brokers) — no Yodlee card.

### CDS-UI-10
- GIVEN a click on any "Sync <Provider>" template card
- WHEN the editor opens and is submitted without changes
- THEN it creates an active automation whose trigger is `interval` at 21600s for banks/IBKR
  or 3600s for Alpaca, `execution.kind: "lucius"`, `permission_mode: "auto"`.

### CDS-UI-11
- GIVEN a direct visit to `/automations?template=sync_plaid`
- WHEN the page loads
- THEN the editor opens prefilled with the Plaid template with no click required, and the
  `template` search param is cleared from the URL afterward.

### CDS-UI-12
- GIVEN a visit to `/automations?template=unknown_key`
- WHEN the page loads
- THEN the gallery renders normally with the editor closed — no error, no console warning.

### CDS-UI-13
- GIVEN the Add/Edit Account modal open on an account with 1+ `external_ids` entries
- WHEN the Options section is viewed
- THEN a "Linked to" list renders each entry as "<Provider label> · <name> <mask>" with an
  Unlink button, and no "Connected" toggle/control exists anywhere in the modal.

### CDS-UI-14
- GIVEN the Add/Edit Account modal open on an account with no `external_ids`
- WHEN the Options section is viewed
- THEN it shows "Not linked to a connector." instead of a list.

### CDS-UI-15
- GIVEN the "Linked to" list showing at least one entry
- WHEN its Unlink button is clicked
- THEN `PUT /api/accounts/{id}` fires with that entry removed and the row disappears from
  the list without a page reload or the modal closing.

### CDS-UI-16
- GIVEN a transaction booked from a connector (an external ref whose source ≠ `email`)
- WHEN its drawer opens
- THEN a "Source" section renders with Provider / Provider account / Fetched rows and a
  "Show raw fields ▾" toggle — not "Source email".

### CDS-UI-17
- GIVEN a transaction booked from email
- WHEN its drawer opens
- THEN the "Source" section renders identically (DOM/classes/copy) to the pre-change
  "Source email" section.

### CDS-UI-18
- GIVEN a connector-sourced entry marked `unavailable: true`
- WHEN its drawer opens
- THEN the drawer shows "Snapshot unavailable — this transaction was booked before evidence
  was kept (or the original record is no longer stored)."

### CDS-UI-19
- GIVEN the Add Account modal's method step
- WHEN "Connect a bank or broker" is clicked
- THEN the app navigates to the Connectors tab and no toast appears.

### CDS-UI-20
- GIVEN the Connectors tab (a provider's Accounts section), the Automations gallery, and a
  connector-sourced transaction drawer
- WHEN viewed at 375px and at 1024/1440px, chat panel open AND closed
- THEN the Accounts list rows wrap label-above-control on phone with no horizontal overflow;
  the "Sync a connector" grid reflows like the existing template grid; the drawer's Source
  section scrolls with the drawer body.

### CDS-UI-21
- GIVEN a manually-tracked (unconnected) account is selected, its kebab menu open
- WHEN "Connect to bank" is clicked
- THEN the app navigates to the Connectors tab, no toast appears, no modal opens, and no
  `PUT /api/accounts/{id}` write fires (the old ConnectModal review-then-spinner flow, which
  wrote `connected: true` — a field the backend now silently drops — is gone).

### CDS-UI-22
- GIVEN a transaction with a connector or email source entry
- WHEN "Unlink source" is clicked
- THEN a POST to `/api/transactions/{id}/refs/detach` fires with that entry's `{source, ref}`,
  the entry disappears without a page reload, and the email branch is otherwise unchanged.
