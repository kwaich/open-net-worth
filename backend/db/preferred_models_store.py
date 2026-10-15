"""PreferredModelsStore — the owner's curated shortlist of models.

Single-dict store holding one list: the model refs (``"<provider_key>:<model_id>"``)
the owner actually wants to see. Non-empty makes every model picker show a
"Preferred" group first; empty means the pickers show the full capability-filtered
list. It selects nothing on its own — the model that runs a turn is either the
instance default (``model_config.json``) or an explicit per-conversation pick.

Succeeds ``RoutingConfigStore``, whose other fields (profile, allow_local,
risk_guard, pins) existed only to feed the removed auto-routing engine.
See ADR ``2026-08-06-auto-model-switching-removed``.

User intent, no secrets → checkpoint-included.

PLAN 4D TASK 2: DB-backed — a row in the shared ``settings`` table, namespace
"preferred_models", via ``NamespacedSettingsStore``. The JsonStore-era
``fail_closed_on_corrupt=False`` (degrade to an empty list on a corrupt file) has no DB
equivalent — a row either has a value or doesn't, so that flag is not ported.
"""

from __future__ import annotations

from backend.db.payload_store import NamespacedSettingsStore

# Upper bounds on the list — a curated shortlist, not a bulk import. Cap the count
# and each ref's length so a malformed/hostile PUT can't bloat the config.
_MAX_MODELS = 50
_MAX_REF_LEN = 200


def clean_model_refs(raw) -> list[str]:
    """Coerce a raw list into clean, deduped, bounded model refs (order kept)."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        ref = item.strip()[:_MAX_REF_LEN]
        if ref and ref not in seen:
            seen.add(ref)
            out.append(ref)
        if len(out) >= _MAX_MODELS:
            break
    return out


class PreferredModelsStore(NamespacedSettingsStore):
    def __init__(self, engine=None):
        super().__init__("preferred_models", engine=engine)

    def resolved(self) -> dict:
        """The config with defaults applied — always the ``models`` key, always valid."""
        return {"models": clean_model_refs(self.get().get("models"))}

    def replace(self, models) -> dict:
        """Replace the whole list (it is a selection, not a patchable object)."""
        self.save({"models": clean_model_refs(models)})
        return self.resolved()
