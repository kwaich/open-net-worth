"""The no-money half of the CLI regression net: snapshot diffing, version tracking, the model
matrix and the boot-time self-check's gating. The live half is ``tests/cli_live`` (opt-in)."""

from __future__ import annotations

import pytest

from backend.ai import cli_probe
from backend.ai import cli_regression as reg
from backend.ai.cli_probe import ProbeResult
from backend.db.cli_probe_state_store import CliProbeStateStore
from backend.db.notification_log_store import NotificationLogStore
from tests.cli_live.matrix import pick_models

FEATURES_TEXT = """\
apps                                     stable             false
code_mode                                under development  false
code_mode_host                           stable             false
fast_mode                                stable             true
plugins                                  stable             true
tool_search_always_defer_mcp_tools       removed            true
"""


def _row(stage: str, enabled: bool) -> dict:
    return {"stage": stage, "enabled": enabled}


# ── feature flags ─────────────────────────────────────────────────────────────


def test_parse_features_handles_two_word_stages():
    parsed = reg.parse_features(FEATURES_TEXT)
    assert parsed["code_mode"] == _row("under development", False)
    assert parsed["plugins"] == _row("stable", True)
    assert len(parsed) == 6


def test_parse_features_skips_banners_and_blank_lines():
    assert reg.parse_features("WARNING: something\n\nnotafeature\n") == {}


def test_a_required_off_flag_that_is_on_or_gone_is_a_violation():
    features = reg.parse_features(FEATURES_TEXT)
    assert reg.required_off_violations(features) == []
    on = {**features, "apps": _row("stable", True)}
    assert "apps" in reg.required_off_violations(on)[0]
    renamed = {k: v for k, v in features.items() if k != "code_mode_host"}
    assert "renamed or removed" in reg.required_off_violations(renamed)[0]


def test_a_risky_feature_turning_on_fails_the_diff():
    base = reg.parse_features(FEATURES_TEXT)
    now = {**base, "code_mode": _row("stable", True)}
    diff = reg.diff_features(base, now)
    assert not diff.ok and "code_mode" in diff.failures[0]


def test_a_new_risky_feature_that_is_on_fails_but_one_that_is_off_is_a_note():
    base = reg.parse_features(FEATURES_TEXT)
    on = reg.diff_features(base, {**base, "connectors_v2": _row("stable", True)})
    off = reg.diff_features(base, {**base, "connectors_v2": _row("stable", False)})
    assert not on.ok
    assert off.ok and off.notes


def test_unrelated_and_downward_changes_are_notes_not_failures():
    base = reg.parse_features(FEATURES_TEXT)
    now = {**base, "fast_mode": _row("stable", False), "plugins": _row("stable", False)}
    del now["tool_search_always_defer_mcp_tools"]
    diff = reg.diff_features(base, now)
    assert diff.ok
    assert len(diff.notes) == 3


def test_an_unchanged_snapshot_is_clean():
    base = reg.parse_features(FEATURES_TEXT)
    diff = reg.diff_features(base, dict(base))
    assert diff.ok and not diff.notes


# ── claude init ───────────────────────────────────────────────────────────────


def test_claude_init_view_reduces_to_sorted_names():
    event = {
        "tools": [],
        "mcp_servers": [],
        "plugins": [{"name": "b", "path": "x"}, {"name": "a"}],
        "agents": ["Plan"],
        "skills": ["run"],
        "cwd": "/ignored",
    }
    assert reg.claude_init_view(event) == {
        "tools": [],
        "mcp_servers": [],
        "plugins": ["a", "b"],
        "agents": ["Plan"],
        "skills": ["run"],
    }


def test_a_tool_or_mcp_server_appearing_in_claude_init_fails_the_diff():
    base = {"tools": [], "mcp_servers": [], "plugins": [], "agents": [], "skills": []}
    assert not reg.diff_claude_init(base, {**base, "tools": ["Bash"]}).ok
    assert not reg.diff_claude_init(base, {**base, "mcp_servers": ["gmail"]}).ok
    drift = reg.diff_claude_init(base, {**base, "skills": ["new-skill"]})
    assert drift.ok and drift.notes


# ── versions ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("last", "installed", "changed"),
    [("", "0.160.0", True), ("0.160.0", "0.160.0", False), ("0.160.0", "0.161.0", True),
     ("0.160.0", "", False)],
)  # fmt: skip
def test_version_changed(last, installed, changed):
    assert reg.version_changed(last, installed) is changed


# ── model matrix ──────────────────────────────────────────────────────────────


def test_matrix_is_default_plus_the_newest_other():
    assert pick_models("gpt-5.5", ["gpt-6.1-sol", "gpt-6-sol", "gpt-5.5"]) == [
        "gpt-5.5",
        "gpt-6.1-sol",
    ]


def test_when_the_default_is_the_newest_the_second_is_the_previous_generation():
    listed = ["gpt-6.1-sol", "gpt-6-astra", "gpt-5.6-sol"]
    assert pick_models("gpt-6.1-sol", listed) == ["gpt-6.1-sol", "gpt-6-astra"]


def test_matrix_never_exceeds_two_and_tolerates_a_lone_default():
    assert len(pick_models("a-1", ["a-1", "a-2", "a-3"])) == 2
    assert pick_models("sonnet", []) == ["sonnet"]


# ── boot-time self-check ──────────────────────────────────────────────────────


class _Ctx:
    def __init__(self, transport="codex_cli", model="gpt-x"):
        self.transport, self.model, self.base_url, self.provider = transport, model, "", object()


