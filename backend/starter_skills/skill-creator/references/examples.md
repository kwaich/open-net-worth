# Examples

## A read-only skill — subscription-audit

- Purpose: find recurring charges and surface ones that grew or look forgotten.
- Tools referenced in the body: `get_transactions`, `get_spending_summary`, `get_payees` — these
  are ordinary tools, not skill permission metadata.
- Input: the date range requested in the current conversation, defaulting to six months.
- Description (pushy on purpose — see the makeover below).
- Body (numbered, imperative, with an exact output template):

  1. Call get_transactions for the requested date range, defaulting to the last six months.
  2. Call get_payees and group the charges by payee (aliases count as one payee).
  3. Flag payees with a same-amount monthly or yearly cadence as recurring.
  4. Compare each recurring charge's latest amount to its earliest; note any increase.
  5. Report in the base currency the tools return, using exactly this template:
     ## Recurring charges
     ## Grew since first seen
     ## Possibly forgotten (still charging, no recent related activity)

- Created with `create_skill` after the user approved the draft. No script
  needed — the tools already return the numbers.

## A skill with a bundled script — Subset-sum finder

- Purpose: given a list of amounts and a target, find any subset that sums to it.
- A bundled script is appropriate here: combinatorics is unreliable to do in-head.
- The Python (reads SKILL_INPUT, prints JSON; Bash or Node work the same way):
  ```python
  import json, os, itertools
  d = json.load(open(os.environ["SKILL_INPUT"]))
  amounts, target = d["amounts"], round(d["target"] * 100)
  cents = [round(a * 100) for a in amounts]
  hits = []
  for r in range(2, 6):
      for combo in itertools.combinations(range(len(cents)), r):
          if sum(cents[i] for i in combo) == target:
              hits.append([amounts[i] for i in combo])
  print(json.dumps({"matches": hits}))
  ```
- Created with `save_code_as_skill`. Saved DISABLED — tell the user to enable it in
  Settings → Skills (and code execution in Settings → Developer) before it can run.

## Description makeover — flat vs pushy

- Flat: "Audits the user's subscriptions."
- Pushy: "Audit recurring charges and subscriptions. Use whenever the user mentions
  subscriptions, recurring payments, 'what am I still paying for', forgotten free trials, or
  asks why a monthly bill grew — even if they never say the word audit."

Same skill — the second one actually triggers. The body never needs trigger text. If a long
Claude-specific trigger list would make the portable description unwieldy, put that extra routing
text in `when_to_use`.
