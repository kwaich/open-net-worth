#!/usr/bin/env bash
# boot_app.sh — Start the Open Net Worth backend + frontend for QA.
#
# Usage:
#   ./boot_app.sh [--backend-port PORT] [--frontend-port PORT] [--data-dir DIR]
#   source <(./boot_app.sh --env)   # export APP_* vars into calling shell
#
# The script:
#   1. Seeds QA data via `make qa-data` (clone_data_for_qa, secrets stripped).
#   2. Builds the frontend so it can be served statically.
#   3. Boots the FastAPI backend on an explicit high port (default 8091).
#   4. Serves the built frontend via `npm run preview` on a second high port
#      (default 8092) with the backend URL injected as VITE_API_URL.
#   5. Health-checks both servers (timeout 60 s each).
#   6. Writes PIDs to .qa_pids so the runner can tear down on exit.
#   7. Prints APP_URL and BACKEND_URL for the runner.
#
# Constraints (from CLAUDE.md):
#   - Backend MUST bind a HIGH port (8090-8099), NEVER 8080.
#   - ONW_DISABLE_TELEGRAM=1 always set.
#   - Servers are background processes; this script exits after health-checks.
#   - The QA runner tears them down via kill $(cat .qa_pids).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

BACKEND_PORT="${BACKEND_PORT:-8091}"
FRONTEND_PORT="${FRONTEND_PORT:-8092}"
DATA_DIR="${DATA_DIR:-}"
PIDS_FILE="${SCRIPT_DIR}/.qa_pids"
LOG_DIR="${SCRIPT_DIR}/artifacts/logs"

mkdir -p "${LOG_DIR}"

# ── DATA SAFETY GUARD ────────────────────────────────────────────────────────
# Refuse to boot if DATA_DIR points at the operator's real data or the repo seed.
# This is the FIRST check, before any data is read or written.  The backend also
# enforces this independently via ONW_QA_MODE=1 (set below), but an early bash
# check gives a much clearer error message and avoids starting uvicorn at all.
_assert_data_dir_safe() {
  local dir="$1"
  local real_home_data
  real_home_data="$(cd "${HOME}" && echo "${HOME}/.open-net-worth/data")"
  local real_repo_data="${REPO_ROOT}/backend/data"

  if [[ -z "${dir}" ]]; then
    echo "ERROR: DATA_DIR is empty — refusing to boot without an isolated data dir." >&2
    echo "       Run:  export DATA_DIR=\$(make qa-data)  before calling boot_app.sh" >&2
    exit 1
  fi

  # Resolve to canonical path for comparison (follow symlinks if present).
  local resolved_dir
  resolved_dir="$(cd "${dir}" 2>/dev/null && pwd -P || echo "${dir}")"

  local resolved_home_data
  resolved_home_data="$(cd "${real_home_data}" 2>/dev/null && pwd -P || echo "${real_home_data}")"

  local resolved_repo_data
  resolved_repo_data="$(cd "${real_repo_data}" 2>/dev/null && pwd -P || echo "${real_repo_data}")"

  if [[ "${resolved_dir}" == "${resolved_home_data}" ]]; then
    echo "ERROR: DATA_DIR resolves to the REAL owner data dir: ${resolved_dir}" >&2
    echo "       Refusing to boot a QA server against live data." >&2
    echo "       Fix: export DATA_DIR=\$(make qa-data)" >&2
    exit 1
  fi

  if [[ "${resolved_dir}" == "${resolved_repo_data}" ]]; then
    echo "ERROR: DATA_DIR resolves to the in-repo seed data: ${resolved_dir}" >&2
    echo "       Refusing to boot a QA server against the tracked repo data." >&2
    echo "       Fix: export DATA_DIR=\$(make qa-data)" >&2
    exit 1
  fi

  # Also reject anything inside ~/.open-net-worth/ (catches subdirs of the live instance).
  local home_onw
  home_onw="$(cd "${HOME}" && echo "${HOME}/.open-net-worth")"
  if [[ "${resolved_dir}" == "${home_onw}"* ]]; then
    echo "ERROR: DATA_DIR (${resolved_dir}) is inside ${home_onw}." >&2
    echo "       Refusing to boot a QA server inside the live instance directory." >&2
    echo "       Fix: export DATA_DIR=\$(make qa-data)" >&2
    exit 1
  fi
}

