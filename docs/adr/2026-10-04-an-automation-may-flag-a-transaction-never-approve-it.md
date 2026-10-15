---
title: An Automation may flag a transaction for review, never approve it
status: Accepted
date: 2026-10-04
deciders: Repo owner, Claude
---

# An Automation may flag a transaction for review, never approve it

## Context

`automation` is not a trusted writer (`core.txn_sources.TRUSTED_WRITERS` is `{manual, feed}`), so
every row an Automation books is born `needs_review=true` and waits in the owner's review queue.
`update_transactions` accepts `needs_review=false` from any caller and its schema advertised it as
"false APPROVES".

On 2026-10-03 an email-logging Automation (`permission_mode=auto`) created a row and, 19 seconds
later in the same run, sent `update_transactions` with `needs_review=false` bundled into a payee
fix. The audit trail rendered it as "Approved — Lucius". Its prompt never asks for that; it says
"Everything you created is clean" and "Never defer a transaction to 'needs review'", which the
model read as licence to clear the flag. Across the book it is occasional (10 of 398
Automation-created rows, 9 runs) — a per-run model judgement, not a rule — which is why a prompt
edit cannot be the fix.

Nothing could stop it. The unattended-write guard is inert by decision (2026-08-10, "unattended
concept removed", `_confirm_gate.enforce_unattended_write`), and an Automation's approval mode is a
tool-permission question: **Skip all approvals** means Lucius never pauses.

## Decision

We will refuse any write that flips `needs_review` from true to false while the audit scope's
`surface` is `"automation"`, at the ledger update choke (`services.ledger.update_transaction`,
and up front in `bulk_update_transactions` so a batch refuses before any row is written), in
`services/review_approval.py`.

- **Whole row refused, never silently stripped.** The rest of the patch is not applied either; the
  agent is told to send it again without the flag. Consistent with "an unknown category RAISES,
  never coerces": a partial apply returned as success is read as full success.
- **The refusal is a message to the agent**, `ReviewApprovalRefusedError`
  (`review_approval_owner_only`, a 409-family `ConflictError`): what was refused, that the owner
  decides it from the review queue, that nothing in the call was applied, to resend without
  `needs_review`, and that leaving the row flagged is the correct end state, not a failure to fix.
  Without that last clause the run's own "everything is clean" checklist reads it as something to
  work around.
- **Only the transition.** Re-flagging is allowed; clearing a flag the row does not carry changes
  nothing and is allowed; a falsy value counts as a clear (the row builder coerces `None` to
  `False`).
- **Every approval mode, Skip included.** It is not a tool permission.
- **The confirm-card preview refuses too** (`_preview_one_update`), so a manual-approval run is
  never shown a card for an edit that can only fail once tapped.
- **Approving is its own operation, not a field on the generic edit.** The incident was a
  deliberate act bundled into a routine payee fix. `services.ledger.approve_transactions` and an
  `approve_transactions` agent tool (`ai/tools/review.py`) are now the way to clear the flag;
  `update_transactions` refuses `needs_review=false` on every entrance (single, batch row, legacy
  alias) with a message that names the tool, and still accepts `true` (re-flag). Inside an
  Automation the Automation reason is what the agent hears — pointing it at `approve_transactions`
  would send it to a second refusal.
- **In chat it is a gated write**: default `needs_approval`, so the owner confirms a card that
  lists each transaction (date · payee · amount, capped at 20 with a "+ N more" line), via the
  generic dispatch gate plus a preview adapter — no handler-side token protocol. Nothing left to
  approve returns its reason instead of a pointless card. The owner can set it `always_allow`;
  an Automation still cannot, because the refusal sits below permissions.
- **Rejecting stays `delete_transactions`.** It already records the rejection; no second name
  (tool names are final).
- **Model-facing text points at the right tool up front**: the `update_transactions` description
  and `needs_review` field, and `approve_transactions`' own description. The tool is non-core —
  discoverable, no per-turn cost.

## Consequences

### Positive

- The review queue means what it says: nothing an Automation wrote leaves it without the owner.
- The model gets an actionable message instead of a silent drop, and the next call succeeds.
- No path round it: the create tool does not forward `needs_review` (pinned by a test), and the
  bulk update path refuses atomically.

### Negative

- The scope is the whole Automation surface, which includes the owner's own conversation continued
  from a run's page. The owner cannot approve from there; they use Accounts → review or a normal
  chat. Distinguishing a human turn inside an automation session was not worth a second signal.
- A legitimately trusted Automation can no longer auto-approve its rows. There is no opt-in; add one
  only as an explicit per-Automation setting, never as an approval-mode side effect.
- A refused row costs the agent one extra tool call when the flag rode along with a real edit.
- "Fix these and approve" is now two calls with two cards (an edit, then an approve) — chosen over
  a combined call that would hide the approval inside an edit.
- One more tool and permission entry, plus a `needs_review` filter on `get_transactions` so the
  agent can list the queue it is asked to approve (all-time — the queue is not a month — unless
  dated). The REST API and the Accounts tab already had that filter; the agent read tool did not.
- Cost, measured against `HEAD`: +182 chars (~45 tokens) per interactive turn, from the two CORE
  tools this touches (`get_transactions`' new filter, `update_transactions`' reworded field); the
  new tool itself is non-core, so it costs nothing until loaded. The default-offer budget keeps
  280 chars of headroom — the first draft of the filter's wording broke it by 17 and was cut.

### Follow-ups

- `docs/product/lucius/automations.md` and `docs/product/features/transactions.md` state the rule.
- The Automation prompt wording that invited this ("Never defer to needs review") is the owner's to
  reword; the guard no longer depends on it.
- `test_tool_schema_budget.py` was already over budget and `create_artifact` over its description
  limit before this change; the new tool and wording add to the total and fix neither.
- **Open, found while checking the delete path:** an Automation's own `delete_transactions` writes
  the permanent rejection record (`rejected_identities`), so a corrected re-booking from the same
  email is refused as "a transaction you already deleted" — an agent's self-correction recorded as
  the owner's verdict. `delete_transactions` is an ordinary write in `auto`/`skip` (only
  `delete_transactions_where` and `apply_payee_rules` are hardline). Not changed here: it amends a
  documented invariant and needs its own decision.

