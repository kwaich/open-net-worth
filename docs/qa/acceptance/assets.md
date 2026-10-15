# Assets tab acceptance

Assets register, stepped create, section editing. IDs `AS-nn`. Redesign of 2026-09-26: PRD
`docs/prd/assets-create-edit-redesign.md`, UI spec `docs/assets-redesign/CREATE_EDIT_UI_SPEC.md`,
ADRs `2026-09-26-assets-stepped-create-and-section-editing` and
`2026-09-26-owned-asset-purchase-point-is-derived`.

### AS-01

- GIVEN the book has no owned assets
- WHEN I open `/assets`
- THEN the page shows its Assets header, Add asset action, summary, class cards, category chips, composition rail, and no review queue

### AS-02

- GIVEN owned assets exist
- WHEN the register renders
- THEN asset cards show category styling, privacy-aware value and gain, sorted by value after class cards

### AS-03

- GIVEN an asset has any valuation date
- WHEN its register card and detail render
- THEN neither surface shows review pips, a Needs a look queue, or an asset-review Automation

### AS-04

- GIVEN category and class chips are visible
- WHEN I select a chip and then All
- THEN the register filters to that category or class and All restores every card

### AS-05

- GIVEN Cash and Investments class cards are visible
- WHEN I click either card
- THEN the app navigates to Accounts or Investments respectively

### AS-06

- GIVEN the viewport is 640 px wide
- WHEN the register renders
- THEN its summary, chips, cards, rail and toast use the specified mobile layout without horizontal clipping

### AS-07

- GIVEN I click Add asset
- WHEN the flow opens
- THEN it starts at "What is it?" (Step 1 of 4) with the category tiles, "Create a category", and an "Or describe it to Lucius" link — no separate chooser screen — and the link opens the real dock prefilled "Add an asset: "

### AS-07A

- GIVEN a category chip is selected on the register
- WHEN I click Add asset
- THEN the flow opens at step 2 with that category chosen, and Back returns to step 1 to change it

### AS-08

- GIVEN I tap a category tile
- WHEN step 2 "Name and value" renders
- THEN it shows Name (a category-specific example) and Worth today with the currency symbol inside the field and thousands separators (a policy reads "Cash value today" with surrender-value help), and Continue with a blank name or a value of 0 shows the inline message without advancing

### AS-08A

- GIVEN step 3 "Is it all yours?"
- WHEN I choose Shared
- THEN Your share appears with an in-field % (default 50) and Shared with; property, vehicle and custom categories also show "Any loan against it?" where a loan already securing another asset is disabled and names that asset; a share outside 0–100 is refused inline

### AS-08B

- GIVEN step 3 for a property with no suitable loan
- WHEN I use "+ Add a loan" with a loan name and amount owed and then add the asset
- THEN the new loan shows as selected "(new)" before saving, is created in Accounts only when the asset is saved (owed, not an asset), is linked to the asset, and abandoning the flow instead creates nothing

### AS-09

- GIVEN step 4 "How much did you pay?" is optional
- WHEN I add an asset with a price and date, or with neither
- THEN the asset saves, a toast "{name} added" appears, the new asset's page opens, and the value history shows a "Bought" point at the purchase date only when both a price and a date were given

### AS-09A

- GIVEN the category is Insurance & endowments
- WHEN I reach step 4
- THEN it reads "How much have you paid in?" with "Premiums paid so far" and "Policy started on", and the saved policy shows no purchase point and no yearly return

### AS-09B

- GIVEN the server refuses a save (a loan already securing another asset, a future purchase date)
- WHEN I press Add asset (or double-click it)
- THEN the dialog stays open with every step's input intact, the server's own sentence appears next to the field it names, and at most one asset (and one loan) is created

### AS-09C

- GIVEN I have typed anything into the flow
- WHEN I press ✕, Escape or the backdrop
- THEN "Discard this asset?" asks first; with nothing entered on step 1 the flow simply closes

### AS-10

- GIVEN the category picker is open
- WHEN I create a custom category and use it for a new asset
- THEN the category persists, remains selected, and appears as a register chip after reload

### AS-11

