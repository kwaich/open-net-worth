"""payee_registry entity logic — resolve / default / learn / usage / list
(P4-PAYEE phase 1). Isolated from the operator's real payee data via db_isolated
(PayeeStore is DB-backed — plan 4d task 2 — so the registry's module singleton
``payee_registry._store`` resolves its engine lazily and is isolated by the ONW_DATABASE_URL
redirect, same as every other DB-backed store)."""

from __future__ import annotations

import pytest

from backend.services import payee_registry


@pytest.fixture
def reg(db_isolated):
    """The registry, backed by a fresh, isolated database for this test."""
    return payee_registry


def test_resolve_creates_then_reuses_same_entity(reg):
    a = reg.resolve_payee_id("Anthropic, PBC #2321")
    b = reg.resolve_payee_id("ANTHROPIC PBC #9999")  # same normalized key
    assert a is not None
    assert a == b  # one entity, not two
    assert len(reg._store.all()) == 1


def test_resolve_blank_returns_none_and_mints_nothing(reg):
    assert reg.resolve_payee_id("") is None
    assert reg.resolve_payee_id("   ") is None
    assert reg._store.all() == []


def test_resolve_pure_digit_description_creates_payee(reg):
    # Numbers are now kept: "123456" normalizes to "123456" (not empty), so a payee
    # is minted. This lets phone-number QuickPay descriptions ("SEND MONEY TO 82220000")
    # resolve to distinct entities.
    pid = reg.resolve_payee_id("123456")
    assert pid is not None
    assert len(reg._store.all()) == 1


def test_resolve_no_create_misses(reg):
    assert reg.resolve_payee_id("Spotify", create=False) is None
    assert reg._store.all() == []


def test_default_category_gated_by_autocategorize(reg):
    from backend.services import categories as categories_service

    cat = categories_service.create_category("Subscriptions", "expense")
    pid = reg.resolve_payee_id("Spotify")
    assert reg.default_category_for(pid) is None  # nothing learned yet
    reg._store.set_default_category_for_kind(pid, "expense", cat["id"])
    assert reg.default_category_for(pid) == cat["id"]
    # Turn autocategorize off → default no longer applied, value retained.
    reg._store.update_by_id(pid, {"autocategorize": False})
    assert reg.default_category_for(pid) is None
    assert reg._store.find_by_id(pid)["default_categories"]["expense"] == cat["id"]


def test_default_category_for_none_or_missing(reg):
    assert reg.default_category_for(None) is None
    assert reg.default_category_for("pay_nope") is None


def test_learn_sets_first_write_wins(reg):
    pid = reg.resolve_payee_id("Grab")
    reg.learn_category(pid, "transport")
    assert reg._store.find_by_id(pid)["default_categories"]["expense"] == "transport"
    reg.learn_category(pid, "food")  # first-write wins — explicit user choice is preserved
    assert reg._store.find_by_id(pid)["default_categories"]["expense"] == "transport"


def test_learn_skips_blank_and_structural(reg):
    from backend.services.categories import STRUCTURAL_KEYS

    pid = reg.resolve_payee_id("Mystery")
    # None/blank is never learned — no category is not a payee signal.
    reg.learn_category(pid, None)
    assert not reg._store.find_by_id(pid).get("default_categories")
    reg.learn_category(pid, "")
    assert not reg._store.find_by_id(pid).get("default_categories")
    # A structural key (e.g. "split" anchor) is never learned either.
    structural = next(iter(STRUCTURAL_KEYS))
    reg.learn_category(pid, structural)
    assert not reg._store.find_by_id(pid).get("default_categories")


def test_learn_ignores_blank_or_missing_inputs(reg):
    pid = reg.resolve_payee_id("X")
    reg.learn_category(pid, "")  # no-op
    reg.learn_category(None, "food")  # no-op
    assert not reg._store.find_by_id(pid).get("default_categories")


def test_note_usage_updates_last_used(reg):
    pid = reg.resolve_payee_id("X")
    reg.note_usage(pid, "2026-04-01")
    p = reg._store.find_by_id(pid)
    assert p["last_used"] == "2026-04-01"
    assert "txn_count" not in p  # never stored — live count only


def test_list_registry_ranks_most_used_first(reg, monkeypatch):
    # list_registry() counts live from TransactionStore. Patch it to report no
    # usage — txn_count=0 for all; ranking then falls back to last_used.
    from backend.db.transaction_store import TransactionStore

    monkeypatch.setattr(TransactionStore, "payee_usage", lambda self: {})

    a = reg.resolve_payee_id("Alpha")
    b = reg.resolve_payee_id("Beta")
    # With no real transactions both have txn_count=0. last_used fallback:
    # Beta="2026-01-02", Alpha="2026-01-03" → Alpha (later) sorts first.
    reg.note_usage(b, "2026-01-01")
    reg.note_usage(b, "2026-01-02")
    reg.note_usage(a, "2026-01-03")
    rows = reg.list_registry(with_usage=True)
    assert [r["id"] for r in rows] == [a, b]
    assert {"id", "name", "default_categories", "hidden", "txn_count", "last_used"} <= set(rows[0])
    # txn_count in list_registry() rows is live-computed (not stored)
    assert isinstance(rows[0]["txn_count"], int)
    # The picker list keeps the same most-used order but carries no usage numbers.
    lean = reg.list_registry()
    assert [r["id"] for r in lean] == [a, b]
    assert not {"txn_count", "last_used"} & set(lean[0])


def test_list_registry_empty_on_fresh(reg):
    assert reg.list_registry() == []


# ── Renaming rules ────────────────────────────────────────────────────────────


def test_rule_contains_takes_precedence_over_alias(reg):
    # A "contains grab" rule should match "Grab* A-9BEIP3LGWFBJAV" even though
    # that description doesn't normalize to "grab" (it normalizes to "a 9beip3lgwfbjav").
    grab_id = reg.resolve_payee_id("Grab")  # mint payee via alias "grab"
    reg.add_payee_rule(grab_id, "contains", "Grab")
    # Now a Grab trip ID description resolves to the same payee.
    assert reg.resolve_payee_id("Grab* A-9BEIP3LGWFBJAV") == grab_id
    # And a plain grab description still resolves via alias (rules run first, alias second).
    assert reg.resolve_payee_id("Grab Delivery") == grab_id


def test_rule_is_exact_match(reg):
    pid = reg.resolve_payee_id("Netflix")
    reg.add_payee_rule(pid, "is", "NETFLIX.COM")
    assert reg.resolve_payee_id("NETFLIX.COM") == pid
    assert reg.resolve_payee_id("NETFLIX.COM/BILLING") != pid  # not exact


def test_rule_regex_match(reg):
    pid = reg.resolve_payee_id("Kestrel Eats")
    reg.add_payee_rule(pid, "regex", r"^fp\s*\*")
    assert reg.resolve_payee_id("fp*Kestrel Eats Singapore") == pid
    assert reg.resolve_payee_id("FP * KESTREL EATS") == pid  # case-insensitive


