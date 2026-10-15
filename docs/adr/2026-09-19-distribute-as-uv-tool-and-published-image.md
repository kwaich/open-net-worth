---
number: 2026-09-19
title: Distribute as a uv tool (`onw`) and a published Docker image
status: Accepted
date: 2026-09-19
deciders: orchestrating Claude agent (Fable 5.1) + repo owner (owner picked the path in chat: "just work on it")
---

# 2026-09-19. Distribute as a uv tool (`onw`) and a published Docker image

## Context

- Every install path assumed a developer machine: git, make, Python 3.12, Node 22, then a 5–10
  minute build. Docker built the image from source on the user's box because no image was
  published anywhere. The README's own "Is this for you?" conceded "not yet, if you need
  install-and-forget."
- "Keeps running, starts at login" already existed (`make autostart`, launchd/systemd) — but
  only reachable through that toolchain.
- The app is ONE Python process serving the API, the built SPA and the embedded docs; data lives
  outside the checkout (`~/.open-net-worth`, ADR `2026-08-23-zero-config-default-data-dir`);
  loopback trust means a local install never needs a login. Those three make an
  installed-package distribution the natural shape.
- Constraints: `backend/` is a namespace package imported as `backend.*` off the repo root
  (hundreds of imports, import-linter roots); the three non-Python assets it serves live OUTSIDE
  `backend/`; `requirements.txt` is the one dependency list the Dockerfile, Makefile and CI
  install from; the Docker first run dead-ended without `AUTH_PASSWORD` (a documented recovery
  via the bootstrap token, but not a 5-minute experience); native Windows is blocked by
  `fcntl`/`os.killpg` in seven files.
- The owner's ask: "so easy to install and use… a desktop app that keeps running and starts when
  the user opens their computer, or at least easy to deploy on any machine/server in under 5
  minutes."

## Decision

We will ship two installable forms from ONE release tag and treat the git checkout as the
developer path only:

1. **A wheel on PyPI (`open-net-worth`)**, installed with `uv tool install open-net-worth`,
   exposing the `onw` CLI (`backend/cli/`): `start [--open]`, `autostart install|stop|status`,
   `upgrade`, `version`. The wheel carries `frontend/dist`, `docs-site/dist-embed` and
   `docs/product` under `backend/_bundled/` (copied by `scripts/bundle_assets.py`, run by
   `make wheel`); `core/shipped_assets.py` resolves each — the REPO copy wins whenever it exists,
   the bundled copy is used only inside an installed wheel. Runtime deps stay in
   `requirements.txt` and reach the wheel metadata through the `hatch-requirements-txt` hook (one
   source of truth, Dockerfile/Makefile/CI untouched). The import package stays `backend`.
   `make setup` installs the checkout in editable mode so `.venv/bin/onw` exists and
   `make autostart` and a uv install run the same code; the bash autostart script is gone.
   The version is `[project].version`, read via `importlib.metadata`, reported on `/api/health`
   and by `onw version`; a checkout that was never installed reports `0.0.0+dev`.
2. **A multi-arch image at `ghcr.io/open-net-worth-ai/open-net-worth`**, built by `release.yml`
   on a `vX.Y.Z` tag that must equal the pyproject version. `docker-compose.yml` PULLS it and
   declares `AUTH_PASSWORD` REQUIRED (`${AUTH_PASSWORD:?…}`), so Compose refuses to start with a
   message rather than boot an instance nobody can operate; `compose.build.yml` overlays a
   from-source build.
3. **`scripts/install.sh`** (`curl | sh`): installs uv if missing, `uv tool install --python 3.12
   open-net-worth`, optional `--autostart`. Windows stays Docker-only.

CI's new `package` job is the evidence: build the wheel, install it into a clean environment,
boot `onw start` from OUTSIDE the checkout on an isolated `ONW_ROOT` + `ONW_QA_MODE=1`, and
probe `/api/health`, `/` and `/docs/`. `docker.yml` does the same for the image and asserts that
Compose refuses a missing password.

