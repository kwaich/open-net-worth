"""Every registered mention type must be reachable from the UI — or be explicitly exempt.

Regression (2026-08-23): `commitment` had a complete backend descriptor
(`services/mentions/descriptors/new_types.py`, order=16) — searchable, resolvable,
sublabelled — and the Commitments tab rendered its rows as a plain `<button>`. Nothing
emitted `data-entity-id`, so a commitment could not be right-clicked ("Ask Lucius about
this"), dragged into the dock, or harvested as an "on this screen" row. The backend half
was done, the frontend half was never wired, and NOTHING failed: registering a descriptor
is what makes a type searchable, and searchability is invisible to every existing test.

`skill` and `watchlist` were in exactly the same state, and `automation` had already been
found by hand once before (see `tabs/automations/lucius.entityWiring.test.tsx`, 2026-08-11).
That is three independent hand-discoveries of one repeating defect — so it becomes a gate.

This is the discovery half of the contract. The per-component
`lucius.entityWiring.test.tsx` files are the behavioural half: they assert the exact
attributes (`data-entity-id` / `data-entity-label` / `draggable`) that
`components/chat/lucius/dragRightClick.tsx` binds on. This test only asserts that SOME
frontend source emits each type at all — it is deliberately a coarse net, because the
failure it catches is "nobody wired this anywhere", not "the wiring is subtly wrong".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.services.mentions.registry import REGISTRY

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"

# Types with no row of their own to render. Each entry needs a REASON, not just a name —
# an exemption is a claim that the type is unreachable BY DESIGN, and the next person has
# to be able to check that claim. Adding a type here to make the suite green is the exact
# mistake this file exists to prevent.
EXEMPT: dict[str, str] = {
    # Singletons/derived selectors: the "entity" is a computed figure, not a list row. They
    # resolve fine when the model names them, and they ride the screen snapshot as facts
    # (`useRegisterScreenFacts`) rather than as draggable rows.
    "net_worth": "Singleton figure — no row; rides the screen snapshot as a fact.",
    "returns": "Derived period selector ('1Y') — no row of its own.",
    "budget_month": "Derived period selector — the Plan month header, not a list row.",
    "report": "Generated artifact addressed by name; no persistent list row in the UI.",
    # Not a wiring gap — an unbuilt feature. Savings targets have a complete backend
    # (TargetsStore, services/targets.py, GET|POST|PUT|DELETE /api/targets, and this mention
    # descriptor) and NO frontend at all: `frontend/src` contains no Target type, no
    # /api/targets caller, and no screen. Wiring a row here would mean inventing the UI.
    # Delete this entry the moment a targets screen ships.
    "target": "No targets UI exists yet — backend-only feature (GET /api/targets has no caller).",
}


def _frontend_sources() -> dict[Path, str]:
    """Every non-test frontend source file, read once."""
    files: list[Path] = [
        p
        for pattern in ("*.tsx", "*.ts")
        for p in FRONTEND_SRC.rglob(pattern)
        if ".test." not in p.name and not p.name.endswith(".d.ts")
    ]
    return {p: p.read_text(encoding="utf-8", errors="ignore") for p in files}


def _emitters(mention_type: str, sources: dict[Path, str]) -> list[Path]:
    """Files that stamp `data-entity-id` for this type, by any of the three sanctioned routes.

    1. `entityRefProps('<type>', …)`      — the raw helper (utils/mentionable.ts)
    2. a literal `data-entity-id` prefix  — hand-rolled string/template attribute
    3. `type="<type>"` in a file that also imports EntityRow/EntityList — the wrapper
       components, which stamp the attribute themselves from their `type` prop.

    Route 3 is scoped to files that actually import the wrapper so an unrelated
    `type="button"`-style attribute can never count as coverage.
    """
    direct = (
        re.compile(r"""entityRefProps\(\s*['"]%s['"]""" % re.escape(mention_type)),
        re.compile(r"""data-entity-id=[{"`\s]*['"`]?%s:""" % re.escape(mention_type)),
    )
    via_wrapper = re.compile(r'\btype="%s"' % re.escape(mention_type))

    found: list[Path] = []
    for path, text in sources.items():
        if any(p.search(text) for p in direct):
            found.append(path)
        elif via_wrapper.search(text) and ("EntityRow" in text or "EntityList" in text):
            found.append(path)
    return found


@pytest.fixture(scope="module")
def sources() -> dict[Path, str]:
    return _frontend_sources()


def test_frontend_sources_are_discoverable(sources: dict[Path, str]) -> None:
    """Guard the guard: if the glob breaks, every other assertion here silently passes."""
    assert len(sources) > 100, f"only found {len(sources)} frontend sources — glob is broken"


@pytest.mark.parametrize("mention_type", sorted(REGISTRY))
def test_every_mention_type_has_a_frontend_emitter(
    mention_type: str, sources: dict[Path, str]
) -> None:
    if mention_type in EXEMPT:
        pytest.skip(f"exempt: {EXEMPT[mention_type]}")

    found = _emitters(mention_type, sources)
    assert found, (
        f"Mention type {mention_type!r} is registered in services/mentions "
        f"(so it is searchable and resolvable) but NO frontend source emits "
        f"data-entity-id for it.\n\n"
        f"That means a {mention_type} cannot be right-clicked 'Ask Lucius about this', "
        f"dragged into the dock, or harvested as an 'on this screen' row.\n\n"
        f'Fix: wrap the row component in <EntityRow type="{mention_type}" …> '
        f"(see components/tabs/commitments/rows.tsx) and add a "
        f"lucius.entityWiring.test.tsx beside it.\n"
        f"If the type genuinely has no row of its own, add it to EXEMPT in this file "
        f"WITH a reason."
    )


def test_exempt_entries_are_still_registered() -> None:
    """An exemption for a type that no longer exists is dead weight — and hides a real gap
    if the name is ever reused."""
    stale = sorted(set(EXEMPT) - set(REGISTRY))
    assert not stale, f"EXEMPT lists unregistered mention types: {stale}"


def test_exempt_entries_carry_a_reason() -> None:
    blank = sorted(t for t, reason in EXEMPT.items() if not reason.strip())
    assert not blank, f"EXEMPT entries missing a reason: {blank}"


def test_every_mention_type_has_a_unique_icon() -> None:
    """A glyph identifies the TYPE, so two types sharing one are indistinguishable in the
    picker, the composer chip and the transcript chip alike.

    Found by the owner, 2026-08-23: four newly-added types silently reused glyphs already
    taken by automation, category/budget_month and envelope/holding, on top of two
    pre-existing clashes (holding/instrument, category/budget_month). Nothing failed —
    an icon is just a string on a descriptor.
    """
    from collections import Counter

    counts = Counter(provider.icon for provider in REGISTRY.values())
    clashes = {
        icon: sorted(t for t, p in REGISTRY.items() if p.icon == icon)
        for icon, n in counts.items()
        if n > 1
    }
    assert not clashes, f"mention types share an icon: {clashes}"
