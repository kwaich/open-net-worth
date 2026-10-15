#!/usr/bin/env bash
# Run the public, fabricated-data-only demo (demo.opennetworth.ai).
#
# Two shapes, one script:
#   ./scripts/run_demo.sh              throwaway — a fresh book in a temp dir, erased on exit
#   ONW_DEMO_ROOT=/srv/onw-demo ./scripts/run_demo.sh
#                                      hosted — a book that survives a restart, in that dir
#
# Either way the data is BUILT, never copied: backend.scripts.seed_demo generates the whole
# book relative to today, so a demo restarted next month shows next month's data instead of a
# fixture rotting in place. `--rebuild` throws the hosted book away and builds a new one, which
# is how you clear whatever visitors did to the last one.
#
# The app runs under `env -i` so a public instance inherits nothing from the launching shell.
# USER is the one exception, and it is not a credential: the Claude Code CLI needs it to reach
# its own login (macOS keychain lookups key off it), and without it every Lucius turn dies with
# "Not logged in - Please run /login" while the CLI still reports itself Ready. Measured, not
# guessed: with only USER added the CLI answers; LOGNAME or SHELL alone do not fix it.
#
# Credentials: none, unless you opt in. ONW_DEMO_ANTHROPIC_API_KEY is passed through to the
# demo process as ANTHROPIC_API_KEY so Lucius can answer on a demo you are hosting on purpose;
# your ambient ANTHROPIC_API_KEY is NOT — a public instance must never inherit a key by
# accident. Every other provider/connector credential is blanked unconditionally.
set -euo pipefail

FRONTEND_PORT="5175"
BACKEND_PORT="8175"
DEMO_HOSTNAME="demo.opennetworth.ai"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Interpreter resolution, most-specific first. The repo venv is the documented
# local setup ('make install'), but CI installs the requirements into the
# runner's ambient Python and never creates a .venv — hardcoding the venv path
# made --smoke exit 1 there before it did any work.
if [[ -n "${ONW_PYTHON:-}" ]]; then
  PYTHON="${ONW_PYTHON}"
elif [[ -x "${REPO_ROOT}/.venv/bin/python3" ]]; then
  PYTHON="${REPO_ROOT}/.venv/bin/python3"
elif [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python3" ]]; then
  PYTHON="${VIRTUAL_ENV}/bin/python3"
else
  PYTHON="$(command -v python3 || true)"
fi
DEMO_ROOT=""
EPHEMERAL=""
BACKEND_PID=""
FRONTEND_PID=""

MODE=""
REBUILD=""
for arg in "$@"; do
  case "${arg}" in
    --smoke) MODE="smoke" ;;
    --rebuild) REBUILD="1" ;;
    *) echo "ERROR: unknown argument: ${arg}" >&2; exit 2 ;;
  esac
done

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM

  if [[ -n "${FRONTEND_PID}" ]] && kill -0 "${FRONTEND_PID}" 2>/dev/null; then
    kill "${FRONTEND_PID}" 2>/dev/null || true
    wait "${FRONTEND_PID}" 2>/dev/null || true
  fi
  if [[ -n "${BACKEND_PID}" ]] && kill -0 "${BACKEND_PID}" 2>/dev/null; then
    kill "${BACKEND_PID}" 2>/dev/null || true
    wait "${BACKEND_PID}" 2>/dev/null || true
  fi

  # A HOSTED root is the operator's directory and is never removed here — only the
  # temp one this script minted itself, matched against the exact pattern it used.
  if [[ -n "${EPHEMERAL}" && -n "${DEMO_ROOT}" && -d "${DEMO_ROOT}" ]]; then
    case "${DEMO_ROOT}" in
      "${DEMO_TMP_BASE}"/onw-demo.*) rm -rf "${DEMO_ROOT}" ;;
      *) echo "ERROR: refusing to remove unexpected demo path: ${DEMO_ROOT}" >&2 ;;
    esac
  fi
  return "${exit_code}"
}
trap cleanup EXIT INT TERM

