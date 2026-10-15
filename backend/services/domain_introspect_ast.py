"""The AST extraction engine behind ``describe`` — recovers each write choke's accepted
shape FROM THE SOURCE THAT ENFORCES IT.

Why this exists: the finance domain is not inspectable at runtime. Every write choke is a
plain ``def apply_<family>_change(change: dict)`` that dispatches on a string discriminator
and then reads ``change.get("field")`` inline. Nothing declares the accepted types or their
fields, so historically the only place they were written down was a hand-maintained tool
schema — which rots independently of the validator it claims to describe.

This module derives them instead. It NEVER holds a field list: it parses the choke's own
source, enumerates the discriminator branches, follows the delegation one bounded hop into
whatever handler actually reads the dict, and reports the ``.get()`` keys it finds. A field
that is added to a handler shows up here on the next call with no edit; a field deleted
from a handler disappears. The output cannot be staler than the code, because it IS the code.

Two deliberate limits, both surfaced rather than hidden:

  * ``Field.required`` means ENFORCED, not "conceptually mandatory", and it is SOUND-first:
    set only where the handler demonstrably refuses without the key, on every call. The
    recognized refusals are a raise OR a returned error (``services.options`` validates
    entirely by returning error strings), reached through an unconditional guard, a helper
    that itself refuses a falsy argument (detected by READING the helper — the name-prefix
    convention it replaced was already stale against ``db.alert_store``'s ``norm_*``), or a
    bare ``float()``/``int()`` that throws on None. A guard nested inside another condition
    is deliberately NOT counted: it constrains one code path, not the op's contract.
    Everything else is reported as merely ACCEPTED. Many chokes do not validate at all —
    they ``.get()`` a key and write whatever comes back — so "an option needs a strike" is
    partly a property of the DOMAIN rather than the code, and where the code is silent this
    stays silent too. Under-reporting is the deliberate error direction (the field still
    appears under ``accepted``); over-reporting would invent a contract that does not exist.
  * A delegation this module cannot statically resolve is recorded in ``Op.unresolved``
    and reported to the caller. It is never silently rendered as "no fields", which would
    be a confident lie about an under-analyzed handler.
"""

from __future__ import annotations

import ast
import functools
import logging
import pathlib
from dataclasses import dataclass
from dataclasses import field as dc_field

# The choke signature: a module-level ``apply_*_change(change)`` in backend/services/.
# This is DISCOVERY, not a registry — adding a new choke that matches the convention makes
# it describable with no edit here.
_CHOKE_PREFIX = "apply_"
_CHOKE_SUFFIX = "_change"

_SERVICES_DIR = pathlib.Path(__file__).resolve().parent
_BACKEND_DIR = _SERVICES_DIR.parent

# How many hops of delegation to follow from a dispatch branch to the code that actually
# reads the dict (branch -> _op_handler -> validate_helper is the deepest real chain).
_MAX_DEPTH = 4

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Field:
    """One accepted key of a change dict, as read by the handler."""

    name: str
    required: bool
    type: str = "unknown"
    default: str = ""


@dataclass
class Op:
    """One accepted change type of one choke."""

    family: str
    value: str
    discriminator: str
    fields: list[Field] = dc_field(default_factory=list)
    constraints: list[str] = dc_field(default_factory=list)
    source: str = ""
    unresolved: list[str] = dc_field(default_factory=list)

    @property
    def name(self) -> str:
        return f"{self.family}.{self.value}"

    @property
    def required(self) -> list[Field]:
        return [f for f in self.fields if f.required]

    @property
    def optional(self) -> list[Field]:
        return [f for f in self.fields if not f.required]


@dataclass
class Choke:
    """One ``apply_*_change`` dispatch function and every op it accepts."""

    family: str
    module: str
    function: str
    discriminator: str
    ops: list[Op] = dc_field(default_factory=list)
    source: str = ""


# ── Source access ────────────────────────────────────────────────────────────────────


@functools.lru_cache(maxsize=64)
def _parse(path: str) -> ast.Module | None:
    try:
        return ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None


@functools.lru_cache(maxsize=64)
def _module_path(dotted: str) -> str:
    """Resolve ``backend.db.alert_store`` to a file path, or "" if it is not ours."""
    if not dotted.startswith("backend."):
        return ""
    rel = dotted.split(".", 1)[1].replace(".", "/")
    for candidate in (_BACKEND_DIR / f"{rel}.py", _BACKEND_DIR / rel / "__init__.py"):
        if candidate.exists():
            return str(candidate)
    return ""


def _rel(path: str) -> str:
    try:
        return str(pathlib.Path(path).relative_to(_BACKEND_DIR.parent))
    except ValueError:
        return path


# ── Symbol resolution (names -> the def that implements them) ────────────────────────


