"""DB-backed verb-validation tests for the GROUP-based 50/30/20 engine (owner design,
2026-09-26; ADR ``docs/adr/2026-09-25-fifty-thirty-twenty-is-envelope-based-with-caps.md``
and its 2026-09-26 amendment: the method's groups are the book's own Zero-Based groups).

Every verb here validates against REAL envelope/category/group data
(``proportional_config._row_envelopes``/``_split_groups``/``categories_service``), so —
unlike ``tests/test_budget_engine_proportional.py``'s pure ``shared``-dict tests — this
file needs ``db_isolated`` + the first-boot envelope migration, mirroring
``tests/test_planning_engines_api.py``'s own isolation strategy.
"""

from __future__ import annotations

import pytest

from backend.core.errors import ValidationError
from backend.db.category_store import CategoryStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import planning as planning_service
from backend.services.budget_engines.proportional import PROPORTIONAL_ENGINE

_FOOD = "cat_13095ba70000"  # Food & Dining (built-in, stable id)
_TRANSPORT = "cat_db5642ab0000"  # Transport
_LIFESTYLE = "cat_504bed6e0000"  # Lifestyle


@pytest.fixture
def seeded(db_isolated):
    """Real category rows + one 1:1 envelope per builtin expense category
    (``ensure_envelopes_migrated``) — no FX/history machinery is needed any more: unlike
    the old bucket-seeding heuristic, ``configure`` is now pure target validation."""
    CategoryStore().bulk_insert(BUILTIN_CATEGORIES)
    envelopes_service.ensure_envelopes_migrated()
    return None


def _env(category_id: str) -> str:
    return envelopes_service.env_of()[category_id]


def _group(name: str) -> str:
    """A real Zero-Based group — the universe ``set_group_targets``/``set_group_excluded``
    validate against (``proportional_config._split_groups``)."""
    result = planning_service.apply_budget_change({"type": "group_create", "name": name})
    return (result.get("group") or result)["id"]


def _apply(verb_id: str, params: dict, own_state: dict | None = None) -> dict:
    return PROPORTIONAL_ENGINE.apply_verb(verb_id, params, own_state or {})


# ── set_group_targets ────────────────────────────────────────────────────────────────


def test_set_group_targets_valid(seeded):
    bills = _group("Bills")
    assert _apply("set_group_targets", {"targets": {bills: 55}}) == {"group_targets": {bills: 55.0}}


def test_set_group_targets_rejects_negative(seeded):
    bills = _group("Bills")
    with pytest.raises(ValidationError):
        _apply("set_group_targets", {"targets": {bills: -1}})


def test_set_group_targets_rejects_over_100(seeded):
    bills = _group("Bills")
    with pytest.raises(ValidationError):
        _apply("set_group_targets", {"targets": {bills: 150}})


def test_set_group_targets_rejects_the_total_across_groups_over_100(seeded):
    bills, fun = _group("Bills"), _group("Fun")
    with pytest.raises(ValidationError, match="100"):
        _apply("set_group_targets", {"targets": {bills: 80, fun: 30}})


def test_set_group_targets_null_or_zero_clears_the_target(seeded):
    bills = _group("Bills")
    cleared_by_null = PROPORTIONAL_ENGINE.apply_verb(
        "set_group_targets", {"targets": {bills: None}}, {"group_targets": {bills: 40.0}}
    )
    assert cleared_by_null == {"group_targets": {}}
    cleared_by_zero = PROPORTIONAL_ENGINE.apply_verb(
        "set_group_targets", {"targets": {bills: 0}}, {"group_targets": {bills: 40.0}}
    )
    assert cleared_by_zero == {"group_targets": {}}


def test_set_group_targets_merges_onto_existing_targets_for_other_groups(seeded):
    bills, fun = _group("Bills"), _group("Fun")
    result = PROPORTIONAL_ENGINE.apply_verb(
        "set_group_targets", {"targets": {fun: 20}}, {"group_targets": {bills: 50.0}}
    )
    assert result == {"group_targets": {bills: 50.0, fun: 20.0}}


def test_set_group_targets_rejects_unknown_group_naming_the_real_tool(seeded):
    with pytest.raises(ValidationError) as exc:
        _apply("set_group_targets", {"targets": {"grp_bogus": 10}})
    assert "list_envelopes" in str(exc.value)