## Consequences

### Positive

- A no-prerequisite install on macOS/Linux in one to three minutes; a one-line Docker install on
  any OS including Windows; one release workflow producing both.
- The packaged form is exercised in CI on every push — its status is earned, not asserted.
- The Docker lockout becomes a refusal with a message at `docker compose up`.
- One `autostart` implementation (Python, unit-tested against a fake `launchctl`/`systemctl`)
  instead of a bash script nothing tested; `stop` now deletes the unit file, so a "stopped"
  LaunchAgent no longer comes back at the next login.

### Negative

- `backend` is a poor top-level name on PyPI. Harmless in uv's isolated tool environment, ugly
  under a plain `pip install` into a shared venv.
- `backend/cli/` adds a layer above `api` (import-linter contract 1 now reads
  `cli → api → ai → services → db → core`).
- A release needs a PyPI trusted-publisher entry and ghcr permissions — owner actions.
- `backend/_bundled/` is a copy step that must run before `uv build`; forgetting it produces a
  wheel with no UI (the CI package job would catch it).
- Docker users must supply a password up front. Deliberate.
- ~8 MB of static assets per release on PyPI.

### Follow-ups

- Owner: create the PyPI pending publisher (project `open-net-worth`, repo
  `open-net-worth-ai/open-net-worth`, workflow `release.yml`, environment `pypi`), then tag
  `v0.1.0`. The README's install line only works after that first publish.
- Native desktop app (menu-bar launcher + signed `.dmg`/AppImage): deferred until the release
  pipeline exists and non-technical demand is shown; it is "the wheel plus a shell".
- Native Windows port: deferred; triggers are a Windows user blocked by Docker, or the desktop
  app being built (that audience is majority Windows).

## Alternatives considered

- **Docker image only** — rejected: Docker Desktop is a heavier prerequisite than uv for a
  Mac/Linux user, and the loopback-trust model that makes a local install password-free never
  engages under Docker.
- **Native desktop app first** (PyInstaller-frozen backend + tray launcher, or a Tauri/Electron
  shell) — rejected for now: needs Apple signing/notarization, an updater, PyInstaller
  hidden-import work across pymupdf/pillow-heif/psycopg, and a Windows port to be worth it.
- **Rename the import package to `open_net_worth`** — rejected now: hundreds of imports,
  import-linter roots and tests for zero user-visible benefit under `uv tool` isolation.
- **Move deps into `[project].dependencies` and export `requirements.txt` from the lock** —
  rejected: it would silently change Docker/Makefile/CI installs from loose constraints to
  lock-pinned ones; the hook keeps one source of truth without touching them.
- **Bundled copy wins over repo copy** — rejected: a stale local `_bundled/` from an earlier
  `make wheel` would shadow live source in every checkout and in Docker.
- **Trust the Docker bridge gateway as loopback** — rejected: a security-model change with LAN
  exposure implications; a required password is the honest fix.
- **Native Windows port now** — deferred: ~20 call sites in 7 files is a day or two, but the
  upkeep (a Windows CI runner, a second implementation of every lock/subprocess change, Task
  Scheduler autostart, sandbox rlimits with no equivalent) is permanent, and Docker already
  covers Windows.

## Agent provenance

- **Decided by:** orchestrating Claude agent (Fable 5.1), path chosen by the repo owner in chat
- **Lane / branch:** `feat/easy-install`
- **Alternatives weighed:** Docker-only; native desktop app first; uv tool + published image
  (chosen); Windows port now vs deferred
- **Driving artifacts:** owner request (2026-09-19 session); README "Is this for you?";
  `docs/product/getting-started/first-run.md#docker` lockout; CLAUDE.md hardening loop ("status
  is earned by evidence, never asserted")
- **Why this way (one line):** the app was already one process, password-free on loopback, with
  its data outside the checkout — the only thing between a user and a 5-minute install was the
  developer toolchain, and a wheel plus a published image removes exactly that and nothing else.
