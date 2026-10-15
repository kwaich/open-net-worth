"""P3-ON / ON3 — AppConfigStore CRUD over its DB-backed row (store pattern).

Hermetic: app_config is DB-backed (Wave 5D2a, JSON->DB) and isolated per test by
conftest's autouse Tier-2 table clear (never the live data dir); each test also clears
the APP_* env so env-seeding is deterministic.
"""

from __future__ import annotations

import pytest

from backend.db.app_config_store import (
    BACKUP_ENABLED_FLAG,
    DEFAULT_APP_NAME,
    DEFAULT_BACKUP_RETENTION_DAYS,
    NOTIFY_UNATTENDED_WRITE_FLAG,
    SCRIPT_SKILLS_FLAG,
    AppConfigStore,
    backup_enabled,
    notify_unattended_write_enabled,
)


@pytest.fixture(autouse=True)
def _clear_branding_env(monkeypatch):
    for key in ("APP_NAME", "APP_ACCENT_COLOR", "APP_LOGO_URL"):
        monkeypatch.delenv(key, raising=False)


def _store(tmp_path):
    del tmp_path  # kept for call-site compatibility; isolation is now conftest's Tier-2 clear
    return AppConfigStore()


def test_get_returns_defaults_when_absent(tmp_path):
    cfg = _store(tmp_path).get()
    # get() is branding-only — the AG4 kill switch is deliberately NOT in this projection.
    assert cfg == {"app_name": DEFAULT_APP_NAME, "accent_color": "", "logo_url": ""}


def test_env_seeds_defaults_when_file_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_NAME", "Acme Wealth")
    monkeypatch.setenv("APP_ACCENT_COLOR", "#112233")
    cfg = _store(tmp_path).get()
    assert cfg["app_name"] == "Acme Wealth"
    assert cfg["accent_color"] == "#112233"
    assert cfg["logo_url"] == ""


def test_update_persists_and_round_trips(tmp_path):
    store = _store(tmp_path)
    out = store.update({"app_name": "My Money", "accent_color": "#abcdef"})
    assert out["app_name"] == "My Money"
    assert out["accent_color"] == "#abcdef"
    # Fresh instance reads from disk (file is authoritative once written).
    assert _store(tmp_path).get()["app_name"] == "My Money"


def test_partial_update_keeps_other_fields(tmp_path):
    store = _store(tmp_path)
    store.update({"app_name": "Brand", "logo_url": "https://x/logo.png"})
    store.update({"accent_color": "#000000"})
    cfg = store.get()
    assert cfg["app_name"] == "Brand"
    assert cfg["logo_url"] == "https://x/logo.png"
    assert cfg["accent_color"] == "#000000"


def test_blank_app_name_falls_back_to_default(tmp_path):
    store = _store(tmp_path)
    store.update({"app_name": ""})
    assert store.get()["app_name"] == DEFAULT_APP_NAME


def test_file_is_authoritative_over_env(tmp_path, monkeypatch):
    _store(tmp_path).update({"app_name": "FromFile"})
    monkeypatch.setenv("APP_NAME", "FromEnv")
    # Env only seeds when the file is ABSENT; a present file wins.
    assert _store(tmp_path).get()["app_name"] == "FromFile"


def test_non_dict_stored_value_falls_back_to_defaults(tmp_path):
    """A stored payload that isn't a proper branding object (a hand-edited or
    partially-migrated row) must fall back to defaults, never crash. Formerly this was
    driven by writing UNPARSEABLE BYTES to app_config.json (a JSON parse failure); a
    DB JSON column has no unparseable-bytes failure mode — a read is always valid JSON,
    just possibly the wrong shape — so a non-dict payload is the DB-era way to reach the
    same fallback path."""
    _store(tmp_path).save(["stray", "list", "value"])
    assert _store(tmp_path).get()["app_name"] == DEFAULT_APP_NAME


def test_unknown_keys_are_ignored(tmp_path):
    _store(tmp_path).save({"app_name": "Keep", "secret": "nope"})
    cfg = _store(tmp_path).get()
    assert "secret" not in cfg
    assert cfg["app_name"] == "Keep"


# ── Instance kill-switch flags live in the same file but OUT of branding get() ──


def test_kill_switch_defaults_off_and_absent_from_branding(tmp_path):
    """A fresh instance has the switch off, and it never rides the public branding
    projection (the auth-exempt GET /api/config)."""
    store = _store(tmp_path)
    assert store.get_flag(SCRIPT_SKILLS_FLAG) is False
    assert SCRIPT_SKILLS_FLAG not in store.get()