port_is_busy() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN -t >/dev/null 2>&1
}

wait_for_url() {
  local label="$1"
  local url="$2"
  local host_header="${3:-}"
  local attempt
  for attempt in $(seq 1 30); do
    if [[ -n "${host_header}" ]]; then
      curl -fsS -H "Host: ${host_header}" "${url}" >/dev/null 2>&1 && return 0
    else
      curl -fsS "${url}" >/dev/null 2>&1 && return 0
    fi
    sleep 1
  done
  echo "ERROR: ${label} did not become ready at ${url}" >&2
  return 1
}

if [[ -z "${PYTHON}" || ! -x "${PYTHON}" ]]; then
  echo "ERROR: no usable python3 found (looked at ONW_PYTHON, ${REPO_ROOT}/.venv, \$VIRTUAL_ENV, PATH). Run 'make install' first." >&2
  exit 1
fi

if [[ "${MODE}" != "smoke" ]]; then
  for port in "${FRONTEND_PORT}" "${BACKEND_PORT}"; do
    if port_is_busy "${port}"; then
      echo "ERROR: port ${port} is already in use; refusing to drive a stale server." >&2
      exit 1
    fi
  done
fi

DEMO_TMP_BASE="${TMPDIR:-/tmp}"
DEMO_TMP_BASE="${DEMO_TMP_BASE%/}"
# --rebuild runs `rm -rf` on a path the operator supplied, so it refuses anything that is
# not recognisably a demo root: a demo root is empty, or contains nothing but the three
# directories this script creates. Pointing ONW_DEMO_ROOT at a home directory and asking for
# a rebuild should print a refusal, not erase it.
assert_rebuildable() {
  local root="$1"
  # Hard refusals first: paths that are never a demo root no matter what they contain.
  case "${root}" in
    ""|"/"|"${HOME}"|"${HOME}/"|"${REPO_ROOT}")
      echo "ERROR: refusing to --rebuild ${root:-<empty>} — that is not a demo root." >&2
      exit 1 ;;
  esac
  # Then PROVE it is one, rather than allow-listing what may be inside. An allowlist has to
  # enumerate every file the app might create — `backups/`, `.instance.lock`, whatever comes
  # next — and each omission refuses a legitimate reset. The presence of `data/onw.db` is
  # positive evidence that this directory IS an Open Net Worth instance, which is the thing
  # actually being asserted. An empty or absent directory is fine too: nothing to destroy.
  if [[ -e "${root}" && ! -f "${root}/data/onw.db" ]]; then
    if [[ -n "$(ls -A "${root}" 2>/dev/null)" ]]; then
      echo "ERROR: refusing to --rebuild ${root} — it is not empty and holds no" >&2
      echo "       data/onw.db, so it does not look like a demo instance. Remove it yourself" >&2
      echo "       if you really mean to." >&2
      exit 1
    fi
  fi
}

if [[ -n "${ONW_DEMO_ROOT:-}" && "${MODE}" != "smoke" ]]; then
  DEMO_ROOT="${ONW_DEMO_ROOT%/}"
  if [[ -n "${REBUILD}" && -d "${DEMO_ROOT}" ]]; then
    assert_rebuildable "${DEMO_ROOT}"
    # Carry the hand-arranged dashboard across the rebuild. It is the only thing in the book
    # that is somebody's actual work rather than generated data, and a rebuild would otherwise
    # silently replace it with the seeded default — with no undo, since a layout save leaves no
    # before-image to roll back to. Saved OUTSIDE the directory about to be deleted.
    LAYOUT_CARRY="${DEMO_TMP_BASE}/onw-demo-layout.$$.json"
    # A STABLE session signing key, kept beside the book rather than inside it.
