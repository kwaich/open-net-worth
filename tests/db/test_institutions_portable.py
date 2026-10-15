"""Institution records travel with account data in a portable bundle."""

import pytest
from sqlalchemy import select

from backend.core import portable
from backend.core.errors import ValidationError
from backend.db import institution_io, money_io, portable_bridge
from backend.db.engine import get_engine
from backend.db.tables import accounts, institutions


def test_institutions_are_a_portable_money_dependency(db_isolated):
    institution = {"id": "inst_dbs", "name": "DBS"}
    account = {
        "id": "acc_dbs",
        "name": "Everyday",
        "type": "cash",
        "currency": "SGD",
        "institution_id": "inst_dbs",
    }
    with get_engine().begin() as conn:
        institution_io.import_institutions(conn, [institution])
        money_io.import_accounts(conn, [account])
        assert institution_io.export_institutions(conn) == [institution]
        assert money_io.export_accounts(conn) == [account]

        portable_bridge.apply_money_bundle(
            conn,
            {
                filename: ([institution] if filename == "institutions.json" else [account])
                if filename in {"institutions.json", "accounts.json"}
                else None
                for filename in portable_bridge.MONEY_FILES
            },
        )
        assert conn.execute(select(institutions.c.id)).scalars().all() == ["inst_dbs"]
        assert conn.execute(select(accounts.c.id)).scalars().all() == ["acc_dbs"]

    names = {path.name for path in portable.PORTABLE_FILES}
    assert "institutions.json" in names
    assert "institutions.json" in portable_bridge.BRIDGED
    assert "institutions.json" in portable_bridge.MONEY_FILES


def test_old_portable_accounts_with_platform_upgrade_to_institutions(db_isolated):
    legacy_account = {
        "id": "acc_legacy",
        "name": "Legacy DBS",
        "type": "cash",
        "currency": "SGD",
        "platform": "DBS",
    }
    with get_engine().begin() as conn:
        portable_bridge.apply_money_bundle(
            conn,
            {
                filename: [legacy_account] if filename == "accounts.json" else None
                for filename in portable_bridge.MONEY_FILES
            },
        )
        restored = money_io.export_accounts(conn)
        restored_institutions = institution_io.export_institutions(conn)

    assert "platform" not in restored[0]
    assert restored[0]["institution_id"] == restored_institutions[0]["id"]
    assert restored_institutions[0]["name"] == "DBS"


def test_current_portable_accounts_require_their_institution_registry_rows(db_isolated):
    account = {
        "id": "acc_missing_institution",
        "name": "Everyday",
        "type": "cash",
        "currency": "SGD",
        "institution_id": "inst_missing",
    }
    with get_engine().begin() as conn:
        with pytest.raises(ValidationError, match="missing institution"):
            portable_bridge.apply_money_bundle(
                conn,
                {
                    filename: [account] if filename == "accounts.json" else None
                    for filename in portable_bridge.MONEY_FILES
                },
            )
