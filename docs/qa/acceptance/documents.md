# Acceptance — Documents (Lucius tab)

Design: ADR `2026-09-26-vault-is-a-view-over-existing-stores` (the feature ships as Documents;
its code keeps the internal name, the Vault); product doc `docs/product/lucius/documents.md`. Verify on an isolated instance (ports 8090–8099, synthetic data),
never `:5174`/`:8080`.

## DOCS-OPEN — entry points

- **DOCS-OPEN-DESKTOP** GIVEN the Lucius tab on desktop WHEN I click **Documents** (top right)
  THEN Documents opens in the right-hand panel beside the chat, the button reads as pressed, and
  the chat's first message is not covered by the button.
- **DOCS-OPEN-PHONE** GIVEN the Lucius tab on a phone WHEN I tap the folder-lock icon in the
  header THEN Documents slides up as a tall sheet over the (dimmed) chat; its ×, a swipe down or
  a tap on the chat closes it; dialogs opened inside it (Rename, the PDF reader, a photo) stack
  over it. WHEN I pick **Ask Lucius about this** from a row's long-press menu THEN the sheet
  closes and the chip is in the composer.
- **DOCS-ITEM-LAYOUT** GIVEN any item's page (phone or desktop) THEN under its preview is ONE row
  of equal action tiles — **Ask Lucius** (Lucius purple, absent when Private), the kind's link
  (**Transaction** / **Account** / **Asset**), **Download**, all the same shape and shadow — then
  flat lists under their titles, with no box and no dividers: the **Note** (only once written),
  **Details**, a file's **Settings** (Year, Private) and **Manage** (**Rename**, **Add a
  note** / **Edit note**, then the removals; a deletion in red). No boxed or rounded
  rows; nothing wraps onto a second line.
- **DOCS-SWIPE-BACK** GIVEN the installed phone app on an item's page WHEN I swipe from the left
  edge THEN the page follows my finger with the list showing behind it and, past about a third
  of the width (or on a flick), returns to the list — at the scroll position I left; a short slow
  pull springs back; a vertical scroll never counts.
- **DOCS-SAFE-AREA** GIVEN an iPhone with a home bar WHEN an item's page is open (at rest or
  scrolled to its end) THEN none of its buttons sits in the home-bar zone; the list may scroll
  under it but its last row can be scrolled clear.
- **DOCS-KEYBOARD** GIVEN a phone WHEN a field takes focus — the search, **Rename**'s name, the
  note, a file's **Year** — THEN the sheet or dialog holding it sits above the keyboard, the field and
  its buttons in view (iOS never shrinks the page for the keyboard; phone sheets follow the
  visible viewport).
- **DOCS-TOGGLE** GIVEN Documents is open on desktop WHEN I click **Documents** again THEN it closes;
  GIVEN an artifact is open in that panel WHEN I click **Documents** THEN the artifact closes and
  Documents shows.
- **DOCS-REFRESH** GIVEN Documents is open (on desktop or a phone) on an item WHEN I refresh the
  page THEN Documents is open again on that same item; closing it and refreshing keeps it closed.

## DOCS-LIST — what is in it

- **DOCS-LIST-KINDS** GIVEN a book with a kept file, an imported statement, a receipt and a saved
  artifact WHEN Documents opens THEN all four are listed, newest first, each with a kind icon and a
  second line (type + year, statement size, receipt date, "Saved from chat").
- **DOCS-LIST-FILTER** GIVEN the list WHEN I click **Statements** THEN only statements show and the
  chip counts match the list; **All** restores everything.
- **DOCS-LIST-SEARCH** GIVEN the list WHEN I type part of a title, file name or account name THEN
  only matching items show; a query with no match shows "Nothing matches".
- **DOCS-LIST-EMPTY** GIVEN an empty book WHEN Documents opens THEN it says "No documents yet" and
  explains what shows up on its own.
- **DOCS-LIST-ENTITY** GIVEN any row WHEN I right-click or drag it into the composer THEN it
  becomes a `document` chip (rows are `document:<id>` entity rows).

## DOCS-ADD — adding files

- **DOCS-ADD-ONE** GIVEN Documents WHEN I add one file with **Add files** (or drop it on the
  panel) THEN it uploads, the list refreshes, and its detail opens so I can set name/type/year.
- **DOCS-ADD-BAD** GIVEN an unsupported file WHEN I add it THEN the panel shows the parser's
  error and nothing is added.

## DOCS-FILE — one file

