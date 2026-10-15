"""Pluggable budget engines — the paradigm-neutral layer over zero-based and 50/30/20.

See ``.claude/rfcs/2026-08-22-pluggable-budget-engines.md``. Phase 0 (this package's first
cut) shipped the contract (``base.py``/``types.py``) and one engine, zero-based
(``zero_based.py``), adapting the existing ``services.planning.budget_month()`` without
changing it at all.

Import the contract from ``backend.services.budget_engines.base`` — it re-exports every
type from ``types.py`` alongside ``BudgetEngine``/``ENGINES``/``get_engine``/``home_engine_id``.
"""

from __future__ import annotations
