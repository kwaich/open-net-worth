"""ArmSpec — an arm is a PARAMETER of the run, not a fork of the code.

The harness compares "configurations the app could ship". A configuration is more than
a tool surface, because several independent levers each trade cost against reliability:

  surface         A | B | C | A-real      how the capability is exposed
  model           any Anthropic model id  the single largest cost lever (~7x haiku↔sonnet)
  result_budget   chars, 0 = unlimited    tool-result truncation
  descriptions    full | compressed       field-description verbosity

Every lever is orthogonal to the task set and the scorer, so a new comparison is a CLI
flag, never a code change. That matters because the risky levers (cheaper model, smaller
results, terser descriptions) all fail the same way: the model still answers, the turn
still completes, and the DATA is quietly wrong. The scorer is what catches that, so all
of them must run through it unchanged.

Spec mini-language (``--arm``):

    A                                   surface A, defaults, key "A"
    haiku=A,model=claude-haiku-4-5      surface A on haiku, key "haiku"
    terse=A,descriptions=compressed     surface A with compressed descriptions
    small=C,result_budget=800           surface C with results truncated to 800 chars
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

VALID_SURFACES = ("A", "B", "C", "A-real")
VALID_DESCRIPTIONS = ("full", "compressed")


@dataclass(frozen=True)
class ArmSpec:
    key: str
    surface: str = "A"
    model: str = ""
    #: Truncate each tool result to this many characters (0 = no truncation).
    result_budget: int = 0
    #: "full" keeps every description verbatim; "compressed" keeps the first sentence.
    descriptions: str = "full"

    def label(self) -> str:
        bits = [f"surface={self.surface}", f"model={self.model}"]
        if self.result_budget:
            bits.append(f"result_budget={self.result_budget}")
        if self.descriptions != "full":
            bits.append(f"descriptions={self.descriptions}")
        return ", ".join(bits)


def parse_arm(text: str, default_model: str = "") -> ArmSpec:
    """Parse one ``--arm`` value into an ArmSpec."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty arm spec")

    key = ""
    if "=" in raw.split(",")[0] and raw.split(",")[0].split("=")[0] not in _FIELDS:
        key, _, raw = raw.partition("=")
        key = key.strip()

    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise ValueError(f"invalid arm spec: {text!r}")

    surface = ""
    options: dict[str, Any] = {}
    for part in parts:
        if "=" not in part:
            surface = part
            continue
        name, _, value = part.partition("=")
        name, value = name.strip(), value.strip()
        if name not in _FIELDS:
            raise ValueError(f"unknown arm option {name!r} (valid: {sorted(_FIELDS)})")
        options[name] = value

    surface = surface or options.pop("surface", "") or "A"
    if surface not in VALID_SURFACES:
        raise ValueError(f"unknown surface {surface!r} (valid: {list(VALID_SURFACES)})")

    spec = ArmSpec(key=key or surface, surface=surface, model=default_model)
    if "model" in options:
        spec = replace(spec, model=options["model"])
    if "result_budget" in options:
        spec = replace(spec, result_budget=int(options["result_budget"]))
    if "descriptions" in options:
        value = options["descriptions"]
        if value not in VALID_DESCRIPTIONS:
            raise ValueError(f"descriptions must be one of {list(VALID_DESCRIPTIONS)}")
        spec = replace(spec, descriptions=value)
    return spec


_FIELDS = {"surface", "model", "result_budget", "descriptions"}


# ── Description compression ──────────────────────────────────────────────────

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def compress_text(text: str) -> str:
    """Keep the first sentence; drop the teaching prose after it.

    NOTE ON FIDELITY: a parallel lane is compressing the real transaction-teaching prose
    and preserving both versions. This is NOT that text — it is a deterministic,
    content-agnostic approximation so the lever can be measured before their version
    lands. Swap this function for their compressed strings to measure the real thing;
    the arm plumbing does not change. Treat any result from this lever as directional.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return cleaned
    first = _SENTENCE_END.split(cleaned, 1)[0].strip()
    # Drop a trailing parenthetical aside, which is where most examples live.
    first = re.sub(r"\s*\([^)]*\)\s*$", "", first).strip()
    return first or cleaned


_REQUIRES = " Requires: "


def compress_description(text: str) -> str:
    """Compress PROSE while preserving structural facts the model needs to call the tool.

    This lever is meant to measure "terser teaching prose", NOT "hide the contract". A
    naive first-sentence cut is destructive asymmetrically: arm A and arm C carry their
    required-field information in JSON Schema (``required``/``enum``, untouched by any
    text compression) or fetch it via ``describe``, but arm B's fat-tool description is
    the ONLY place its per-action required fields exist. Cutting to the first sentence
    would leave arm B structurally blind while barely touching A or C — comparing
    "fully specified" against "guessing", not "verbose" against "terse".

    So compression is line-aware: an action line keeps its ``Requires: ...`` clause and
    only its prose is compressed.
    """
    cleaned = (text or "").strip()
    if "\n" not in cleaned:
        return compress_text(cleaned)

    kept: list[str] = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- ") and _REQUIRES in stripped:
            prose, _, requires = stripped.partition(_REQUIRES)
            label, _, summary = prose.partition(": ")
            kept.append(f"{label}: {compress_text(summary)}{_REQUIRES}{requires}")
        elif stripped.startswith("- "):
            kept.append(compress_text(stripped))
        else:
            kept.append(compress_text(stripped))
    return "\n".join(kept)


def compress_schema(schema: dict) -> dict:
    """Return a copy of a tool schema with every description compressed."""
    out = dict(schema)
    if "description" in out:
        out["description"] = compress_description(out["description"])
    properties = (out.get("input_schema") or {}).get("properties")
    if isinstance(properties, dict):
        new_schema = dict(out["input_schema"])
        new_schema["properties"] = {
            name: (
                {**spec, "description": compress_text(spec["description"])}
                if isinstance(spec, dict) and spec.get("description")
                else spec
            )
            for name, spec in properties.items()
        }
        out["input_schema"] = new_schema
    return out
