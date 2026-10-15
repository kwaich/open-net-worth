"""Preview-enrichment dry-run (services/statements/enrich.py, RFC D5).

THE parity contract: the category/payee the preview shows == what commit assigns, because the
dry-run runs the SAME Automation + payee-default chain the write choke does — side-effect-free
(no payee minted, no Automation counter bumped). The one deliberate divergence: an LLM
``category_hint`` is shown (✦, source "hint") but NEVER auto-applied at commit by
``create_transaction``/``bulk_create_transactions`` themselves — statement-import's own review
screen (ImportModal) is what turns a shown category into a real one, by sending it back as an
ordinary explicit edit (see ``test_import_category_hint_fallback.py``, frontend side)."""

from __future__ import annotations

import pytest

from backend.db.automation_store import AutomationStore
from backend.services import categories as categories_service
from backend.services import ledger as ledger_service
from backend.services import payee_registry
from backend.services.statements.enrich import enrich_drafts


@pytest.fixture(autouse=True)
def env(monkeypatch, db_isolated):
    # AutomationStore/PayeeStore are DB-backed now (plan 4d task 2) — db_isolated redirects
    # the process-wide singleton, so both resolve against this fixture's isolated database
    # with no per-instance/module-constant path-sync needed.
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    ledger_service.accounts.insert(
        {"id": "acc", "name": "Card", "kind": "liability", "currency": "SGD"}
    )


@pytest.fixture
def transport_cat():
    return categories_service.create_category("Enrich Transport", "expense")["id"]


def _draft(**over):
    base = {
        "date": "2026-05-21",
        "kind": "expense",
        "description": "GRAB* A-98A2E5 HA NOI VN",
        "payee": None,
        "from_account_id": "acc",
        "from_amount": 3.01,
        "from_currency": "SGD",
        "source": "csv_import",
    }
    base.update(over)
    return base


def _create_transform(category_id: str) -> dict:
    return AutomationStore().create(
        {
            "id": "automation_grab",
            "name": "grab",
            "status": "active",
            "trigger": {
                "kind": "event",
                "event": "txn",
                "when": {"description_contains": "grab"},
                "limit": "each",
            },
            "execution": {
                "kind": "transaction_transform",
                "set": {"category": category_id},
                "priority": 100,
                "stop": True,
                "override_existing": False,
            },
        }
    )


# Removed 2026-08-02: these pinned a DECLARATIVE transaction transform categorizing the
# row inside create_transaction. Rules are event-triggered Automations now — they run
# AFTER the commit — so there is nothing to observe at the write. The surviving
# attribution is pinned in tests/test_rule_audit_provenance.py.


def test_payee_default_previews_and_commits_identically(transport_cat):
    payee = payee_registry.create_payee("Grab", default_category=transport_cat)
    draft = _draft(payee="Grab")
    verdict = enrich_drafts([draft])[0]
    assert verdict["payee_id"] == payee["id"]
    assert verdict["payee_name"] == "Grab"
    assert verdict["category"] == transport_cat
    assert verdict["category_source"] == "payee"
    stored = ledger_service.create_transaction(dict(draft))
    assert stored["payee_id"] == payee["id"]
    assert stored["category"] == verdict["category"]


def test_dry_run_is_side_effect_free(transport_cat):
    """Enriching a statement draft must mint nothing. The Automation half of this — that a
    dry run never bumped a rule's match_count — went with the transform engine; the payee
    half is the one that can still surprise you, since payee resolution DOES mint on a real
    write."""
    before_payees = len(payee_registry._store.all())
    enrich_drafts([_draft(), _draft(payee="Some Brand New Merchant")])
    assert len(payee_registry._store.all()) == before_payees  # nothing minted


def test_hint_is_shown_but_never_committed(transport_cat):
    label = categories_service.all_category_rows()
    label = next(r["label"] for r in label if r["id"] == transport_cat)
    draft = _draft(note="UNKNOWN MERCHANT 42", category_hint=label)
    verdict = enrich_drafts([draft])[0]
    assert verdict["category"] == transport_cat
    assert verdict["category_source"] == "hint"
    commit_draft = {k: v for k, v in draft.items() if k != "category_hint"}
    stored = ledger_service.create_transaction(commit_draft)
    assert stored["category"] is None


def test_unknown_hint_is_ignored():
    verdict = enrich_drafts([_draft(note="X 1", category_hint="Not A Real Category")])[0]
    assert verdict["category"] is None and verdict["category_source"] is None
