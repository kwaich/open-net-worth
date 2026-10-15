"""Shared institution lifecycle operations."""

from __future__ import annotations

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.db.account_store import AccountStore
from backend.db.engine import locked_unit_of_work
from backend.db.institution_store import InstitutionStore
from backend.db.tables import accounts as accounts_table
from backend.db.tables import institutions as institutions_table


def list_institutions() -> list[dict]:
    accounts = AccountStore().all()
    counts: dict[str, int] = {}
    for account in accounts:
        if institution_id := account.get("institution_id"):
            counts[institution_id] = counts.get(institution_id, 0) + 1
    return [
        {
            "id": institution["id"],
            "name": institution["name"],
            "account_count": counts.get(institution["id"], 0),
        }
        for institution in sorted(InstitutionStore().all(), key=lambda row: row["name"].casefold())
    ]


def rename_institution(institution_id: str, name: object) -> dict:
    display_name = " ".join(str(name or "").split())
    if not display_name:
        raise ValidationError("Institution name cannot be empty")
    with locked_unit_of_work(accounts_table, institutions_table) as conn:
        institutions = InstitutionStore()
        current = institutions.find_by_id(institution_id, conn=conn)
        if current is None:
            raise NotFoundError("Institution not found")
        duplicate = institutions.find_by_normalized_name(display_name, conn=conn)
        if duplicate is not None and duplicate["id"] != institution_id:
            raise ConflictError(f"Institution {duplicate['name']!r} already exists")
        renamed = institutions.update_by_id(institution_id, {"name": display_name}, conn=conn)
        count = sum(
            account.get("institution_id") == institution_id
            for account in AccountStore().all(conn=conn)
        )
    return {"id": renamed["id"], "name": renamed["name"], "account_count": count}


def delete_institution(institution_id: str) -> dict:
    with locked_unit_of_work(accounts_table, institutions_table) as conn:
        institutions = InstitutionStore()
        if institutions.find_by_id(institution_id, conn=conn) is None:
            raise NotFoundError("Institution not found")
        account_store = AccountStore()
        linked_ids = [
            account["id"]
            for account in account_store.all(conn=conn)
            if account.get("institution_id") == institution_id
        ]
        account_store.bulk_patch(
            {account_id: {"institution_id": None} for account_id in linked_ids}, conn=conn
        )
        institutions.delete(institution_id, conn=conn)
    return {
        "id": institution_id,
        "unlinked_account_count": len(linked_ids),
        "unlinked_account_ids": linked_ids,
    }
