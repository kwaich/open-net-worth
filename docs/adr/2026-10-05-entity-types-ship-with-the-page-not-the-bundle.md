---
title: Entity types ship with the page, not the bundle
status: Accepted
date: 2026-10-05
deciders: orchestrating agent (Claude) + repo owner
---

# Entity types ship with the page, not the bundle

## Context

The mention registry (each type's icon, label, tone and field schema: what chips, the `@`
picker and the detail popover render) is defined once, in
`backend/services/mentions/descriptors/`. RFC `2026-06-25-lucius-generic-entity-registry` §1b
gave the frontend a **build-time copy**, `entityRegistry.generated.ts`, so chips render before
`GET /api/entity-types` resolves. That copy failed in four ways:

- **It drifted without anyone noticing.** `npm run build`'s prebuild produced it by importing
  the backend through Python. Docker's `node:22-slim` stage has no `python3`, and the CI
  wheel/release jobs have Python without `requirements.txt`. In both cases the generator fell
  back to a hard-coded list written for 16 types. By 2026-10-05 the backend had 35 types, and
  every published image and wheel shipped the stale list: wrong icons (`holding` showed `◈`),
  missing types (`automation`, `connector`, `report`, …) and dead ones (`rule`, `agent`). Six
  frontend tests fail against that list, but no build ever ran them against it.
- **It was a second source of display truth.** The generator layered `CLIENT_OVERRIDES` (labels
  and tones) on top. The runtime fetch returned the backend's own values, so a chip read
  "Security" at first paint and "Instrument" a moment later.
- **It went stale for whole sessions.** The boot-time fetch is authenticated. A tab that booted
  on the login screen got a 401 and kept the bundled copy for the entire session.
- **Committing it and adding a drift check (209da2113) only moved the problem.** The copy
  remained, and so did a guard that holds only if CI runs. CI was down that same day for
  billing.

Two facts make a copy unnecessary. The SPA is always served by the backend that owns the
descriptors (`_SPAStaticFiles`). And the registry is schema only: no values, no user data.

## Decision

We will ship no frontend copy of the entity-type registry. `services.mentions.entity_types()`
is the one wire payload. The server embeds it in `index.html` as it serves the shell, as
`<script type="application/json" id="onw-entity-types">` with `<` escaped. The client seeds its
registry from that embed synchronously, before first render. `GET /api/entity-types` returns
the same function's output and remains the refresh path. The display values the client used to
override (labels such as "Security", tones such as `invest`) move into the backend descriptors
themselves.

Frontend unit tests have no server, so they seed the registry from
`frontend/src/lucius/entityTypes.fixture.json`, the exact backend payload. That file never
ships. `tests/test_entity_types_fixture.py` pins it to the descriptors, so a stale fixture fails
`make test` locally, without depending on CI.

## Consequences

### Positive
- Nothing that ships can drift. Docker, the wheel, the desktop app and the web app all render
  what the running backend defines, from the first frame, including before login.
- The generator, the committed copy, its `--check`, the CI step and the prettier/gitignore
  exceptions are all gone, along with the unused raw-fetch path (`wireEntityRegistryFetch` /
  `refreshEntityRegistry`). Descriptor display values now live in one place.
- The test fixture is a golden file with a guard in `make test`. Contract tests (for example,
  that PayeesPanel's emitted keys exist in the real payee schema) still run against real
  schema.

### Negative
- Serving `index.html` now does a little work per request: it reads the file and serializes
  about 35 small descriptors. That's negligible, and the shell is already `no-store`.
- A page that didn't come through `_SPAStaticFiles` has no first-paint seed: the Vite dev server
  (:5174) and `vite preview` (`make demo`, the QA lanes) serve `index.html` themselves. There,
  chips are bare until the `/api/entity-types` fetch resolves.
- The registry is now readable before login, where `/api/entity-types` sits behind auth. That's
  safe while descriptors are static schema. If they ever become per-instance (for example
  connector-defined types), revisit what the shell carries.
- After a descriptor change, the test fixture still has to be regenerated
  (`python -m backend.scripts.entity_types_fixture`). The pytest guard tells you when.

### Follow-ups
- RFC `2026-06-25-lucius-generic-entity-registry` §1b still describes the bundled fallback. This
  ADR supersedes that part of it.

## Alternatives considered

- **Commit the generated file and check it for drift in CI** (209da2113): rejected. It leaves
  a copy that needs a guard, and that guard only holds if CI runs.
- **Generate the file in a Python Docker stage and `COPY` it into the frontend stage:**
  rejected. It fixes only Docker. The wheel builds would still fall back silently, and the copy
  remains.
- **Fail the build loudly when the backend can't be imported:** rejected on its own. Docker's
  node stage would then always fail, so it needs one of the options above anyway.
- **Keep the client overrides in the frontend:** rejected. They are a second source of display
  truth, and the runtime fetch overwrote them anyway.
- **A vitest global setup that runs Python for live descriptors:** rejected. Every frontend
  test run would need the backend venv, which most `.claude/worktrees/` checkouts lack, so it
  would fail where agents work most.

## Agent provenance

- **Decided by:** orchestrating Claude agent, at the owner's direction ("do what you suggest,
  as the best").
- **Lane / branch:** `claude/kind-villani-d36491` worktree, landed on `main`.
- **Alternatives weighed:** committed copy + CI check; Python Docker stage; fail loudly; client
  overrides; Python-backed vitest setup.
- **Driving artifacts:** Docker build log (`python3: not found` → "using hard-coded
  fallback"); RFC `2026-06-25-lucius-generic-entity-registry` §1b; commit 209da2113.
- **Why this way (one line):** the backend that owns the descriptors always serves the page,
  so sending them with the page leaves nothing to drift and nothing to guard.
