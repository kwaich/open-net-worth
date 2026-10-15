"""`python -m backend.cli` — what the autostart service runs, so it needs no `onw` on PATH."""

from backend.cli import main

raise SystemExit(main())
