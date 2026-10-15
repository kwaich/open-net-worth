# Recording investments during onboarding

Read this before recording any position.

For each position:

1. Resolve the ticker or coin name to its real instrument FIRST. This pins the live price source and is essential for crypto. Confirm the resolved name back ("Apple Inc. — right?").
2. Find the venue's securities account BEFORE recording. Each broker or exchange has ONE securities account named after it ("IBKR") that holds ALL its positions, plus a separate cash wallet per currency ("IBKR USD", "IBKR SGD"), all with the same institution. Call `list_accounts` and reuse the securities account if the broker is already there (its `account_id`). If not, create it ONCE with `create_account` (type investment, name and institution = the broker) and keep its id; cash they hold there goes in a currency wallet (type investment, same institution, named "IBKR USD"), never on the securities account. Then pass the securities account's `account_id` to `add_positions` for EVERY holding there. Never pass the broker's name instead of the id (a name that matches nothing silently creates a second account, with a stray Starting Balance), never record a position on a cash wallet, and never "tidy up" by creating another account and moving positions.
   - PHYSICAL metal (gold, silver, bullion): the venue is the STORAGE LOCATION, not the metal — "Home safe", "Safe deposit box", "BullionVault". Treat it as a custody holding and REUSE ONE storage venue across all physical metals; never create "Physical Gold" and "Physical Silver" as separate venues.
3. Record the position: the resolved instrument, the quantity, the account_id above, and the cost basis if they know it — NEVER block on cost basis.
4. Offer to add more. Same instrument at several venues: resolve once, one position per venue (account).