def test_rule_validation_rejects_bad_match_type(reg):
    pid = reg.resolve_payee_id("X")
    with pytest.raises(Exception):
        reg.add_payee_rule(pid, "startswith", "foo")


def test_rule_validation_rejects_bad_regex(reg):
    pid = reg.resolve_payee_id("X")
    with pytest.raises(Exception):
        reg.add_payee_rule(pid, "regex", "[invalid(")


def test_remove_payee_rule(reg):
    pid = reg.resolve_payee_id("Grab")
    reg.add_payee_rule(pid, "contains", "Grab")
    reg.add_payee_rule(pid, "contains", "GrabFood")
    result = reg.remove_payee_rule(pid, 0)
    assert len(result.get("rules", [])) == 1
    assert result["rules"][0]["value"] == "GrabFood"


def test_rules_folded_on_merge(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    monkeypatch.setattr(TransactionStore, "all", lambda self: [])

    src = reg.resolve_payee_id("GrabFood")
    tgt = reg.resolve_payee_id("Grab")
    reg.add_payee_rule(src, "contains", "grabfood")
    reg.add_payee_rule(tgt, "contains", "grab")
    reg.merge_payees(src, tgt)
    updated = reg._store.find_by_id(tgt)
    values = {r["value"] for r in updated.get("rules", [])}
    assert "grabfood" in values and "grab" in values


def test_rules_included_in_list_registry(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    monkeypatch.setattr(TransactionStore, "all", lambda self: [])

    pid = reg.resolve_payee_id("Grab")
    reg.add_payee_rule(pid, "contains", "grab")
    rows = reg.list_registry()
    row = next(r for r in rows if r["id"] == pid)
    assert row["rules"] == [{"match_type": "contains", "value": "grab"}]


def test_apply_payee_rules_to_history_updates_matching_txns(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    # Set up a payee with a rule
    pid = reg.resolve_payee_id("Grab")
    reg.add_payee_rule(pid, "contains", "grab")

    # Two txns: one already assigned, one unassigned that should now match
    txns = [
        {"id": "t1", "description": "Grab* A-12345", "payee_id": None, "deleted": False},
        {"id": "t2", "description": "GRAB Food delivery", "payee_id": None, "deleted": False},
        {"id": "t3", "description": "Netflix", "payee_id": None, "deleted": False},
    ]
    saved = []

    def fake_all(self):
        return txns

    def fake_replace_all(self, rows):
        saved.extend(rows)

    monkeypatch.setattr(TransactionStore, "all", fake_all)
    monkeypatch.setattr(TransactionStore, "replace_all", fake_replace_all)

    result = reg.apply_payee_rules_to_history()
    assert result["updated"] == 2
    assigned = {r["id"]: r["payee_id"] for r in saved}
    assert assigned["t1"] == pid
    assert assigned["t2"] == pid
    assert assigned["t3"] is None  # no rule matches Netflix


# ── require_payee ─────────────────────────────────────────────────────────────


def test_require_payee_returns_entity(reg):
    pid = reg.resolve_payee_id("Shopee")
    p = reg.require_payee(pid)
    assert p["id"] == pid
    assert p["name"] == "Shopee"


def test_require_payee_raises_not_found(reg):
    from backend.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        reg.require_payee("pay_doesnotexist")


# ── default_category_for — reserved keys filtered ─────────────────────────────


def test_default_category_for_filters_kind_name(reg):
    """A stored default that is a transaction kind name must be silently dropped."""
    pid = reg.resolve_payee_id("SomeBank")
    # Bypass the create_payee guard by writing directly through the store.
    reg._store.set_default_category_for_kind(pid, "expense", "income")
    assert reg.default_category_for(pid) is None


def test_default_category_for_filters_structural_key(reg):
    """A stored default that is a STRUCTURAL key must be silently dropped."""
    pid = reg.resolve_payee_id("Misc")
    reg._store.set_default_category_for_kind(pid, "expense", "split")
    assert reg.default_category_for(pid) is None


def test_default_category_for_none_payee_id(reg):
    assert reg.default_category_for(None) is None


def test_default_category_for_unknown_payee(reg):
    assert reg.default_category_for("pay_unknown") is None


# ── default_category_for — live-category guard (regression: dead/label defaults) ─────


def test_default_category_for_dead_category_id_returns_none(reg):
    """THE bug: a payee's stored default can outlive its category (deleted later). Trusting
    it blindly used to hand ``resolve_transaction_row`` a dangling id, which it then filled
    into a blank category and raised 'Unknown category' on — failing the whole create (and,
    in bulk, the whole batch). Must resolve to None (Uncategorized) instead."""
    from backend.services import categories as categories_service

    cat = categories_service.create_category("Temporary", "expense")
    pid = reg.resolve_payee_id("TempMerchant")
    reg._store.set_default_category_for_kind(pid, "expense", cat["id"])
    categories_service.delete_category(cat["id"])  # no txns reference it — succeeds
    assert reg.default_category_for(pid) is None


def test_default_category_for_resolves_a_label_form_default_to_its_id(reg):
    """A default stored as a LABEL (pre-opaque-id data, or any writer that bypassed
    normalization) resolves exactly like ``categories.resolve_category_ref`` — id or label,
    case-insensitive exact match — and hands back the ID, never the raw label."""
    from backend.services import categories as categories_service

    cat = categories_service.create_category("Groceries", "expense")
    pid = reg.resolve_payee_id("FairPrice")
    reg._store.set_default_category_for_kind(pid, "expense", "groceries")  # label, not id
    assert reg.default_category_for(pid) == cat["id"]


# ── create_payee ──────────────────────────────────────────────────────────────


def test_create_payee_mints_entity(reg):
    p = reg.create_payee("Lazada")
    assert p["id"].startswith("pay_")
    assert p["name"] == "Lazada"
    # Alias is the normalized name.
    assert "lazada" in p.get("aliases", [])


def test_create_payee_with_default_category(reg):
    from backend.services import categories as categories_service

    cat = categories_service.create_category("Shopping", "expense")
    p = reg.create_payee("Shopee", default_category=cat["id"])
    pid = p["id"]
    assert reg.default_category_for(pid) == cat["id"]


def test_create_payee_with_default_category_by_label_resolves_to_id(reg):
    """create_payee stores default_category through the SAME normalization update_payee
    uses (normalize_default_categories) — a label resolves to the category's id, never
    stored raw (regression: create_payee used to bypass normalization entirely)."""
    from backend.services import categories as categories_service

    cat = categories_service.create_category("Groceries", "expense")
    p = reg.create_payee("FairPrice", default_category="Groceries")
    assert p["default_categories"]["expense"] == cat["id"]
    assert reg.default_category_for(p["id"]) == cat["id"]


def test_create_payee_with_dead_default_category_is_dropped(reg):
    """A default_category that resolves to no live category (garbage id, typo'd label) is
    silently dropped — never stored raw and unchecked."""
    p = reg.create_payee("Ghost Co", default_category="cat_doesnotexist0000")
    assert p.get("default_categories", {}) == {}
    assert reg.default_category_for(p["id"]) is None


def test_create_payee_empty_name_raises(reg):
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError, match="non-empty"):
        reg.create_payee("   ")


def test_create_payee_structural_default_raises(reg):
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError, match="structural"):
        reg.create_payee("X", default_category="split")


def test_create_payee_kind_name_default_raises(reg):
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError, match="structural"):
        reg.create_payee("Y", default_category="transfer")


