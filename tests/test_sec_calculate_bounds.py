"""scan#3 — `calculate` cannot be used to exhaust CPU or memory."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from backend.ai.tools import execute_tool


def call(**kwargs):
    text, _ = asyncio.run(execute_tool("calculate", kwargs))
    return json.loads(text)


@pytest.mark.parametrize(
    "expr",
    [
        "9**9**9**9",
        "2**100000",
        "10**10**10",
        "9.0**100000",
        "[0]*10**10",
        "(1,)*10**10",
        "'a'*10**10",
        "[1]+[2]",
        "round(10**50, -10**9)",
        "(-8.0)**0.5",
        "1+" * 300 + "1",
        "(" * 300 + "1" + ")" * 300,
        "round",
        "__import__('os')",
    ],
)
def test_hostile_expressions_are_refused_quickly(expr):
    started = time.monotonic()
    out = call(expression=expr)
    assert "error" in out, out
    assert time.monotonic() - started < 2


def test_normal_arithmetic_still_works():
    rs = call(
        expressions=[
            "round((543.21 + 189.4) * 1.287, 2)",
            "1.07**30",
            "2**64",
            "sum([1, 2, 3]) / 3",
            "-5 % 3",
            "max(1, 2, 3)",
        ]
    )["results"]
    assert rs[0]["result"] == pytest.approx(942.87, abs=0.01)
    assert rs[1]["result"] == pytest.approx(7.612255, rel=1e-5)
    assert rs[2]["result"] == 2**64
    assert [r["result"] for r in rs[3:]] == [2.0, 1, 3]
