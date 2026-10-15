"""The frontend's entity-type test fixture IS the backend payload — never a second source.

Scar (2026-10-05): the frontend used to bundle a build-time copy of the registry, produced by
a generator that fell back to a hand-written list whenever the backend could not be imported
(Docker's node-only stage, the CI wheel build). That list had drifted to 16 of 35 types with
wrong icons, and every published image shipped it. The app now ships no copy (the server
embeds the registry in index.html); only vitest uses a snapshot, and this test pins it to
the descriptors so a frontend contract test can never pass against stale schema.
"""

from __future__ import annotations

from backend.scripts.entity_types_fixture import FIXTURE, render


def test_frontend_entity_types_fixture_matches_backend_descriptors():
    assert FIXTURE.exists(), f"missing {FIXTURE}"
    assert FIXTURE.read_text(encoding="utf-8") == render(), (
        "frontend/src/lucius/entityTypes.fixture.json is stale — a mention descriptor changed. "
        "Run: python -m backend.scripts.entity_types_fixture"
    )
