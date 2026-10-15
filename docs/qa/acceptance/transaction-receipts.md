# Acceptance criteria — Transaction Receipts (TXN-REC)

Feature: attaching, viewing, and removing receipt images/PDFs on a transaction from the
transaction detail drawer. Receipts are durable evidence files (photo/scan of a receipt,
invoice, statement, etc.) linked to one or more transactions. Frontend component reads via
`GET /api/transactions/{txn_id}/receipts` and attaches via `POST /api/transactions/{txn_id}/receipts`
(promoting a chat upload or linking an existing receipt) and removes via `DELETE /api/transactions/{txn_id}/receipts/{receipt_id}`.

Backend: `backend/api/receipts.py`, `backend/services/receipts.py`, `backend/db/receipt_store.py`.
Frontend: `frontend/src/components/tabs/accounts/ReceiptsSection.tsx`, `ReceiptViewer.tsx`, 
`frontend/src/hooks/useTransactionReceipts.ts`, wired into `TxnDetail.tsx` between the split 
block and Provenance section (keyed on `txn.id` to isolate state per row).

### TXN-REC-01 Empty state
- GIVEN I open the detail drawer for a transaction with no receipts attached
- WHEN the drawer renders
- THEN a "Receipts" section appears showing "No receipts attached yet." and a "+ Attach receipt" button
- AND no table or list rows render

### TXN-REC-02 Loading state
- GIVEN I open the detail drawer for a transaction and the receipts list is still fetching
- WHEN the drawer renders during the fetch window
- THEN the "Receipts" section shows a loading skeleton (2 placeholder rows), no real data

### TXN-REC-03 Error state on load
- GIVEN I open the detail drawer and the GET /api/transactions/{txn_id}/receipts fetch fails (e.g. network error, 500)
- WHEN the drawer finishes rendering
- THEN the "Receipts" section shows "Couldn't load receipts." with a "Retry" button
- WHEN I click "Retry"
- THEN the section transitions to loading skeleton (TXN-REC-02) and re-issues the fetch

### TXN-REC-04 List state with receipts
- GIVEN at least one receipt is linked to the displayed transaction
- WHEN the drawer renders
- THEN a "Receipts (N)" header appears (N = count)
- AND each receipt row shows: a thumbnail (image) or icon (📄 for PDF, 📎 for other), filename (truncatable with title), and date formatted as "Mon DD, YYYY"
- AND a "Remove" button appears on the right

### TXN-REC-05 Image vs. PDF icon
- GIVEN the drawer is showing a list with at least one receipt of each type
- WHEN I examine the icon/thumb for an image receipt (e.g. PNG, JPG)
- THEN a thumbnail image displays inline (loaded from `/api/receipts/{receipt_id}/file`)
- WHEN I examine the icon/thumb for a PDF receipt
- THEN a 📄 glyph displays instead of an image
- WHEN I examine the icon/thumb for another type (e.g. no media_type or non-image)
- THEN a 📎 glyph displays

### TXN-REC-06 Image thumbnail fallback
- GIVEN a receipt row with a broken/missing image thumbnail (e.g. the file was deleted from disk, 404)
- WHEN the image fails to load
- THEN the 📎 glyph appears in place of the broken image (no broken-image icon)

### TXN-REC-07 Attach receipt: successful image upload
- GIVEN the drawer is open on a transaction (empty or with existing receipts)
- WHEN I click the "+ Attach receipt" button
- THEN a file picker dialog opens
- WHEN I select a JPG or PNG file (size < 10 MB, any image)
- THEN the button shows "Attaching…" (loading state)
- AND the file picker closes
- WHEN the attach completes (a few seconds)
- THEN the "Attaching…" button returns to "+ Attach receipt"
- AND the selected image appears as a new row in the list (or creates the list if empty)
- AND the new row shows: the image thumbnail, filename, today's date, and a "Remove" button

### TXN-REC-08 Attach receipt: successful PDF upload
- GIVEN the drawer is open on a transaction
- WHEN I click the "+ Attach receipt" button
- THEN a file picker opens (accepts `image/*,.pdf`)
- WHEN I select a PDF file
- THEN the button shows "Attaching…"
- AND the file picker closes
- WHEN the attach completes
- THEN the "Attaching…" button returns to "+ Attach receipt"
- AND the PDF appears as a new row showing: 📄 glyph, filename, today's date, and a "Remove" button
- AND the list now shows "Receipts (N+1)" in the header