def test_set_group_targets_requires_a_targets_dict(seeded):
    with pytest.raises(ValidationError):
        _apply("set_group_targets", {})


def test_set_group_targets_accepts_an_empty_owner_group_and_no_group_is_income(seeded):
    """Every envelope group can take a share, an empty one included; creating an income
    category makes no group at all (no group is "income" any more)."""
    names_before = {g["name"] for g in planning_service.list_groups()}
    categories_service.create_category("Salary", kind="income")
    groups = planning_service.list_groups()
    assert {g["name"] for g in groups} == names_before
    assert all("income" not in g for g in groups)

    new_group = planning_service.apply_budget_change({"type": "group_create", "name": "Fun"})[
        "group"
    ]["id"]
    assert _apply("set_group_targets", {"targets": {new_group: 10}}) == {
        "group_targets": {new_group: 10.0}
    }


# ── set_group_excluded ───────────────────────────────────────────────────────────────


def test_set_group_excluded_true_then_false(seeded):
    bills = _group("Bills")
    excluded = _apply("set_group_excluded", {"group_id": bills, "excluded": True})
    assert excluded == {"excluded_groups": [bills]}
    included_again = PROPORTIONAL_ENGINE.apply_verb(
        "set_group_excluded", {"group_id": bills, "excluded": False}, {"excluded_groups": [bills]}
    )
    assert included_again == {"excluded_groups": []}


def test_set_group_excluded_rejects_unknown_group(seeded):
    with pytest.raises(ValidationError):
        _apply("set_group_excluded", {"group_id": "grp_bogus", "excluded": True})


def test_set_group_excluded_requires_a_boolean(seeded):
    bills = _group("Bills")
    with pytest.raises(ValidationError):
        _apply("set_group_excluded", {"group_id": bills, "excluded": "yes"})


def test_set_group_excluded_carries_the_derived_legacy_list_forward(seeded):
    """The FIRST toggle stores the WHOLE shown list — including whatever the legacy
    per-envelope 'skip' buckets derive (``proportional.py``'s ``_shown_excluded``) — not
    just the one group this call names, so that earlier choice is never silently lost."""
    food_env = _env(_FOOD)
    bills = _group("Bills")
    fun = _group("Fun")
    planning_service.apply_budget_change(
        {"type": "move_category", "category": food_env, "group_id": bills}
    )
    # Every envelope in `bills` (just Food) was bucketed "skip" under the legacy model ->
    # `bills` is derived-excluded the first time excluded_groups is ever read.
    result = PROPORTIONAL_ENGINE.apply_verb(
        "set_group_excluded", {"group_id": fun, "excluded": True}, {"buckets": {food_env: "skip"}}
    )
    assert set(result["excluded_groups"]) == {bills, fun}


# ── configure ────────────────────────────────────────────────────────────────────────


def test_configure_with_no_group_targets_just_marks_the_method_set_up(seeded):
    """No heuristic seeding any more — an empty call simply persists (an empty)
    group_targets, which alone is enough for `is_configured` to read true forever after."""
    assert _apply("configure", {}) == {"group_targets": {}}


def test_configure_validates_and_saves_group_targets(seeded):
    bills = _group("Bills")
    result = _apply("configure", {"group_targets": {bills: 60}})
    assert result == {"group_targets": {bills: 60.0}}


def test_configure_group_targets_validation_still_applies(seeded):
    bills, fun = _group("Bills"), _group("Fun")
    with pytest.raises(ValidationError, match="100"):
        _apply("configure", {"group_targets": {bills: 90, fun: 40}})


def test_configure_merges_onto_existing_group_targets(seeded):
    """Unlike the old bucket-seeding heuristic, configure never touches envelopes at all
    any more — it is pure target validation, identical in shape to set_group_targets."""
    bills, fun = _group("Bills"), _group("Fun")
    result = PROPORTIONAL_ENGINE.apply_verb(
        "configure", {"group_targets": {fun: 20}}, {"group_targets": {bills: 50.0}}
    )
    assert result == {"group_targets": {bills: 50.0, fun: 20.0}}


# ── set_cap ──────────────────────────────────────────────────────────────────────────
# Cap months are 2099-xx on purpose: a cap before the CURRENT calendar month is refused, so a
# real month hard-coded here goes red the day the calendar passes it.


