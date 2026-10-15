"""Shared pydantic base for the domain + API schemas in ``backend/models/``.

``models/`` is a leaf layer beside ``core/`` (RFC P1-A3 §3): it may import only
stdlib, ``pydantic``, and ``backend.core`` — never ``db``/``services``/``api``/``ai``.

``ONBaseModel`` codifies the two config choices that keep these schemas additive
in wave 1 (RFC §5):

- ``extra="ignore"`` so parsing a *stored* dict that carries extra/legacy keys
  (``created_at``/``updated_at``/etc.) never raises — forward-compatible with the
  store ``_make_*`` factories.
- ``use_enum_values=True`` so ``.model_dump()`` emits the plain string value of an
  enum, keeping the on-disk JSON byte-identical to today (no ``"AccountKind.asset"``
  leakage). This is the guard that makes the model describe — not change — what the
  stores already write.

``_blank_to_none`` is lifted from ``api/profile.py`` (form fields arrive as ``""``
and must coerce to ``None`` before validation); centralized here so the rule is
stated once.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


def _blank_to_none(v: Any) -> Any:
    """Treat an empty/whitespace string (a blank form field) as 'unset' → ``None``.

    Mirrors ``api/profile.py:_blank_to_none`` so a half-filled form never 422s the
    whole save. Used as a ``mode="before"`` validator on Optional fields that the
    frontend may submit as ``""``.
    """
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


class ONBaseModel(BaseModel):
    """Base for all Open Net Worth domain + API schemas.

    See module docstring for why ``extra="ignore"`` + ``use_enum_values=True`` are
    load-bearing rather than stylistic.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        use_enum_values=True,
    )