def test_create_payee_conflict_on_duplicate_alias(reg):
    from backend.core.errors import ConflictError

    reg.create_payee("Netflix")
    with pytest.raises(ConflictError):
        reg.create_payee("NETFLIX")  # same normalized alias


# ── update_payee ──────────────────────────────────────────────────────────────


def test_update_payee_name(reg):
    pid = reg.create_payee("OldName")["id"]
    result = reg.update_payee(pid, {"name": "NewName"})
    assert result["name"] == "NewName"


def test_update_payee_autocategorize_false(reg):
    pid = reg.create_payee("Spotify")["id"]
    reg.update_payee(pid, {"autocategorize": False})
    assert reg._store.find_by_id(pid)["autocategorize"] is False


def test_update_payee_hidden_true(reg):
    pid = reg.create_payee("HiddenPayee")["id"]
    reg.update_payee(pid, {"hidden": True})
    assert reg._store.find_by_id(pid)["hidden"] is True


def test_update_payee_clear_default_category(reg):
    pid = reg.create_payee("ClearMe", default_category="food")["id"]
    reg.update_payee(pid, {"default_categories": {}})
    assert reg._store.find_by_id(pid)["default_categories"] == {}


def test_update_payee_empty_name_raises(reg):
    from backend.core.errors import ValidationError

    pid = reg.create_payee("Valid")["id"]
    with pytest.raises(ValidationError, match="non-empty"):
        reg.update_payee(pid, {"name": ""})


def test_update_payee_unknown_field_raises(reg):
    from backend.core.errors import ValidationError

    pid = reg.create_payee("Valid")["id"]
    with pytest.raises(ValidationError, match="unknown payee fields"):
        reg.update_payee(pid, {"color": "red"})


def test_update_payee_structural_default_dropped(reg):
    pid = reg.create_payee("X")["id"]
    # "split" is the only structural key — cannot be a payee default; normalize_default_categories
    # silently drops it rather than raising (default_categories is a bulk replace-map field).
    result = reg.update_payee(pid, {"default_categories": {"expense": "split"}})
    assert result["default_categories"] == {}


def test_rename_seeds_the_new_name_as_an_alias(reg):
    """A renamed payee must ANSWER to its new name. Without this, cleaning up an
    auto-minted bank line and later typing the tidy name on a manual transaction
    mints a SECOND entity with the same name — the mint path bypasses the uniqueness
    guard by design (it sits on the ledger write path and must never fail)."""
    pid = reg.resolve_payee_id("TST* BLUE BOTTLE 442")
    reg.update_payee(pid, {"name": "Blue Bottle Coffee"})

    assert reg.resolve_payee_id("Blue Bottle Coffee") == pid
    assert len(reg._store.all()) == 1


def test_rename_keeps_the_pre_rename_aliases(reg):
    """Additive ONLY — the old alias is the bank-feed matcher. Dropping it would
    silently stop imports resolving and re-mint duplicates on the next statement."""
    pid = reg.resolve_payee_id("TST* BLUE BOTTLE 442")
    reg.update_payee(pid, {"name": "Blue Bottle Coffee"})

    aliases = reg._store.find_by_id(pid)["aliases"]
    assert "blue bottle 442" in aliases  # the original bank line still resolves
    assert "blue bottle coffee" in aliases
    assert reg.resolve_payee_id("TST* BLUE BOTTLE 442") == pid


def test_rename_to_a_name_already_aliased_to_self_adds_no_duplicate(reg):
    pid = reg.create_payee("Netflix")["id"]
    reg.update_payee(pid, {"name": "NETFLIX!"})  # same normalized form
    assert reg._store.find_by_id(pid)["aliases"] == ["netflix"]


def test_rename_to_a_blank_normalizing_name_adds_no_alias(reg):
    """ "..." normalizes to empty — a name can be punctuation, an alias cannot."""
    pid = reg.create_payee("Netflix")["id"]
    reg.update_payee(pid, {"name": "..."})
    assert reg._store.find_by_id(pid)["aliases"] == ["netflix"]


def test_non_name_update_leaves_aliases_untouched(reg):
    pid = reg.create_payee("Netflix")["id"]
    reg.update_payee(pid, {"hidden": True})
    assert reg._store.find_by_id(pid)["aliases"] == ["netflix"]


def test_update_payee_rename_onto_another_name_raises(reg):
    """Regression: rename was the hole in payee-name uniqueness — create_payee
    refused a duplicate, then update_payee let you produce one a call later."""
    from backend.core.errors import ConflictError

    reg.create_payee("Netflix")
    other = reg.create_payee("Spotify")["id"]
    with pytest.raises(ConflictError, match="Netflix"):
        reg.update_payee(other, {"name": "NETFLIX"})  # same normalized name
    assert reg._store.find_by_id(other)["name"] == "Spotify"


def test_update_payee_rename_onto_another_alias_raises(reg):
    """A name that collides with someone else's ALIAS is equally indistinguishable
    in autocomplete — an alias is what resolves a description to the entity."""
    from backend.core.errors import ConflictError

    a = reg.create_payee("PayeeA")["id"]
    b = reg.create_payee("PayeeB")["id"]
    reg.add_alias(b, "Bee Corp")
    with pytest.raises(ConflictError, match="PayeeB"):
        reg.update_payee(a, {"name": "Bee Corp"})


def test_update_payee_rename_to_own_name_variant_allowed(reg):
    """Recasing / repunctuating your OWN name must not self-collide."""
    pid = reg.create_payee("netflix")["id"]
    assert reg.update_payee(pid, {"name": "Netflix, Inc."})["name"] == "Netflix, Inc."


def test_update_payee_non_name_change_ignores_duplicate_names(reg):
    """Pre-existing duplicates (auto-minted before the guard) stay editable —
    the guard only fires on a name change."""
    a = reg.create_payee("Dupe")["id"]
    b = reg.create_payee("Other")["id"]
    reg._store.update_by_id(b, {"name": "Dupe"})  # simulate legacy duplicate
    assert reg.update_payee(b, {"hidden": True})["hidden"] is True
    assert reg.update_payee(a, {"autocategorize": False})["autocategorize"] is False


