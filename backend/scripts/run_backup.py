"""On-demand full-data backup — the CLI twin of the "Back up now" Settings button
and the daily scheduled job (backend.scheduler._run_daily_backup).

Usage:
    python3 -m backend.scripts.run_backup            # backs up DATA_DIR now
    make backup
"""

from __future__ import annotations

import json

from backend.services.backup import run_backup


def main() -> int:
    result = run_backup(force=True)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
