"""Carry a hand-arranged dashboard layout across a demo rebuild.

Arranging widgets by hand is the only thing in a demo that represents real human effort, and
it is the easiest thing to lose: a rebuild starts from a fresh book with the seeded default,
and there is no undo — ``write_log`` captures before-images only during an assistant turn, so
an ordinary layout save leaves nothing to roll back to. Losing it twice in one afternoon is
what put this file here.

Reads and writes the store DIRECTLY rather than over HTTP, so it works with no server running
— which is exactly the moment a rebuild needs it (the old instance is already stopped and the
new one has not started).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def export_layout(dest: Path) -> bool:
    """Write the current layout to ``dest``. False when there is nothing worth saving."""
    from backend.db.dashboard_layout_store import DashboardLayoutStore

    try:
        state = DashboardLayoutStore().get_state()
    except Exception:  # noqa: BLE001 — a missing/blank book is not an error here
        return False
    if not (state or {}).get("views"):
        return False
    dest.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return True


def import_layout(src: Path) -> bool:
    """Restore a layout saved by ``export_layout``. False when there is nothing to restore."""
    if not src.is_file():
        return False
    try:
        state = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not (state or {}).get("views"):
        return False
    from backend.db.dashboard_layout_store import DashboardLayoutStore

    DashboardLayoutStore().save_state(state.get("activeViewId"), state["views"])
    return True


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in ("export", "import"):
        print(
            "usage: python -m backend.scripts.demo.layout_io export|import <file>", file=sys.stderr
        )
        return 2
    action, path = sys.argv[1], Path(sys.argv[2])
    ok = export_layout(path) if action == "export" else import_layout(path)
    print(f"{action}: {'ok' if ok else 'nothing to do'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