def test_set_cap_valid(seeded):
    food_env = _env(_FOOD)
    result = _apply("set_cap", {"envelope_id": food_env, "month": "2099-09", "amount": 650})
    assert result == {"caps": {food_env: [["2099-09", 650.0]]}}


def test_set_cap_refuses_a_month_before_the_current_calendar_month(seeded):
    with pytest.raises(ValidationError, match="past months are history"):
        _apply("set_cap", {"envelope_id": _env(_FOOD), "month": "2020-01", "amount": 100})


def test_set_cap_rejects_unknown_envelope(seeded):
    with pytest.raises(ValidationError):
        _apply("set_cap", {"envelope_id": "env_bogus", "month": "2099-09", "amount": 100})


def test_set_cap_rejects_negative_amount(seeded):
    with pytest.raises(ValidationError):
        _apply("set_cap", {"envelope_id": _env(_FOOD), "month": "2099-09", "amount": -5})


def test_set_cap_rejects_malformed_month(seeded):
    with pytest.raises(ValidationError):
        _apply("set_cap", {"envelope_id": _env(_FOOD), "month": "September", "amount": 100})


def test_set_cap_applies_from_month_onward_replacing_later_entries(seeded):
    env_id = _env(_FOOD)
    current_caps = {env_id: [["2099-09", 500.0], ["2099-11", 700.0]]}
    result = PROPORTIONAL_ENGINE.apply_verb(
        "set_cap",
        {"envelope_id": env_id, "month": "2099-10", "amount": 600},
        {"caps": current_caps},
    )
    assert result == {"caps": {env_id: [["2099-09", 500.0], ["2099-10", 600.0]]}}


# ── set_caps (bulk) ──────────────────────────────────────────────────────────────────


def test_set_caps_bulk_sets_several_envelopes_in_one_call(seeded):
    food_env, transport_env = _env(_FOOD), _env(_TRANSPORT)
    result = _apply("set_caps", {"month": "2099-09", "caps": {food_env: 500, transport_env: 200}})
    assert result == {"caps": {food_env: [["2099-09", 500.0]], transport_env: [["2099-09", 200.0]]}}


def test_set_caps_rejects_the_whole_batch_if_one_envelope_is_unknown(seeded):
    with pytest.raises(ValidationError):
        _apply("set_caps", {"month": "2099-09", "caps": {_env(_FOOD): 500, "env_bogus": 200}})


def test_set_caps_refuses_a_past_month(seeded):
    with pytest.raises(ValidationError, match="past months are history"):
        _apply("set_caps", {"month": "2020-01", "caps": {_env(_FOOD): 500}})


def test_set_caps_rejects_empty_caps_object(seeded):
    with pytest.raises(ValidationError):
        _apply("set_caps", {"month": "2099-09", "caps": {}})


# ── copy_targets_to_caps ─────────────────────────────────────────────────────────────


def test_copy_targets_to_caps_reads_monthly_target_verbatim(seeded):
    food_env = _env(_FOOD)
    planning_service.write_category_targets({food_env: {"type": "monthly", "amount": 650.0}})
    result = _apply("copy_targets_to_caps", {"month": "2099-09"})
    assert result["applied"] == 1
    assert result["caps"][food_env] == [["2099-09", 650.0]]


def test_copy_targets_to_caps_converts_yearly_to_one_twelfth(seeded):
    food_env = _env(_FOOD)
    planning_service.write_category_targets({food_env: {"type": "yearly", "amount": 1200.0}})
    result = _apply("copy_targets_to_caps", {"month": "2099-09"})
    assert result["caps"][food_env] == [["2099-09", 100.0]]


def test_copy_targets_to_caps_leaves_an_envelope_with_no_target_untouched(seeded):
    food_env, transport_env = _env(_FOOD), _env(_TRANSPORT)
    planning_service.write_category_targets({food_env: {"type": "monthly", "amount": 400.0}})
    result = _apply("copy_targets_to_caps", {"month": "2099-09"})
    assert result["applied"] == 1
    assert transport_env not in result["caps"]


def test_copy_targets_to_caps_refuses_a_past_month(seeded):
    with pytest.raises(ValidationError, match="past months are history"):
        _apply("copy_targets_to_caps", {"month": "2020-01"})


def test_copy_targets_to_caps_zero_applied_when_nothing_has_a_target(seeded):
    result = _apply("copy_targets_to_caps", {"month": "2099-09"})
    assert result == {"caps": {}, "applied": 0}


