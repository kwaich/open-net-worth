#!/usr/bin/env bash
# The release security gate: known-vulnerable dependencies and high-severity static findings.
# One definition, run by the release workflow before anything is built or pushed.
#
#   pip-audit   the hash-pinned runtime lock (what the desktop app and the image install)
#   npm audit   frontend + desktop production dependencies, high and critical
#   semgrep     python + typescript rules, ERROR severity (avoid-sqlalchemy-text is excluded: it
#               flags every f-string table name in the migrations, none user-reachable)
#   bandit      high severity + high confidence (B324 excluded: sha1 used as a non-security id)
#
# docs-site (the Astro docs build, never shipped at runtime) is audited for information only.
# Needs: pip-audit, semgrep, bandit on PATH (pip install pip-audit semgrep bandit), node 22+.
# Findings print their rule and location, never file contents.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
failed=0
step() { printf '\n== %s\n' "$1"; }
run() { "$@" || { echo "FAILED: $1"; failed=1; }; }

step "pip-audit (desktop/runtime-requirements.lock)"
run pip-audit -r desktop/runtime-requirements.lock --no-deps --disable-pip --progress-spinner off

for dir in frontend desktop; do
  step "npm audit ($dir, production, high+)"
  (cd "$dir" && npm audit --omit=dev --audit-level=high) || { echo "FAILED: npm audit $dir"; failed=1; }
done

# semgrep and bandit print the offending source line by default; the release runs in a public
# repo's logs, so both write JSON/templated output and only rule ids and locations are printed.
step "semgrep"
semgrep_out="$(mktemp)"
semgrep scan --config p/python --config p/typescript --severity ERROR --metrics off --quiet \
  --exclude-rule python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text \
  --json --output "$semgrep_out" backend frontend/src desktop/src >/dev/null 2>&1
python3 - "$semgrep_out" <<'PY' || { echo "FAILED: semgrep"; failed=1; }
import json, sys
results = json.load(open(sys.argv[1]))["results"]
for r in results:
    print(f"{r['check_id']}  {r['path']}:{r['start']['line']}")
print(f"{len(results)} finding(s)")
sys.exit(1 if results else 0)
PY

step "bandit"
run bandit -r backend -lll -iii -q -s B324 --exclude backend/db/alembic \
  -f custom --msg-template "{test_id} {severity} {relpath}:{line}"

step "npm audit (docs-site, information only)"
(cd docs-site && npm audit --omit=dev --audit-level=high) || echo "note: docs-site has advisories (build-time only, not gating)"

if [ "$failed" = 0 ]; then echo "security scan: clean"; else echo "security scan: FAILED"; exit 1; fi
