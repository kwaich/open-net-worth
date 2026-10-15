# Manual test — the ledger confirm gate

**Why by hand:** a browser agent can drive the app but is a poor judge of whether a card is
*right*. These four checks each hinge on reading a card carefully and comparing it to what
actually got stored — exactly the thing to do with your own eyes.

```bash
export ANTHROPIC_API_KEY=...     # or let `make qa-manual` read it from your model_config
make qa-manual                   # → http://localhost:8092
make qa-manual-stop              # when done
```

Data is a disposable clone. **Your live book at `~/.open-net-worth` is not touched**, so
anything you book here is throwaway — book freely.

---

## 1 · The card renders, and shows RESOLVED values

Open Lucius. Type — substituting a real asset account name from your book:

> `log a 13.80 expense for a Grab ride home from Meridian Bank`

**PASS** — a card appears with a confirm and a dismiss, showing:
- an amount of 13.80
- a **category label that is not blank**, with provenance next to it — a rule name, a payee
  default, "as specified", or "Uncategorized"
- the account as **"Meridian Bank"**, not `acc_1a2b3c`

**FAIL** if: Lucius just replies "booked!" with no card (the gate didn't fire) · the category
cell is empty · the account renders as a raw id.

> The category and the account name are the whole point. Both are decided *silently* inside
> `create_transaction` — the rules engine can override a category and you'd never see it. A
> card that just echoes your words back tells you nothing about what will be stored.

---

## 2 · Confirm stores EXACTLY what the card promised

From the card in test 1 — before tapping ✓, **write down the amount and category.**

Tap ✓. Then open the account register and find the new row.

**PASS** — one row, amount and category identical to what you wrote down.

**FAIL** if: more than one row appeared · any value differs from the card, however slightly.

> This is the security property. The ✓ re-calls the tool with a token that pops
> *server-stored* args — every other argument on the confirming call is discarded. A card
> showing $12.40 must never be able to commit $1,240, no matter what the model reconstructs.

---

## 3 · Dismiss writes NOTHING

Type:

> `log a 45.00 dinner expense from Meridian Bank`

When the card appears, tap **dismiss / ✗**. Then check the register.

**PASS** — no new row. Nothing at all.

**FAIL** — any row appeared.

---

## 4 · A bulk add is ONE card, not N

Type:

> `log three expenses from Meridian Bank: 12.00 coffee, 30.00 groceries, 8.50 bus fare`

**PASS** — a **single** card covering all three, with a total. Tap ✓ → exactly three rows.

**FAIL** if: three separate cards · the total doesn't match the rows · a different number of
rows than the card showed.

---

## Also worth a glance while you're in there

- **The Accounts page took 6+ seconds to load** in the automated run, long enough that the
  browser agent sat re-waiting on "Loading accounts…". See if that matches your experience.
- Try a **foreign-currency** expense (e.g. a VND amount) and check the card's base amount is
  sane. A bug where a 250,000 VND leg previewed as 250,000 base instead of 30.00 was found
  and fixed tonight; a second pair of eyes on it is worth having.

---

## If something fails

Note which test, what you saw vs. expected, and the exact text you typed. The backend log is
at `qa/artifacts/logs/manual-backend.log`.