- **DOCS-FILE-EDIT** GIVEN a file's detail WHEN I set a year THEN the change
  saves on blur and the list shows it.
- **DOCS-RENAME** GIVEN any item WHEN I click **Rename**, type a name and click **Save** (or press
  Enter) THEN the header and the list show the new name (an asset file's asset page too); an
  empty name puts a file back to its file name and a statement or receipt back to its automatic
  name; **Save** is disabled for an empty artifact or asset-file name.
- **DOCS-NOTE** GIVEN any item (a file, statement, receipt, saved artifact or asset file) WHEN I
  click **Add a note**, type a few lines and click **Save** (or press Cmd/Ctrl+Enter) THEN the
  note shows above **Details** as typed, line breaks kept and its text selectable, the row reads
  **Edit note**, and searching the list for a word in it finds the item; **Remove** (or saving it
  empty) deletes it; a note over 1,000 characters is refused with a message and the typed text
  stays. On a Private file the dialog says Lucius can still read the note.
- **DOCS-ROW-MENU** GIVEN the list WHEN I open a row's **⋮** (desktop: shown while the row is
  hovered; phone: always shown, opening a sheet titled with the item's name) THEN it offers
  **Rename**, **Add a note / Edit note**, **Add tags / Edit tags** and the removals that kind
  allows (a deletion in red), each opening the same dialog as the item's **Manage** — the item
  itself is not opened, a removal asks first, and on the phone the Documents sheet stays open
  behind every dialog. On the phone, a tap on the dimmed area around the menu only closes it —
  the row under the finger is not opened; the next tap opens rows as usual.
- **DOCS-TAGS** GIVEN any item WHEN I open **Manage → Add tags**, type a tag and press Enter (or
  tap a tag already in use) and click **Save** THEN the item's page shows it as a chip, the
  filter row shows **#Tag n** after the kinds (one chip per tag, counted across kinds, case never
  splitting one), clicking it lists only tagged items (with any kind chosen), **All** clears it,
  a tag chip on an item's page lists everything that has it, and search matches tags; up to 10
  tags of 32 characters each, refused past that.
- **DOCS-FILE-PRIVATE** GIVEN a file WHEN I switch **Private** on THEN the switch moves at once,
  the row shows a lock, and **Ask Lucius about this** is replaced by the Private note.
- **DOCS-FILE-DELETE** GIVEN a file added in Documents WHEN I click **Delete** and confirm
  THEN it is gone (the confirm says it is deleted for good).
- **DOCS-FILE-REMOVE** GIVEN a file kept from a chat WHEN I click **Remove from Documents** and confirm
  THEN it leaves Documents (the confirm says it stays in its chat).
- **DOCS-FILE-DELETE-CHAT** GIVEN a file kept from a chat THEN its detail offers both **Remove from
  Documents** and **Delete file**; WHEN I click **Delete file** and confirm THEN the file is gone
  from Documents and from Settings → Uploaded files (the confirm says the chat can no longer open
  it).
- **DOCS-PREVIEW-ARTIFACT** GIVEN a saved artifact WHEN I open it THEN the report itself shows in
  the same 440px frame a PDF's page gets — drawn at a report's width and scaled to fit, in the
  chat's sandboxed frame, not scrollable or clickable inside — with an **Open** hint; WHEN I tap
  it THEN it opens full size in the artifact viewer.
- **DOCS-ZOOM** GIVEN the full-size reader, a photo opened large, or a transaction's receipt on a
  phone (a PDF shows as the same stacked reader; a photo as the same zooming photo) WHEN I pinch,
  double-tap (or, on a computer, double-click or pinch the trackpad) THEN it zooms around my
  fingers / the pointer, up to 4× (pages) or 5× (a photo), and a second double-tap returns to the
  whole page; zoomed, I pan by scrolling (pages) or dragging (a photo); a pinch never slides the
  phone sheet; the page pill hides while zoomed.
- **DOCS-PREVIEW-TEXT** GIVEN a text file (.txt, .md, .json, a bank's OFX/QFX/QBO/QIF/MT940/camt
  export) WHEN I open it THEN its text shows in the same 440px frame, wrapped and scrolling inside
  (markup stays text; a UTF-16 file decodes; past 256 KB it shows the start and says to download
  the rest); a type the app can't draw (a Word file) keeps the "No preview" place.