#
# The key normally lives in the book's own secret_config, so regenerating the book mints a new
# one — and every visitor's cookie stops verifying. The app's 401 handler then clears the query
# cache and flips to "not authenticated", and when auth re-resolves the index redirect sends
# them to `localStorage.wealthTab`. To the visitor that reads as "I tapped a row, it went
# somewhere, then threw me back to the tab" — which is exactly the bug reported on this demo,
# and nothing about it looks like a login problem.
#
# AuthStore resolves the key as store > env > generated, so seeding AUTH_SESSION_KEY from a file
# that OUTLIVES the book keeps every session valid across a reset. It is a sibling of DEMO_ROOT,
# not a child, because `--rebuild` deletes the root.
if [[ -n "${ONW_DEMO_ROOT:-}" ]]; then
  DEMO_KEY_FILE="${DEMO_ROOT}.session-key"
  if [[ ! -s "${DEMO_KEY_FILE}" ]]; then
    ( umask 077; "${PYTHON}" -c 'import secrets; print(secrets.token_urlsafe(48))' > "${DEMO_KEY_FILE}" )
    echo "==> minted a demo session key (${DEMO_KEY_FILE})"
  fi
  DEMO_SESSION_KEY="$(cat "${DEMO_KEY_FILE}")"
fi

DATA_DIR="${DEMO_ROOT}/data" CACHE_DIR="${DEMO_ROOT}/cache" LOGS_DIR="${DEMO_ROOT}/logs" \
      "${PYTHON}" -m backend.scripts.demo.layout_io export "${LAYOUT_CARRY}" 2>/dev/null \
      && echo "==> kept your dashboard arrangement" || true
    echo "==> --rebuild: clearing ${DEMO_ROOT}"
    rm -rf "${DEMO_ROOT}"
  fi
else
  DEMO_ROOT="$(mktemp -d "${DEMO_TMP_BASE}/onw-demo.XXXXXX")"
  EPHEMERAL="1"
fi
DATA_DIR="${DEMO_ROOT}/data"
CACHE_DIR="${DEMO_ROOT}/cache"
LOGS_DIR="${DEMO_ROOT}/logs"
mkdir -p "${DATA_DIR}" "${CACHE_DIR}" "${LOGS_DIR}"

cd "${REPO_ROOT}"
# DATA_DIR/CACHE_DIR/LOGS_DIR must be EXPORTED for both steps below: backend.core.paths
# resolves them from the environment at import time, and every store is DB-backed — the
# builder writes through whatever backend.db.engine.get_engine() resolves to, which is these
# same env vars. There is no --dest to point at: a table is not a directory.
# The isolated DB also does not exist yet at this point (ensure_migrated() normally runs at
# app startup, which for --smoke never happens), so the builder migrates it to head first.
#
# The builder REFUSES a non-empty book, so re-running against a hosted root is a safe no-op
# that leaves whatever is already there — only --rebuild replaces it.
DATA_DIR="${DATA_DIR}" CACHE_DIR="${CACHE_DIR}" LOGS_DIR="${LOGS_DIR}" \
  "${PYTHON}" -m backend.scripts.seed_demo --commit --quiet || {
    status=$?
    # 2 = "book already has data", which is the expected outcome on a hosted restart.
    [[ "${status}" -eq 2 ]] || exit "${status}"
    echo "==> existing demo book kept (pass --rebuild to replace it)"
  }

# Lucius runs on the Claude Code CLI by default. You are hosting this demo, so it runs on the
# `claude` login already on this machine — there is no separate service account to create and no
# API key to put on a public box. What bounds visitors is DEMO_TOKENS_* below, not the provider.
#
#   ONW_DEMO_MODEL=none ./scripts/run_demo.sh                  # leave Lucius unconfigured
#   ONW_DEMO_MODEL=anthropic:claude-sonnet-5 ...               # a separately-billed key instead
#
# Skipped when the book already had a provider (a hosted restart) so this never overwrites a
# choice made in Settings, and skipped silently when the named CLI is not installed.
DEMO_MODEL="${ONW_DEMO_MODEL:-claude_cli:sonnet}"
if [[ "${MODE}" != "smoke" && "${DEMO_MODEL}" != "none" ]]; then
  DATA_DIR="${DATA_DIR}" CACHE_DIR="${CACHE_DIR}" LOGS_DIR="${LOGS_DIR}" \
    "${PYTHON}" - "${DEMO_MODEL}" <<'PYSETUP' || echo "==> could not set the demo model (continuing without one)"
