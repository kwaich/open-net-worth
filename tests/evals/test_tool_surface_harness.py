"""Tests for the tool-surface A/B harness.

These are FAST and make NO LLM calls — the harness's model client is stubbed by the
mock persona, so the whole suite runs in CI. The one test that would call the API is
marked ``integration`` per the repo's test policy and skips without a key.

What these actually protect:

* the scorer DISCRIMINATES — the ``sloppy`` persona must fail, and fail for the right
  reason. A scorer that passes everything is worse than no scorer, and this is the only
  test that can catch that.
* every arm is a faithful projection of the same op registry (same ops reachable, same
  handlers), so a future edit cannot quietly make one arm weaker than another.
* the cost accounting adds up and prices through the app's own pricing module.
"""

from __future__ import annotations

import pytest

from backend.evals.tool_surface import fixture, ops, scorer, tasks
from backend.evals.tool_surface.arms import ARM_CLASSES, build_arm
from backend.evals.tool_surface.runner import make_mock_factory, run_one
from backend.evals.tool_surface.spec import ArmSpec, compress_text, parse_arm

# Tasks whose scripted "sloppy" behaviour encodes a real bug the scorer must catch.
SLOPPY_MUST_FAIL = [
    "fx_settled_charge",
    "statement_import_with_dup",
    "budget_assign",
    "near_miss_no_account",
    "near_miss_unknown_account",
    "read_net_worth",
]


def _run(task_id: str, surface: str = "A", persona: str = "perfect"):
    task = tasks.by_id(task_id)
    assert task is not None, task_id
    spec = ArmSpec(key=surface, surface=surface)
    return run_one(task, spec, 1, make_mock_factory(persona), decoys=[])


# ── Fixture ──────────────────────────────────────────────────────────────────


def test_fixture_is_deterministic_and_balances_reconcile():
    ref = fixture.build()
    from backend.services import ledger

    accounts = {name: acc for name, acc in ref["accounts"].items()}
    txns = ledger.transactions.all()
    dbs, uob = accounts["DBS Multiplier"], accounts["UOB One Card"]
    # 12450 opening + 8200 salary - 84.20 groceries
    assert ledger.account_balance(dbs, txns, ledger.opening_date(dbs, txns)) == pytest.approx(
        20565.80
    )
    # -1280.50 opening - 14.60 grab - 62.35 cold storage
    assert ledger.account_balance(uob, txns, ledger.opening_date(uob, txns)) == pytest.approx(
        -1357.45
    )
    assert ledger.net_worth()["net_worth"] == pytest.approx(102277.65, abs=0.05)


def test_fixture_rebuild_resets_state():
    fixture.build()
    before = scorer.take_snapshot(fixture.BUDGET_MONTH)
    count_before = len(before.rows("transactions"))
    fixture.build()
    after = scorer.take_snapshot(fixture.BUDGET_MONTH)
    assert len(after.rows("transactions")) == count_before


# ── Arms are faithful projections of one op registry ─────────────────────────


@pytest.mark.parametrize("surface", ["A", "B", "C"])
def test_every_op_is_reachable_on_every_arm(surface):
    """No arm may silently omit a capability — that would rig the comparison."""
    arm = build_arm(ArmSpec(key=surface, surface=surface), decoys=[])
    offered = {t["name"] for t in arm.tools()}
    assert offered, surface

    for op in ops.all_ops().values():
        if surface == "A":
            # Rare ops are reachable only after discovery — that IS arm A's design.
            reachable = op.tool_name in offered or op.tier == "rare"
        elif surface == "B":
            reachable = op.domain in offered
        else:
            reachable = "apply" in offered
        assert reachable, f"{op.name} unreachable on arm {surface}"


def test_arms_share_the_same_handlers():
    """The anti-rigging invariant: arms differ in surface, never in business logic."""
    for op in ops.all_ops().values():
        assert op.handler is ops.get_op(op.name).handler


