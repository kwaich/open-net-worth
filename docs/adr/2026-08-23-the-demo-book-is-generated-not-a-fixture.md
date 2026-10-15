---
title: The demo book is GENERATED from the app's own write chokes, relative to today — not a checked-in JSON fixture
status: Accepted
date: 2026-08-23
deciders: owner (demo.opennetworth.ai must show real-looking data) + implementing Claude agent
---

# The demo book is generated, not a fixture

## Context

The public demo (`demo.opennetworth.ai`, `make demo` → `scripts/run_demo.sh`) was seeded from
`backend/data/examples/*.json` — sixteen hand-written fixture files copied into `DATA_DIR` by
`backend/scripts/seed_examples.py`. By 2026-08-23 that dataset was **broken against the schema it
was written for**, in ways nothing detected because nobody reconciles a demo against a bank:

- **Its categories could not exist.** Transactions carried `"food"`, `"transport"`,
  `"uncategorized"`. Category ids became opaque `cat_<12hex>` in the categories-as-data
  re-architecture, and the `uncategorized` sink was REMOVED. `create_transaction` **raises**
  `ValidationError` on an unknown category — the fixture only loaded at all because
  `seed_examples` `bulk_insert`ed rows verbatim, bypassing the write choke. The demo was showing
  rows the app itself would refuse to accept.
- **Its positions carried retired fields.** `"bucket": "liquid"` — replaced by `vesting_state`
  in ADR `2026-07-31-split-position-bucket`.
- **Half the file copies were dead.** The JSON→DB migration (Plans 1–4e + Wave 5) moved every
  store into the database. `seed_examples` grew a `_seed_*_db` function per wave to compensate
  (`instruments`, `money`, `planning`, `investments`, `watchlist`) while still copying all
  sixteen files, of which `profile.json`, `alerts.json`, `dashboard_layout.json`,
  `model_config.json` and `rules.json` were by then read by nothing. `rules.json` described the
  categorization-rules engine, retired in favour of Automations in 2026-08-02.
- **It was frozen in time.** Dates ran 2026-01-01 → 2026-03-15. Six months later a visitor to
  the demo saw a book that had stopped.
- **It was thin.** Five accounts, 33 transactions, three positions, no budget history, no
  vesting, no dividends, no goals.

The shape of the problem is the point: **a fixture cannot follow a schema.** Each of the five
migration waves that touched a store had to remember to patch the seeder, and the ones that only
changed a FIELD (opaque category ids, `bucket` → `vesting_state`) had no reason to think of it at
all. The demo is also the dataset with the least feedback: it is never reconciled, so a wrong
number in it survives indefinitely — and gets screenshotted.

## Decision

Delete the fixture set and **generate the book by calling the app**.

`backend/scripts/demo/` builds a complete two-and-a-half-year book — accounts, ~3,000
transactions, instruments with daily price history, lots, disposals, dividends, an RSU grant with
a live vesting ladder, envelopes, targets, 30 months of budget assignments, goals, commitments, a
watchlist and five dashboard views — and `backend/scripts/seed_demo.py` is its CLI.

Four properties are load-bearing:

1. **Every money fact goes through its own write choke** — `ledger.bulk_create_transactions`,
   `lots.apply_lot_change`, `income.apply_income_change`, `equity.apply_equity_change`,
   `planning.apply_budget_change`. A demo built by bypassing the write path can show a state the
   app cannot actually produce, which is exactly how the old one drifted. The corollary is that
   the builder **cannot** drift silently: when a choke's contract changes, the build fails.
2. **Dates are relative to the build date.** `_timeline.Timeline` is anchored on "today" and
   passed down; no generator reads `date.today()` on its own. The newest transaction is always
   yesterday, the next vest is always weeks away, and the current budget month is always the
   current month. A hosted demo restarted in six months shows six-months-from-now's book.
3. **It is deterministic, given `--offline`.** `(anchor date, seed)` fixes the entire book —
   per-generator RNG streams keyed by name, so adding a subscription does not reshuffle every
   grocery amount, and a defect found on the demo is reproducible with `--as-of --offline`.
   Online, one thing is deliberately not fixed: the generated price history is **anchored on
   today's real price per ticker**. These are real tickers on real price sources, so the app
   fetches live prices the moment it boots; a history ending on a GUESSED anchor meets that live
   price at a visible cliff (an early build walked Bitcoin to 96,500 on the Friday and the live
   fetch put it at 69,360 on the Sunday). The anchor is best-effort per symbol and falls back to
   the built-in one, so a host with no network still builds a complete, self-consistent book.
   Two ordering consequences: instrument ROWS must be created before the anchor lookup, because
   `prices._crypto_routing_with_currencies` resolves a coin's source by reading its instrument
   row and otherwise sends `bitcoin` to Yahoo as `$BITCOIN`; and the series is scaled so its
   LAST EMITTED close is the anchor, not its last calendar day — on a weekend build an equity's
   newest close is Friday's, which is exactly what a provider quotes as its current price.