import shutil
import sys

from backend.db.model_config_store import ModelConfigStore

ref = sys.argv[1]
store = ModelConfigStore()
if (store.public().get("default_model") or "").strip():
    print("==> keeping the model already configured on this book")
    raise SystemExit(0)
if ref.startswith("claude_cli:") and not shutil.which("claude"):
    print("==> Claude Code CLI not on PATH — leaving Lucius unconfigured")
    raise SystemExit(0)
store.update({"default_model": ref})
print(f"==> Lucius will run on {ref}")
PYSETUP
fi

# Restore the arrangement carried across the rebuild, now that the new book exists.
if [[ -n "${LAYOUT_CARRY:-}" && -f "${LAYOUT_CARRY}" ]]; then
  DATA_DIR="${DATA_DIR}" CACHE_DIR="${CACHE_DIR}" LOGS_DIR="${LOGS_DIR}" \
    "${PYTHON}" -m backend.scripts.demo.layout_io import "${LAYOUT_CARRY}" >/dev/null 2>&1 \
    && echo "==> restored your dashboard arrangement" || true
  rm -f "${LAYOUT_CARRY}"
fi

if [[ "${MODE}" == "smoke" ]]; then
  SUMMARY="$(
    DATA_DIR="${DATA_DIR}" CACHE_DIR="${CACHE_DIR}" LOGS_DIR="${LOGS_DIR}" \
      "${PYTHON}" -c '
import json
from backend.db.account_store import AccountStore
from backend.db.transaction_store import TransactionStore
from backend.services.profile import read_profile_raw
profile = read_profile_raw()
print(json.dumps({
    "profile_name": profile.get("name"),
    "is_demo": bool(profile.get("is_demo")),
    "accounts": AccountStore().count(),
    "transactions": TransactionStore().count(),
    "seed_source": "backend.scripts.seed_demo",
}))'
  )"
  cleanup
  DEMO_ROOT=""
  "${PYTHON}" -c 'import json,sys; d=json.loads(sys.argv[1]); d["data_dir_removed"]=True; print(json.dumps(d))' "${SUMMARY}"
  exit 0
fi

# Visitors need no password: DEMO_MODE + DEMO_OPEN_ACCESS make the backend treat every request as
# signed in (ADR 2026-10-07-the-public-demo-needs-no-password). The book still carries a password
# NOBODY KNOWS — a fresh random one per launch, never printed or published — so the instance
# stays "configured": the first-run set-a-password path is closed, loopback is not trusted, and the
# Bearer-only /mcp endpoint keeps its token. The launcher is the only holder, and does not use it.
DEMO_UNPUBLISHED_PASSWORD="$("${PYTHON}" -c 'import secrets; print(secrets.token_urlsafe(32))')"