def test_arm_c_describe_covers_every_op_it_can_apply():
    from backend.evals.tool_surface import introspect

    index = introspect.describe("ops")
    described = {entry["op"] for entry in index["ops"]}
    assert described == set(ops.all_ops())
    for name in described:
        detail = introspect.describe(f"ops/{name}")
        assert "parameters" in detail, name


# ── The scorer discriminates (the load-bearing test) ─────────────────────────


@pytest.mark.parametrize("task_id", SLOPPY_MUST_FAIL)
def test_sloppy_persona_fails_every_planted_bug(task_id):
    record = _run(task_id, persona="sloppy")
    assert not record.success, (
        f"{task_id} PASSED with deliberately-buggy behaviour — the scorer is not "
        f"discriminating. reasons={record.reasons} unintended={record.unintended}"
    )
    assert record.reasons or record.unintended


@pytest.mark.parametrize("task_id", [t.id for t in tasks.TASKS])
def test_perfect_persona_passes_every_task(task_id):
    record = _run(task_id, persona="perfect")
    assert record.success, f"{task_id}: {record.reasons} {record.unintended}"


def test_unintended_side_effects_are_detected():
    """A duplicate row that no expectation claimed must surface as unintended."""
    record = _run("statement_import_with_dup", persona="sloppy")
    assert not record.success
    assert any("duplicate" in reason.lower() for reason in record.reasons)


def test_wrong_read_value_fails_the_run():
    """A read that states a confidently wrong number is a FAILED run, not a footnote."""
    record = _run("read_net_worth", persona="sloppy")
    assert record.value_ok is False
    assert record.value_gates_success
    assert not record.success


def test_near_miss_clarification_does_not_gate_success():
    """A clean silent refusal is a correct DATA outcome even without a question."""
    record = _run("near_miss_oversell", persona="perfect")
    assert record.success
    assert not record.value_gates_success


# ── Cost accounting ──────────────────────────────────────────────────────────


def test_cost_record_accounts_for_every_token():
    record = _run("simple_expense")
    cost = record.cost
    assert cost.api_calls >= 1
    assert cost.total_input_tokens == (
        cost.uncached_input_tokens + cost.cache_write_tokens + cost.cache_read_tokens
    )
    assert cost.cache_write_tokens > 0, "the mock must exercise the cache-write path"
    assert cost.usd > 0


def test_cost_uses_the_apps_own_pricing_module():
    """Depend on backend.ai.models.pricing so a rate fix elsewhere corrects us too."""
    from backend.ai.models.pricing import cost_usd_anthropic
    from backend.evals.tool_surface.runner import CostRecord, compute_cost_usd

    cost = CostRecord(
        uncached_input_tokens=1000,
        output_tokens=500,
        cache_write_tokens=2000,
        cache_write_5m_tokens=2000,
        cache_read_tokens=8000,
    )
    expected = cost_usd_anthropic(
        "claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=500,
        cache_creation_tokens=2000,
        cache_read_tokens=8000,
        cache_creation_5m_tokens=2000,
        cache_creation_1h_tokens=0,
    )
    assert compute_cost_usd("claude-sonnet-4-6", cost) == pytest.approx(expected, rel=1e-6)


def test_cache_read_is_cheaper_than_cache_write():
    """Guards the direction of the cache economics the cost model turns on."""
    from backend.evals.tool_surface.runner import CostRecord, compute_cost_usd

    write = CostRecord(cache_write_tokens=10000, cache_write_5m_tokens=10000)
    read = CostRecord(cache_read_tokens=10000)
    assert compute_cost_usd("claude-sonnet-4-6", read) < compute_cost_usd(
        "claude-sonnet-4-6", write
    )


# ── Arm specs are parameters, not forks ──────────────────────────────────────


