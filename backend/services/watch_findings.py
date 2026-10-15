"""The one shape every watch rule returns: ``{kind, tone, weight, severity, text}`` (+ optional
``meta`` for the numbers a later rule — materiality, the explained alert — needs)."""

from __future__ import annotations


def finding(kind: str, tone: str, weight: float, severity: str, text: str) -> dict:
    return {
        "kind": kind,
        "tone": tone,
        "weight": round(weight, 1),
        "severity": severity,
        "text": text,
    }