def test_create_payee_conflict_on_duplicate_name_without_alias(reg):
    """Uniqueness is on the normalized NAME too, not only the alias index — an
    entity whose alias set no longer carries its own name still blocks a twin."""
    from backend.core.errors import ConflictError

    pid = reg.create_payee("Netflix")["id"]
    reg._store.update_by_id(pid, {"aliases": []})
    with pytest.raises(ConflictError):
        reg.create_payee("NETFLIX")


def test_update_payee_not_found_raises(reg):
    from backend.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        reg.update_payee("pay_ghost", {"name": "Ghost"})


def test_update_payee_no_changes_returns_existing(reg):
    pid = reg.create_payee("Stable")["id"]
    result = reg.update_payee(pid, {})
    assert result["id"] == pid


def test_update_payee_no_changes_missing_raises(reg):
    from backend.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        reg.update_payee("pay_missing", {})


# ── delete_payee ──────────────────────────────────────────────────────────────


def test_delete_payee_removes_entity(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    monkeypatch.setattr(TransactionStore, "all", lambda self: [])
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: None)

    pid = reg.create_payee("ToDelete")["id"]
    reg.delete_payee(pid)
    assert reg._store.find_by_id(pid) is None


def test_delete_payee_clears_txn_payee_id(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    pid = reg.create_payee("ClearTxns")["id"]
    txns = [
        {"id": "t1", "payee_id": pid, "description": "shop"},
        {"id": "t2", "payee_id": "pay_other", "description": "other"},
    ]
    saved = []

    monkeypatch.setattr(TransactionStore, "all", lambda self: list(txns))
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: saved.extend(rows))

    reg.delete_payee(pid)
    by_id = {r["id"]: r for r in saved}
    assert by_id["t1"]["payee_id"] is None
    assert by_id["t2"]["payee_id"] == "pay_other"


def test_delete_payee_reassign_to_moves_txns(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    src_id = reg.create_payee("Source")["id"]
    tgt_id = reg.create_payee("Target")["id"]
    txns = [{"id": "t1", "payee_id": src_id, "description": "x"}]
    saved = []

    monkeypatch.setattr(TransactionStore, "all", lambda self: list(txns))
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: saved.extend(rows))

    reg.delete_payee(src_id, reassign_to=tgt_id)
    assert reg._store.find_by_id(src_id) is None
    # Transaction should now point to target
    by_id = {r["id"]: r for r in saved}
    assert by_id["t1"]["payee_id"] == tgt_id


def test_delete_payee_not_found_raises(reg):
    from backend.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        reg.delete_payee("pay_ghost")


def test_delete_payee_reassign_to_missing_raises(reg, monkeypatch):
    from backend.core.errors import NotFoundError

    pid = reg.create_payee("X")["id"]
    with pytest.raises(NotFoundError):
        reg.delete_payee(pid, reassign_to="pay_nowhere")


def test_delete_payee_empty_id_raises(reg):
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError):
        reg.delete_payee("")


# ── merge_payees ──────────────────────────────────────────────────────────────


def test_merge_payees_moves_aliases(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    monkeypatch.setattr(TransactionStore, "all", lambda self: [])
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: None)

    # Use names that normalize to different strings to avoid a ConflictError.
    src_id = reg.create_payee("GrabFood")["id"]
    tgt_id = reg.create_payee("GrabDelivery")["id"]

    # Add a distinct alias to source so we can verify it folds into target.
    reg._store.add_alias(src_id, "grabfood sg")

    result = reg.merge_payees(src_id, tgt_id)
    assert reg._store.find_by_id(src_id) is None  # source deleted
    assert "grabfood sg" in result.get("aliases", [])


def test_merge_payees_moves_rules(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    monkeypatch.setattr(TransactionStore, "all", lambda self: [])
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: None)

    src_id = reg.create_payee("GrabFood")["id"]
    tgt_id = reg.create_payee("GrabDelivery")["id"]

    # Target already has a rule; source has that same rule plus a distinct one.
    # Seed directly on the store to bypass add_payee_rule's cross-payee conflict guard.
    reg._store.update_by_id(tgt_id, {"rules": [{"match_type": "is", "value": "GRAB*FOOD"}]})
    reg._store.update_by_id(
        src_id,
        {
            "rules": [
                {"match_type": "is", "value": "GRAB*FOOD"},
                {"match_type": "contains", "value": "grabfood promo"},
            ]
        },
    )

    result = reg.merge_payees(src_id, tgt_id)
    assert reg._store.find_by_id(src_id) is None  # source deleted
    rules = result.get("rules", [])
    assert {"match_type": "contains", "value": "grabfood promo"} in rules
    # Overlapping rule folds once, not twice — proves the dedup-by-equality logic.
    assert rules.count({"match_type": "is", "value": "GRAB*FOOD"}) == 1


def test_merge_payees_same_id_raises(reg):
    from backend.core.errors import ValidationError

    pid = reg.create_payee("X")["id"]
    with pytest.raises(ValidationError, match="different"):
        reg.merge_payees(pid, pid)


def test_merge_payees_source_not_found_raises(reg):
    from backend.core.errors import NotFoundError

    tgt_id = reg.create_payee("Target")["id"]
    with pytest.raises(NotFoundError):
        reg.merge_payees("pay_ghost", tgt_id)


def test_merge_payees_target_not_found_raises(reg):
    from backend.core.errors import NotFoundError

    src_id = reg.create_payee("Source")["id"]
    with pytest.raises(NotFoundError):
        reg.merge_payees(src_id, "pay_ghost")


def test_merge_payees_takes_latest_last_used(reg, monkeypatch):
    """merge_payees folds last_used (max date) into target. txn_count is not stored
    — it is live-computed from the re-pointed transactions by list_registry()."""
    from backend.db.transaction_store import TransactionStore

    monkeypatch.setattr(TransactionStore, "all", lambda self: [])
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: None)

    src_id = reg.create_payee("Src")["id"]
    tgt_id = reg.create_payee("Tgt")["id"]
    reg._store.bump_usage(src_id, when="2026-03-05")
    reg._store.bump_usage(tgt_id, when="2026-01-15")

    result = reg.merge_payees(src_id, tgt_id)
    # last_used: max("2026-03-05", "2026-01-15") = "2026-03-05"
    assert result["last_used"] == "2026-03-05"
    # txn_count is NOT a stored field on the returned raw dict
    assert "txn_count" not in result


def test_merge_payees_reassigns_transactions(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    src_id = reg.create_payee("Src")["id"]
    tgt_id = reg.create_payee("Tgt")["id"]
    txns = [
        {"id": "t1", "payee_id": src_id, "description": "src txn"},
        {"id": "t2", "payee_id": tgt_id, "description": "tgt txn"},
    ]
    saved = []

    monkeypatch.setattr(TransactionStore, "all", lambda self: list(txns))
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: saved.extend(rows))

    reg.merge_payees(src_id, tgt_id)
    by_id = {r["id"]: r for r in saved}
    assert by_id["t1"]["payee_id"] == tgt_id
    assert by_id["t2"]["payee_id"] == tgt_id