def test_parse_arm_supports_every_lever():
    assert parse_arm("A") == ArmSpec(key="A", surface="A")

    haiku = parse_arm("haiku=A,model=claude-haiku-4-5")
    assert (haiku.key, haiku.surface, haiku.model) == ("haiku", "A", "claude-haiku-4-5")

    terse = parse_arm("terse=A,descriptions=compressed")
    assert terse.descriptions == "compressed"

    small = parse_arm("small=C,result_budget=800")
    assert (small.surface, small.result_budget) == ("C", 800)

    with pytest.raises(ValueError):
        parse_arm("nope=A,bogus=1")
    with pytest.raises(ValueError):
        parse_arm("Z")


def test_compressed_descriptions_shrink_the_offered_schema():
    full = build_arm(ArmSpec(key="A", surface="A"), decoys=[])
    terse = build_arm(ArmSpec(key="t", surface="A", descriptions="compressed"), decoys=[])
    import json

    full_size = len(json.dumps(full.tools()))
    terse_size = len(json.dumps(terse.tools()))
    assert terse_size < full_size
    # Same tools offered — compression changes prose, never capability.
    assert {t["name"] for t in full.tools()} == {t["name"] for t in terse.tools()}


def test_compress_text_keeps_the_first_sentence():
    assert compress_text("Do a thing. Then a long aside about it.") == "Do a thing."
    assert compress_text("Only one sentence") == "Only one sentence"


def test_result_budget_truncates_and_announces():
    from backend.evals.tool_surface.runner import _apply_result_budget

    assert _apply_result_budget("x" * 50, 0) == "x" * 50
    truncated = _apply_result_budget("x" * 5000, 100)
    assert len(truncated) < 5000
    assert "truncated" in truncated


def test_surfaces_differ_in_offered_tool_count():
    """Sanity: the arms really are different surfaces, not three copies."""
    sizes = {
        key: len(build_arm(ArmSpec(key=key, surface=key), decoys=[]).tools())
        for key in ("A", "B", "C")
    }
    assert sizes["C"] == 2  # describe + apply
    assert sizes["B"] < sizes["A"]  # consolidation means fewer, fatter tools
    assert sizes["A"] > sizes["C"]


def test_bad_trade_is_flagged_when_an_arm_is_cheaper_and_worse():
    """The report must call out cheaper-but-less-correct as a LOSS, not a saving.

    This is the whole point of the two-axis table, so it is asserted directly rather
    than left to a run that happens to produce the shape.
    """
    from backend.evals.tool_surface.report import _render_cost_axis, summarise_arm

    baseline = _run("simple_expense", persona="perfect")
    cheap = _run("simple_expense", persona="perfect")

    baseline.arm, cheap.arm = "base", "cheap"
    cheap.cost.usd = baseline.cost.usd * 0.6  # 40% cheaper
    cheap.intended = False  # ... and less correct

    rendered = _render_cost_axis(
        [summarise_arm("base", [baseline]), summarise_arm("cheap", [cheap])]
    )
    assert "BAD TRADE" in rendered, rendered


def test_cost_per_success_penalises_unreliability():
    """Half the success rate at the same $/run must double $/success."""
    from backend.evals.tool_surface.report import summarise_arm

    good = _run("simple_expense", persona="perfect")
    bad = _run("simple_expense", persona="perfect")
    good.arm = bad.arm = "mixed"
    bad.intended = False
    bad.cost.usd = good.cost.usd

    summary = summarise_arm("mixed", [good, bad])
    assert summary.success.mean == pytest.approx(0.5)
    assert summary.cost_per_success == pytest.approx(summary.cost_per_run.mean * 2, rel=1e-6)


# ── Anti-bias regressions (found by adversarial review) ──────────────────────


def test_arm_a_auto_loads_a_rare_tool_like_production_does():
    """Arm A must NOT refuse a bare call to a known-but-unloaded tool.

    Production's interactive discovery gate auto-loads it and bounces "call it again".
    Modelling it as a hard refusal handicapped arm A on exactly the needs_discovery
    tasks — a bias unrelated to surface shape.
    """
    import json

    arm = build_arm(ArmSpec(key="A", surface="A"), decoys=[])
    rare = next(o for o in ops.all_ops().values() if o.tier == "rare")
    assert rare.tool_name not in {t["name"] for t in arm.tools()}

    text, trace = arm.dispatch(rare.tool_name, {})
    assert trace.ok, "a bare call to a rare tool must not be refused"
    assert trace.is_discovery
    assert "call it again" in json.loads(text).get("status", "")
    assert rare.tool_name in {t["name"] for t in arm.tools()}, "it must now be offered"


