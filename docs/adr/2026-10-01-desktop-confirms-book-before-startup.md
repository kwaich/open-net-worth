# Desktop confirms its first book before startup

Status: accepted. Scope: desktop host; browser/server onboarding remains unchanged.

A first launch has no confirmed desktop book. Starting the suggested `~/.open-net-worth`
server before the user chooses a folder can surface a lock error before Welcome, and an
alternate empty folder can then repeat Welcome inside the web UI.

The desktop host renders an offline Welcome with Get started and Restore from backup.
Get started suggests the default folder and handles both new and existing books in the same
folder choice. Directory creation, the instance lock,
migrations and backend startup wait for explicit folder confirmation. Picker and confirmation
cancellation leave book files untouched. Restore accepts only a genuinely empty destination,
then uses the existing portable-export preview and explicit import confirmation.

A fresh confirmed book enters `/setup/folder` directly: currency, model, optional security,
then Lucius. The currency view does not repeat the chosen folder. Back stops the owned backend
and returns to the native folder choice, preserving the book. This action is accepted only
from the owned currency page while setup is still fresh. An existing configured book opens directly. Successful opening persists the canonical selected
root, so subsequent launches open it automatically.

An unsuccessful first selection never starts an unconfirmed fallback. Retry requires a
candidate that passed the filesystem recheck, and retains its canonical path, device and
inode. This permits the owned backend to create its database without admitting a substituted
folder. Normal switches between already selected books retain their existing rollback behavior.

Welcome, loading and recovery use the canonical transparent logo, bundled fonts and frontend
palette. Their default follows native system appearance, including changes while a local
screen is visible. Frontend pre-paint resolution follows the same System default and preserves
explicit palette choices.
The same logo, layout and palette continue through the frontend access and setup checks.
Progress describes the real stage: creating, opening, preparing restore, or closing a book;
the setup renderer says it is getting setup ready instead of flashing a second wordmark.

Pre-backend pages receive only bounded initial-choice and recovery actions. The host validates
the exact owned top-level frame and exact generated document. General desktop capabilities
and backend credentials remain unavailable until the owned app document loads.
