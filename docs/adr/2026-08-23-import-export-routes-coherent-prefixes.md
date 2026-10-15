---
title: Import/export REST routes renamed onto coherent prefixes — clean break, no alias
status: Accepted
date: 2026-08-23
deciders: owner (the repo owner) + senior-engineer implementation lane (Claude)
---

# Import/export REST routes renamed onto coherent prefixes — clean break, no alias

## Context

Two distinct concepts shared the `/api/import*`/`/api/export` prefix:

- **Whole-book portability** — export/restore the entire instance as a `.zip` bundle
  (`backend/api/portability.py`): `GET /api/export`, `POST /api/import/validate`,
  `POST /api/import`.
- **Statement import** — one bank/broker file (CSV/OFX/QIF/XLSX/PDF) into the ledger
  (`backend/api/import_api.py`): `POST /api/import/preview`, `POST /api/import/source-peek`,
  `POST /api/import/commit`, `POST /api/import/formats`-adjacent routes, `POST /api/import/merge`,
  and the CSV-pinned `POST /api/import/csv[/preview]` aliases.

The two concepts didn't just share a prefix, they COLLIDED: both declared a bare
`POST /api/import`. FastAPI does not error on a duplicate route registration — it resolves
by first-registration order and silently shadows every route after the first.
`backend/main.py` included `import_api`'s router before `portability`'s, so every bundle
restore was actually dispatched into the CSV statement importer and died on
`_csv.Error: new-line character seen in unquoted field`. That specific collision was fixed
same-day, first, by moving `import_api.py`'s handler to `POST /api/statements/import`
(`tests/test_route_uniqueness.py` pins it). This ADR covers the follow-on, broader rename
that fixes the remaining incoherence: statement-import sub-paths still lived under
`/api/import/*` while the statement commit itself already lived under `/api/statements/*`.

## Decision

Renamed onto two coherent prefixes:

| old | new |
|---|---|
| `GET  /api/export`             | `GET  /api/data/export` |
| `POST /api/import`             | `POST /api/data/import` |
| `POST /api/import/validate`    | `POST /api/data/import/validate` |
| `POST /api/statements/import`  | unchanged |
| `POST /api/import/preview`     | `POST /api/statements/preview` |
| `POST /api/import/source-peek` | `POST /api/statements/source-peek` |
| `POST /api/import/commit`      | `POST /api/statements/commit` |
| `GET  /api/import/formats`     | `GET  /api/statements/formats` |
| `DELETE /api/import/formats/{id}` | `DELETE /api/statements/formats/{id}` |
| `POST /api/import/merge`       | `POST /api/statements/merge` |
| `POST /api/import/csv/preview` | `POST /api/statements/csv/preview` |
| `POST /api/import/csv`         | `POST /api/statements/csv` |

`/api/data/*` is whole-book portability; `/api/statements/*` is statement import. Every
caller (frontend, backend prose, tests) was updated to the new paths in the same change.

## Clean break, no alias — a deliberate exception to the rename-requires-alias convention

`CLAUDE.md` states a renamed AI tool requires an alias, because a tool name is persisted
data (it lives in the `automations` table, skill frontmatter, backups, in-flight
transcripts). The initial plan for this REST rename followed the same convention:
register every old path as a deprecated alias to the same handler.

**The owner overrode this mid-implementation**: no aliases, a clean break — the app has no
external users yet, so backwards compatibility over these specific REST paths is not worth
carrying. This is a narrower, deliberate exception, not a reversal of the general
convention:

- An AI tool name is called by an LLM from memory and a persisted `automations` row that
  can outlive a deploy — the caller may not be recompiled alongside the rename. A REST path
  here is called only by this repo's own frontend and test suite,
  both updated in the same commit; there is no persisted "call this path" record anywhere
  in the data model (unlike a tool name, no REST path string is ever written into
  `automations`/skills/backups).
- The app is pre-launch (`docs/launch/BOARD.md`'s Hardening Loop, still driving toward
  Foundation → Budgeting → Investments) with no real self-hosted deployments to break.

**Consequence, called out explicitly**: `frontend/src/components/firstrun/StepRestore.tsx`
(out of scope for this change — another lane owns `frontend/src/components/firstrun/`)
still calls the old `/api/import` and `/api/import/validate`, which now 404. That file's
owner must update it to `/api/data/import` and `/api/data/import/validate` before the
first-run restore flow works again.

**If this repo ever ships to a real self-hosted user base, this exception stops applying**
— a REST path used by someone's own script, an MCP server, or iOS Shortcuts automation at
that point becomes exactly the kind of persisted-external-contract the tool-rename
convention protects, and a future rename should carry an alias again.

## Alternatives rejected

- **Alias every old path** (the original plan): correct once there are real external
  callers; over-engineering pre-launch, since it would mean carrying deprecated routes with
  no one calling them, forever, until a deprecation cycle nobody re-owns actually removes
  them.
- **Alias only the bare `/api/import` / `/api/export`** (the two names most likely to be
  bookmarked/scripted, leave the deeper sub-paths clean-break): rejected for the same
  pre-launch reasoning, and for consistency — a mixed policy (some paths aliased, most not)
  is a worse invariant to hold than either "always alias" or "never alias, not yet".
