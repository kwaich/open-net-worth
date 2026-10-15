"""Re-export shim — unit conversion moved to ``backend.core.units``.

Kept so existing ``from backend import units`` / ``from backend.units import …``
call sites keep working without a sweep in this PR (RFC P1-A1 §3.3). ruff
F401/F403 is allowlisted for this file in pyproject.toml.
"""

from backend.core.units import *  # noqa: F401,F403

__all__ = [
    "GRAMS_PER",
    "METAL_UNITS",
    "NATIVE_PRECIOUS_UNIT",
    "normalize_unit",
    "quote_per_unit",
]
