"""The owned asset every Assets tool hands back — ``get_asset`` and each write, in
``assets.py`` and ``asset_values.py`` alike (security review H2)."""

from __future__ import annotations

from backend.ai.tools import _bounded as _b

ASSET_ID_DESC = "The owned asset's id, from list_assets or get_asset."


def _readable_docs(docs: list[dict] | None) -> list[dict]:
    """Doc rows for a FULL-detail context (get_asset, or the asset returned by a write) —
    security review H2. A doc the user marked ``lucius_readable=false`` is withheld
    entirely: no ``ref_id`` (a fetchable content pointer) and no ``added_on``, just enough
    for Lucius to know it exists and that it may not open it. A readable doc keeps its
    full shape, ref_id included."""
    out = []
    for d in docs or []:
        if d.get("lucius_readable", True):
            out.append(d)
        else:
            out.append({"name": d.get("name") or "", "lucius_readable": False, "withheld": True})
    return out


def project_asset(asset: dict) -> dict:
    """Full asset detail for get_asset, and for the asset every write hands back — security
    review H2. Two projections on top of the service's own `project()`:
    a non-readable doc's `ref_id` is withheld (see `_readable_docs`), and `marks` is
    capped to the newest 50 with a `marks_total` count (an asset re-marked monthly for
    years is not a bounded read otherwise — get_asset is a detail read, not a full
    history dump; `list_assets` already omits marks entirely)."""
    marks = asset.get("marks") or []
    page, total = _b.cap(marks, _b.DEFAULT_ASSET_MARK_LIMIT)
    return {
        **asset,
        "marks": page,
        "marks_total": total,
        "docs": _readable_docs(asset.get("docs")),
    }
