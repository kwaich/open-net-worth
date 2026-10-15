"""Target ("save X by date Y") schemas (P2-BU1).

``models/`` is a leaf layer (stdlib + pydantic + ``backend.core`` only). ``Target`` is
the stored shape; ``TargetCreate``/``TargetUpdate`` are the request bodies for
``api/budgets.py``. All money math (required-monthly, pace verdict, auto-allocation)
lives in ``services.targets`` — none here.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import field_validator

from backend.models.base import ONBaseModel, _blank_to_none


def _clean_account_ids(v):
    """Coerce a linked-accounts value to a clean list[str]: drop blanks, dedupe (order-preserving).

    Accepts None (→ []) or a list of ids. The singular ``linked_account_id`` was folded into this
    plural list by the forward migration (scripts/migrate_goals.py) — there is no dual-read."""
    if v is None:
        return []
    if not isinstance(v, (list, tuple)):
        v = [v]
    out: List[str] = []
    for item in v:
        s = str(item).strip() if item is not None else ""
        if s and s not in out:
            out.append(s)
    return out


class Target(ONBaseModel):
    """A deadline savings target — stored shape (see ``db/targets_store``)."""

    id: str
    name: str
    target_amount: float
    current_amount: float = 0.0
    currency: str = ""
    target_date: str = ""
    linked_account_ids: List[str] = []
    category: Optional[str] = None
    priority: int = 1
    status: str = "active"  # active | completed | cancelled
    note: str = ""
    created_at: str = ""

    _clean_links = field_validator("linked_account_ids", mode="before")(_clean_account_ids)


class TargetCreate(ONBaseModel):
    """POST /api/targets body."""

    name: str
    target_amount: float
    current_amount: float = 0.0
    currency: Optional[str] = None
    target_date: str
    linked_account_ids: List[str] = []
    category: Optional[str] = None
    priority: int = 1
    note: str = ""

    _blank = field_validator("currency", "category", mode="before")(_blank_to_none)
    _clean_links = field_validator("linked_account_ids", mode="before")(_clean_account_ids)


class TargetUpdate(ONBaseModel):
    """PATCH/PUT /api/targets/{id} body — all-optional; blanks coerce to None."""

    name: Optional[str] = None
    target_amount: Optional[float] = None
    current_amount: Optional[float] = None
    currency: Optional[str] = None
    target_date: Optional[str] = None
    linked_account_ids: Optional[List[str]] = None
    category: Optional[str] = None
    priority: Optional[int] = None
    status: Optional[str] = None
    note: Optional[str] = None

    _blank = field_validator("currency", "category", mode="before")(_blank_to_none)

    @field_validator("linked_account_ids", mode="before")
    @classmethod
    def _clean_links(cls, v):
        # Keep None as "unset" (PATCH semantics); clean a provided list.
        return None if v is None else _clean_account_ids(v)
