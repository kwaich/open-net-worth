# Moving a book is a resumable protocol, and keys and connections travel separately

Status: accepted. Amends [desktop hosted sign-in](2026-10-03-desktop-hosted-sign-in-uses-the-system-browser-and-pkce.md)
(its in-memory, 100 MB move). Scope: `backend/api/move.py`, `backend/services/move*.py`, `desktop/src/main/move-*.ts`.

The first move held the whole export in memory on both ends, capped at the server's 100 MB
upload limit, and dropped every key and connection. A real book (years of statements, receipts,
attachments) is gigabytes, and a move that loses the AI key and the Telegram bot is not a move.

## Decision

- **A move is a protocol on the receiving server**, usable between any two instances (a computer
  and a remote server, either way): `begin` (size, sha256 → `move_id`, `part_size`, `max_size`),
  `PUT parts/n` (0-based, raw bytes, `X-Part-Sha256`; idempotent), `GET` (received parts, status,
  progress), `commit`, `secrets`, `finish`, and `DELETE` to abort. Parts are written to a temp
  file under the cache directory; the whole file's sha256 is verified before the import reads it.
- **`max_size` is half the free disk minus a margin**, not free minus margin: the bundle is stored,
  then extracted, so the number stated to the sender is what the import can actually finish.
- **A move belongs to the credential that began it**; anyone else sees 404. One move runs at a
  time per server, the same owner may restart its own unfinished upload, beginning is
  rate-limited, and every temp file is removed on finish, abort or after 24 hours. State lives in
  `meta.json` on disk, so a restart loses nothing and an import cut short reads as failed.
- **The import is injected** (the API layer wraps `portability.apply_import` in the same
  pre-import checkpoint as `POST /api/data/import`), because `services` may not import `ai`.
- **Secrets are a separate step and never touch the bundle.** `secrets/export` reads AI provider
  keys, connector configurations (the `secret_config` namespaces) and remote MCP servers from the
  sender; `secrets` writes them into the receiver. Responses name items and reasons, never values;
  values are never logged, and each export is logged by name and caller.
- **Exporting credentials is gated harder than anything else.** AI keys and MCP tokens are
  write-only everywhere else, so `secrets/export` refuses a Bearer API token (a phone Shortcut
  token must not be enough), loopback trust, a disabled or unconfigured login and a gateway
  pass-through. It accepts only this computer's own desktop backend, a gateway session minted in
  the last 15 minutes (the Terminal's rule), or a self-hosted login cookie. It also needs a
  one-time, five-minute token from `POST /api/move/export-nonce`, bound to the same caller, so the
  export is a deliberate second step. The desktop reads the keys first, before packing a large book,
  because a gateway session can go stale during a long upload.
- **Received credentials are not trusted.** Each connector config goes through that connector's own
  store (unknown fields and machine-minted tokens are dropped); an MCP server is rebuilt from
  id, name, URL and credentials only, so tools, publisher and verification are whatever this server
  observes itself; `operator_trust` is never imported and is listed in the checklist; `finish` only
  switches on what the receiver itself switched off.
- **Nothing goes live until `finish`.** The receiver stores every connector switched off and
  remembers which were on; the import pauses the automations it brought in. `finish` switches
  exactly those on; the sender then pauses its own copy (`pause-copy`), so a Telegram bot is never
  polled from two places and an automation never runs twice.
- **What cannot move is reported, not guessed at**: a model on the sender's computer, a CLI
  subscription sign-in, and MCP servers that run a local program or sit at a private address,
  each with a one-line fix in the checklist.
- **The desktop drives it through a hidden signed-in page** so the local backend keeps running
  (it must be asked for the export and the keys) while the gateway still sees same-origin
  requests; the preload accepts only the move paths. Move back reuses the pipeline with the
  endpoints swapped and a temporary background backend for the local side.
- The old copy is marked in desktop preferences (`moved_to`, `moved_at`, what was paused) rather
  than in the book, so the marker never travels with the export.

## Consequences

- Upload resumes within a run (a lost reply is checked against `GET` before a part is resent);
  resuming after the desktop app itself restarts begins a new upload. Persisting the move id is
  the follow-up if that proves common.
- Import still reads the uploaded file into memory until `services.portability` can import from
  a path (`services/move.py::read_bundle` is the one seam to change).