# ── add_alias / remove_alias ──────────────────────────────────────────────────


def test_add_alias_normalizes_and_stores(reg):
    pid = reg.create_payee("Grab")["id"]
    # "Grab Taxi" normalizes to "grab taxi" (no processor-prefix stripping here).
    reg.add_alias(pid, "Grab Taxi")
    p = reg._store.find_by_id(pid)
    assert "grab taxi" in p.get("aliases", [])


def test_add_alias_empty_raises(reg):
    from backend.core.errors import ValidationError

    pid = reg.create_payee("X")["id"]
    with pytest.raises(ValidationError, match="empty string"):
        reg.add_alias(pid, "   ")


def test_add_alias_conflict_with_other_payee_raises(reg):
    from backend.core.errors import ConflictError

    a = reg.create_payee("PayeeA")["id"]
    reg.create_payee("PayeeB")  # normalizes to "payeeb"
    # "payeeb" is already claimed by PayeeB; trying to add it to PayeeA should fail.
    with pytest.raises(ConflictError):
        reg.add_alias(a, "PayeeB")


def test_add_alias_payee_not_found_raises(reg):
    from backend.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        reg.add_alias("pay_ghost", "something")


def test_add_alias_same_payee_no_conflict(reg):
    pid = reg.create_payee("Foo")["id"]
    # Adding an alias already on THIS payee should not raise.
    reg.add_alias(pid, "foo")  # "foo" is already the alias from create; no-op or no raise
    p = reg._store.find_by_id(pid)
    assert p is not None


def test_remove_alias_removes_entry(reg):
    pid = reg.create_payee("Netflix")["id"]
    reg._store.add_alias(pid, "netflix.com")
    result = reg.remove_alias(pid, "netflix.com")
    assert "netflix.com" not in result.get("aliases", [])


def test_remove_alias_noop_when_missing(reg):
    pid = reg.create_payee("Netflix")["id"]
    # Removing an alias that doesn't exist should not raise.
    result = reg.remove_alias(pid, "nonexistent")
    assert result["id"] == pid


def test_remove_alias_payee_not_found_raises(reg):
    from backend.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        reg.remove_alias("pay_ghost", "alias")


# ── set_payee_rules ────────────────────────────────────────────────────────────


def test_set_payee_rules_replaces_all(reg):
    pid = reg.create_payee("X")["id"]
    reg.add_payee_rule(pid, "contains", "old_rule")
    result = reg.set_payee_rules(
        pid,
        [
            {"match_type": "is", "value": "new_exact"},
            {"match_type": "contains", "value": "contains_val"},
        ],
    )
    assert len(result["rules"]) == 2
    values = {r["value"] for r in result["rules"]}
    assert "old_rule" not in values
    assert "new_exact" in values


def test_set_payee_rules_validates_entries(reg):
    from backend.core.errors import ValidationError

    pid = reg.create_payee("X")["id"]
    with pytest.raises(ValidationError, match=r"rule\[0\]"):
        reg.set_payee_rules(pid, [{"match_type": "bad_type", "value": "foo"}])


def test_set_payee_rules_not_list_raises(reg):
    from backend.core.errors import ValidationError

    pid = reg.create_payee("X")["id"]
    with pytest.raises(ValidationError, match="list"):
        reg.set_payee_rules(pid, "not a list")  # type: ignore[arg-type]


def test_set_payee_rules_not_found_raises(reg):
    from backend.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        reg.set_payee_rules("pay_ghost", [])


def test_set_payee_rules_empty_clears_rules(reg):
    pid = reg.create_payee("Y")["id"]
    reg.add_payee_rule(pid, "contains", "match_me")
    result = reg.set_payee_rules(pid, [])
    assert result.get("rules", []) == []


# ── remove_payee_rule edge cases ──────────────────────────────────────────────


def test_remove_payee_rule_out_of_range_raises(reg):
    from backend.core.errors import ValidationError

    pid = reg.create_payee("Z")["id"]
    reg.add_payee_rule(pid, "contains", "only")
    with pytest.raises(ValidationError, match="out of range"):
        reg.remove_payee_rule(pid, 5)


def test_remove_payee_rule_not_found_raises(reg):
    from backend.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        reg.remove_payee_rule("pay_ghost", 0)


# ── _match_rule edge cases ────────────────────────────────────────────────────


def test_match_rule_empty_value_returns_false(reg):
    """A rule with no value should never match anything."""
    from backend.services.payee_registry import _match_rule

    assert _match_rule("anything", {"match_type": "contains", "value": ""}) is False
    assert _match_rule("anything", {"match_type": "is", "value": None}) is False


def test_match_rule_bad_regex_returns_false(reg):
    from backend.services.payee_registry import _match_rule

    assert _match_rule("foo bar", {"match_type": "regex", "value": "[invalid("}) is False


def test_match_rule_unknown_match_type_returns_false(reg):
    from backend.services.payee_registry import _match_rule

    assert _match_rule("foo", {"match_type": "startswith", "value": "f"}) is False


def test_match_rule_empty_description_always_false(reg):
    from backend.services.payee_registry import _match_rule

    # Empty description against a 'contains' rule: val not in "" → False
    assert _match_rule("", {"match_type": "contains", "value": "grab"}) is False


# ── _validate_rule ────────────────────────────────────────────────────────────


def test_validate_rule_empty_value_raises(reg):
    from backend.core.errors import ValidationError
    from backend.services.payee_registry import _validate_rule

    with pytest.raises(ValidationError, match="non-empty"):
        _validate_rule({"match_type": "contains", "value": "   "})


def test_validate_rule_valid_regex_passes(reg):
    from backend.services.payee_registry import _validate_rule

    _validate_rule({"match_type": "regex", "value": r"^\d+"})  # should not raise


# ── add_payee_rule — conflict detection ──────────────────────────────────────


def test_add_payee_rule_conflict_with_other_payee_raises(reg):
    from backend.core.errors import ConflictError

    a = reg.create_payee("PayeeA")["id"]
    b = reg.create_payee("PayeeB")["id"]
    reg.add_payee_rule(a, "contains", "shared_keyword")
    with pytest.raises(ConflictError):
        reg.add_payee_rule(b, "contains", "shared_keyword")


def test_add_payee_rule_not_found_raises(reg):
    from backend.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        reg.add_payee_rule("pay_ghost", "contains", "foo")


# ── apply_payee_rules_to_existing ─────────────────────────────────────────────


