---
name: write-test
description: pytest + characterization-test conventions for this repo. Use when adding or updating backend tests — store CRUD tests, service unit tests, endpoint smoke tests, AI-tool tests, or regression tests for a bug fix.
---

# Write a test

Backend tests live in flat `tests/` (config in `pyproject.toml`, async + the `integration` marker wired in `tests/conftest.py`).

## The two gates

```bash
# FAST gate — what CI blocks on and what you run constantly:
.venv/bin/python3 -m pytest -q -m "not integration"
# Full suite (LLM/market-API tests; needs ANTHROPIC_API_KEY; ~minutes):
.venv/bin/python3 -m pytest -q
```

`make test` runs the fast gate. Tests that hit an LLM or a live market API are marked `@pytest.mark.integration` and **skip automatically** when `ANTHROPIC_API_KEY` is unset.

## Hard rules

- **Unit tests never touch the network and never touch live data.** Isolate to `tmp_path` by monkeypatching the relevant store **path constant** (defined in `backend/paths.py`). Never run a test that reads/writes `backend/data/*.json`.
- **One behavior per test**, Arrange-Act-Assert, descriptive name.
- **Every bug fix ships a regression test** that fails before the fix.

## What must exist (coverage policy)

- every **store** → a CRUD/query test — pattern `tests/test_positions_stores.py`, `tests/test_commitments_store.py`
- every **service** method → a unit test (deterministic; money math especially — `tests/test_char_ledger_math.py`, `tests/test_fx_pivot.py`)
- every **endpoint** → a `TestClient` status+shape smoke test — pattern `tests/test_commitments_api.py`
- every **AI tool** → a behavioral test via the dispatcher (below)

## Isolating a store/path

```python
def test_widget_crud(tmp_path, monkeypatch):
    import backend.db.widget_store as mod
    monkeypatch.setattr(mod, "WIDGETS_JSON", tmp_path / "widgets.json")
    store = mod.WidgetStore()
    store.create({"name": "A"})
    assert len(store.all()) == 1
```

## Testing an AI tool (async dispatch)

`execute_tool` is async; drive it with `asyncio.run`. Monkeypatch call-time names on the `backend.ai.tools` package namespace (the registry re-exports them for exactly this).

```python
import asyncio, json
from backend.ai.tools import execute_tool

def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed

def test_my_tool(monkeypatch):
    import backend.ai.tools as tools
    monkeypatch.setattr(tools, "read_rows", lambda: [{"ticker": "MSFT"}])
    res, proposed = call("get_portfolio")
    assert proposed is None
```

## Characterization tests (the Phase-1 regression net)

`tests/test_char_tool_dispatch.py` (dispatch contract) and `tests/test_char_ledger_math.py` (ledger math) pin **current behavior, warts and all** — they are a safety net for the boundary reorg, not a spec. When you intentionally change behavior they protect (e.g. adding a tool changes the frozen name set), update them **deliberately** and note why; never loosen one just to make a refactor pass. New characterization tests assert structural invariants: the frozen tool-name **set**, the `proposed_change` discipline (non-None only for `propose_position_*`), the unknown-tool sentinel, and recompute-from-native-leg money invariants.

## Verify

`make test` green before you commit. If you touched lint-relevant code too, run `make check`.
