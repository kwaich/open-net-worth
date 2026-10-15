"""DedupConfigStore — user-tunable settings for the Transaction Match & Dedup engine's LLM
(L3) judgment layer (RFC transaction-dedup-engine §user-controlled policy).

A single config dict. Separate from ``dedup_policy`` (which lives in ``profile.json`` /
the ``profile`` table and governs auto-merge aggressiveness) — this store governs the LLM
layer:

    l3_enabled    : bool   master KILL SWITCH for the L3 LLM judge (default True). When off,
                           the judgment pass is a no-op and every match stays for human review.
    l3_model      : str    the model id the L3 judge uses ("" = the instance default model).
    l3_max_per_run: int    COST CAP — the maximum number of LLM judgments a single judge pass
                           will make (default 25). Protects against a runaway per-import bill.

Typed (bool/int/str) — unlike the str-only ``AppConfigStore`` — so the engine can read them
directly.

WAVE 5C (JSON->DB): replaces the hand-rolled ``dedup_config.json`` file I/O with a row in the
shared ``settings`` table, namespace ``"dedup_config"`` — the SAME table ``memory_settings``/
``preferred_models`` already share, since dedup_config's existing semantics (checkpoint-
INCLUDED, NOT portable) match that table's exactly. Subclasses ``NamespacedSettingsStore``
(mirrors ``MemorySettingsStore``/``PreferredModelsStore``) rather than wrapping it by
composition, so ``update()`` below can reach the protected ``_use_conn`` seam: unlike
``MemorySettingsStore`` (one key, so ``get()`` then ``save()`` as two separate calls is
correct — see that store's own docstring), dedup_config has THREE independent keys, so a
naive two-call read-modify-write would let two concurrent PUTs changing different keys
lose one's change to the other's stale read. The pre-migration file store held a
``threading.Lock`` around the whole read-modify-write to prevent exactly that; ``update()``
here reproduces the same atomicity by opening ONE transaction (``_use_conn(None)``) that
covers both the read and the write.
"""

from __future__ import annotations

from typing import Optional

from backend.db.payload_store import NamespacedSettingsStore

DEFAULTS: dict = {
    "l3_enabled": True,
    "l3_model": "",
    "l3_max_per_run": 25,
}

_BOOL_FIELDS = {"l3_enabled"}
_INT_FIELDS = {"l3_max_per_run"}
_STR_FIELDS = {"l3_model"}


def _coerce(key: str, value):
    if key in _BOOL_FIELDS:
        return bool(value)
    if key in _INT_FIELDS:
        try:
            v = int(value)
        except (TypeError, ValueError):
            return DEFAULTS[key]
        # Negative values are nonsensical for a cost cap — coerce to the safe default (25).
        # 0 is valid (means unlimited) and passes through unchanged.
        return v if v >= 0 else DEFAULTS[key]
    return str(value or "").strip()


class DedupConfigStore(NamespacedSettingsStore):
    def __init__(self, engine: Optional[object] = None):
        super().__init__("dedup_config", engine=engine)

    def get(self, *, conn=None) -> dict:
        """The active config — typed defaults merged under the stored values."""
        data = super().get(conn=conn)
        return {k: _coerce(k, data[k]) if k in data else v for k, v in DEFAULTS.items()}

    def update(self, changes: dict) -> dict:
        """Persist a partial patch (only known, non-None fields), atomically — see class
        docstring for why this needs ONE transaction spanning the read and the write.
        Returns the merged, typed config."""
        with self._use_conn(None) as conn:
            raw = super().get(conn=conn)  # RAW stored dict — only the keys ever persisted
            for k in DEFAULTS:
                if k in changes and changes[k] is not None:
                    raw[k] = _coerce(k, changes[k])
            self.save(raw, conn=conn)
        return self.get()