def test_apply_payee_rules_to_existing_updates_matching(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    pid = reg.create_payee("Grab")["id"]
    reg.add_payee_rule(pid, "contains", "grab")

    txns = [
        {"id": "t1", "payee_id": None, "description": "GrabFood delivery", "deleted": False},
        {
            "id": "t2",
            "payee_id": pid,
            "description": "Grab Ride",
            "deleted": False,
        },  # already assigned
        {"id": "t3", "payee_id": None, "description": "Netflix subscription", "deleted": False},
    ]
    saved = []
    monkeypatch.setattr(TransactionStore, "all", lambda self: list(txns))
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: saved.extend(rows))

    result = reg.apply_payee_rules_to_existing(pid)
    assert result["updated"] == 1
    by_id = {r["id"]: r for r in saved}
    assert by_id["t1"]["payee_id"] == pid
    assert by_id["t2"]["payee_id"] == pid  # unchanged
    assert by_id["t3"]["payee_id"] is None  # no match


def test_apply_payee_rules_to_existing_skips_deleted(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    pid = reg.create_payee("Lazada")["id"]
    reg.add_payee_rule(pid, "contains", "lazada")

    txns = [
        {"id": "t1", "payee_id": None, "description": "lazada purchase", "deleted": True},
    ]
    saved = []
    monkeypatch.setattr(TransactionStore, "all", lambda self: list(txns))
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: saved.extend(rows))

    result = reg.apply_payee_rules_to_existing(pid)
    assert result["updated"] == 0


def test_apply_payee_rules_to_existing_no_rules_returns_zero(reg):
    pid = reg.create_payee("NoRules")["id"]
    result = reg.apply_payee_rules_to_existing(pid)
    assert result == {"updated": 0}


def test_apply_payee_rules_to_existing_not_found_raises(reg):
    from backend.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        reg.apply_payee_rules_to_existing("pay_ghost")


# ── apply_default_category_to_transactions ────────────────────────────────────


def test_apply_default_category_stamps_expense_only(reg, monkeypatch):
    # Per-kind (2026-07-10): a payee carrying only an EXPENSE default. Apply stamps its
    # expense rows; income rows are NOT stamped (that cross-kind bleed was the bug this
    # feature fixes).
    from backend.db.transaction_store import TransactionStore
    from backend.services import categories as categories_service

    cat = categories_service.create_category("Subscriptions", "expense")
    pid = reg.create_payee("Spotify", default_category=cat["id"])["id"]

    txns = [
        {
            "id": "t1",
            "payee_id": pid,
            "kind": "expense",
            "category": "uncategorized",
            "deleted": False,
        },
        {"id": "t2", "payee_id": pid, "kind": "income", "category": "salary", "deleted": False},
        {
            "id": "t3",
            "payee_id": pid,
            "kind": "transfer",
            "category": None,
            "deleted": False,
        },  # skipped
        {
            "id": "t4",
            "payee_id": pid,
            "kind": "expense",
            "category": "split",
            "deleted": False,
        },  # skipped
        {
            "id": "t5",
            "payee_id": pid,
            "kind": "expense",
            "category": cat["id"],
            "deleted": False,
        },  # already correct
    ]
    saved = []
    monkeypatch.setattr(TransactionStore, "all", lambda self: list(txns))
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: saved.extend(rows))

    result = reg.apply_default_category_to_transactions(pid)
    assert result["updated"] == 1  # only the one uncategorized EXPENSE row
    by_id = {r["id"]: r for r in saved}
    assert by_id["t1"]["category"] == cat["id"]
    assert by_id["t2"]["category"] == "salary"  # income NOT stamped with the expense default
    assert by_id["t3"]["category"] is None  # transfer untouched
    assert by_id["t4"]["category"] == "split"  # split untouched


def test_apply_default_category_no_category_raises(reg):
    from backend.core.errors import ValidationError

    pid = reg.create_payee("NoCat")["id"]
    with pytest.raises(ValidationError, match="no default category"):
        reg.apply_default_category_to_transactions(pid)


def test_apply_default_category_not_found_raises(reg):
    from backend.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        reg.apply_default_category_to_transactions("pay_ghost")


def test_apply_default_category_skips_deleted(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore
    from backend.services import categories as categories_service

    cat = categories_service.create_category("Food", "expense")
    pid = reg.create_payee("X", default_category=cat["id"])["id"]
    txns = [
        {
            "id": "t1",
            "payee_id": pid,
            "kind": "expense",
            "category": "uncategorized",
            "deleted": True,
        },
    ]
    saved = []
    monkeypatch.setattr(TransactionStore, "all", lambda self: list(txns))
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: saved.extend(rows))

    result = reg.apply_default_category_to_transactions(pid)
    assert result["updated"] == 0


# ── apply_payee_rules_to_history — no-update path ────────────────────────────


