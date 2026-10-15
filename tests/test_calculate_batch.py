"""calculate tool: batch expressions in one call (cost: avoid N round-trips)."""

import asyncio
import json


def call(**kwargs):
    from backend.ai.tools import execute_tool

    text, _ = asyncio.run(execute_tool("calculate", kwargs))
    return json.loads(text)


def test_single_expression_backward_compat():
    out = call(expression="1441.45 - 441.45")
    assert out["result"] == 1000.00
    assert out["expression"] == "1441.45 - 441.45"


def test_batch_returns_ordered_results():
    out = call(expressions=["1+1", "round(10/3, 2)", "max(5, 9)"])
    rs = out["results"]
    assert [r["result"] for r in rs] == [2, 3.33, 9]


def test_batch_item_error_does_not_kill_batch():
    out = call(expressions=["1+1", "__import__('os')", "2*3"])
    rs = out["results"]
    assert rs[0]["result"] == 2
    assert "error" in rs[1]
    assert rs[2]["result"] == 6


def test_no_input_is_error():
    assert "error" in call()


def test_disallowed_expression_still_blocked():
    out = call(expression="open('/etc/passwd')")
    assert "error" in out
