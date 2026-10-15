"""entity_types_fixture — the frontend TEST fixture of the entity-type registry.

The app never ships a copy of the registry: the server embeds it in index.html and serves
``GET /api/entity-types``, both from ``services.mentions.entity_types``. Frontend unit tests
have no server, so ``frontend/src/vitest.setup.ts`` seeds the registry from this file — the
exact backend payload — so contract tests (e.g. that a screen's ``data-ctx-fields`` keys
exist in the real payee schema) run against the real descriptors, not hand-written ones.

``tests/test_entity_types_fixture.py`` fails the moment a descriptor changes without this
being re-run, so the fixture cannot quietly drift:

    .venv/bin/python3 -m backend.scripts.entity_types_fixture          # rewrite
    .venv/bin/python3 -m backend.scripts.entity_types_fixture --check  # exit 1 on drift
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.services.mentions.entity_types import entity_types

FIXTURE = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "lucius" / "entityTypes.fixture.json"
)


def render() -> str:
    return json.dumps(entity_types(), ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the fixture is stale")
    args = parser.parse_args(argv)
    if args.check:
        current = FIXTURE.read_text(encoding="utf-8") if FIXTURE.exists() else ""
        if current != render():
            sys.stderr.write(
                f"{FIXTURE} is stale — run: python -m backend.scripts.entity_types_fixture\n"
            )
            return 1
        return 0
    FIXTURE.write_text(render(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