# Track PIDs so the EXIT trap can clean up even on early failure.
BACKEND_PID=""
FRONTEND_PID=""

_cleanup() {
  # Kill both child processes by recorded PID, then by process-name pattern,
  # then by port — triple belt-and-suspenders so no zombie survives.
  for pid in ${BACKEND_PID} ${FRONTEND_PID}; do
    if [[ -n "${pid}" ]]; then
      kill -9 "${pid}" 2>/dev/null || true
    fi
  done
  # Pattern kill covers any process that re-forked away from the recorded PID.
  pkill -9 -f "uvicorn.*${BACKEND_PORT}" 2>/dev/null || true
  pkill -9 -f "vite.*${FRONTEND_PORT}"   2>/dev/null || true
  pkill -9 -f "vite preview.*${FRONTEND_PORT}" 2>/dev/null || true
  # Port-level kill as final backstop.
  lsof -ti tcp:"${BACKEND_PORT}"  2>/dev/null | xargs kill -9 2>/dev/null || true
  lsof -ti tcp:"${FRONTEND_PORT}" 2>/dev/null | xargs kill -9 2>/dev/null || true
}
trap _cleanup EXIT

# --- Parse args ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend-port) BACKEND_PORT="$2"; shift 2 ;;
    --frontend-port) FRONTEND_PORT="$2"; shift 2 ;;
    --data-dir) DATA_DIR="$2"; shift 2 ;;
    --env)
      # Print export statements for the caller to source
      echo "export BACKEND_PORT=${BACKEND_PORT}"
      echo "export FRONTEND_PORT=${FRONTEND_PORT}"
      echo "export APP_URL=http://localhost:${FRONTEND_PORT}"
      echo "export BACKEND_URL=http://localhost:${BACKEND_PORT}"
      exit 0
      ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# Validate port range (8090-8099)
if [[ "${BACKEND_PORT}" -lt 8090 || "${BACKEND_PORT}" -gt 8099 ]]; then
  echo "ERROR: BACKEND_PORT must be 8090-8099, got ${BACKEND_PORT}" >&2
  exit 1
fi
if [[ "${FRONTEND_PORT}" -lt 8090 || "${FRONTEND_PORT}" -gt 8099 ]]; then
  echo "ERROR: FRONTEND_PORT must be 8090-8099, got ${FRONTEND_PORT}" >&2
  exit 1
fi

# --- Pick app Python venv ---
APP_PYTHON="${REPO_ROOT}/.venv/bin/python3"
if [[ ! -x "${APP_PYTHON}" ]]; then
  APP_PYTHON="python3"
fi

# --- Seed QA data if not supplied ---
if [[ -z "${DATA_DIR}" ]]; then
  echo "[boot_app] Seeding QA data..."
  DATA_DIR="$(cd "${REPO_ROOT}" && make qa-data)"
fi

# Guard: validate DATA_DIR is an isolated temp/clone dir, never the real one.
_assert_data_dir_safe "${DATA_DIR}"
echo "[boot_app] DATA_DIR=${DATA_DIR} (safety-checked OK)"

# Isolate CACHE_DIR + LOGS_DIR too. .env points all three at the live
# ~/.open-net-worth tree; without these overrides the QA backend would write its
# cache + logs into the operator's production instance (the backend's ONW_QA_MODE
# guard now refuses to start in that case). Derive both as siblings of the
# safety-checked DATA_DIR so the whole QA instance is self-contained and isolated.
CACHE_DIR="${DATA_DIR%/}-cache"
LOGS_DIR="${DATA_DIR%/}-logs"
echo "[boot_app] CACHE_DIR=${CACHE_DIR}  LOGS_DIR=${LOGS_DIR}"

