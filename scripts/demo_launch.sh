#!/usr/bin/env bash
# Start / reset / rebuild the hosted demo, detached, and report honestly.
#
# This exists because the same logic inside a Makefile recipe kept going wrong in ways that
# had nothing to do with the demo: shell quoting across `\`-continued lines, `$!` capturing a
# wrapper rather than the launcher, and — the one that actually bit — a shared log file that
# the PREVIOUS still-running server was writing into while the new run truncated it, so the
# wait loop was reading someone else's output and declaring failure for a demo that was fine.
#
# One process, one log, one place to get it right.
#
#   demo_launch.sh host      start (or restart) — keeps the book
#   demo_launch.sh rebuild   rebuild the frontend bundle, keep the book
#   demo_launch.sh reset     wipe and regenerate the book (layout is carried across)
set -euo pipefail

MODE="${1:-host}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO_ROOT="${ONW_DEMO_ROOT:-${HOME}/.open-net-worth-demo}"
# A FRESH log per launch, outside DEMO_ROOT (which `reset` deletes) and never shared with a
# still-running previous instance.
LOG="$(mktemp -t onw-demo-launch)"
STABLE_LOG="${TMPDIR:-/tmp}/onw-demo-launch.log"

cd "${REPO_ROOT}"

# ── Stop whatever is running, and WAIT for it ────────────────────────────────
pkill -f run_demo.sh 2>/dev/null || true
for port in 5175 8175; do
  for pid in $(lsof -nP -iTCP:"${port}" -sTCP:LISTEN -t 2>/dev/null); do
    kill "${pid}" 2>/dev/null || true
  done
done
for i in $(seq 1 30); do
  busy=""
  for port in 5175 8175; do
    lsof -nP -iTCP:"${port}" -sTCP:LISTEN -t >/dev/null 2>&1 && busy="${busy} ${port}"
  done
  [[ -z "${busy}" ]] && break
  if [[ "${i}" -eq 15 ]]; then
    for port in ${busy}; do
      for pid in $(lsof -nP -iTCP:"${port}" -sTCP:LISTEN -t 2>/dev/null); do
        kill -9 "${pid}" 2>/dev/null || true
      done
    done
  fi
  sleep 1
done
echo "==> stopped any running demo"

# macOS ships bash 3.2, where `"${arr[@]}"` on an EMPTY array trips `set -u` as "unbound
# variable". The `${arr[@]+...}` guard expands to nothing when the array is empty instead.
ARGS=()
case "${MODE}" in
  host)    ;;
  # EXPORT, never an assignment-prefix built by expansion: bash recognises `VAR=x cmd`
  # prefixes from LITERAL words at parse time, so a `${arr[@]}` that expands to one is read
  # as the command name instead — `ONW_DEMO_REBUILD_FRONTEND=1: command not found`.
  rebuild) export ONW_DEMO_REBUILD_FRONTEND=1; echo "==> rebuilding the frontend bundle (~30s)" ;;
  reset)   ARGS=(--rebuild); echo "==> regenerating the book (your dashboard layout is carried across)" ;;
  *) echo "usage: $0 [host|rebuild|reset]" >&2; exit 2 ;;
esac

ONW_DEMO_ROOT="${DEMO_ROOT}" \
  nohup ./scripts/run_demo.sh ${ARGS[@]+"${ARGS[@]}"} > "${LOG}" 2>&1 < /dev/null &
LAUNCHER_PID=$!

for i in $(seq 1 300); do
  if grep -aq "Demo is running" "${LOG}" 2>/dev/null; then
    cp "${LOG}" "${STABLE_LOG}" 2>/dev/null || true
    grep -aE "kept your dashboard|restored your dashboard|Demo book built|existing demo book kept|Lucius will run|Lucius:" "${LOG}" 2>/dev/null || true
    echo
    echo "  http://127.0.0.1:5175   (no login)"
    echo "  logs: ${LOG}   ·   stop: make demo-stop"
    exit 0
  fi
  if ! kill -0 "${LAUNCHER_PID}" 2>/dev/null; then
    echo "ERROR: the demo launcher exited before it was serving:" >&2
    tail -n 30 "${LOG}" >&2
    exit 1
  fi
  sleep 1
done

echo "ERROR: the demo did not come up within 5 minutes:" >&2
tail -n 30 "${LOG}" >&2
exit 1