def test_apply_payee_rules_to_history_no_matches_does_not_replace(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    replace_called = []
    monkeypatch.setattr(TransactionStore, "all", lambda self: [])
    monkeypatch.setattr(
        TransactionStore, "replace_all", lambda self, rows: replace_called.append(rows)
    )

    result = reg.apply_payee_rules_to_history()
    assert result["updated"] == 0
    assert replace_called == []  # replace_all not called when nothing changed


def test_apply_payee_rules_to_history_skips_deleted(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    pid = reg.create_payee("Grab")["id"]
    reg.add_payee_rule(pid, "contains", "grab")

    txns = [{"id": "t1", "description": "Grab ride", "payee_id": None, "deleted": True}]
    saved = []
    monkeypatch.setattr(TransactionStore, "all", lambda self: list(txns))
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: saved.extend(rows))

    result = reg.apply_payee_rules_to_history()
    assert result["updated"] == 0


def test_apply_payee_rules_to_history_skips_empty_description(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    pid = reg.create_payee("Grab")["id"]
    reg.add_payee_rule(pid, "contains", "grab")

    txns = [{"id": "t1", "description": "", "payee_id": None, "deleted": False}]
    saved = []
    monkeypatch.setattr(TransactionStore, "all", lambda self: list(txns))
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: saved.extend(rows))

    result = reg.apply_payee_rules_to_history()
    assert result["updated"] == 0


# ── list_registry — live txn_count path ──────────────────────────────────────


def test_list_registry_live_txn_count_from_transactions(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    pid = reg.create_payee("Grab")["id"]

    txns = [
        {"id": "t1", "payee_id": pid, "date": "2026-05-01", "deleted": False},
        {"id": "t2", "payee_id": pid, "date": "2026-06-01", "deleted": False},
        {"id": "t3", "payee_id": pid, "date": "2026-04-01", "deleted": True},  # deleted — excluded
        {
            "id": "t4",
            "payee_id": None,
            "date": "2026-05-01",
            "deleted": False,
        },  # no payee — excluded
    ]
    usage = {}
    for t in txns:
        if t["deleted"] or not t["payee_id"]:
            continue
        n, last = usage.get(t["payee_id"], (0, ""))
        usage[t["payee_id"]] = (n + 1, max(last, t["date"]))
    monkeypatch.setattr(TransactionStore, "payee_usage", lambda self: usage)

    rows = reg.list_registry(with_usage=True)
    assert len(rows) == 1
    assert rows[0]["txn_count"] == 2
    assert rows[0]["last_used"] == "2026-06-01"


def test_list_registry_autocategorize_field_present(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    monkeypatch.setattr(TransactionStore, "all", lambda self: [])

    reg.create_payee("AutocatTest")
    rows = reg.list_registry()
    assert "autocategorize" in rows[0]
    assert rows[0]["autocategorize"] is True


# ── note_usage — edge cases ───────────────────────────────────────────────────


def test_note_usage_none_payee_id_is_noop(reg):
    reg.note_usage(None, "2026-01-01")  # must not raise


def test_note_usage_none_when_leaves_last_used_none(reg):
    pid = reg.resolve_payee_id("Test")
    reg.note_usage(pid, None)
    p = reg._store.find_by_id(pid)
    assert p["last_used"] is None
    assert "txn_count" not in p  # not stored


# ── regression: orphaned payee txn_count = 0 ─────────────────────────────────


def test_orphaned_payee_reports_zero_live_txn_count(reg, monkeypatch):
    """An auto-minted payee whose transaction was reassigned to another payee must
    report txn_count=0 from list_registry() — not a stale stored '1' from the old
    bump_usage call made at create time. Regression for the drift bug."""
    from backend.db.transaction_store import TransactionStore

    pid = reg.resolve_payee_id("Orphan Merchant")
    # Simulate: the transaction that minted this payee was later reassigned to another payee
    monkeypatch.setattr(
        TransactionStore, "payee_usage", lambda self: {"pay_other": (1, "2026-06-01")}
    )

    # Call note_usage as the ledger choke point would have at create time
    reg.note_usage(pid, "2026-06-01")
    # Stored row must NOT have txn_count
    assert "txn_count" not in reg._store.find_by_id(pid)
    # list_registry() must report 0 (no transactions reference this payee)
    rows = reg.list_registry(with_usage=True)
    orphan = next(r for r in rows if r["id"] == pid)
    assert orphan["txn_count"] == 0


# ── learn_category — kind-name filtering ─────────────────────────────────────


def test_learn_category_kind_names_are_skipped(reg):
    """Kind names (expense, income, transfer, adjustment, investment) must never be learned."""
    pid = reg.resolve_payee_id("Bank")
    for kind_name in ("expense", "income", "transfer", "adjustment", "investment"):
        reg.learn_category(pid, kind_name)
    assert not reg._store.find_by_id(pid).get("default_categories")


# ── resolve_payee_id — normalizes-to-empty branch ────────────────────────────


def test_resolve_payee_id_processor_prefix_only_returns_none(reg):
    """A description that is ONLY a card-processor prefix (e.g. 'SQ *') strips to
    empty after normalization and must return None without minting a payee."""
    result = reg.resolve_payee_id("SQ *")
    assert result is None
    assert reg._store.all() == []


def test_resolve_payee_id_processor_prefix_only_no_create_returns_none(reg):
    result = reg.resolve_payee_id("SQ *", create=False)
    assert result is None


# ── update_payee — empty payee_id guard ──────────────────────────────────────


def test_update_payee_empty_id_raises(reg):
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError):
        reg.update_payee("", {"name": "X"})


# ── AUDIT1 regression: bulk payee ops must emit txn_audit events ─────────────
# merge_payees / apply_payee_rules_to_history / apply_payee_rules_to_existing /
# apply_default_category_to_transactions all bulk-rewrite payee_id/category on
# transactions via a raw TransactionStore.replace_all() — a write that bypasses
# services/audit.py entirely unless the call site explicitly batch-records it.
# These pin: (1) an audit event is recorded per row actually changed, (2) op is
# "update", (3) an untouched row gets NO event.


def test_merge_payees_records_audit_events(reg, monkeypatch):
    from backend.db.transaction_audit_store import TransactionAuditStore
    from backend.db.transaction_store import TransactionStore

    src_id = reg.create_payee("Src")["id"]
    tgt_id = reg.create_payee("Tgt")["id"]
    txns = [
        {"id": "t1", "payee_id": src_id, "description": "src txn", "source": "manual"},
        {"id": "t2", "payee_id": tgt_id, "description": "tgt txn", "source": "manual"},
    ]
    monkeypatch.setattr(TransactionStore, "all", lambda self: list(txns))
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: None)

    reg.merge_payees(src_id, tgt_id)

    audit_store = TransactionAuditStore()
    t1_events = audit_store.for_txn("t1")
    assert len(t1_events) == 1
    assert t1_events[0]["op"] == "update"
    # t2 already belonged to the target — it never changed, so it must get NO event.
    assert audit_store.for_txn("t2") == []


def test_apply_payee_rules_to_history_records_audit_events(reg, monkeypatch):
    from backend.db.transaction_audit_store import TransactionAuditStore
    from backend.db.transaction_store import TransactionStore

    pid = reg.create_payee("Grab")["id"]
    reg.add_payee_rule(pid, "contains", "grab")
    txns = [
        {
            "id": "t1",
            "description": "Grab* A-12345",
            "payee_id": None,
            "deleted": False,
            "source": "manual",
        },
        {
            "id": "t2",
            "description": "Netflix",
            "payee_id": None,
            "deleted": False,
            "source": "manual",
        },
    ]
    monkeypatch.setattr(TransactionStore, "all", lambda self: list(txns))
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: None)

    result = reg.apply_payee_rules_to_history()
    assert result["updated"] == 1

    audit_store = TransactionAuditStore()
    t1_events = audit_store.for_txn("t1")
    assert len(t1_events) == 1
    assert t1_events[0]["op"] == "update"
    # t2 matched no rule — must get NO event (the audit call must not cover the whole ledger).
    assert audit_store.for_txn("t2") == []


def test_apply_payee_rules_to_existing_records_audit_events(reg, monkeypatch):
    from backend.db.transaction_audit_store import TransactionAuditStore
    from backend.db.transaction_store import TransactionStore

    pid = reg.create_payee("Grab")["id"]
    reg.add_payee_rule(pid, "contains", "grab")
    txns = [
        {
            "id": "t1",
            "payee_id": None,
            "description": "GrabFood delivery",
            "deleted": False,
            "source": "manual",
        },
        {
            "id": "t2",
            "payee_id": None,
            "description": "Netflix subscription",
            "deleted": False,
            "source": "manual",
        },
    ]
    monkeypatch.setattr(TransactionStore, "all", lambda self: list(txns))
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: None)

    result = reg.apply_payee_rules_to_existing(pid)
    assert result["updated"] == 1

    audit_store = TransactionAuditStore()
    t1_events = audit_store.for_txn("t1")
    assert len(t1_events) == 1
    assert t1_events[0]["op"] == "update"
    assert audit_store.for_txn("t2") == []