def test_kill_switch_round_trips_and_coerces_to_bool(tmp_path):
    assert _store(tmp_path).set_flag(SCRIPT_SKILLS_FLAG, True) is True
    # File is authoritative; a fresh store reads it back, branding untouched + flag hidden.
    fresh = _store(tmp_path)
    assert fresh.get_flag(SCRIPT_SKILLS_FLAG) is True
    assert fresh.get() == {"app_name": DEFAULT_APP_NAME, "accent_color": "", "logo_url": ""}
    # And it can be turned back off.
    assert _store(tmp_path).set_flag(SCRIPT_SKILLS_FLAG, False) is False


def test_kill_switch_set_preserves_branding_and_vice_versa(tmp_path):
    store = _store(tmp_path)
    store.update({"app_name": "Brand", "accent_color": "#abcdef"})
    store.set_flag(SCRIPT_SKILLS_FLAG, True)
    # The flag write kept branding; a later branding write keeps the flag.
    assert store.get_flag(SCRIPT_SKILLS_FLAG) is True
    assert store.get()["app_name"] == "Brand"
    store.update({"logo_url": "https://x/logo.png"})
    assert store.get_flag(SCRIPT_SKILLS_FLAG) is True
    assert store.get()["accent_color"] == "#abcdef"


# ── backup_enabled / backup_retention_days (services/backup.py) ──────────────


def test_backup_enabled_defaults_true(tmp_path):
    """Opt-out, not opt-in — a fresh instance backs up daily until explicitly disabled."""
    assert _store(tmp_path).get_flag(BACKUP_ENABLED_FLAG, default=True) is True


def test_backup_enabled_round_trips(tmp_path):
    store = _store(tmp_path)
    assert store.set_flag(BACKUP_ENABLED_FLAG, False) is False
    assert _store(tmp_path).get_flag(BACKUP_ENABLED_FLAG, default=True) is False


def test_backup_enabled_helper(tmp_path):
    assert backup_enabled() is True  # absent row → default on
    AppConfigStore().set_flag(BACKUP_ENABLED_FLAG, False)
    assert backup_enabled() is False


def test_backup_retention_days_defaults_to_30(tmp_path):
    assert _store(tmp_path).get_backup_retention_days() == DEFAULT_BACKUP_RETENTION_DAYS


def test_backup_retention_days_round_trips(tmp_path):
    store = _store(tmp_path)
    assert store.set_backup_retention_days(7) == 7
    assert _store(tmp_path).get_backup_retention_days() == 7


def test_backup_retention_days_clamped_to_valid_range(tmp_path):
    store = _store(tmp_path)
    assert store.set_backup_retention_days(0) == 1
    assert store.set_backup_retention_days(-5) == 1
    assert store.set_backup_retention_days(9999) == 365


def test_backup_retention_days_bad_value_on_disk_falls_back_to_default(tmp_path):
    _store(tmp_path).save({"backup_retention_days": "not-a-number"})
    assert _store(tmp_path).get_backup_retention_days() == DEFAULT_BACKUP_RETENTION_DAYS


# ── notify_unattended_write_enabled (Settings > Permissions toggle) ──────────


def test_notify_unattended_write_defaults_on(tmp_path):
    """Default is ON (True) — unlike the script/code-exec kill switches, which default OFF.
    An unattended write's per-write ping is today's established behavior; this flag is an
    opt-OUT, not an opt-in, so an upgrading install sees no behavior change until a human
    explicitly turns it off."""
    assert _store(tmp_path).get_flag(NOTIFY_UNATTENDED_WRITE_FLAG, default=True) is True


def test_notify_unattended_write_set_flag_persists(tmp_path):
    store = _store(tmp_path)
    assert store.set_flag(NOTIFY_UNATTENDED_WRITE_FLAG, False) is False
    assert _store(tmp_path).get_flag(NOTIFY_UNATTENDED_WRITE_FLAG, default=True) is False
    assert _store(tmp_path).set_flag(NOTIFY_UNATTENDED_WRITE_FLAG, True) is True


def test_notify_unattended_write_helper(tmp_path):
    assert notify_unattended_write_enabled() is True  # absent row → default on
    AppConfigStore().set_flag(NOTIFY_UNATTENDED_WRITE_FLAG, False)
    assert notify_unattended_write_enabled() is False
