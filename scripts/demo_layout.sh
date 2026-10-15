#!/usr/bin/env bash
# Save / restore the demo's dashboard layout.
#
# The layout is the one thing in a demo that represents real human effort — arranging widgets
# by hand — and it is the easiest thing to lose: any reseed, any stray PUT, any fresh book
# starts from the seeded default and there is no undo (`write_log` only captures before-images
# during an assistant turn, so an ordinary save leaves nothing to roll back to).
#
#   ./scripts/demo_layout.sh save            -> writes demo-layout.json
#   ./scripts/demo_layout.sh restore         -> puts it back
#   ./scripts/demo_layout.sh save my.json    -> a named snapshot
set -euo pipefail

ACTION="${1:-save}"
FILE="${2:-demo-layout.json}"
BACKEND="${ONW_DEMO_BACKEND:-http://127.0.0.1:8175}"

# The demo needs no login (DEMO_OPEN_ACCESS), so the backend answers these directly.
curl -fsS -o /dev/null "${BACKEND}/api/health" || {
  echo "ERROR: could not reach ${BACKEND} — is the demo running?" >&2
  exit 1
}

case "${ACTION}" in
  save)
    curl -fsS "${BACKEND}/api/dashboard/layout" > "${FILE}"
    echo "saved -> ${FILE}"
    ;;
  restore)
    [[ -f "${FILE}" ]] || { echo "ERROR: ${FILE} not found — run 'save' first." >&2; exit 1; }
    code=$(curl -fsS -o /dev/null -w '%{http_code}' -X PUT \
      "${BACKEND}/api/dashboard/layout" -H 'Content-Type: application/json' -d @"${FILE}")
    echo "restored from ${FILE} (HTTP ${code}) — reload the tab"
    ;;
  *)
    echo "usage: $0 [save|restore] [file]" >&2
    exit 2
    ;;
esac