# A FUNCTION, not a one-shot: the supervisor loop below relaunches the backend when it dies,
# and the only safe way to do that is to replay the exact same argv/env. Inlining this once
# and copying it for the restart is how the two drift.
start_backend() {
/usr/bin/env -i \
  PATH="${PATH}" HOME="${HOME}" LANG="${LANG:-C.UTF-8}" TMPDIR="${TMPDIR:-/tmp}" \
  USER="${USER:-}" \
  DATA_DIR="${DATA_DIR}" CACHE_DIR="${CACHE_DIR}" LOGS_DIR="${LOGS_DIR}" \
  HOST="127.0.0.1" PORT="${BACKEND_PORT}" \
  DEMO_MODE="1" DEMO_OPEN_ACCESS="1" \
  DEMO_TURNS_PER_WINDOW="${ONW_DEMO_TURNS_PER_WINDOW:-30}" \
  DEMO_TURN_WINDOW_S="${ONW_DEMO_TURN_WINDOW_S:-300}" \
  DEMO_TOKENS_PER_IP_DAILY="${ONW_DEMO_TOKENS_PER_IP_DAILY:-3000000}" \
  DEMO_TOKENS_TOTAL_DAILY="${ONW_DEMO_TOKENS_TOTAL_DAILY:-30000000}" \
  DEMO_TRUSTED_PROXY_HOPS="${ONW_DEMO_TRUSTED_PROXY_HOPS:-1}" \
  AUTH_DISABLED="false" AUTH_TRUST_LOOPBACK="false" \
  AUTH_PASSWORD="${DEMO_UNPUBLISHED_PASSWORD}" \
  AUTH_SESSION_KEY="${DEMO_SESSION_KEY:-}" \
  ONW_DISABLE_TELEGRAM="1" \
  ANTHROPIC_API_KEY="${ONW_DEMO_ANTHROPIC_API_KEY:-}" \
  OPENAI_API_KEY="" \
  "${PYTHON}" -m uvicorn backend.main:app \
  --host 127.0.0.1 --port "${BACKEND_PORT}" \
  >>"${LOGS_DIR}/backend.log" 2>&1 &
BACKEND_PID=$!
}

echo "==> Starting isolated backend on 127.0.0.1:${BACKEND_PORT}..."
start_backend

# The frontend is BUILT once and served from `vite preview`, never the dev server. A hosted
# demo must not be recompiling modules on a visitor's first click, and HMR would reload
# everyone's tab whenever a file on the host changed. `vite.config.ts` already carries a
# `preview` block with this hostname allow-listed and its own /api proxy — `preview` does not
# inherit `server.proxy`, which is why that block exists separately.
# Build only when there is nothing to serve, or when asked. Vite hashes every chunk, so a
# rebuild changes every filename — and any tab still open from the previous build then fails
# every lazy import with "Failed to fetch dynamically imported module". Rebuilding on each
# `make demo-reset` would do that to every visitor who had the demo open, for a bundle that
# usually did not change at all. Force one with ONW_DEMO_REBUILD_FRONTEND=1.
if [[ ! -f "${REPO_ROOT}/frontend/dist/index.html" || -n "${ONW_DEMO_REBUILD_FRONTEND:-}" ]]; then
  echo "==> Building the frontend..."
  (
    cd frontend
    /usr/bin/env -i \
      PATH="${PATH}" HOME="${HOME}" LANG="${LANG:-C.UTF-8}" TMPDIR="${TMPDIR:-/tmp}" \
      VITE_KEEP_OLD_CHUNKS=1 \
      npm run build
  ) >"${LOGS_DIR}/frontend-build.log" 2>&1 || {
    echo "ERROR: frontend build failed" >&2
    tail -n 40 "${LOGS_DIR}/frontend-build.log" >&2
    exit 1
  }
else
  echo "==> Reusing the existing frontend build (ONW_DEMO_REBUILD_FRONTEND=1 to rebuild)"
fi

echo "==> Serving the built frontend on 0.0.0.0:${FRONTEND_PORT}..."
(
  cd frontend
  /usr/bin/env -i \
    PATH="${PATH}" HOME="${HOME}" LANG="${LANG:-C.UTF-8}" TMPDIR="${TMPDIR:-/tmp}" \
    VITE_API_URL="http://127.0.0.1:${BACKEND_PORT}" \
    npx vite preview --host 0.0.0.0 --port "${FRONTEND_PORT}" --strictPort
) >"${LOGS_DIR}/frontend.log" 2>&1 &
FRONTEND_PID=$!

wait_for_url "backend" "http://127.0.0.1:${BACKEND_PORT}/api/health" || {
  tail -n 80 "${LOGS_DIR}/backend.log" >&2
  exit 1
}
wait_for_url "frontend" "http://127.0.0.1:${FRONTEND_PORT}/" "${DEMO_HOSTNAME}" || {
  tail -n 80 "${LOGS_DIR}/frontend.log" >&2
  exit 1
}

