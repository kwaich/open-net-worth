"""``describe`` — the inspectability primitive.

The problem it solves: this domain cannot answer questions about itself. A model that wants
to know what ``apply_budget_change`` accepts has no way to ASK, so it has to be TOLD, and
the only places to tell it are tool schemas and the system prompt. Both grow with the domain
and both rot independently of the code they describe.

``describe`` answers those questions from the code that enforces them. Every field, type,
constraint and example it returns is derived by :mod:`domain_introspect_ast` from the choke's
own source, so it cannot drift from the validator — if a handler stops reading a key, the key
stops being described, with no edit here.

Layering note: this module is deliberately ignorant of the AI tool registry, which lives one
layer up (``services`` must never import ``ai``). The read-side "views" are therefore INJECTED
by the caller as plain dicts rather than looked up here, the same inversion ``core.io`` and
``core.llm_seam`` use.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from backend.services import domain_introspect_ast as introspect

# Output caps. A list mode must stay small no matter how big the domain gets — the per-op
# detail is what carries the weight, and a caller that wants it asks for one op.
_MAX_LIST = 80
_MAX_CONSTRAINTS = 6
_MAX_SUGGESTIONS = 8

_PLACEHOLDERS = {
    "string": "…",
    "number": 0.0,
    "integer": 0,
    "boolean": False,
    "array": [],
    "object": {},
    "unknown": "…",
}


# ── Ranking ──────────────────────────────────────────────────────────────────────────


def _rank(needle: str, hay: str) -> int:
    """3 = exact, 2 = prefix, 1 = substring, 0 = no match."""
    needle, hay = needle.lower(), hay.lower()
    if not needle:
        return 1
    if hay == needle:
        return 3
    if hay.startswith(needle):
        return 2
    return 1 if needle in hay else 0


def _best_rank(needle: str, candidates: Iterable[str]) -> int:
    return max((_rank(needle, c) for c in candidates), default=0)


def _closest(needle: str, candidates: Sequence[str]) -> list[str]:
    """Nearest names to an unknown target. Never returns nothing when anything exists —
    an empty result for a typo is the least useful possible answer."""
    import difflib

    scored = [(c, _rank(needle, c)) for c in candidates]
    hits = [c for c, r in sorted(scored, key=lambda x: -x[1]) if r > 0]
    if not hits:
        hits = difflib.get_close_matches(needle, candidates, n=_MAX_SUGGESTIONS, cutoff=0.3)
    if not hits:
        hits = list(candidates)
    return hits[:_MAX_SUGGESTIONS]


# ── Rendering ────────────────────────────────────────────────────────────────────────


def _field_dict(f: introspect.Field) -> dict[str, Any]:
    out: dict[str, Any] = {"name": f.name}
    if f.type != "unknown":
        out["type"] = f.type
    if f.default:
        out["default"] = f.default
    return out


def _example_for(op: introspect.Op) -> dict[str, Any]:
    """A worked example: the discriminator plus every field the code ENFORCES.

    Derived, not written — the keys come from the extraction and the placeholder value from
    each field's inferred type. Fields the handler merely accepts are left out so the example
    is the minimal call that gets past the validator, not a maximal one."""
    example: dict[str, Any] = {op.discriminator: op.value}
    for f in op.required:
        example[f.name] = _PLACEHOLDERS.get(f.type, "…")
    return example


def _op_detail(op: introspect.Op) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "op": op.name,
        "choke": f"apply_{op.family}_change",
        "discriminator": {op.discriminator: op.value},
        "required": [_field_dict(f) for f in op.required],
        "accepted": [_field_dict(f) for f in op.optional],
        "example": _example_for(op),
        "source": op.source,
    }
    if op.constraints:
        detail["constraints"] = op.constraints[:_MAX_CONSTRAINTS]
        if len(op.constraints) > _MAX_CONSTRAINTS:
            detail["constraints_omitted"] = len(op.constraints) - _MAX_CONSTRAINTS
    if op.unresolved:
        # Say so rather than implying the field list is complete.
        detail["partial"] = (
            "some delegated calls could not be analyzed statically, so the accepted list "
            "may be incomplete: " + ", ".join(op.unresolved)
        )
    return detail


def _op_summary(op: introspect.Op) -> dict[str, Any]:
    required = [f.name for f in op.required]
    summary: dict[str, Any] = {"op": op.name, "accepts": len(op.fields)}
    if required:
        summary["required"] = required
    return summary


_REQUIRED_NOTE = (
    "'required' means the code demonstrably refuses without it (a raise guard, a validate "
    "helper, or a coercion that throws on null). 'accepted' fields are read by the handler "
    "but not enforced — some are mandatory in practice, the code just does not check."
)


# ── Views (read side) ────────────────────────────────────────────────────────────────


def _view_detail(view: dict) -> dict[str, Any]:
    """Render one injected read-tool schema as a view.

    Params come from the tool's JSON Schema. Null semantics are reported ONLY where the
    schema documents them — this deliberately does not invent an answer for a parameter
    whose null meaning was never written down, it flags it as undocumented instead."""
    schema = view.get("input_schema") or {}
    props = schema.get("properties") or {}
    required = set(schema.get("required") or ())
    params = []
    for name, spec in list(props.items())[:_MAX_LIST]:
        entry: dict[str, Any] = {"name": name, "required": name in required}
        if spec.get("type"):
            entry["type"] = spec["type"]
        if spec.get("enum"):
            entry["enum"] = spec["enum"]
        desc = (spec.get("description") or "").strip()
        if desc:
            entry["description"] = desc
        if name not in required:
            mentions_null = any(w in desc.lower() for w in ("omit", "null", "default", "all "))
            entry["when_omitted"] = desc if mentions_null else "not documented in the schema"
        params.append(entry)
    return {
        "view": view.get("name", ""),
        "description": (view.get("description") or "").strip(),
        "params": params,
    }


def _view_names(views: Sequence[dict]) -> list[str]:
    return [v.get("name", "") for v in views]


# ── Public entry point ───────────────────────────────────────────────────────────────


def describe(
    target: str = "", q: str = "", views: Sequence[dict] = (), root: str | None = None
) -> dict[str, Any]:
    """Answer a question about the domain's own shape.

    ``target``  '' | 'views' | 'views/<name>' | 'ops' | 'ops/<name>'; prefix-matches, so
                'ops/budget.' returns the whole budget family.
    ``q``       keyword filter, ranked exact > prefix > substring.
    ``views``   read-tool schemas injected by the AI layer (see the module docstring).
    ``root``    alternate services directory to introspect; defaults to the live one. The
                seam that lets a test point the whole pipeline at a fixture choke and prove
                the output follows the CODE rather than anything cached or hand-written.
    """
    target = (target or "").strip().strip("/")
    q = (q or "").strip()
    chokes = introspect.discover_chokes(root)
    ops = [op for c in chokes for op in c.ops]

    head, _, rest = target.partition("/")
    head = head.lower()

    if head in ("", "index"):
        # A bare q is a SEARCH, not a request for the index — returning the map to someone
        # who asked a question is the same non-answer this primitive exists to remove.
        return _search(ops, views, q) if q else _index(chokes, ops, views, q)
    if head == "ops":
        return _ops(ops, rest, q)
    if head == "views":
        return _views(views, rest, q)
    return {
        "error": f"unknown target {target!r}",
        "valid_targets": ["", "ops", "ops/<name>", "views", "views/<name>"],
        "did_you_mean": _closest(head, ["ops", "views"] + [op.name for op in ops]),
    }


def _search(ops, views: Sequence[dict], q: str) -> dict[str, Any]:
    """Search both sides at once. Ops lead: a keyword search is nearly always someone about
    to WRITE something and wanting the shape."""
    matched_ops = _filter_ops(ops, q)
    view_hits = [v for v in views if _rank(q, v.get("name", ""))]
    if not matched_ops and not view_hits:
        universe = [op.name for op in ops] + _view_names(views)
        return {"error": f"nothing matches {q!r}", "did_you_mean": _closest(q, universe)}
    out: dict[str, Any] = {"query": q}
    if matched_ops:
        out["ops"] = [_op_summary(op) for op in matched_ops[:_MAX_LIST]]
        out["note"] = _REQUIRED_NOTE
    if view_hits:
        out["views"] = [v.get("name", "") for v in view_hits[:_MAX_LIST]]
    out["usage"] = "describe('ops/<name>') or describe('views/<name>') for the full detail"
    return out


def _index(chokes, ops, views, q: str) -> dict[str, Any]:
    """The map: what is describable, and how to drill in. Deliberately tiny."""
    return {
        "describes": "the write chokes (ops) and read tools (views) of this domain",
        "generated_from": "the source of each choke's dispatcher — never a hand-written table",
        "ops": {
            "total": len(ops),
            "families": [
                {
                    "family": c.family,
                    "ops": len(c.ops),
                    "discriminator": c.discriminator,
                    "target": f"ops/{c.family}.",
                }
                for c in chokes
            ],
        },
        "views": {"total": len(views), "target": "views"},
        "usage": "describe('ops/budget.assign') for one op; describe(q='envelope') to search",
        "note": _REQUIRED_NOTE,
    }


def _ops(ops, rest: str, q: str) -> dict[str, Any]:
    names = [op.name for op in ops]
    rest = rest.strip()

    if rest:
        exact = [op for op in ops if op.name.lower() == rest.lower()]
        if exact:
            return _op_detail(exact[0])
        # Prefix: a family ('budget'), or an explicit family prefix ('budget.').
        prefix = [op for op in ops if op.name.lower().startswith(rest.lower().rstrip("."))]
        if not prefix:
            return {
                "error": f"no op matches {rest!r}",
                "did_you_mean": _closest(rest, names),
            }
        if len(prefix) == 1:
            return _op_detail(prefix[0])
        return {
            "matched": rest,
            "count": len(prefix),
            "ops": [_op_summary(op) for op in prefix[:_MAX_LIST]],
            "note": _REQUIRED_NOTE,
        }

    selected = _filter_ops(ops, q) if q else ops
    if q and not selected:
        return {"error": f"no op matches {q!r}", "did_you_mean": _closest(q, names)}
    return {
        "count": len(selected),
        "ops": [_op_summary(op) for op in selected[:_MAX_LIST]],
        "truncated": max(0, len(selected) - _MAX_LIST),
        "usage": "describe('ops/<name>') for the full shape of one op",
        "note": _REQUIRED_NOTE,
    }


def _filter_ops(ops, q: str) -> list:
    """Rank by the best match across the op name and its field names, so a search for a
    FIELD ('envelope_id') finds the ops that take it, not just ops named after it."""
    scored = []
    for op in ops:
        rank = max(_rank(q, op.name), _best_rank(q, [f.name for f in op.fields]))
        if rank:
            scored.append((rank, op))
    scored.sort(key=lambda x: (-x[0], x[1].name))
    return [op for _, op in scored]


def _views(views: Sequence[dict], rest: str, q: str) -> dict[str, Any]:
    names = _view_names(views)
    rest = rest.strip()
    if rest:
        exact = [v for v in views if v.get("name", "").lower() == rest.lower()]
        if exact:
            return _view_detail(exact[0])
        prefix = [v for v in views if v.get("name", "").lower().startswith(rest.lower())]
        if not prefix:
            return {"error": f"no view matches {rest!r}", "did_you_mean": _closest(rest, names)}
        if len(prefix) == 1:
            return _view_detail(prefix[0])
        return {"matched": rest, "count": len(prefix), "views": [v.get("name") for v in prefix]}

    selected = views
    if q:
        scored = [(v, _rank(q, v.get("name", ""))) for v in views]
        selected = [v for v, r in sorted(scored, key=lambda x: -x[1]) if r]
        if not selected:
            return {"error": f"no view matches {q!r}", "did_you_mean": _closest(q, names)}
    # The unfiltered roster is names only. Attaching a description to each of ~100 read
    # tools reproduces exactly the bulk problem this primitive exists to remove; a caller
    # who wants prose asks for one view, or narrows with q.
    if q:
        listed: list = [
            {"view": v.get("name", ""), "description": _first_sentence(v.get("description", ""))}
            for v in selected[:_MAX_LIST]
        ]
    else:
        listed = [v.get("name", "") for v in selected[:_MAX_LIST]]
    return {
        "count": len(selected),
        "views": listed,
        "truncated": max(0, len(selected) - _MAX_LIST),
        "usage": "describe('views/<name>') for its params; describe(target='views', q=…) to search",
    }


def _first_sentence(text: str) -> str:
    text = " ".join((text or "").split())
    head = text.split(". ")[0]
    return head[:160]
