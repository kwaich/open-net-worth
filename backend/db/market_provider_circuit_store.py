"""Cross-process circuit state for a fixed internal market-data provider.

Each circuit is ``state.circuit.<name>`` on the price-source connector row that owns it
(``coingecko*`` -> ``coingecko``, ``yahoo*`` -> ``yahoo_finance``); every write is ONE serialised
transaction (``price_source_store.update_state``).
"""

from __future__ import annotations

import math
from typing import Callable

from backend.db import price_source_store

_DEFAULT_BACKOFF_SECONDS = 30
_MAX_BACKOFF_SECONDS = 300
_DEFAULT_PROBE_LEASE_SECONDS = 30


def _float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _integer(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


class MarketProviderCircuitStore:
    """Persist one provider circuit in the owning price source's connector state."""

    def __init__(self, provider: str):
        allowed = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_")
        if not provider or any(char not in allowed for char in provider):
            raise ValueError("provider circuit id must be a lowercase internal identifier")
        if provider.startswith("coingecko"):
            self._connector_id = price_source_store.COINGECKO
        elif provider.startswith("yahoo"):
            self._connector_id = price_source_store.YAHOO_FINANCE
        else:
            raise ValueError("provider circuit id must name a price source (coingecko or yahoo)")
        self._provider = provider

    def _transact(self, work: Callable[[dict], tuple[dict, object]]):
        """Run ``work(circuit) -> (new circuit, result)`` in one transaction; return its result."""
        outcome: list = []

        def change(state: dict) -> dict:
            circuits = dict(state.get("circuit") or {})
            circuit, result = work(self._state(circuits.get(self._provider) or {}))
            outcome.append(result)
            circuits[self._provider] = circuit
            return {**state, "circuit": circuits}

        price_source_store.update_state(self._connector_id, change)
        return outcome[0]

    @staticmethod
    def _state(doc: dict) -> dict:
        return {
            "cooldown_until": _float(doc.get("cooldown_until")),
            "probe_lease_until": _float(doc.get("probe_lease_until")),
            "failure_streak": _integer(doc.get("failure_streak")),
            "generation": _integer(doc.get("generation")),
            "last_ok_at": _float(doc.get("last_ok_at")),
        }

    def try_acquire(
        self,
        now: float,
        *,
        probe_lease_seconds: int | float = _DEFAULT_PROBE_LEASE_SECONDS,
    ) -> dict:
        """Admit healthy traffic, or exactly one half-open probe after cooldown."""

        def work(state: dict) -> tuple[dict, dict]:
            if state["failure_streak"] == 0:
                state["cooldown_until"] = 0.0
                state["probe_lease_until"] = 0.0
                return state, {
                    "allowed": True,
                    "probe": False,
                    "retry_after": 0,
                    "generation": state["generation"],
                }
            blocked_until = max(state["cooldown_until"], state["probe_lease_until"])
            if blocked_until > now:
                return state, {
                    "allowed": False,
                    "probe": False,
                    "retry_after": max(1, int(math.ceil(blocked_until - now))),
                }
            state["generation"] += 1
            state["probe_lease_until"] = now + max(1.0, float(probe_lease_seconds))
            return state, {
                "allowed": True,
                "probe": True,
                "retry_after": 0,
                "generation": state["generation"],
            }

        return self._transact(work)

    def record_success(self, now: float, *, generation: int | None = None) -> None:
        """Close the circuit only when this response belongs to its current generation."""

        def work(state: dict) -> tuple[dict, None]:
            state["last_ok_at"] = max(state["last_ok_at"], float(now))
            if generation is None or generation == state["generation"]:
                state["failure_streak"] = 0
                state["cooldown_until"] = 0.0
                state["probe_lease_until"] = 0.0
            return state, None

        self._transact(work)

    def record_failure(
        self,
        now: float,
        *,
        retry_after: int | float | None = None,
        generation: int | None = None,
        open_circuit: bool = True,
    ) -> float:
        """Record a failure and optionally open/reopen the circuit."""

        def work(state: dict) -> tuple[dict, float]:
            if generation is not None and generation != state["generation"]:
                return state, state["cooldown_until"]
            state["failure_streak"] += 1
            state["generation"] += 1
            if open_circuit:
                base = max(1.0, float(retry_after or _DEFAULT_BACKOFF_SECONDS))
                exponent = min(state["failure_streak"] - 1, 10)
                delay = min(float(_MAX_BACKOFF_SECONDS), base * (2**exponent))
                state["cooldown_until"] = max(state["cooldown_until"], float(now) + delay)
            state["probe_lease_until"] = 0.0
            return state, state["cooldown_until"]

        return self._transact(work)

    def public_state(self, now: float) -> dict:
        """Return bounded non-secret health and circuit state (a pure read)."""
        row = price_source_store.read(self._connector_id)
        state = self._state((row["state"].get("circuit") or {}).get(self._provider) or {})
        return {
            "last_ok_at": state["last_ok_at"] or None,
            "failure_streak": state["failure_streak"],
            "cooldown_until": state["cooldown_until"] if state["cooldown_until"] > now else 0.0,
        }

    def clear(self) -> None:
        """Reset this circuit for a deliberate test/runtime reset."""
        self._transact(lambda state: ({}, None))