- GIVEN I open an asset
- WHEN its page renders
- THEN it shows breadcrumb, editable thumbnail, title and description, a visible Update value (beside the ••• menu on desktop; one full-width button under the figures on a phone), "Your {n}% · {amount}" when jointly owned, Net equity captioned "Whole asset", a ••• menu with exactly Rename or change category · Add photo (or Change photo) · Sell or dispose · Delete asset; a photo, when there is one, shown big (the page's cover on a phone) and opening full size on tap, with Change photo and Remove photo; Value, Net equity and Unrealized at one size, the stepped chart, and Value history · Documents tabs

### AS-11A

- GIVEN the viewport is 390 px wide
- WHEN Value history renders
- THEN it is a two-line list per value (date and amount, then note and change) with every note readable — never wrapped one letter per line

### AS-12

- GIVEN the asset page's Ownership section
- WHEN I open its Edit and change the share, who with, or the loan
- THEN only share, share note and loan are saved (a toast "Ownership updated." appears), the section shows the loan's split as a bar with Equity and Owed rows (amount · %) or, with no loan on a property or vehicle, a **+ Link a loan** chip, and the value history is unchanged

### AS-13

- GIVEN the asset page's Details section
- WHEN it renders and I use its editor
- THEN the filled guided fields show as labelled rows, then Insured (only when it is; never for policies), Matures on (policies), Where it's kept (portable things) and the other details, all in one list with no subheading; each unfilled guided field is its own **+** chip in the record's chip row; saving persists them; no "Added" fact or "Added {Mon YYYY}" caption appears anywhere

### AS-13R

- GIVEN any asset page, on a phone or desktop
- WHEN it renders
- THEN the record is flat groups — Ownership and Purchase beside Details and Notes on desktop (each column its own height), one column when narrow — with no boxed cards; a group with nothing in it is not drawn (Ownership always is: your share); everything missing is ONE row of dashed **+** chips after the groups, each opening the editor its group's **Edit** opens; **+ Custom detail** is always there, after the category's own detail chips, and opens Details with a new, empty row whose label has the cursor — left empty, it saves nothing; a record with everything filled shows only **+ Custom detail**

### AS-13A

- GIVEN the asset page's Purchase section
- WHEN I add, change or clear the price and date
- THEN the purchase point moves or disappears in the history and chart, and a purchase date after the first recorded value is refused inline

### AS-13B

- GIVEN the asset page's Notes section
- WHEN I add or edit a note
- THEN it saves ("Note saved.") and shows in the section, or "Add a note" when empty

### AS-13C

- GIVEN ••• → Rename or change category
- WHEN I change the name, short description or category and save
- THEN they persist, and a category change keeps every detail (details the new category doesn't guide stay in the Details list, after its own)

### AS-14

- GIVEN asset detail is open
- WHEN I upload a thumbnail image
- THEN the photo appears in detail and on the card and survives reload

### AS-15

- GIVEN the Documents tab
- WHEN I add a PDF or image with "Add file", try an unsupported file, and remove a file
- THEN the file lists with its date and a persistent "Lucius can read" switch, the unsupported file shows the server's message, and Remove (after confirming) takes the file away

### AS-16

- GIVEN Update value is open
- WHEN I enter a new value, an "As of" date (default today, not in the future, not before the purchase) and a note, and save
- THEN a new dated value is added, earlier values remain, the page refreshes, and "Value updated." appears

### AS-16A

- GIVEN the value history
- WHEN I tap a value row, or the purchase row
- THEN a value row opens "Correct this value" prefilled (saving replaces that value) with a "Remove value" action that asks first and deletes it — disabled with "An asset needs at least one value." on the last one; the purchase row opens the Purchase editor; rows carry no separate pencil/trash buttons

### AS-17

- GIVEN any section editor (Ownership, Purchase, Details, Notes, Rename or change category)
- WHEN I save
- THEN only that section's fields change, the current value and every recorded value stay byte-for-byte unchanged, and a failed save keeps the editor open with the server's message

### AS-18

- GIVEN ••• → Delete asset
- WHEN I confirm or cancel
- THEN the confirmation reads "…If you sold it, use Sell or dispose instead — that keeps its history."; confirming deletes the asset and its history and returns to the register with a toast; cancelling changes nothing

### AS-19

- GIVEN Sell or dispose is open
- WHEN I record proceeds into a cash account with a Sold on date
- THEN the gain is shown, exactly one ledger transaction is posted on that date, the asset is archived, its loan link is released, a date before the purchase is refused inline, and retry cannot double-post

### AS-20

- GIVEN an asset has a partial ownership share and dated values
- WHEN net worth and history are calculated
- THEN current and historical values use the share-adjusted newest eligible value in the correct band

### AS-21

- GIVEN Lucius works with owned assets
- WHEN it lists, creates, edits, revalues, corrects or removes a value, disposes or deletes an asset
- THEN current tool schemas (including correct_asset_value and remove_asset_value) contain no review fields, writes confirm, and the Assets tab refetches

### AS-22

- GIVEN an `/assets?id=<id>` deep link
- WHEN I load it and navigate back
- THEN the matching detail opens and browser back returns to the register

### AS-23

- GIVEN a property owned 50%
- WHEN the register renders
- THEN the Asset Composition percentages come from the server and sum to 100, and the property's card reads "Property · Your 50%"

### AS-24

- GIVEN any share entry (create step 3 or the Ownership editor)
- WHEN I enter 150
- THEN it is refused with a message and nothing is saved — it is never silently stored as 100%

### AS-25

- GIVEN a register with 4 or more assets in one category (and fewer in another)
- WHEN the register renders
- THEN that category shows as ONE stacked card — its name, "N items", its total (your share, in the base currency), its gain over the items with a purchase price, and its three most valuable things by name with "+N more" — placed among the other cards by value; a category of fewer keeps a card per asset; tapping the stack (or its chip) opens the category as its own cards, most valuable first, nothing above them; the category is in the URL, so back (the gesture or the browser) or the All chip returns to All
