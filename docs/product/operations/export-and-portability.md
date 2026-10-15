---
title: Export and portability
description: Export your whole book as one .zip and load it into another install or a new computer — what travels, what doesn't, and how to restore it.
category: operations
order: 20
audience: [user, operator]
status: stable
related: [operations/backup-and-restore, operations/security-and-privacy, operations/desktop]
keywords: [export, import, portable, migrate, move, new computer, zip, data ownership, restore, I have a backup to restore]
---

# Export and portability

An export is your whole book in one `.zip` file that any Open Net Worth install can load. Use it
to move to a new computer, switch from a server to the desktop app (or back), or simply keep a
copy of your data outside the app.

It's different from a [backup](backup-and-restore.md): a backup rolls **this** install back to an
earlier point; an export carries your book **into another** install.

## Export your book

Any of these saves the same file:

- **Settings → Data → Backup & restore → Export backup (.zip)**
- Desktop app: **File → Export book…** (<kbd>⇧</kbd><kbd>⌘</kbd><kbd>E</kbd> /
  <kbd>Ctrl</kbd><kbd>Shift</kbd><kbd>E</kbd>), or **Settings → Desktop → Your book → Export
  backup…**. The desktop app asks where to save it; pick a place outside your book folder.

The file is named like `open-net-worth-export-2026-10-10….zip`.

### What's in it

- Your ledger: accounts, transactions, categories, payees, budget, holdings and lots, owned
  assets, equity grants, commitments, goals and plans, watchlists and alerts.
- Your files: attachments, receipt images, the statement files you imported, documents Lucius
  made for you, and your custom skills.
- Lucius's memory, your Automations (and their run history), and your chat history.
- A manifest with a checksum for every part, so the import can tell if anything was damaged or
  altered.

### What's left out

- **Every credential**: your login password, API token, connector passwords and keys, and AI
  provider key.
- **This install's own settings**: your AI provider and model choice, tool permissions, duplicate
  detection settings and backup settings.
- Local working data: caches, logs and undo history.

## Load an export into another install

### On a brand-new install

1. Install Open Net Worth on the new computer and open it.
2. On the first setup screen, click **I have a backup to restore**.
   (Desktop app: if the app asks for a book folder, choose an empty one.)
3. Under **Choose a backup file**, pick your export `.zip`.
4. Check the preview — base currency, number of records and the main lists. Nothing has been
   written yet.
5. Click **Restore this backup**. Setup then continues with connecting your AI provider.

### Into an install you've already set up

1. Open **Settings → Data**.
2. Click **Choose import file…** (or **Restore from a file instead…** under the backup list — it
   does the same thing) and pick the export `.zip`.
3. The import starts **as soon as you pick the file** — there's no preview here — and the app
   reloads when it's done ("Imported — reloading").

**It replaces everything.** An import is not a merge: anything in this install that isn't in the
file is cleared. A checkpoint is saved first so the import can be undone, except that chat history
replaced by an import can't be brought back. Take a [backup](backup-and-restore.md) first (**Back
up now**) if this install holds anything you want to keep.

### After importing: reconnect

Credentials never travel, so on the new install:

1. Connect your AI provider again (**Settings → AI**).
2. Re-add each connector — email, Telegram, bank or brokerage sync, web search.
3. Review **Settings → Permissions** and your backup settings.
4. If other AI apps or phone shortcuts used your book, set them up again with the new install's
   API token — see [Use your book from other AI apps](connect-ai-apps.md).

## Safety checks

Before anything is written, the import checks the file:

- every part is compared with its checksum in the manifest, and the file can only write to the
  places an export is allowed to;
- the data is read and validated in full first, and your files are staged and verified before
  they replace the current ones — a damaged file stops the import with your book untouched;
- only one import runs at a time.

## Limits

- An export made by a much older version can be refused: "This backup predates the seven-type
  account schema (2026-09-07)…". Load it into a version from before that date, let it update,
  then export again.
- The data part of an export (everything except attachments and other files) is limited to
  200 MB in total. Attachments and files are limited only by free disk space.
- Folder access for watched statement folders belongs to one computer and doesn't travel.

## Troubleshooting

**"This file isn't an Open Net Worth backup. Choose the .zip you exported from Settings → Data."**
— you picked something other than an export (for example a downloaded backup, which can't be
loaded this way). Export from the old install and use that file.

**"missing manifest.json"** — the same: the file is a downloaded backup or another zip, not an
export.

**"schema_version mismatch"** — the export is from an incompatible version; see
[Limits](#limits).

**"an import is already in progress"** — wait for the first one to finish.

## Related pages

- [Backup and restore](backup-and-restore.md) — rolling this install back
- [Desktop app](desktop.md) — exporting from the desktop app and opening another book folder
- [Security and privacy](security-and-privacy.md) — what's stored and where