# --- Build frontend ---
echo "[boot_app] Building frontend..."
cd "${REPO_ROOT}/frontend"
npm run build > "${LOG_DIR}/frontend_build.log" 2>&1
echo "[boot_app] Frontend built."

# --- Aggressively free both ports before boot ---
# Pass 1: kill by port (SIGKILL so vite preview cannot linger).
lsof -ti tcp:"${BACKEND_PORT}"  2>/dev/null | xargs kill -9 2>/dev/null || true
lsof -ti tcp:"${FRONTEND_PORT}" 2>/dev/null | xargs kill -9 2>/dev/null || true
# Pass 2: kill by process name (handles cases where the port mapping hasn't
# cleared yet or the process forked away from the lsof-visible PID).
pkill -9 -f "uvicorn.*${BACKEND_PORT}"      2>/dev/null || true
pkill -9 -f "vite.*${FRONTEND_PORT}"        2>/dev/null || true
pkill -9 -f "vite preview.*${FRONTEND_PORT}" 2>/dev/null || true
# Brief pause so the OS releases the port before we bind it again.
sleep 1
# Pass 3: confirm ports are free; abort loudly if still occupied.
for port in "${BACKEND_PORT}" "${FRONTEND_PORT}"; do
  if lsof -ti tcp:"${port}" > /dev/null 2>&1; then
    echo "ERROR: port ${port} still occupied after kill attempts" >&2
    exit 1
  fi
done

# --- Start backend ---
echo "[boot_app] Starting backend on port ${BACKEND_PORT}..."
cd "${REPO_ROOT}"
ONW_DISABLE_TELEGRAM=1 \
ONW_QA_MODE=1 \
DATA_DIR="${DATA_DIR}" \
CACHE_DIR="${CACHE_DIR}" \
LOGS_DIR="${LOGS_DIR}" \
AUTH_DISABLED=true \
  "${APP_PYTHON}" -m uvicorn backend.main:app \
    --host 127.0.0.1 \
    --port "${BACKEND_PORT}" \
    > "${LOG_DIR}/backend.log" 2>&1 &
BACKEND_PID=$!

# --- Start frontend preview ---
echo "[boot_app] Starting frontend preview on port ${FRONTEND_PORT}..."
cd "${REPO_ROOT}/frontend"
VITE_API_URL="http://localhost:${BACKEND_PORT}" \
  npx vite preview \
    --host 127.0.0.1 \
    --port "${FRONTEND_PORT}" \
    > "${LOG_DIR}/frontend.log" 2>&1 &
FRONTEND_PID=$!

# Write both PIDs atomically (truncate then append) so the runner can tear down.
printf '%s\n%s\n' "${BACKEND_PID}" "${FRONTEND_PID}" > "${PIDS_FILE}"

# --- Health checks ---
wait_for_http() {
  local url="$1"
  local label="$2"
  local timeout=60
  local elapsed=0
  echo -n "[boot_app] Waiting for ${label} at ${url}"
  while ! curl -sf "${url}" > /dev/null 2>&1; do
    sleep 2
    elapsed=$((elapsed + 2))
    echo -n "."
    if [[ ${elapsed} -ge ${timeout} ]]; then
      echo ""
      echo "ERROR: ${label} did not come up within ${timeout}s" >&2
      echo "[boot_app] Last log lines:" >&2
      tail -20 "${LOG_DIR}/backend.log" >&2 || true
      exit 1
    fi
  done
  echo " OK"
}

wait_for_http "http://localhost:${BACKEND_PORT}/api/health" "backend"
wait_for_http "http://localhost:${FRONTEND_PORT}/" "frontend"

echo "[boot_app] Both servers are up."
echo "APP_URL=http://localhost:${FRONTEND_PORT}"
echo "BACKEND_URL=http://localhost:${BACKEND_PORT}"
echo "PIDS_FILE=${PIDS_FILE}"

# Disarm the EXIT trap now that both servers are confirmed healthy.
# The trap was guard-only: clean up if something fails BEFORE the servers
# come up.  After this point the servers must stay running — owned by the
# caller (runner.py reads .qa_pids and kills them on teardown).
trap - EXIT