def _imports_of(tree: ast.Module) -> dict[str, str]:
    """Map every imported NAME to the dotted module it came from.

    Covers module-level and function-level imports alike — a handler that does a local
    ``from backend.services import categories as categories_service`` to dodge a cycle is
    extremely common here and must still resolve."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                out[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out[alias.asname or alias.name] = alias.name
    return out


def _top_defs(tree: ast.Module) -> dict[str, ast.AST]:
    """Module-level functions and classes by name."""
    return {
        n.name: n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def _method_of(cls: ast.ClassDef, name: str) -> ast.FunctionDef | None:
    for n in cls.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n  # type: ignore[return-value]
    return None


def _resolve_name(name: str, path: str) -> tuple[ast.AST | None, str]:
    """Find the def/class ``name`` refers to, following one import hop into our own tree."""
    tree = _parse(path)
    if tree is None:
        return None, path
    local = _top_defs(tree).get(name)
    if local is not None:
        return local, path
    dotted = _imports_of(tree).get(name)
    if not dotted:
        return None, path
    target = _module_path(dotted.rsplit(".", 1)[0])
    if not target:
        return None, path
    sub = _parse(target)
    if sub is None:
        return None, target
    return _top_defs(sub).get(dotted.rsplit(".", 1)[1]), target


def _returns_class(fn: ast.AST, path: str) -> tuple[ast.ClassDef | None, str]:
    """The class a zero-arg factory returns, via its RETURN ANNOTATION.

    ``services.alerts`` reaches its store through ``store = alert_store()``, and
    ``alert_store`` is annotated ``-> AlertStore``. The annotation is the only static link
    from the call to the class whose methods actually read the change dict."""
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.returns is None:
        return None, path
    ann = fn.returns
    cls_name = ann.id if isinstance(ann, ast.Name) else getattr(ann, "attr", None)
    if not cls_name:
        return None, path
    node, where = _resolve_name(cls_name, path)
    return (node if isinstance(node, ast.ClassDef) else None), where


# ── Field extraction ─────────────────────────────────────────────────────────────────


def _dict_get_key(node: ast.AST, var: str) -> str | None:
    """The literal key of a ``<var>.get("k")`` / ``<var>["k"]`` read, else None."""
    if isinstance(node, ast.Call):
        fn = node.func
        if (
            isinstance(fn, ast.Attribute)
            and fn.attr == "get"
            and isinstance(fn.value, ast.Name)
            and fn.value.id == var
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            return node.args[0].value
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == var
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
    ):
        return node.slice.value
    return None


def _literal(node: ast.AST) -> str:
    try:
        return repr(ast.literal_eval(node))
    except (ValueError, TypeError, SyntaxError):
        return ""


def _type_from_value(node: ast.AST) -> str:
    try:
        val = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return "unknown"
    for py, name in (
        (bool, "boolean"),
        (int, "integer"),
        (float, "number"),
        (str, "string"),
        (list, "array"),
        (dict, "object"),
    ):
        if isinstance(val, py):
            return name
    return "unknown"


# Coercions the handlers actually use, and what each implies about the accepted value.
_COERCION_TYPES = {
    "float": "number",
    "int": "integer",
    "round": "number",
    "bool": "boolean",
    "str": "string",
    "list": "array",
    "dict": "object",
    "sorted": "array",
}
_STRING_METHODS = {"strip", "lower", "upper", "title", "lstrip", "rstrip"}

# Literals that mean "coerce to a usable type", not "this field has a default".
_EMPTYISH = frozenset({"''", '""', "{}", "[]", "None", "0", "0.0"})


# Coercions that RAISE on a missing value: ``float(change.get("amount"))`` blows up with
# TypeError when the key is absent, which makes the field required in practice even though
# no explicit guard mentions it.
_STRICT_COERCIONS = {"float", "int", "round"}

# Helper-name conventions this codebase uses for "resolve this or raise".
_REQUIRE_PREFIXES = ("_require", "_valid", "require_", "validate_")


class _FieldVisitor(ast.NodeVisitor):
    """Collect every ``change.get(...)`` read in a handler, with required-ness and type
    inferred from HOW the surrounding expression treats it."""

    def __init__(self, var: str) -> None:
        self.var = var
        self.found: dict[str, Field] = {}
        # A read is optional if an enclosing expression supplies a fallback for it.
        self._fallback: list[tuple[ast.AST, str]] = []
        self._coerce: list[str] = []
        self._strict: list[ast.AST] = []
        self._require: list[ast.AST] = []
        # field name -> the local variable its value was bound to, for guard analysis.
        self.bound: dict[str, str] = {}
        self._target: list[str] = []
        self._depth_dict = 0
        self._depth_cond = 0

    def visit_Assign(self, node: ast.Assign) -> None:
        target = node.targets[0] if len(node.targets) == 1 else None
        name = target.id if isinstance(target, ast.Name) else ""
        self._target.append(name)
        self.visit(node.value)
        self._target.pop()

    def visit_Dict(self, node: ast.Dict) -> None:
        # A read that is a VALUE in a constructed row is a passthrough, not a demand:
        # ``{"note": change.get("note"), ...}`` stores None just fine.
        for key in node.keys:
            if key is not None:
                self.visit(key)
        self._depth_dict += 1
        for value in node.values:
            self.visit(value)
        self._depth_dict -= 1

    def visit_If(self, node: ast.If) -> None:
        self._visit_condition(node.test)
        for stmt in node.body + node.orelse:
            self.visit(stmt)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self._visit_condition(node.test)
        self.visit(node.body)
        self.visit(node.orelse)

    def _visit_condition(self, test: ast.AST) -> None:
        # ``if not change.get("allow_duplicate"):`` is a PRESENCE CHECK — you only test for
        # something that is allowed to be absent. Reading it as a demand marked every
        # optional boolean flag in the codebase required.
        self._depth_cond += 1
        self.visit(test)
        self._depth_cond -= 1

    # -- context tracking ------------------------------------------------------------

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        # ``change.get("k") or <fallback>`` — the FIRST operand is defaulted by the rest.
        if isinstance(node.op, ast.Or) and len(node.values) > 1:
            self._fallback.append((node.values[0], _literal(node.values[1])))
            self.visit(node.values[0])
            self._fallback.pop()
            for extra in node.values[1:]:
                self.visit(extra)
            return
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        key = self._record(node)
        if key is not None:
            # ``change.get("k")`` itself — its own args carry no further context.
            for arg in node.args[1:]:
                self.visit(arg)
            return
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
        coerced = _COERCION_TYPES.get(name) or ("string" if name in _STRING_METHODS else "")
        strict = name in _STRICT_COERCIONS
        requires = name.startswith(_REQUIRE_PREFIXES)
        if strict:
            self._strict.append(node)
        if requires:
            self._require.append(node)
        if coerced:
            self._coerce.append(coerced)
        self.generic_visit(node)
        if coerced:
            self._coerce.pop()
        if requires:
            self._require.pop()
        if strict:
            self._strict.pop()

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if self._record(node) is None:
            self.generic_visit(node)

    # -- recording -------------------------------------------------------------------

    def _record(self, node: ast.AST) -> str | None:
        """Classify one read.

        The distinction that matters: ``change.get(k, d)`` and ``change.get(k) or "expense"``
        genuinely DEFAULT the field, but ``(change.get(k) or "").strip()`` is a string
        COERCION — it says nothing about optionality, and reading it as a default was the
        single biggest source of wrong answers in the first cut of this extractor. An
        empty-ish fallback therefore only means optional when nothing else demands the
        value."""
        key = _dict_get_key(node, self.var)
        if key is None:
            return None
        default = ""
        explicit = False
        emptyish = False
        if isinstance(node, ast.Call) and len(node.args) > 1:
            # An EMPTY explicit default is not a default — ``store.find_by_id(
            # change.get("instrument_id", ""))`` writes `""` only so the lookup does not
            # choke on None, and the real demand is the `if not inst: raise` right after.
            # Treating it as proof of optionality overrode that guard.
            literal = _literal(node.args[1])
            if literal in _EMPTYISH:
                emptyish = True
            else:
                default = literal
                explicit = True
        for target, fallback in self._fallback:
            if target is node:
                if fallback and fallback not in _EMPTYISH:
                    default = default or fallback
                    explicit = True
                else:
                    emptyish = True

        # REQUIRED here means ENFORCED: the handler demonstrably raises without the key.
        # Deliberately NOT inferred from a bare read. Most of these chokes do not validate
        # at all — they ``.get()`` a key and write whatever comes back — so "the handler read
        # it bare" is evidence about the DOMAIN (an option needs a strike), not about the
        # code. Guessing from it produced confidently wrong answers in both directions; the
        # honest split is "the validator refuses without this" vs "the handler accepts it".
        # A fallback disarms BOTH signals: ``float(x or 0)`` cannot raise, and
        # ``validate_task(change.get("task") or change)`` validates something that is
        # guaranteed non-None, so neither proves the key itself is demanded.
        enforced = bool(self._require or self._strict) and not emptyish
        required = enforced and not (explicit or self._depth_dict or self._depth_cond)

        ftype = self._coerce[-1] if self._coerce else "unknown"
        if ftype == "unknown" and default:
            ftype = _type_from_value(ast.parse(default, mode="eval").body)
        self._merge(Field(key, required, ftype, default))
        if self._target and self._target[-1]:
            self.bound.setdefault(key, self._target[-1])
        return key

    def _merge(self, new: Field) -> None:
        """Same key read twice. An explicit default anywhere is definitive proof the handler
        tolerates absence, so it wins; otherwise REQUIRED wins, because the two failure modes
        are not symmetric — under-marking makes a caller omit a field the validator demands
        (a runtime error), while over-marking only makes it send one it could have skipped."""
        old = self.found.get(new.name)
        if old is None:
            self.found[new.name] = new
            return
        default = old.default or new.default
        self.found[new.name] = Field(
            new.name,
            required=False if default else (old.required or new.required),
            type=old.type if old.type != "unknown" else new.type,
            default=default,
        )


def _constraints_in(node: ast.AST, path: str = "") -> list[str]:
    """Every raise message in a handler — the enforcement text, verbatim from the code."""
    out: list[str] = []
    for n in ast.walk(node):
        if not isinstance(n, ast.Raise) or not isinstance(n.exc, ast.Call):
            continue
        exc = n.exc.func
        name = exc.id if isinstance(exc, ast.Name) else getattr(exc, "attr", "")
        if name not in ("ValidationError", "ValueError", "ConflictError", "NotFoundError"):
            continue
        for arg in n.exc.args[:1]:
            text = _message_text(arg)
            if path:
                text = _resolve_constants(text, path)
            if text and text not in out:
                out.append(text)
    # Returned refusals are constraints too — see ``_refuses``. Without this, every rule
    # ``services.options`` enforces on an option open is silently dropped.
    for n in ast.walk(node):
        if not isinstance(n, ast.Return) or not _is_error_value(n.value):
            continue
        value = n.value
        if isinstance(value, ast.Dict):
            value = next(
                (
                    v
                    for k, v in zip(value.keys, value.values)
                    if getattr(k, "value", None) == "error"
                ),
                None,
            )
        text = _message_text(value) if value is not None else ""
        if path and text:
            text = _resolve_constants(text, path)
        if text and text not in out:
            out.append(text)
    return out


@functools.lru_cache(maxsize=64)
def _module_constants(path: str) -> tuple[tuple[str, str], ...]:
    """Module-level ALL_CAPS literals — the enumerated vocabularies a constraint message
    refers to by name (``VALID_ACTIONS``, ``TARGET_TYPES``). Resolved from the AST rather
    than by importing, so introspection never triggers a module's import side effects."""
    tree = _parse(path)
    if tree is None:
        return ()
    out: list[tuple[str, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or not target.id.isupper():
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError, SyntaxError):
            continue
        if isinstance(value, (set, frozenset, list, tuple)):
            out.append((target.id, ", ".join(sorted(str(v) for v in value))))
    return tuple(out)


def _resolve_constants(text: str, path: str) -> str:
    """Inline a constraint's referenced vocabulary so the reader sees the real values."""
    for name, rendered in _module_constants(path):
        if name in text:
            text = text.replace(f"{{sorted({name})}}", rendered).replace(name, rendered)
    return text


def _message_text(node: ast.AST) -> str:
    """Render a raise message, keeping f-string/concat placeholders readable."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.strip()
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            elif isinstance(v, ast.FormattedValue):
                parts.append(f"{{{ast.unparse(v.value)}}}")
        return "".join(parts).strip()
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return (_message_text(node.left) + " " + _message_text(node.right)).strip()
    return ""


# ── Delegation following ─────────────────────────────────────────────────────────────


# Builtins are never worth following and must never be reported as an unresolved delegation.
_NOISE_CALLEES = frozenset(
    {
        "float", "int", "bool", "str", "dict", "list", "set", "tuple", "len", "round",
        "isinstance", "sorted", "any", "all", "min", "max", "abs", "repr", "print",
        "getattr", "hasattr", "enumerate", "zip", "range", "type", "id",
    }
)  # fmt: skip


def _delegations(body: list[ast.stmt], vars_: set[str]) -> list[tuple[ast.AST, int]]:
    """Calls that hand the WHOLE change dict onward, as (callee, argument index).

    Deliberately whole-dict only. An earlier cut also followed ``change.get("transaction")``
    into the template validator, which flattened a NESTED shape's fields up into the
    top-level required list — a nested transaction template came out demanding ``amount`` and
    ``from_account_id``, which belong inside ``transaction``, not beside it. Describing a
    nested shape is a separate job; inventing top-level fields is a wrong answer."""
    out: list[tuple[ast.AST, int]] = []
    for stmt in body:
        for n in ast.walk(stmt):
            if not isinstance(n, ast.Call):
                continue
            name = n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
            if name in _NOISE_CALLEES:
                continue
            for i, arg in enumerate(n.args):
                if _passes_dict(arg, vars_):
                    out.append((n.func, i))
                    break
    return out


def _passes_dict(node: ast.AST, vars_: set[str]) -> bool:
    """True if this argument IS the change dict (possibly via an ``x or change`` fallback)."""
    if isinstance(node, ast.Name) and node.id in vars_:
        return True
    if isinstance(node, ast.BoolOp):
        return any(_passes_dict(v, vars_) for v in node.values)
    return False


def _aliases_of(body: list[ast.stmt], vars_: set[str]) -> set[str]:
    """Locals that ARE the change dict.

    several service chokes open with
    ``spec = change.get("agent") or change`` — every subsequent read and delegation goes
    through ``spec``, so without alias tracking those two chokes look like they accept
    nothing at all."""
    found = set(vars_)
    for _ in range(3):  # transitive, but shallow — these chains are one or two links
        grew = False
        for stmt in body:
            for n in ast.walk(stmt):
                if not isinstance(n, ast.Assign) or len(n.targets) != 1:
                    continue
                target = n.targets[0]
                if not isinstance(target, ast.Name) or target.id in found:
                    continue
                if _passes_dict(n.value, found):
                    found.add(target.id)
                    grew = True
        if not grew:
            break
    return found


def _callee_target(func: ast.AST, path: str) -> tuple[ast.AST | None, str, str]:
    """Resolve a call target to (def node, its file, a label if unresolved)."""
    if isinstance(func, ast.Name):
        node, where = _resolve_name(func.id, path)
        return node, where, ("" if node is not None else func.id)
    if isinstance(func, ast.Attribute):
        base = func.value
        label = f"{ast.unparse(base)}.{func.attr}"
        if isinstance(base, ast.Name):
            # A module alias: ``categories_service.create_category(...)``.
            tree = _parse(path)
            dotted = _imports_of(tree).get(base.id, "") if tree else ""
            target = _module_path(dotted)
            if target:
                sub = _parse(target)
                node = _top_defs(sub).get(func.attr) if sub else None
                if node is not None:
                    return node, target, ""
            # A local store handle: ``store = alert_store()`` -> ``-> AlertStore``.
            factory, where = _resolve_name(base.id, path)
            cls, cls_path = _returns_class(factory, where) if factory else (None, path)
            if cls is None:
                cls, cls_path = _store_handle_class(base.id, path)
            if cls is not None:
                method = _method_of(cls, func.attr)
                if method is not None:
                    return method, cls_path, ""
        return None, path, label
    return None, path, ""


def _store_handle_class(var: str, path: str) -> tuple[ast.ClassDef | None, str]:
    """Resolve ``store = alert_store()`` by finding the assignment then its factory."""
    tree = _parse(path)
    if tree is None:
        return None, path
    for n in ast.walk(tree):
        if not isinstance(n, ast.Assign) or not isinstance(n.value, ast.Call):
            continue
        if not any(isinstance(t, ast.Name) and t.id == var for t in n.targets):
            continue
        callee = n.value.func
        if isinstance(callee, ast.Name):
            resolved, where = _resolve_name(callee.id, path)
            if isinstance(resolved, ast.ClassDef):
                return resolved, where
            cls, cls_path = _returns_class(resolved, where) if resolved else (None, path)
            if cls is not None:
                return cls, cls_path
    return None, path


def _param_name(fn: ast.AST, index: int = 0) -> str:
    if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = fn.args.args
        # Skip ``self`` on a bound method.
        if args and args[0].arg == "self":
            args = args[1:]
        if len(args) > index:
            return args[index].arg
    return ""


# ── Branch analysis ──────────────────────────────────────────────────────────────────


def _refuses(body: list[ast.stmt]) -> bool:
    """Does this branch REFUSE — by raising, or by returning an error?

    Raising is not the only refusal in this codebase. ``services.options`` validates
    ``open`` entirely through returned error strings (``_validate_open`` returns
    ``"missing required field: strike"``, and ``_apply_open`` turns that into
    ``{"error": ...}``), and ``services.lots``/``services.positions`` do the same for a
    failed id lookup. A raise-only detector reports the single most complex write op in
    the domain as having ZERO required fields and ZERO constraints, which is worse than
    saying nothing.

    UNCONDITIONAL refusal only — a refusal nested inside a FURTHER condition is not a
    demand for the tested value. ``if not change.get("allow_duplicate"): dup = find(...);
    if dup: return {"error": ...}`` refuses because a duplicate exists, not because
    ``allow_duplicate`` was missing; counting it marked every optional flag required."""
    for stmt in body:
        if isinstance(stmt, (ast.Raise,)):
            return True
        if isinstance(stmt, ast.Return) and _is_error_value(stmt.value):
            return True
        # Descend through wrappers that do not add a condition (try/with), never into a
        # nested if.
        if isinstance(stmt, (ast.Try, ast.With)) and _refuses(list(stmt.body)):
            return True
    return False


def _is_error_value(node: ast.AST | None) -> bool:
    """An ``{"error": ...}`` dict, or a bare message string returned as a rejection."""
    if node is None:
        return False
    if isinstance(node, ast.Dict):
        return any(isinstance(k, ast.Constant) and k.value == "error" for k in node.keys)
    if isinstance(node, ast.JoinedStr):
        return True
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and bool(node.value)


def _taint_map(body: list[ast.stmt], seeds: dict[str, set[str]]) -> dict[str, set[str]]:
    """Which fields each local derives from.

    Required-ness detection used to be single-hop: it only recognized a guard testing the
    exact variable a field was read into. One more hop of derivation broke it silently, and
    that hop is the DOMINANT idiom here —
    ``agent_id = change.get("id"); existing = store.find_by_id(agent_id); if existing is
    None: raise`` guards on ``existing``, not on ``agent_id``. That one pattern hid the
    required ``id`` on lifecycle ops."""
    taint = {var: set(fields) for var, fields in seeds.items()}
    for _ in range(4):  # these chains are short; iterate to a fixed point
        grew = False
        for stmt in body:
            for node in ast.walk(stmt):
                if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                    continue
                target = node.targets[0]
                if not isinstance(target, ast.Name):
                    continue
                sources: set[str] = set()
                for name in ast.walk(node.value):
                    if isinstance(name, ast.Name) and name.id in taint:
                        sources |= taint[name.id]
                if sources - taint.get(target.id, set()):
                    taint.setdefault(target.id, set()).update(sources)
                    grew = True
        if not grew:
            break
    return taint


def _guarded_fields(
    body: list[ast.stmt], bound: dict[str, str], vars_: set[str], path: str
) -> set[str]:
    """Fields the handler explicitly REFUSES to proceed without.

    The one hard, non-heuristic required-ness signal available statically: the validator
    itself says so. A guard counts when it tests the ABSENCE of anything derived from the
    field (see ``_taint_map``) and its branch refuses (see ``_refuses``)."""
    required: set[str] = set()
    seeds: dict[str, set[str]] = {}
    for field_name, var in bound.items():
        seeds.setdefault(var, set()).add(field_name)
    taint = _taint_map(body, seeds)

    for node in _unconditional_ifs(body):
        if not _refuses(node.body):
            continue
        names, keys = _absence_demands(node.test, vars_)
        for var in names:
            required |= taint.get(var, set())
        required |= keys

    # An enforcing helper applied to a value derived from a field demands that field even
    # when the read and the call are lines apart:
    #   from_ref = (change.get("from_envelope_id") or "").strip()
    #   ...
    #   src = _require_envelope_id(from_ref)
    # Restricted to a TOP-LEVEL statement passing a BARE NAME. Both restrictions are load
    # bearing: a call nested inside an ``if`` is conditional (so it proves nothing about the
    # unconditional contract), and walking an arbitrary argument expression for names swept
    # in every field that merely contributed to it.
    for stmt in body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            if not isinstance(node.args[0], ast.Name):
                continue
            fname = node.func.id if isinstance(node.func, ast.Name) else ""
            if not fname or not _is_enforcing(fname, path):
                continue
            required |= taint.get(node.args[0].id, set())
    return required


def _unconditional_ifs(body: list[ast.stmt]) -> list[ast.If]:
    """Guards that run on EVERY call, i.e. not nested inside another ``if``.

    ``if method == "specific_id": ... if not selection: raise`` demands a ``lot_selection``
    only for one disposal method. Walking every If in the subtree reported it as part of
    the unconditional contract for all disposals."""
    out: list[ast.If] = []
    for stmt in body:
        if isinstance(stmt, ast.If):
            out.append(stmt)
        elif isinstance(stmt, (ast.Try, ast.With)):
            out += _unconditional_ifs(list(stmt.body))
    return out


def _is_enforcing(name: str, path: str) -> bool:
    """A helper that refuses a missing value — read from the callee, name as a fallback."""
    return _callee_enforces(name, path) or name.startswith(_REQUIRE_PREFIXES)


def _absence_demands(test: ast.AST, vars_: set[str]) -> tuple[set[str], set[str]]:
    """(local names, dict keys) whose ABSENCE this test refuses on.

    Sound by construction: only shapes that fire unconditionally when the value is missing.
    An ``and`` is deliberately NOT descended into — ``if action == "notify" and not
    message: raise`` refuses only for one action, so it does not make ``message`` part of
    the op's unconditional contract, and treating it as one marked five conditional
    conditionally accepted fields required."""
    names: set[str] = set()
    keys: set[str] = set()
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        for sub in ast.walk(test.operand):
            key = next((k for v in vars_ if (k := _dict_get_key(sub, v))), None)
            if key:
                keys.add(key)
            elif isinstance(sub, ast.Name):
                names.add(sub.id)
    elif isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        # ``if not a or not b: raise`` — each disjunct is independently fatal.
        for value in test.values:
            sub_names, sub_keys = _absence_demands(value, vars_)
            names |= sub_names
            keys |= sub_keys
    else:
        names |= _absence_tested(test)
        for sub in ast.walk(test):
            key = next((k for v in vars_ if (k := _dict_get_key(sub, v))), None)
            if key and _absence_tested(test):
                keys.add(key)
    return names, keys


@functools.lru_cache(maxsize=256)
def _callee_enforces(name: str, path: str) -> bool:
    """Does calling ``name(x)`` refuse when ``x`` is absent?

    Answered by READING the callee, not by matching its name. The name-prefix convention
    (``_require_*``/``validate_*``) was the first cut and was already stale on arrival:
    ``db.alert_store`` spells the identical contract as ``norm_ticker``/``norm_op``/
    ``norm_value``, so every field on ``alert.create`` came back unenforced. A prefix list
    is domain knowledge that rots; "the function raises on a falsy argument" is a property
    of the code, and it generalizes to the next convention without an edit here.

    Three refusal shapes, all present in this codebase:
      * ``if not t: raise`` over something derived from the parameter (norm_ticker);
      * ``if x not in VOCAB: raise`` — an empty value is never in a literal vocabulary
        (norm_op);
      * a bare ``float(param)`` with no fallback, which throws on None (norm_value).
    """
    node, where = _resolve_name(name, path)
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    param = _param_name(node)
    if not param:
        return False
    body = list(node.body)
    taint = _taint_map(body, {param: {param}})
    for stmt in body:
        for inner in ast.walk(stmt):
            if isinstance(inner, ast.If) and _refuses(inner.body):
                tested = _absence_tested(inner.test) | _vocabulary_tested(inner.test)
                if any(param in taint.get(v, set()) for v in tested):
                    return True
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name):
                if inner.func.id in _STRICT_COERCIONS and inner.args:
                    arg = inner.args[0]
                    if isinstance(arg, ast.Name) and param in taint.get(arg.id, set()):
                        return True
    return False


def _vocabulary_tested(test: ast.AST) -> set[str]:
    """``if op not in ALERT_OPS: raise`` — absence can never be a member, so this refuses
    a missing value exactly like ``not op`` would."""
    out: set[str] = set()
    if isinstance(test, ast.Compare) and isinstance(test.left, ast.Name):
        for op in test.ops:
            if isinstance(op, ast.NotIn):
                out.add(test.left.id)
    return out


def _looped_required_fields(body: list[ast.stmt], vars_: set[str]) -> set[str]:
    """Fields demanded by a loop over a literal list of key names.

    ``services.options._validate_open`` spells its required set as
    ``for field in ("option_type", "side", "strike", "expiry", "contracts"):
    if not change.get(field): return "missing required field: ..."``. The key is a VARIABLE
    there, so the normal literal-key extraction cannot see it — but the tuple being iterated
    is a list of literals, which is the declaration, and it is right there in the AST."""
    required: set[str] = set()
    for stmt in body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.For) or not isinstance(node.target, ast.Name):
                continue
            if not isinstance(node.iter, (ast.Tuple, ast.List, ast.Set)):
                continue
            names = [e.value for e in node.iter.elts if isinstance(e, ast.Constant)]
            if not names or not all(isinstance(n, str) for n in names):
                continue
            loop_var = node.target.id
            for inner in ast.walk(node):
                if not isinstance(inner, ast.If) or not _refuses(inner.body):
                    continue
                # Does the test read the dict under the loop variable?
                for call in ast.walk(inner.test):
                    if not isinstance(call, ast.Call):
                        continue
                    fn = call.func
                    if (
                        isinstance(fn, ast.Attribute)
                        and fn.attr == "get"
                        and isinstance(fn.value, ast.Name)
                        and fn.value.id in vars_
                        and call.args
                        and isinstance(call.args[0], ast.Name)
                        and call.args[0].id == loop_var
                    ):
                        required |= set(names)
    return required


def _absence_tested(test: ast.AST) -> set[str]:
    """Locals this test refuses on the ABSENCE of.

    Only a negative check counts. ``if snooze_until and len(snooze_until) < 7: raise`` is a
    CONDITIONAL validation — it fires only when the value IS present, so it proves the field
    is optional, not required. Treating any mention as a demand marked exactly those
    conditionally-validated fields required."""
    out: set[str] = set()
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        out |= {n.id for n in ast.walk(test.operand) if isinstance(n, ast.Name)}
    elif isinstance(test, ast.Compare):
        left = test.left
        # ``len(x) < 7`` fires on the empty string too, so it refuses absence exactly like
        # ``not x`` does — the by_date target's ``date`` is guarded only this way.
        if (
            isinstance(left, ast.Call)
            and isinstance(left.func, ast.Name)
            and left.func.id == "len"
            and left.args
        ):
            for op, cmp in zip(test.ops, test.comparators):
                if isinstance(op, (ast.Lt, ast.LtE)) and isinstance(cmp, ast.Constant):
                    out |= {n.id for n in ast.walk(left.args[0]) if isinstance(n, ast.Name)}
        elif isinstance(left, ast.Name):
            for op, cmp in zip(test.ops, test.comparators):
                is_none = isinstance(cmp, ast.Constant) and cmp.value is None
                empty = isinstance(cmp, ast.Constant) and cmp.value == ""
                if isinstance(op, (ast.Is, ast.Eq)) and (is_none or empty):
                    out.add(left.id)
    elif isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        # ``if not a or not b: raise`` — every disjunct is independently fatal.
        for value in test.values:
            out |= _absence_tested(value)
    return out


def _harvest(body: list[ast.stmt], vars_: set[str], path: str, depth: int, seen: set) -> tuple:
    """Fields + constraints + unresolved delegations for a chunk of handler code."""
    tracked = _aliases_of(body, vars_)
    fields: dict[str, Field] = {}
    bound: dict[str, str] = {}
    for var in sorted(tracked):
        visitor = _FieldVisitor(var)
        for stmt in body:
            visitor.visit(stmt)
        for name, fld in visitor.found.items():
            existing = fields.get(name)
            if existing is None:
                fields[name] = fld
            elif not existing.default and fld.default:
                fields[name] = fld
        bound.update(visitor.bound)

    demanded = _guarded_fields(body, bound, tracked, path) | _looped_required_fields(body, tracked)
    for name in demanded:
        fld = fields.get(name)
        if fld is None:
            # A loop-declared key the literal-key extraction never saw as a read.
            fields[name] = Field(name, True)
        elif not fld.default:
            fields[name] = Field(fld.name, True, fld.type, fld.default)

    constraints = list(_constraints_in(ast.Module(body=list(body), type_ignores=[]), path))
    unresolved: list[str] = []

    if depth < _MAX_DEPTH:
        for func, argi in _delegations(body, tracked):
            node, where, label = _callee_target(func, path)
            if node is None:
                if label and label not in unresolved:
                    unresolved.append(label)
                continue
            key = (where, getattr(node, "name", ""), getattr(node, "lineno", 0))
            if key in seen:
                continue
            seen.add(key)
            # Bind to the parameter that ACTUALLY received the dict, not blindly to the
            # first one — otherwise a callee's unrelated dict argument gets mined for
            # fields and reported as this op's shape.
            inner = _param_name(node, argi)
            if not inner:
                continue
            sub_f, sub_c, sub_u = _harvest(
                list(getattr(node, "body", [])), {inner}, where, depth + 1, seen
            )
            for name, fld in sub_f.items():
                existing = fields.get(name)
                if existing is None:
                    fields[name] = fld
                elif fld.required and not existing.required and not existing.default:
                    # The delegate enforces what the caller merely read — e.g. the option
                    # fields appear as bare reads in _apply_open's row literal, and only
                    # _validate_open demands them. Dropping the delegate's verdict here left
                    # option.open claiming zero required fields.
                    fields[name] = Field(name, True, existing.type or fld.type, existing.default)
            for c in sub_c:
                if c not in constraints:
                    constraints.append(c)
            for u in sub_u:
                if u not in unresolved:
                    unresolved.append(u)
    return fields, constraints, unresolved


def _branch_values(test: ast.AST, var: str) -> list[str]:
    """The discriminator values this ``if`` matches, if it tests the discriminator."""
    if isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == var:
        out: list[str] = []
        for op, cmp in zip(test.ops, test.comparators):
            if isinstance(op, ast.Eq) and isinstance(cmp, ast.Constant):
                if isinstance(cmp.value, str):
                    out.append(cmp.value)
            elif isinstance(op, ast.In) and isinstance(cmp, (ast.Tuple, ast.List, ast.Set)):
                out += [e.value for e in cmp.elts if isinstance(e, ast.Constant)]
        return out
    return []


def _find_discriminator(fn: ast.AST) -> tuple[str, str]:
    """(variable, dict key) of the value the dispatcher branches on.

    Chosen by BRANCH COUNT, not by name: ``services.options`` assigns
    ``op = change.get("type")``, so trusting the variable name would report the wrong key
    for a fifth of the ops."""
    candidates: dict[str, str] = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        for sub in ast.walk(node.value):
            key = _dict_get_key(sub, _param_name(fn) or "change")
            if key:
                candidates.setdefault(target.id, key)
                break
    best, best_key, best_n = "", "", 0
    for var, key in candidates.items():
        n = sum(len(_branch_values(i.test, var)) for i in ast.walk(fn) if isinstance(i, ast.If))
        if n > best_n:
            best, best_key, best_n = var, key, n
    return best, best_key


def _walk_branches(body: list[ast.stmt], var: str, out: list[tuple[list[str], list[ast.stmt]]]):
    """Collect (matched values, branch body) for every discriminator branch, incl. elif."""
    for stmt in body:
        if not isinstance(stmt, ast.If):
            continue
        values = _branch_values(stmt.test, var)
        if values:
            out.append((values, stmt.body))
        else:
            _walk_branches(stmt.body, var, out)
        _walk_branches(stmt.orelse, var, out)


# ── Public extraction ────────────────────────────────────────────────────────────────


def family_of(function_name: str) -> str:
    """``apply_budget_change`` -> ``budget``; ``apply_automation_change`` -> ``automation``."""
    return function_name[len(_CHOKE_PREFIX) : -len(_CHOKE_SUFFIX)]


def extract_choke(path: str, fn: ast.FunctionDef) -> Choke | None:
    """Derive every accepted op of one dispatch function."""
    var, key = _find_discriminator(fn)
    if not var:
        return None
    family = family_of(fn.name)
    module = _rel(path)
    choke = Choke(
        family=family,
        module=module,
        function=fn.name,
        discriminator=key,
        source=f"{module}:{fn.lineno}",
    )
    branches: list[tuple[list[str], list[ast.stmt]]] = []
    _walk_branches(fn.body, var, branches)
    param = _param_name(fn) or "change"

    # Reads that sit OUTSIDE any branch apply to every op BELOW them.
    # Lifecycle chokes commonly resolve ``entity_id = change.get("id")`` between the
    # create branch and the rest, so cancel/pause/resume look field-less unless the preamble
    # is folded in — but create returns before that line ever runs, so folding it in
    # unconditionally would have create demanding an ``id`` it never reads. Position decides.
    # Only the DISCRIMINATOR branches are excluded — a plain ``if`` in the preamble is
    # usually the shared guard ("look the id up, refuse if it is not there"), and dropping
    # every If dropped exactly those, leaving all four agent ops and all four
    # lifecycle ops reporting no required id at all.
    preamble = [s for s in fn.body if not (isinstance(s, ast.If) and _branch_values(s.test, var))]

    for values, body in branches:
        fields, constraints, unresolved = _harvest(body, {param}, path, 0, set())
        # The discriminator is the selector, not a payload field.
        fields.pop(key, None)
        line = body[0].lineno if body else fn.lineno
        above = [s for s in preamble if s.lineno < line]
        if above:
            common, common_c, _ = _harvest(above, {param}, path, 0, set())
            common.pop(key, None)
            for name, fld in common.items():
                fields.setdefault(name, fld)
            for c in common_c:
                if c not in constraints:
                    constraints.append(c)
        for value in values:
            choke.ops.append(
                Op(
                    family=family,
                    value=value,
                    discriminator=key,
                    fields=sorted(fields.values(), key=lambda f: (not f.required, f.name)),
                    constraints=constraints,
                    source=f"{module}:{line}",
                    unresolved=unresolved,
                )
            )
    return choke


def discover_chokes(root: str | None = None) -> list[Choke]:
    """Every ``apply_*_change`` dispatcher in backend/services/, derived from source.

    Discovery, not a registry: a new choke following the convention becomes describable
    with no edit to this module."""
    out: list[Choke] = []
    for path in sorted(pathlib.Path(root or _SERVICES_DIR).rglob("*.py")):
        if path.name.startswith("domain_introspect"):
            continue
        tree = _parse(str(path))
        if tree is None:
            continue
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if not (node.name.startswith(_CHOKE_PREFIX) and node.name.endswith(_CHOKE_SUFFIX)):
                continue
            if not _param_name(node):
                continue
            # One choke with an AST shape the extractor has not seen must not take down
            # describe() for the other nine. Losing one family degrades gracefully;
            # raising here would make the whole domain uninspectable.
            try:
                choke = extract_choke(str(path), node)
            except (RecursionError, AttributeError, TypeError, ValueError):
                logger.warning("describe: could not introspect %s in %s", node.name, path.name)
                continue
            if choke and choke.ops:
                out.append(choke)
    return sorted(out, key=lambda c: c.family)


def clear_cache() -> None:
    """Drop parsed-source caches (tests that rewrite a choke fixture need this)."""
    _parse.cache_clear()
    _module_constants.cache_clear()
    _module_path.cache_clear()