echo
echo "Demo is running with fabricated data only."
echo "  Public host: https://${DEMO_HOSTNAME}"
echo "  Local URL:   http://127.0.0.1:${FRONTEND_PORT}"
echo "  Backend:     http://127.0.0.1:${BACKEND_PORT}"
echo "  Access:      open — no login (visitors land straight in the app)"
echo "  Data:        ${DATA_DIR}${EPHEMERAL:+  (temporary — erased on exit)}"
echo "  Lucius:      ${DEMO_MODEL} · limits ${ONW_DEMO_TURNS_PER_WINDOW:-30} turns/${ONW_DEMO_TURN_WINDOW_S:-300}s, ${ONW_DEMO_TOKENS_PER_IP_DAILY:-3000000} tok/visitor/day, ${ONW_DEMO_TOKENS_TOTAL_DAILY:-30000000} tok/day total"
echo "Press Ctrl-C to stop."

# ── Supervisor ───────────────────────────────────────────────────────────────
# The backend can die in a way NO Python handler can catch: a native abort inside a C
# extension takes the whole interpreter with it. That is not hypothetical here — libxml2
# aborted twice on 2026-08-25 (SIGABRT, `find_zone_and_free` under lxml's HTML parser,
# reached through trafilatura while researching an asset), and each time the process simply
# vanished mid-request. This loop used to EXIT on that, and `cleanup` then killed the
# frontend too, so a public demo stayed dark until a human noticed — ~11 hours across two
# outages. Restart the backend instead.
#
# The cap is the other half: a demo whose backend cannot start at all (a bad build, a failed
# migration) must fail LOUDLY rather than spin forever pretending to recover. Counting per
# WINDOW, not for all time, is what keeps a long-lived healthy demo from eventually
# exhausting its budget on unrelated crashes months apart.
#
# Arithmetic is `[ ]`, never `(( ))`: under `set -e` a false `(( ))` returns 1 and would kill
# the supervisor — the one loop in this script that must never exit by accident.
RESTART_MAX="${ONW_DEMO_RESTART_MAX:-20}"
RESTART_WINDOW_S="${ONW_DEMO_RESTART_WINDOW_S:-600}"
restarts=0
window_start=${SECONDS}

while :; do
  if ! kill -0 "${FRONTEND_PID}" 2>/dev/null; then
    echo "ERROR: the demo frontend exited unexpectedly." >&2
    tail -n 40 "${LOGS_DIR}/frontend.log" >&2 || true
    exit 1
  fi

  if ! kill -0 "${BACKEND_PID}" 2>/dev/null; then
    code=0
    wait "${BACKEND_PID}" 2>/dev/null || code=$?

    if [ "$(( SECONDS - window_start ))" -gt "${RESTART_WINDOW_S}" ]; then
      restarts=0
      window_start=${SECONDS}
    fi
    restarts=$(( restarts + 1 ))

    if [ "${restarts}" -gt "${RESTART_MAX}" ]; then
      echo "ERROR: backend died ${restarts} times within ${RESTART_WINDOW_S}s — not restarting again." >&2
      tail -n 40 "${LOGS_DIR}/backend.log" >&2 || true
      exit 1
    fi

    msg="$(date '+%Y-%m-%dT%H:%M:%S') SUPERVISOR backend exited (status ${code}) — restarting (${restarts}/${RESTART_MAX} in this ${RESTART_WINDOW_S}s window)"
    echo "${msg}" >&2
    echo "${msg}" >>"${LOGS_DIR}/backend.log"

    start_backend
    if ! wait_for_url "backend" "http://127.0.0.1:${BACKEND_PORT}/api/health"; then
      echo "ERROR: backend did not answer /api/health after a restart." >&2
      tail -n 40 "${LOGS_DIR}/backend.log" >&2 || true
      exit 1
    fi
    echo "$(date '+%Y-%m-%dT%H:%M:%S') SUPERVISOR backend is serving again" >&2
  fi

  sleep 1
done