### TXN-REC-09 Attach receipt: failed upload, error shown
- GIVEN the drawer is open and the attach button is ready
- WHEN I select a file to attach
- WHEN the upload fails (e.g. network error, backend 500, server down)
- THEN the "Attaching…" loading state dismisses
- AND an inline error message appears: "Couldn't attach that file — try again."
- AND a "Retry" button appears below the error

### TXN-REC-10 Attach receipt: retry after error
- GIVEN an attach error is showing (TXN-REC-09)
- WHEN I click the "Retry" button
- THEN the error dismisses and "Attaching…" reappears
- AND the last selected file is re-uploaded
- WHEN the retry succeeds
- THEN the receipt appears in the list as in TXN-REC-07/TXN-REC-08
- WHEN the retry fails again
- THEN the same error message reappears and "Retry" is available again

### TXN-REC-11 View receipt: click filename opens ReceiptViewer
- GIVEN a receipt row is displaying in the list
- WHEN I click the filename (the blue link text)
- THEN a modal opens showing the receipt file
- AND the modal title is the filename
- AND for an image receipt, the image renders at readable size
- AND for a PDF, a PDF viewer (or a placeholder for supported MIME) renders
- AND a "↓ Download original" button appears in the modal footer

### TXN-REC-12 ReceiptViewer: download button
- GIVEN the ReceiptViewer modal is open
- WHEN I click "↓ Download original"
- THEN the browser downloads the receipt file with the original filename
- AND the download attribute is set (the browser save dialog appears, never inline load)

### TXN-REC-13 ReceiptViewer: close modal
- GIVEN the ReceiptViewer modal is open
- WHEN I click the X (close button) or press Escape
- THEN the modal closes and the drawer's list is still visible
- WHEN I click the same filename again
- THEN the modal opens again (state is not retained across close/reopen)

### TXN-REC-14 Remove receipt: two-step confirmation
- GIVEN a receipt row is displaying with a "Remove" button
- WHEN I click the "Remove" button
- THEN the button is replaced by two inline buttons: "Confirm" (red/danger) and "Cancel" (secondary)
- AND this armed state persists until I click one of them

### TXN-REC-15 Remove receipt: cancel disarms
- GIVEN a receipt row is armed (showing Confirm/Cancel per TXN-REC-14)
- WHEN I click "Cancel"
- THEN the "Confirm" and "Cancel" buttons are replaced by the original "Remove" button
- AND the receipt row is NOT removed from the list

### TXN-REC-16 Remove receipt: confirm removes
- GIVEN a receipt row is armed (showing Confirm/Cancel)
- WHEN I click "Confirm"
- THEN the "Confirm" button shows a brief loading state
- WHEN the DELETE /api/transactions/{txn_id}/receipts/{receipt_id} succeeds
- THEN the receipt row disappears from the list
- AND if it was the last receipt, the list transitions to the empty state (TXN-REC-01)
- AND the header updates from "Receipts (N)" to just "Receipts" (empty)

### TXN-REC-17 Remove receipt: error on confirm
- GIVEN a receipt row is armed and I click "Confirm"
- WHEN the DELETE request fails (network error, 500, server down)
- THEN the "Confirm" loading state dismisses and an error message appears below the row: "Couldn't remove that receipt — try again."
- AND the row stays armed (Confirm/Cancel still visible)
- AND I can click "Confirm" again to retry

### TXN-REC-18 State isolation: switching transactions resets armed/viewing state
- GIVEN the drawer is open on transaction A, a receipt is armed (showing Confirm/Cancel), and I have ReceiptViewer open for another receipt on A
- WHEN I close the drawer and open the drawer on a DIFFERENT transaction B
- THEN transaction B's ReceiptsSection starts fresh: no armed rows, no viewing modal
- WHEN I navigate back to transaction A
- THEN A's armed/viewing state is NOT preserved (all state is reset)

