#!/usr/bin/env bash
# Open Net Worth — one-command local setup. Backs `make setup`.
#
# venv -> backend deps -> frontend deps -> frontend build -> docs build -> DB migration -> URL.
# Idempotent: safe to re-run (venv creation, pip install, npm ci, and the DB migration are
# all no-ops when there's nothing new to do). Every prerequisite check fails LOUDLY with an
# actionable message instead of letting a later step die with a confusing traceback.
#
# macOS/Linux (and WSL2) only — see the Windows check below.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
REPO_ROOT="$(pwd)"

# ── Windows detection ─────────────────────────────────────────────────────────
# backend/core/io.py does a bare `import fcntl` (POSIX-only file locking), so the
# backend cannot even import on native Windows. `make`/this script only run at all
# on Windows via a POSIX-ish shell (Git Bash/MSYS/Cygwin) or WSL2 — WSL2 is a real
# Linux kernel (`uname -s` reports "Linux") and is fully supported; the other three
# are native Windows underneath and must be refused here, not left to fail deep
# inside a Python import with an unreadable traceback.
case "$(uname -s 2>/dev/null || echo unknown)" in
  MINGW*|MSYS*|CYGWIN*)
    cat >&2 <<'EOF'

ERROR: native Windows is not supported.

  backend/core/io.py imports the POSIX-only `fcntl` module (and the sandbox/MCP
  process-group signals need `os.killpg`), so the backend cannot even import on
  native Windows.

  Use one of:
    - docker compose up   (works on any OS — see README.md "Option B — Docker")
    - WSL2 (Windows Subsystem for Linux) — a real Linux kernel, fully supported;
      re-run `make setup` from inside a WSL2 shell instead of Git Bash/MSYS/Cygwin.

  See docs/adr/2026-08-23-zero-config-default-data-dir.md.
EOF
    exit 1
    ;;
esac

log() { printf '==> %s\n' "$1"; }
fail() {
  printf '\nERROR: %s\n' "$1" >&2
  exit 1
}

# ── Prerequisite: Python 3.12+ ────────────────────────────────────────────────
PY_INTERP=""
for candidate in python3.12 python3.13 python3.14 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
      PY_INTERP="$(command -v "$candidate")"
      break
    fi
  fi
done
# Prefer uv's own Python 3.12 when uv is present: a venv built on a Homebrew interpreter dangles
# the moment `brew upgrade` deletes that patch version, and the live server dies on its next start.
if command -v uv >/dev/null 2>&1; then
  uv python install 3.12 >/dev/null 2>&1 || true
  uv_py="$(cd / && uv python find --managed-python --no-project 3.12 2>/dev/null || true)"
  [ -n "$uv_py" ] && PY_INTERP="$uv_py"
fi
if [ -z "$PY_INTERP" ]; then
  found_version="$(command -v python3 >/dev/null 2>&1 && python3 --version 2>&1 || echo 'not found')"
  fail "Python 3.12+ is required (pyproject.toml: requires-python = \">=3.12\"). Found: $found_version.
  Install Python 3.12+ (e.g. \`brew install python@3.12\` on macOS, or your distro's package
  manager on Linux) and re-run \`make setup\`."
fi
log "Python OK: $("$PY_INTERP" --version) ($PY_INTERP)"

# ── Prerequisite: Node.js 22+ + npm ───────────────────────────────────────────
if ! command -v node >/dev/null 2>&1; then
  fail "Node.js 22+ is required to build the frontend, and no \`node\` was found on PATH.
  Install it (e.g. \`brew install node\` on macOS, or via nvm: https://github.com/nvm-sh/nvm)
  and re-run \`make setup\`. (Skip this entirely with \`docker compose up\` instead.)"
fi
if ! command -v npm >/dev/null 2>&1; then
  fail "npm was not found on PATH (node is present, but npm should ship with it).
  Reinstall Node.js and re-run \`make setup\`."
fi
node_major="$(node -e 'console.log(process.versions.node.split(".")[0])' 2>/dev/null || echo 0)"
if [ "${node_major:-0}" -lt 22 ] 2>/dev/null; then
  fail "Node.js 22+ is required. Found: $(node --version).
  Upgrade Node.js (e.g. via nvm: \`nvm install 22\`) and re-run \`make setup\`."
fi
log "Node OK: $(node --version)"

# ── venv ───────────────────────────────────────────────────────────────────────
if [ ! -x "$REPO_ROOT/.venv/bin/python3" ]; then
  log "Creating .venv ..."
  "$PY_INTERP" -m venv --clear "$REPO_ROOT/.venv"
else
  log ".venv already exists — reusing it."
fi
VENV_PY="$REPO_ROOT/.venv/bin/python3"

# ── Backend deps ─────────────────────────────────────────────────────────────
log "Installing backend dependencies ..."
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet -r "$REPO_ROOT/requirements.txt"
# Editable, no deps (they were just installed): this is what puts `onw` in .venv/bin and
# makes `make autostart` and a `uv tool` install run the same code path.
log "Installing the onw command (editable — runs this checkout) ..."
"$VENV_PY" -m pip install --quiet --no-deps -e "$REPO_ROOT"

# ── TLS trust store check ─────────────────────────────────────────────────────
# A python.org macOS build whose "Install Certificates.command" was never run has NO CA
# bundle, so every stdlib HTTPS call fails CERTIFICATE_VERIFY_FAILED. The app self-heals
# at boot (core.pinned_http.ensure_default_trust_store falls back to certifi), so this is
# advisory: tell the operator how to fix the interpreter itself.
"$VENV_PY" - <<'PY' || true
import os, ssl, sys
paths = ssl.get_default_verify_paths()
cafile = paths.cafile or paths.openssl_cafile
if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR") or (cafile and os.path.isfile(cafile)):
    sys.exit(0)
print(f"==> WARNING: this Python has no CA bundle at {cafile}. The app will use certifi's bundle,")
print("    but other tools on this interpreter will fail TLS verification. On a python.org")
print(f"    macOS build, run: \"/Applications/Python {sys.version_info.major}.{sys.version_info.minor}/Install Certificates.command\"")
PY

# ── Frontend deps + build ────────────────────────────────────────────────────
log "Installing frontend dependencies ..."
(cd "$REPO_ROOT/frontend" && npm ci)
log "Building frontend (this serves from frontend/dist/ — no separate frontend server needed) ..."
(cd "$REPO_ROOT/frontend" && npm run build)
log "Building the in-app documentation (Help opens this; no network needed at runtime) ..."
(cd "$REPO_ROOT/docs-site" && npm ci && npm run build:embed)

# ── DB migration ──────────────────────────────────────────────────────────────
# Fail-closed, idempotent (backend/db/migrate.py::ensure_migrated) — a no-op if
# already at head, a verified-backup-then-upgrade if not. Runs against whatever
# DATA_DIR/ONW_DATABASE_URL currently resolves to (the new zero-config default,
# ~/.open-net-worth/data, unless overridden) — exactly the real target this
# install is for.
log "Running database migrations ..."
"$VENV_PY" -c "from backend.db.migrate import ensure_migrated; ensure_migrated(allow_live_instance=True)"

# ── Done — print the URL ─────────────────────────────────────────────────────
host_port="$("$VENV_PY" -c "
from backend.core.config import get_settings
s = get_settings()
print(f'{s.host}:{s.port}')
")"
echo ""
echo "==> Setup complete."
echo "    Data directory: $("$VENV_PY" -c 'from backend.core.config import get_settings; print(get_settings().data_dir)')"
echo "    Run the app:    make start   (or: .venv/bin/onw start --open)"
echo "    Then open:      http://${host_port}"
echo ""
