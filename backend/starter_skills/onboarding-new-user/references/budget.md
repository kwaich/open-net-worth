# Setting up their budget (zero-based)

Read this before touching an envelope. The budget is built from the user's words onto the book that already exists, funded from the cash they have today, and it is not done until money is assigned.

Load the tools in one call: list_envelopes, create_envelope, rename_categories, delete_categories, assign_budget, get_budget_month.

## 1. Start from what is already there

A fresh book is NOT empty of envelopes: it carries a starter template — Rent/Mortgage, Phone & Internet, Utilities, Groceries, Medical expenses, Emergency fund, Subscriptions, Dining out, Entertainment, Vacation, Stuff I forgot to plan for. Call list_envelopes before creating anything.

For each thing the user names, map it onto an existing envelope by meaning before making a new one:

- Same thing, different word ("Food" for Groceries, "Going out" for Dining out, "Phone" for Phone & Internet): rename it to their word with rename_categories (a one-category envelope follows its category's name). Their words win.
- Genuinely missing ("Parents allowance"): create_envelope. It comes with a category of the same name, so spending counts against it at once — no separate category step, no "link categories" follow-up.
- Ambiguous ("Food" could be groceries, eating out or both): ask in one line which starter it is; never guess and never keep both.

Never leave "Food" beside "Groceries" or "Going out" beside "Dining out".

## 2. Offer once to clear the starters they did not mention

After the mapping, list the starter envelopes the user did not touch and ask once whether to remove them ("Your book came with a few starter envelopes you haven't mentioned — Utilities, Medical expenses, … Want me to clear them?"). On yes, one delete_categories call with a `deletions` entry per category: a starter category with no transactions goes together with its envelope. Report any it refused and leave those. On no, or no answer, keep them and never raise it again. Do not remove one the user named, or one that already holds money or transactions.

## 3. Assign real money, once, from cash on hand

An envelope with nothing assigned is a label, not a budget.

- Read get_budget_month: `ready_to_assign` is the cash they have today. That is what you can assign now. Their monthly pay is NOT cash yet — a plan sized to the pay that has not arrived is the wrong plan. Say it plainly: "you have S$2,150 now; the rest of the plan comes when your pay lands."
- Propose amounts only from figures the user has given you (rent, a bill, what they spend on food). Fixed bills first, then the envelopes they named, in a short list whose total is at most `ready_to_assign`. Where they gave no figure, say so and leave that envelope at zero until they do; never fill it with a typical amount. Leave anything undecided in Ready to Assign.
- Confirm the list once, then assign it all in ONE assign_budget call with `assignments`. One call covers the whole batch.
- When their pay is more than the cash now, offer to assign the remainder on payday: when it lands and they tell you, assign the rest then.

## 4. Day to day: card purchases and monthly bills

- A categorized card purchase moves its amount from the category's envelope into the card's payment envelope by itself, so the money for the card bill is already set aside. Never tell them to fund it again, or to cover it "from your next pay"; assigning it too would count it twice. Only an overspent category needs covering, and only then do you offer to move money into it.
- Before funding rent or any other monthly bill from the cash on hand, ask once whether this month's has already been paid. A bill that is paid has nothing left to fund, and assigning it again takes cash from the rest of the plan.

## 5. Done means assigned

Do not say the budget is "in place", "set up" or "ready" until get_budget_month shows money assigned. Check it after the card is approved and report the figures it returns: how much is assigned, what is left in Ready to Assign. If the card is still waiting, or they declined, say the envelopes exist but nothing is funded yet.

Then send them to [Plan](/plan?sub=budget) to fine-tune.

For 50/30/20, the same mapping applies; set the caps with the budget-review skill's steps.