### TXN-REC-19 State isolation: key-based reset on row switch
- GIVEN the drawer is displaying transaction A with a list of receipts
- WHEN I switch to transaction B (the drawer doesn't unmount, only the `txn` prop changes)
- THEN the ReceiptsSection component is re-mounted (via React key={txn.id}), fetching B's receipts
- AND no local UI state from A (armed rows, selected viewer, in-flight attach) appears on B
- AND B's receipts list is fresh (never a stale response from A's fetch)

### TXN-REC-20 Attach error state in empty list context
- GIVEN the drawer is on a transaction with no receipts (empty state) and the attach button is visible
- WHEN I select a file and the attach fails
- THEN the error message "Couldn't attach that file — try again." appears IN THE EMPTY STATE BLOCK (not below a list)
- AND a "Retry" button appears below the error
- WHEN I click "Retry" and it succeeds
- THEN the empty state is replaced by a list with the new receipt

### TXN-REC-21 Attach error state in populated list context
- GIVEN the drawer is on a transaction with existing receipts (list visible) and the attach button is at the bottom
- WHEN I select a file and the attach fails
- THEN the error message appears at the bottom, BELOW the list (in the footer area)
- AND a "Retry" button is shown
- WHEN I click "Retry" and it succeeds
- THEN the new receipt appears in the list and the error dismisses

### TXN-REC-22 Multiple receipts of mixed types
- GIVEN a transaction has 5 receipts: 2 images (JPG/PNG), 2 PDFs, 1 unknown type
- WHEN the drawer renders the list
- THEN all 5 rows appear with correct icons: 2 image thumbs, 2 📄 glyphs, 1 📎 glyph
- AND each row has the correct filename and date
- AND the header reads "Receipts (5)"

### TXN-REC-23 Very long filename truncation
- GIVEN a receipt has a very long filename (e.g. 150+ characters)
- WHEN the receipt row renders
- THEN the filename is truncatable/scrollable or wrapped in a way that doesn't break the layout
- AND a `title` attribute on the name shows the full filename on hover

### TXN-REC-24 Receipt dates are consistent across drawer
- GIVEN a receipt is attached and linked to the transaction
- WHEN the receipt row shows a "created" date
- THEN the date format matches other dates in the drawer (e.g. "Aug 21, 2026" format, matching the transaction's Provenance dates)

### TXN-REC-25 Attach flow works end-to-end with real image file
- GIVEN I have a small test image file (JPG/PNG, under 5 MB)
- WHEN I open a transaction drawer and click "+ Attach receipt"
- WHEN I select the test image file
- THEN the file is uploaded to `/api/upload` (returns a `ref_id`)
- THEN the `ref_id` is promoted to a receipt via POST `/api/transactions/{txn_id}/receipts` with `attachment_id`
- THEN GET `/api/transactions/{txn_id}/receipts` returns the new receipt in the list
- THEN GET `/api/receipts/{receipt_id}/file` returns the raw image bytes (verified by the thumbnail rendering)
- AND the complete round-trip succeeds end-to-end

### TXN-REC-26 Attach flow works end-to-end with real PDF file
- GIVEN I have a small test PDF file (under 5 MB)
- WHEN I open a transaction drawer and click "+ Attach receipt"
- WHEN I select the test PDF file
- THEN the file is uploaded to `/api/upload` (returns a `ref_id`)
- THEN the `ref_id` is promoted to a receipt via POST `/api/transactions/{txn_id}/receipts` with `attachment_id`
- THEN GET `/api/transactions/{txn_id}/receipts` returns the new receipt in the list with `media_type: "application/pdf"`
- THEN GET `/api/receipts/{receipt_id}/file` returns the raw PDF bytes with correct headers
- AND the row displays the 📄 glyph (not an image thumb)

### TXN-REC-27 Unlink flow end-to-end
- GIVEN a transaction has receipts and I want to remove one
- WHEN I click "Remove" on a receipt row
- WHEN I click "Confirm"
- THEN DELETE `/api/transactions/{txn_id}/receipts/{receipt_id}` is called
- THEN GET `/api/transactions/{txn_id}/receipts` is re-fetched (or the list is updated optimistically)
- THEN the receipt is no longer in the list
- AND the row disappears from the UI