## Alternatives considered

- **Edit the Automation's prompt ("never set needs_review")** — rejected as the only fix: advisory,
  per-Automation, and the model already ignores adjacent instructions case by case.
- **Key on `core.unattended.is_unattended()`** — rejected: it has exactly one sanctioned reader
  (`run_script`'s gate, pinned by `test_unattended_signal.py`) because the previous flag grew into a
  tool ceiling and refused every tool on every run. A second reader is that ceiling growing back.
- **Silently drop `needs_review` and apply the rest** — rejected: success-shaped partial writes are
  how a model reports "approved" in its summary. Refuse and say why.
- **Enforce only in the tool layer** — rejected: the choke is the one place every caller shares, and
  `bulk_update_transactions` would otherwise write earlier rows before refusing a later one.
- **Keep approve as `needs_review=false` on `update_transactions`, guarded only by the Automation
  rule** — rejected: a deliberate act bundled into a routine edit is how the incident happened,
  and a card of N "needs_review true → false" lines gives a person nothing they can sign off on.
- **A self-staging tool with its own token protocol** — rejected: the dispatch-level gate plus a
  preview adapter already yields a resolved, row-listing card with no handler-side plumbing.
- **Treat it as an approval-mode concern (a third "pauses even on auto" operation)** — rejected:
  Skip would still bypass it, and a card asking the owner to approve their own review queue from
  inside a run is the wrong surface for it.

## Agent provenance

- **Decided by:** orchestrating Claude agent, on the repo owner's instructions ("go with 1 … return
  it as an informative message to agent"; then, asked whether approve belongs in the generic edit
  or its own tool, "let's go" on a separate `approve_transactions`).
- **Lane / branch:** `main`, working tree (uncommitted at the time of writing).
- **Alternatives weighed:** prompt edit / `is_unattended()` / silent strip / tool-layer only /
  approval-mode card — see above.
- **Driving artifacts:** the audit trail of a 2026-10-03 run of the email-logging Automation (a
  `create`, then `needs_review` true→false 19 seconds later in the same run — no row data is
  reproduced here); `tests/test_automation_cannot_approve_review.py`.
- **Why this way (one line):** the review flag is the owner's decision, so the one place every write
  passes refuses it for an Automation and tells the agent exactly what to do instead.
