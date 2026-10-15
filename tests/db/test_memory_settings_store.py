"""MemorySettingsStore on NamespacedSettingsStore (plan 4d, task 2) — round trip, the
safe-default-when-absent behavior, and the memory_enabled_for AND-gate.
"""

from __future__ import annotations

from backend.db.memory_settings_store import MemorySettingsStore, memory_enabled_for


def test_enabled_defaults_true_when_absent(db_engine):
    store = MemorySettingsStore(engine=db_engine)
    assert store.enabled() is True


def test_set_enabled_round_trip(db_engine):
    store = MemorySettingsStore(engine=db_engine)
    store.set_enabled(False)
    assert store.enabled() is False
    store.set_enabled(True)
    assert store.enabled() is True


def test_namespace_is_memory_settings(db_engine):
    from sqlalchemy import select

    from backend.db.tables import settings

    store = MemorySettingsStore(engine=db_engine)
    store.set_enabled(False)
    with db_engine.connect() as conn:
        row = conn.execute(
            select(settings).where(settings.c.namespace == "memory_settings")
        ).first()
    assert row is not None and row.payload == {"enabled": False}


# ── Domain parity: the AND-gate over the global toggle + per-agent flag ────────────────────


def test_memory_enabled_for_and_gate(db_isolated):
    assert memory_enabled_for(None) is True  # global default is enabled, no agent given
    assert memory_enabled_for({"memory_enabled": True}) is True
    assert memory_enabled_for({"memory_enabled": False}) is False  # per-agent override wins

    MemorySettingsStore().set_enabled(False)
    assert memory_enabled_for(None) is False
    assert memory_enabled_for({"memory_enabled": True}) is False  # global OFF wins regardless