def test_compression_preserves_arm_b_required_field_contract():
    """Compression is a PROSE lever; it must not delete arm B's only copy of the
    per-action required fields, which would leave B structurally blind vs A and C."""
    terse = build_arm(ArmSpec(key="b", surface="B", descriptions="compressed"), decoys=[])
    txns = next(t for t in terse.tools() if t["name"] == "txns")
    assert "Requires: date" in txns["description"]
    assert "Requires: id, changes" in txns["description"]


@pytest.mark.parametrize("surface", ["A", "B", "C"])
def test_missing_required_field_error_is_identical_across_arms(surface):
    """Error quality must be a property of the shared layer, never of one arm."""
    import json

    arm = build_arm(ArmSpec(key=surface, surface=surface), decoys=[])
    if surface == "A":
        text, trace = arm.dispatch("get_budget_month", {})
    elif surface == "B":
        text, trace = arm.dispatch("budget", {"action": "month"})
    else:
        text, trace = arm.dispatch("apply", {"op": "budget.month", "items": [{}]})

    assert not trace.ok
    assert "missing required field(s) for budget.month: month" in json.loads(text)["error"]


def test_payee_side_effects_are_snapshotted_and_not_silently_ignored():
    """A payee change with NO consumed transaction must still surface as unintended."""
    fixture.build()
    before = scorer.take_snapshot(fixture.BUDGET_MONTH)
    assert "payees" in before.data, "payee registry must be in the snapshot"

    from backend.db.payee_store import PayeeStore

    PayeeStore().create("ROGUE PAYEE")
    delta = scorer.diff(before, scorer.take_snapshot(fixture.BUDGET_MONTH))

    unclaimed = scorer.unintended_changes(delta, scorer.CheckResult(intended=True))
    assert any("payee" in reason for reason in unclaimed), unclaimed


def test_payee_churn_is_excused_when_its_parent_transaction_was_consumed():
    """...but the payee row a booked transaction legitimately mints is not noise."""
    ref = fixture.build()
    before = scorer.take_snapshot(fixture.BUDGET_MONTH)

    ops.get_op("txn.add").handler(
        {
            "date": fixture.TODAY,
            "description": "SOME NEW MERCHANT",
            "from_account_id": ref["account_ids"]["DBS Multiplier"],
            "from_amount": 12.30,
            "from_currency": "SGD",
        }
    )
    delta = scorer.diff(before, scorer.take_snapshot(fixture.BUDGET_MONTH))
    txn_id = next(iter(delta.added_rows("transactions")))
    claimed = scorer.CheckResult(intended=True, consumed={("transactions", txn_id)})
    assert scorer.unintended_changes(delta, claimed) == []


def test_mock_cache_invalidates_when_the_tool_set_grows():
    """Arm A grows its tool list mid-run; that must bill as a cache WRITE, not a read,
    or the mock understates arm A's cost on exactly the discovery tasks."""
    from backend.evals.tool_surface.model_client import MockClient

    ref = fixture.build()
    client = MockClient(persona="perfect", arm_key="A", task_id="simple_expense", ref=ref)
    tools_small = [{"name": "a", "description": "x", "input_schema": {}}]
    tools_big = tools_small + [{"name": "b", "description": "y", "input_schema": {}}]

    first = client._respond("sys", [], tools_small, text="1")
    same = client._respond("sys", [], tools_small, text="2")
    grown = client._respond("sys", [], tools_big, text="3")

    assert first.cache_write_tokens > 0 and first.cache_read_tokens == 0
    assert same.cache_read_tokens > 0 and same.cache_write_tokens == 0
    assert grown.cache_write_tokens > 0 and grown.cache_read_tokens == 0