- **DOCS-PREVIEW-PDF** GIVEN any PDF item (a statement, a receipt, an added, kept or asset file)
  WHEN I open it THEN its whole first page shows, scaled to fit with its proportions kept (never
  stretched or cropped), with an **Open** hint;
  WHEN I tap it THEN a full-size reader opens with every page stacked — scrolling (or swiping)
  moves through them, each loading as it nears — and a floating "‹ 1 / N ›" pill that stays in
  view, follows the page I'm on and jumps between pages; a PDF that can't be drawn says so and
  **Download** still works.
- **DOCS-PREVIEW-TABLE** GIVEN a CSV or Excel item WHEN I open it THEN its rows show as a grid
  with column letters and row numbers, scrolling inside a preview-sized frame, with "Rows 1–200 of
  N — scroll for more"; WHEN I scroll to the bottom THEN the next rows load, each once and in
  order, until all are shown; an Excel file with several sheets shows one tab per sheet; a
  password-protected one says so.
- **DOCS-PREVIEW-NONE** GIVEN an item that is not a PDF, spreadsheet or photo THEN a preview-sized
  placeholder says there is no preview and to download it.
- **DOCS-ASSETS** GIVEN a file attached to an asset THEN it is listed under **Assets** with the
  asset's name; **Open asset** opens that asset; **Delete file** takes it off the asset and
  deletes it; WHEN the asset's
  **Lucius can read** is switched off THEN the item shows as Private and Lucius can't read it;
  its **Settings** has the same switch as **Private** (no Year), and switching it here turns the
  asset page's **Lucius can read** the other way.
- **DOCS-LINKS** GIVEN a receipt linked to a transaction WHEN I click **View transaction** THEN the
  Accounts tab opens that transaction's drawer; GIVEN a statement WHEN I click **Open account**
  THEN the Accounts tab opens its account.
- **DOCS-THUMBS** GIVEN a photo item THEN its list row shows the photo as a small thumbnail (the
  icon if it can't be read); PDFs keep their icon.
- **DOCS-STATEMENT** GIVEN a statement's detail WHEN I click **Delete statement** and confirm THEN
  it leaves Documents and its account's statement list, and the transactions it imported stay;
  GIVEN a receipt's detail WHEN I click **Delete receipt** and confirm THEN it is gone and its
  transaction stays, without it. Neither has a Private switch.

## DOCS-ASK — Lucius

- **DOCS-ASK-CHIP** GIVEN a non-private item WHEN I click **Ask Lucius about this** THEN a chip for
  it appears in the composer (phone: Documents closes so the composer shows).
- **DOCS-ASK-AT** GIVEN the composer WHEN I type `@` and part of a document's title THEN the
  picker lists it under "Document"; WHEN I type a bare `@` THEN every mentionable kind has its
  own section (Documents among them — a type no longer waits behind the first few kinds'
  whole lists); WHEN I type `@docs` or `@files` THEN it lists the documents.
- **DOCS-OPEN-SIDE** GIVEN the chat side panel on any tab WHEN I click its Documents icon
  (header) THEN Documents opens over the page (a tall dialog on desktop); **Ask Lucius about
  this** closes it and puts the file in the side panel's message as a chip; on a computer a row
  also DRAGS onto the side panel (left undimmed beside it) and lands there as a chip, with
  Documents staying open for the next.
- **DOCS-ASK-PRIVATE** GIVEN a Private file WHEN Lucius is asked about it THEN Lucius says it is
  Private and does not describe its contents (every read tool refuses it).
- **DOCS-ASK-NOTE** GIVEN an item with a note WHEN Lucius lists, reads or is asked about it THEN
  it has the note — a Private file's too (the note is the user's words, not the file's).

## DOCS-SAVE — artifacts from chat

- **DOCS-SAVE-CARD** GIVEN an artifact card in a chat WHEN I click **Save** THEN it reads
  **Saved** at once, and the **Artifacts** filter in Documents lists it.
- **DOCS-SAVE-UNDO** GIVEN a **Saved** card WHEN I click it THEN it reads **Save** again and the
  artifact leaves Documents (it stays in the chat).
- **DOCS-SAVE-NARROW** GIVEN the card on a phone or in the docked panel THEN Save and Download
  show as icons and the title is not squeezed to a few letters.
- **DOCS-SAVE-OPEN** GIVEN a saved artifact in Documents WHEN I click **Open** THEN it opens in the
  artifact panel (phone: full screen); closing it returns to Documents.

## States

- **Loading:** "Loading documents…" while the list loads.
- **Error:** list failure → "Couldn't load documents" with Retry; a failed save on a card reads
  "Try again" with the reason as its tooltip; a failed edit shows the reason under the fields.