def test_apply_default_category_to_transactions_records_audit_events(reg, monkeypatch):
    from backend.db.transaction_audit_store import TransactionAuditStore
    from backend.db.transaction_store import TransactionStore
    from backend.services import categories as categories_service

    cat = categories_service.create_category("Subscriptions", "expense")
    pid = reg.create_payee("Spotify", default_category=cat["id"])["id"]
    txns = [
        {
            "id": "t1",
            "payee_id": pid,
            "kind": "expense",
            "category": "uncategorized",
            "deleted": False,
            "source": "manual",
        },
        {
            "id": "t2",
            "payee_id": pid,
            "kind": "expense",
            "category": cat["id"],  # already correct — must not change
            "deleted": False,
            "source": "manual",
        },
    ]
    monkeypatch.setattr(TransactionStore, "all", lambda self: list(txns))
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: None)

    result = reg.apply_default_category_to_transactions(pid)
    assert result["updated"] == 1

    audit_store = TransactionAuditStore()
    t1_events = audit_store.for_txn("t1")
    assert len(t1_events) == 1
    assert t1_events[0]["op"] == "update"
    assert audit_store.for_txn("t2") == []


def test_delete_payee_without_reassign_records_audit_events(reg, monkeypatch):
    from backend.db.transaction_audit_store import TransactionAuditStore
    from backend.db.transaction_store import TransactionStore

    pid = reg.create_payee("Doomed")["id"]
    other_id = reg.create_payee("Bystander")["id"]
    txns = [
        {"id": "t1", "payee_id": pid, "description": "doomed txn 1", "source": "manual"},
        {"id": "t2", "payee_id": pid, "description": "doomed txn 2", "source": "manual"},
        {"id": "t3", "payee_id": other_id, "description": "bystander txn", "source": "manual"},
    ]
    monkeypatch.setattr(TransactionStore, "all", lambda self: list(txns))
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: None)

    reg.delete_payee(pid)

    audit_store = TransactionAuditStore()
    for txn_id in ("t1", "t2"):
        events = audit_store.for_txn(txn_id)
        assert len(events) == 1
        assert events[0]["op"] == "update"
        assert events[0]["note"] == f"payee_delete_clear:{pid}"
    # t3 belongs to another payee — it never changed, so it must get NO event.
    assert audit_store.for_txn("t3") == []


def test_alias_prefix_fallback_survives_bank_suffix(reg):
    """LIVE BUG: the book's alias '7 eleven 138 robinso' missed the statement's
    '7-ELEVEN - 138 ROBINSO Singapore SG' (exact-alias only) → booked Uncategorized despite a
    default category. A long alias matching at a token boundary now wins; short aliases
    and ambiguous ties never prefix-match."""
    from backend.services.payee_registry import _store, resolve_payee_id

    p = _store.create("7-Eleven Robinson", aliases=["7 eleven 138 robinso"])
    assert resolve_payee_id("7-ELEVEN - 138 ROBINSO Singapore SG", create=False) == p["id"]
    # token boundary: the alias must not claim an unrelated longer token
    _store.create("Grab Rides", aliases=["grab ride"])
    assert resolve_payee_id("GRAB RIDEshare fraud co", create=False) is None
    # ambiguity: two payees tying on the same-length prefix → no guess
    a = _store.create("Ambig A", aliases=["acme corp x1"])
    _store.create("Ambig B", aliases=["acme corp x2"])
    assert resolve_payee_id("ACME CORP X1", create=False) == a["id"]  # exact still wins


# ── on_category_deleted — recurrence fix: re-point/clear payee defaults ──────────
# categories.delete_category / rename_category(merge_into=...) don't call this yet (out of
# scope here — see the fix report for the exact one-line call each choke needs); these tests
# pin the payee-side function in isolation.


def test_on_category_deleted_clears_matching_defaults(reg):
    from backend.services import categories as categories_service

    cat = categories_service.create_category("Fleeting", "expense")
    pid = reg.create_payee("Fleeting Merchant", default_category=cat["id"])["id"]

    result = reg.on_category_deleted(cat["id"])

    assert result == {"repointed": 0, "cleared": 1}
    assert reg._store.find_by_id(pid)["default_categories"] == {}


def test_on_category_deleted_repoints_to_reassign_target(reg):
    from backend.services import categories as categories_service

    old = categories_service.create_category("Old Cat", "expense")
    new = categories_service.create_category("New Cat", "expense")
    pid = reg.create_payee("Repoint Merchant", default_category=old["id"])["id"]

    result = reg.on_category_deleted(old["id"], reassign_to=new["id"])

    assert result == {"repointed": 1, "cleared": 0}
    assert reg._store.find_by_id(pid)["default_categories"]["expense"] == new["id"]


def test_on_category_deleted_only_touches_the_matching_kind(reg):
    from backend.services import categories as categories_service

    exp_cat = categories_service.create_category("Exp Cat", "expense")
    inc_cat = categories_service.create_category("Inc Cat", "income")
    pid = reg.create_payee("Multi Kind Merchant")["id"]
    reg.set_default_categories(pid, {"expense": exp_cat["id"], "income": inc_cat["id"]})

    result = reg.on_category_deleted(exp_cat["id"])

    assert result == {"repointed": 0, "cleared": 1}
    stored = reg._store.find_by_id(pid)["default_categories"]
    assert stored == {"income": inc_cat["id"]}  # income slot untouched


def test_on_category_deleted_ignores_payees_that_dont_reference_it(reg):
    from backend.services import categories as categories_service

    cat = categories_service.create_category("Unrelated Cat", "expense")
    other = categories_service.create_category("Other Cat", "expense")
    pid = reg.create_payee("Bystander", default_category=other["id"])["id"]

    result = reg.on_category_deleted(cat["id"])

    assert result == {"repointed": 0, "cleared": 0}
    assert reg._store.find_by_id(pid)["default_categories"]["expense"] == other["id"]


def test_on_category_deleted_never_raises_on_blank_id(reg):
    assert reg.on_category_deleted("") == {"repointed": 0, "cleared": 0}


def test_on_category_deleted_reassign_to_self_or_reserved_falls_back_to_clear(reg):
    """A reassign target that is the id itself, or a structural/kind-name key, is never a
    usable destination — same defensive posture as ``normalize_default_categories``."""
    from backend.services import categories as categories_service

    cat = categories_service.create_category("Self Target", "expense")
    pid = reg.create_payee("Self Target Merchant", default_category=cat["id"])["id"]

    result = reg.on_category_deleted(cat["id"], reassign_to=cat["id"])
    assert result == {"repointed": 0, "cleared": 1}
    assert reg._store.find_by_id(pid)["default_categories"] == {}