# ── set_income_categories ────────────────────────────────────────────────────────────


def test_set_income_categories_valid_list_resolves_to_ids(seeded):
    income_cat = categories_service.create_category("Salary", kind="income")
    result = _apply("set_income_categories", {"category_ids": [income_cat["id"]]})
    assert result == {"income_category_ids": [income_cat["id"]]}


def test_set_income_categories_resolves_by_label(seeded):
    income_cat = categories_service.create_category("Bonus", kind="income")
    result = _apply("set_income_categories", {"category_ids": ["Bonus"]})
    assert result == {"income_category_ids": [income_cat["id"]]}


def test_set_income_categories_null_clears_the_filter(seeded):
    assert _apply("set_income_categories", {"category_ids": None}) == {"income_category_ids": None}


def test_set_income_categories_rejects_unknown_category(seeded):
    with pytest.raises(ValidationError):
        _apply("set_income_categories", {"category_ids": ["not_a_real_cat"]})


def test_set_income_categories_rejects_a_non_income_category(seeded):
    with pytest.raises(ValidationError, match="not an income category"):
        _apply("set_income_categories", {"category_ids": [_FOOD]})  # Food & Dining is expense


# ── the shallow-merge composition, through the real service (not just proportional.py) ──


def test_service_apply_verb_shallow_merges_successive_verbs(seeded, monkeypatch):
    """Every earlier verb's own key survives every later, DIFFERENT-key verb call —
    proven across FOUR of the new verbs (configure/set_cap/set_income_categories/
    set_group_excluded), the same shallow-merge property the pre-redesign engine had."""
    from backend.services.budget_engines import service as engines_service

    monkeypatch.setattr(engines_service, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(engines_service, "base_currency", lambda: "SGD")
    food_env = _env(_FOOD)
    bills = _group("Bills")

    engines_service.apply_verb("proportional", "configure", {"group_targets": {bills: 50}})
    engines_service.apply_verb(
        "proportional", "set_cap", {"envelope_id": food_env, "month": "2099-09", "amount": 500}
    )
    engines_service.apply_verb("proportional", "set_income_categories", {"category_ids": None})
    final = engines_service.apply_verb(
        "proportional", "set_group_excluded", {"group_id": bills, "excluded": True}
    )

    assert final["group_targets"] == {bills: 50.0}
    assert final["caps"] == {food_env: [["2099-09", 500.0]]}
    assert final["income_category_ids"] is None
    assert final["excluded_groups"] == [bills]


# ── counted income categories follow a category delete / merge (2026-10-13 audit) ─────


def _counted_income_ids():
    from backend.db.budget_engine_state_store import BudgetEngineStateStore

    return (BudgetEngineStateStore().get("proportional") or {}).get("income_category_ids")


def _count_income(monkeypatch, *ids):
    from backend.services.budget_engines import service as engines_service

    monkeypatch.setattr(engines_service, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(engines_service, "base_currency", lambda: "SGD")
    engines_service.apply_verb("proportional", "set_income_categories", {"category_ids": list(ids)})


def test_deleting_a_counted_income_category_with_reassign_carries_it(seeded, monkeypatch):
    # Left alone, the list named a dead id — income re-keyed onto the target stopped counting.
    salary = categories_service.create_category("Salary2", "income")["id"]
    bonus = categories_service.create_category("Bonus2", "income")["id"]
    side = categories_service.create_category("Side gig", "income")["id"]
    _count_income(monkeypatch, bonus, salary)

    categories_service.delete_category(bonus, reassign=True, reassign_to=side)

    assert _counted_income_ids() == [side, salary]


def test_deleting_a_counted_income_category_without_a_target_drops_it(seeded, monkeypatch):
    salary = categories_service.create_category("Salary2", "income")["id"]
    bonus = categories_service.create_category("Bonus2", "income")["id"]
    _count_income(monkeypatch, salary, bonus)

    categories_service.delete_category(bonus)

    assert _counted_income_ids() == [salary]


def test_merging_a_counted_income_category_folds_it_into_the_target(seeded, monkeypatch):
    salary = categories_service.create_category("Salary2", "income")["id"]
    bonus = categories_service.create_category("Bonus2", "income")["id"]
    _count_income(monkeypatch, salary, bonus)

    categories_service.rename_category(bonus, merge_into=salary)

    assert _counted_income_ids() == [salary]