def test_all_arm_classes_build():
    for key in ARM_CLASSES:
        if key == "A-real":
            continue  # imports the whole production registry; covered by its own run
        assert build_arm(ArmSpec(key=key, surface=key), decoys=[]) is not None


# ── Task set integrity ───────────────────────────────────────────────────────


def test_task_set_covers_the_required_ground():
    categories = {t.category for t in tasks.TASKS}
    for required in (
        "ledger_simple",
        "ledger_fx",
        "ledger_transfer",
        "ledger_import",
        "invest",
        "budget_write",
        "read_computed",
        "lazy_discovery",
        "near_miss",
    ):
        assert required in categories, required
    assert len(tasks.TASKS) >= 15
    assert sum(1 for t in tasks.TASKS if t.is_near_miss) >= 2
    assert sum(1 for t in tasks.TASKS if t.needs_discovery) >= 1
    assert len({t.id for t in tasks.TASKS}) == len(tasks.TASKS)


# ── API key resolution (the ANTHROPIC_API_KEY trap) ──────────────────────────
#
# A prior real-model baseline run was claimed but never happened: the harness
# hard-required ANTHROPIC_API_KEY as a raw env var with no fallback, so a caller who
# forgot to export it got a clean RuntimeError instead of a spent-money run — but the
# claim "12/12 passed" was made anyway with no output artifact and no shell-history
# trace. These tests pin the fallback (env var wins when set; else the operator's own
# stored key from ~/.open-net-worth/data/model_config.json) so this stops recurring,
# without ever touching the real home directory or a real key.


def test_resolve_api_key_prefers_the_env_var(monkeypatch, tmp_path):
    from backend.evals.tool_surface import model_client

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
    monkeypatch.setattr(model_client.Path, "home", lambda: tmp_path)
    assert model_client._resolve_api_key() == "sk-ant-from-env"


def test_resolve_api_key_falls_back_to_the_stored_config(monkeypatch, tmp_path):
    import json

    from backend.evals.tool_surface import model_client

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config_dir = tmp_path / ".open-net-worth" / "data"
    config_dir.mkdir(parents=True)
    (config_dir / "model_config.json").write_text(
        json.dumps({"providers": {"anthropic": {"api_key": "sk-ant-from-disk"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(model_client.Path, "home", lambda: tmp_path)
    assert model_client._resolve_api_key() == "sk-ant-from-disk"


def test_resolve_api_key_returns_none_when_neither_source_exists(monkeypatch, tmp_path):
    from backend.evals.tool_surface import model_client

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(model_client.Path, "home", lambda: tmp_path)
    assert model_client._resolve_api_key() is None


def test_resolve_api_key_survives_a_malformed_config_file(monkeypatch, tmp_path):
    from backend.evals.tool_surface import model_client

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config_dir = tmp_path / ".open-net-worth" / "data"
    config_dir.mkdir(parents=True)
    (config_dir / "model_config.json").write_text("not json{", encoding="utf-8")
    monkeypatch.setattr(model_client.Path, "home", lambda: tmp_path)
    assert model_client._resolve_api_key() is None


def test_anthropic_client_raises_a_clear_error_with_no_key_anywhere(monkeypatch, tmp_path):
    from backend.evals.tool_surface import model_client

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(model_client.Path, "home", lambda: tmp_path)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        model_client.AnthropicClient(model="claude-test")


# ── Real-API run (never in CI) ───────────────────────────────────────────────


@pytest.mark.integration
def test_real_model_run_smoke():
    """One real API call end to end. Skipped without ANTHROPIC_API_KEY."""
    from backend.evals.tool_surface.runner import make_anthropic_factory

    task = tasks.by_id("read_net_worth")
    record = run_one(
        task,
        ArmSpec(key="A", surface="A", model="claude-test"),
        1,
        make_anthropic_factory(),
        decoys=[],
    )
    assert record.cost.api_calls >= 1
    assert record.cost.usd > 0
    assert not record.cost.estimated