@pytest.fixture
def wired(db_isolated, monkeypatch):
    """A configured Codex CLI at 0.161.0, with the probe replaced by a recorder."""
    calls: list[dict] = []
    outcome = {"result": ProbeResult("ok")}

    async def fake_probe(provider, model, **kw):
        calls.append({"model": model, **kw})
        return outcome["result"]

    monkeypatch.setattr("backend.ai.assistant.resolve_model_context", lambda *_: _Ctx())
    monkeypatch.setattr("backend.db.model_config_store.cli_binary_path", lambda *_: "/bin/codex")
    monkeypatch.setattr(cli_probe, "cli_version", lambda _binary: "0.161.0")
    monkeypatch.setattr(cli_probe, "probe_tool_reachability", fake_probe)
    return calls, outcome


def _notifications() -> list[dict]:
    return NotificationLogStore().all()


async def test_first_boot_probes_records_and_stays_quiet_when_tools_work(wired):
    calls, _ = wired
    await cli_probe.check_on_startup()
    assert len(calls) == 1 and calls[0]["round_trip"] is False
    assert CliProbeStateStore().last_probed_version("codex_cli") == "0.161.0"
    assert _notifications() == []


async def test_an_already_probed_version_is_not_probed_again(wired):
    calls, _ = wired
    CliProbeStateStore().record("codex_cli", "0.161.0", True)
    await cli_probe.check_on_startup()
    assert calls == []


async def test_a_new_version_is_probed_again(wired):
    calls, _ = wired
    CliProbeStateStore().record("codex_cli", "0.160.0", True)
    await cli_probe.check_on_startup()
    assert len(calls) == 1


async def test_broken_tools_notify_once_per_version(wired):
    calls, outcome = wired
    outcome["result"] = ProbeResult("unreachable", "model said no")
    await cli_probe.check_on_startup()
    await cli_probe.check_on_startup()
    assert len(calls) == 1
    (note,) = _notifications()
    assert note["title"] == "Codex 0.161.0 changed how tools work"
    assert "Lucius can't use your book's tools" in note["summary"]
    assert note["severity"] == "warning"
    assert CliProbeStateStore().last_probed_version("codex_cli") == "0.161.0"


async def test_an_inconclusive_probe_records_nothing_and_stays_quiet(wired):
    _, outcome = wired
    outcome["result"] = ProbeResult("error", "not logged in")
    await cli_probe.check_on_startup()
    assert CliProbeStateStore().last_probed_version("codex_cli") == ""
    assert _notifications() == []


async def test_a_non_cli_model_is_never_probed(wired, monkeypatch):
    calls, _ = wired
    monkeypatch.setattr("backend.ai.assistant.resolve_model_context", lambda *_: _Ctx("anthropic"))
    await cli_probe.check_on_startup()
    assert calls == []


async def test_the_self_check_never_raises(wired, monkeypatch):
    def boom(*_):
        raise RuntimeError("no config")

    monkeypatch.setattr("backend.ai.assistant.resolve_model_context", boom)
    await cli_probe.check_on_startup()


# ── probe protocol ────────────────────────────────────────────────────────────


class _ScriptedProvider:
    """Replies with each scripted event list in turn, recording the messages it was sent."""

    def __init__(self, *rounds):
        self.rounds, self.seen = list(rounds), []

    async def stream_chat(self, messages, tools, system, model, effort="medium"):
        self.seen.append(messages)
        for event in self.rounds.pop(0):
            yield event


def _call(name="get_secret_number"):
    return {"type": "tool_use", "id": "t1", "name": name, "input": {}}


async def test_probe_passes_when_the_nonce_round_trips():
    holder: dict = {}

    class Provider(_ScriptedProvider):
        async def stream_chat(self, messages, *a, **kw):
            if len(messages) == 3:
                nonce = messages[-1]["content"][0]["content"]
                holder["nonce"] = nonce
                self.rounds = [[{"type": "text_delta", "text": f"It is {nonce}."}]]
            async for e in super().stream_chat(messages, *a, **kw):
                yield e

    result = await cli_probe.probe_tool_reachability(Provider([_call()], []), "m")
    assert result.ok and holder["nonce"] in result.answer


async def test_probe_fails_when_the_model_declines_the_tool():
    provider = _ScriptedProvider([{"type": "text_delta", "text": "That tool isn't available."}])
    result = await cli_probe.probe_tool_reachability(provider, "m")
    assert result.status == "unreachable" and "isn't available" in result.detail


async def test_probe_fails_when_the_final_answer_loses_the_nonce():
    provider = _ScriptedProvider([_call()], [{"type": "text_delta", "text": "It is 42."}])
    assert (await cli_probe.probe_tool_reachability(provider, "m")).status == "unreachable"


async def test_a_provider_exception_is_an_error_not_a_verdict():
    class Broken:
        async def stream_chat(self, *a, **kw):
            raise RuntimeError("not logged in")
            yield

    assert (await cli_probe.probe_tool_reachability(Broken(), "m")).status == "error"


async def test_a_model_that_asks_for_the_tool_twice_is_answered_twice():
    class Provider(_ScriptedProvider):
        async def stream_chat(self, messages, *a, **kw):
            if len(messages) == 5:
                nonce = messages[-1]["content"][0]["content"]
                self.rounds = [[{"type": "text_delta", "text": f"It is {nonce}."}]]
            async for e in super().stream_chat(messages, *a, **kw):
                yield e

    result = await cli_probe.probe_tool_reachability(Provider([_call()], [_call()]), "m")
    assert result.ok


async def test_a_model_that_never_stops_asking_is_unreachable():
    provider = _ScriptedProvider([_call()], [_call()], [_call()])
    assert (await cli_probe.probe_tool_reachability(provider, "m")).status == "unreachable"