4. **The budget is derived from the ledger it just wrote**, not typed beside it: each envelope's
   target is that envelope's own median month of real activity. A hand-typed target drifts the
   moment a spending generator changes, silently — which is how a Tax envelope ends up holding
   $34,000.

`POST /api/demo/seed` now calls the builder and **refuses (409) a book that already holds
accounts, transactions or positions**. This preserves the endpoint's original safety promise by a
different mechanism: the fixture version kept it by only filling absent/empty FILES, which has no
meaning once every store is a table. Force stays CLI-only.

### Two places the generated book deliberately differs from what the chokes leave behind

Both are follow-up actions a careful owner would take, done through ordinary write paths — not
special-casing:

- **A vest is settled against its own income credit.** `equity._vest_apply` credits the
  destination account with the full FMV (the taxable compensation event) *and* mints the
  FMV-basis lot. Both are true, but shipping it there shows the same value twice — once as
  settlement cash, once as shares. The builder books the offsetting `investment` row.
- **Dividend and vest income get categorized.** Both chokes write their cash leg with
  `category=None` by design (ADR `2026-07-12-options-tax-facts-not-verdicts`: the write path
  records facts, the report derives the verdict). Correct for the app, wrong for a demo — it
  leaves a five-figure "Unsorted" pile on the transactions screen.

A disposal is booked WITHOUT `proceeds_account_id`, settling as an ordinary `investment` row
instead. The choke's own proceeds path routes cash through a synthetic `Market (USD)` counterparty
account, which would put a negative-balance account named "Market" on the demo's Accounts screen.

## Consequences

- **`backend/data/examples/` and `backend/scripts/seed_examples.py` are DELETED**, along with
  `tests/test_seed_examples.py` and the fifteen `test_seed_examples_*` cases across
  `tests/db/` that asserted each migration wave had remembered to teach the seeder about its
  table. That whole class of test has no subject any more.
- **`make seed` now builds the demo book** rather than copying files, and refuses a non-empty
  book instead of silently filling gaps in one. The old "fills only absent/empty stores"
  behaviour is gone: it existed to make a fixture copy safe, and a generator that stacks a
  second book on top of a real one is not safe in any dosage.
- **`tests/test_demo_book.py` is the reconciliation the demo never had** — every leg resolves,
  no asset account is ever overdrawn on any end-of-day, the card stays inside its limit, the
  budget's Ready-to-Assign plus everything assigned equals the on-budget cash, positions agree
  with their lots, nothing is Unsorted. It asserts PROPERTIES, not figures, so tuning the book
  does not redden the suite. It builds once per module and restores a golden SQLite copy before
  each test, because the suite's autouse fixtures reset the profile and wipe Tier-2 tables
  against whatever engine is live.
- **The builder is slower than a file copy** — roughly 15 seconds for 3,000 transactions through
  the real chokes, against near-instant for `shutil.copy`. That is the price of the data being
  real, and it is paid once per demo boot.
- **`scripts/run_demo.sh` gains a hosted mode.** `ONW_DEMO_ROOT` keeps the book across restarts
  (cleanup refuses to delete an operator-chosen directory — only the `mktemp` one it minted
  itself); `--rebuild` throws it away and builds a new one, which is how you clear whatever
  visitors did. `ONW_DEMO_ANTHROPIC_API_KEY` is a deliberately DIFFERENT variable name from
  `ANTHROPIC_API_KEY`: the launcher runs the app under `env -i`, so a key reaches a public
  instance only when the operator names the opt-in variable — never by inheriting the shell's.
  `--rebuild` is the one destructive path in the script, so it refuses any root that is not
  recognisably a demo root (`/`, `$HOME`, the repo, or a directory holding anything but the
  three the launcher creates) — `ONW_DEMO_ROOT` is whatever the operator typed, and a rebuild
  that erased it on trust is one typo away from deleting a home directory.
- **`services.categories.ensure_builtins_seeded()` was extracted** from `main.py`'s lifespan.
  The builder writes a whole book without ever starting the app, and a fresh book has no
  categories at all; two copies of that seed would be two things to forget.
- **A note on the `Market` account.** Booking sale proceeds through it appears to reduce net
  worth by the proceeds (the position falls by X, the Market account falls by X, the destination
  rises by X). The demo now sidesteps that path rather than fixing it; whether it is a real
  defect in `lots._book_deposit_proceeds` is left open, and is worth its own look.
