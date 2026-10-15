# Acceptance — Settings → Data → Uploaded files (UploadsPanel)

Feature: persistent chat-attachment uploads, user-deletable in Settings.
Backend: `GET/DELETE /api/uploads[/{id}]` · panel: `settings/UploadsPanel.tsx`.

## UP-1 Empty state
GIVEN no stored uploads
WHEN I open Settings → Data
THEN the "Uploaded files" panel shows "Nothing stored yet — attach a file in chat…"
AND no table renders.

## UP-2 List rows
GIVEN at least one file was attached in chat
WHEN I open Settings → Data
THEN each row shows filename (with kind icon), human size (B/KB/MB), uploaded date,
and last-used date ("—" when never used)
AND payload content is never rendered or fetched.

## UP-3 Delete one (two-step)
GIVEN a listed upload
WHEN I click its "Delete" button
THEN the row shows Confirm/Cancel in place (no browser dialog)
AND Cancel restores the Delete button without deleting
AND Confirm removes the row; a subsequent GET /api/uploads no longer lists it
AND a chat Retry referencing that ref now yields the model reporting the attachment
is no longer available (never a silent blind answer).

## UP-4 Delete all (two-step)
GIVEN ≥2 listed uploads
WHEN I click "Delete all…" then "Confirm delete all (N)"
THEN the list becomes the UP-1 empty state and GET /api/uploads returns [].

## UP-5 Error state
GIVEN GET /api/uploads fails (backend down / 500)
WHEN the panel loads
THEN it shows "Could not load stored uploads." with a Retry button
AND Retry re-fetches and renders rows on success.

## UP-6 Persistence across restart
GIVEN a file attached in chat
WHEN the backend restarts
THEN the upload still lists in Settings and a chat Retry still resolves the
attachment content.
