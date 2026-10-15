---
name: budget-review
description: "Use this skill to set or review the amounts on the user's budget envelopes against what they really spend — Zero-Based targets or 50/30/20 caps, in whichever method they budget with. Use it for 'review my budget', 'am I over budget?', 'adjust my targets', 'set my caps', 'use my targets as caps', or 'my budget doesn't fit any more', even when they never say 'envelope'. Not for tidying the books or covering this month's overspending (catch-up), whether a new cost fits (can-i-afford-it), or how last month went (monthly-report)."
---

Set or review the user's budget amounts. They live on envelopes, in the budget method the user
runs — the system prompt's Budget method line says which: Zero-Based targets, or 50/30/20 caps.

## Gotchas

- **An amount belongs to the envelope on its own row.** list_envelopes gives each envelope by
  name with its own target and cap; quote them as they come. A live chat once paired
  Zero-Based target amounts with envelope names by guesswork — a wrong number with the user's
  name on it.
- **The budget tools are not loaded by default.** That same chat never loaded them and guessed
  instead, so load them first (step 1).
- **copy_targets_to_caps leaves an envelope with no target alone** — it keeps its existing cap.
- **50/30/20 groups are the Zero-Based groups**, so a group share applies to the same groups the
  user already sees.
- Before any write, present the numbers and ask the user to approve them. Amounts are in the
  base currency the tools report.

## Steps

1. Load the budget tools in one call: list_envelopes, get_budget_view, apply_budget_operation, set_envelope_targets, get_spending_summary.
2. Call list_envelopes. Each row is one envelope by name, with its group, its linked categories, its Zero-Based target (zero_based_target) and, once 50/30/20 is set up, its cap (fifty_thirty_twenty).
3. For this month call get_budget_view (the home method's view); for a trend, get_spending_summary for each of the last 3 months.
4. To set amounts:
   - 50/30/20 caps from the Zero-Based targets: apply_budget_operation(engine_id="proportional", operation="copy_targets_to_caps", params={"month": "<this month, YYYY-MM>"}). One call.
   - Chosen 50/30/20 caps: apply_budget_operation(engine_id="proportional", operation="set_caps", params={"month": "<YYYY-MM>", "caps": {"<envelope_id>": <amount>}}).
   - 50/30/20 group shares: apply_budget_operation(engine_id="proportional", operation="set_group_targets", params={"targets": {"<group_id>": <percent>}}).
   - Zero-Based targets: set_envelope_targets.
5. When reviewing, compare each envelope's target or cap with what it actually spent. Call out chronic overruns, unused headroom, and a target that no longer fits (a finished one-off still set as monthly, a target on an archived envelope).
