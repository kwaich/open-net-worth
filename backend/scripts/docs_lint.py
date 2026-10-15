"""Corpus lint for ``docs/product/**/*.md`` — the CI-blocking gate on the frontmatter and
layout contract frozen in
``docs/superpowers/specs/2026-08-23-product-docs-and-launch-design.md`` (including its
2026-08-23 cross-link correction).

Unlike ``services/product_docs.py`` (which fails closed on the FIRST bad file, so a
broken corpus never serves half-parsed data at runtime), this script's job is the
opposite: visit EVERY file, collect EVERY problem, and report all of them at once with
``file:line`` — the fast feedback loop an author wants while writing, run as its own
pass (``make docs-lint``) rather than every time the app boots. Every message names the
file, the line, what's wrong, and (wherever there's a concrete fix) what it should be —
so a problem is fixable without opening the file.

Checks (one report line per problem, ``<path>:<line>: <message>``):
  - missing frontmatter block / invalid YAML
  - a missing OR invalid required key (title/description/category/order/audience/status)
  - a non-string entry in ``audience``/``related``/``keywords`` — an unquoted YAML
    scalar like ``8080`` in ``keywords: [..., 8080]`` parses as an int, which is fatal
    to the Astro docs-site content-collection build for the WHOLE collection, not just
    the offending page (a real incident, 2026-08-23)
  - ``category`` not equal to the parent directory
  - ``description`` over 160 chars
  - a ``related`` entry that isn't a real doc_id
  - an inline cross-link that is absolute (``/help/<doc_id>`` is explicitly FORBIDDEN —
    it resolves in the in-app viewer but 404s on the public site, which serves
    ``/<doc_id>`` with no ``/help`` prefix), missing its required ``.md`` extension, or
    doesn't resolve (relative to the LINKING file's own directory) to a real doc_id
  - an inline link's ``#anchor`` that doesn't match a real ``##`` heading slug in the
    target document (or the current one, for a same-page ``#anchor`` link) — computed
    with the EXACT SAME slug algorithm ``services.product_docs.get_section`` uses at
    runtime, imported from there rather than reimplemented, so "the linter says it
    resolves" and "read_doc(doc_id, section=...) actually finds it" can never disagree
  - a duplicate ``order`` within one category
  - a missing or duplicated H1
  - an H1 whose text doesn't match frontmatter ``title``
  - a page under ``features/`` with no ``status`` (redundant with the required-key check
    today, since ``status`` is required everywhere — kept as an explicit, named check so
    a future relaxation of that global requirement can't silently drop it for features/)

CROSS-LINK CONTRACT this linter enforces (frozen spec, "Cross-links (CORRECTED
2026-08-23)"): an inline cross-link is a RELATIVE path with a ``.md`` extension, e.g.
``[the ledger](../concepts/the-ledger.md)`` from ``features/budgeting.md``, or
``[Memory](memory.md)`` for a same-category sibling (confirmed against the real corpus:
same-category links are written bare/``./``-relative, cross-category links carry exactly
one ``../`` — the corpus is flat, one directory level, so a single ``../`` from ANY page
lands at the corpus root). ``related:`` frontmatter entries are the one exception — bare
``doc_id``s, no path, no extension, unchanged by this correction. An image reference
(``![alt](...)``) is not a cross-link and is never checked here (no doc_id to resolve).

Usage:
    .venv/bin/python3 -m backend.scripts.docs_lint
"""

from __future__ import annotations

import posixpath
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

import backend.services.product_docs as product_docs
from backend.core.paths import PRODUCT_DOCS_DIR

_REQUIRED_KEYS = ("title", "description", "category", "order", "audience", "status")
_AUDIENCES = ("user", "operator", "developer")
_STATUSES = ("stable", "beta", "coming-soon")
_CATEGORIES = (
    "getting-started",
    "concepts",
    "features",
    "lucius",
    "connectors",
    "operations",
    "troubleshooting",
    "reference",
)
_MAX_DESCRIPTION_CHARS = 160
_STRING_LIST_FIELDS = ("audience", "related", "keywords")

_FRONTMATTER_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?(.*)$", re.DOTALL)
_H1_RE = re.compile(r"^#[ \t]+(.*\S)[ \t]*$")
_FENCE_RE = re.compile(r"^(```+|~~~+)")
# Group 1: '!' for an image (skipped — not a cross-link). Group 2: the link target.
_LINK_RE = re.compile(r"(!?)\[[^\]]*\]\(([^)]+)\)")
_URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


class _Problems(list):
    def add(self, rel: Path, line: int, message: str) -> None:
        self.append(f"{rel}:{line}: {message}")


@dataclass
class _ParsedFile:
    rel: Path
    category_dir: str
    doc_id: str
    meta: dict
    raw_frontmatter: str
    body: str
    body_line_offset: int  # file-line number just BEFORE the body's own line 1


def _scan_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.md") if p.is_file())


def _find_key_line(raw_frontmatter: str, key: str) -> int:
    """1-based FILE line for ``key:`` inside the frontmatter block, or line 1 (the
    opening ``---``) when the key is simply absent."""
    for i, line in enumerate(raw_frontmatter.splitlines()):
        if line.strip().startswith(f"{key}:"):
            return i + 2  # +1 to 1-index, +1 for the opening '---' line
    return 1


def _h1_lines(body: str) -> list[tuple[str, int]]:
    """``(heading text, body-relative 1-based line)`` for every top-level ``#`` heading,
    skipping anything inside a fenced code block."""
    results: list[tuple[str, int]] = []
    in_fence = False
    fence_marker = ""
    for i, line in enumerate(body.splitlines(), start=1):
        stripped = line.strip()
        fence = _FENCE_RE.match(stripped)
        if fence:
            marker = fence.group(1)[:3]
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence, fence_marker = False, ""
            continue
        if in_fence:
            continue
        m = _H1_RE.match(line)
        if m:
            results.append((m.group(1).strip(), i))
    return results


def _parse_file(root: Path, path: Path, problems: _Problems) -> Optional[_ParsedFile]:
    rel = path.relative_to(root)
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text.lstrip("﻿"))
    if not m:
        problems.add(rel, 1, "missing YAML frontmatter (expected a leading '---' block)")
        return None
    raw_fm = m.group(1)
    try:
        meta = yaml.safe_load(raw_fm)
    except yaml.YAMLError as exc:
        problems.add(rel, 1, f"invalid YAML frontmatter ({exc})")
        return None
    if not isinstance(meta, dict):
        problems.add(rel, 1, "frontmatter must be a YAML mapping")
        return None
    if len(rel.parts) != 2:
        problems.add(rel, 1, "a doc must live directly under exactly one category directory")
        return None

    category_dir, filename = rel.parts
    raw_body = m.group(2)
    body = raw_body.lstrip("\n")
    # The newline count in the file BEFORE body's text starts — added to a body-relative
    # line number (both 1-based) to report the real file line. Accounts for any blank
    # lines the lstrip above dropped, so it stays in sync with the STRIPPED body.
    leading_blank_lines = len(raw_body) - len(body)
    body_line_offset = text[: m.start(2)].count("\n") + leading_blank_lines
    return _ParsedFile(
        rel=rel,
        category_dir=category_dir,
        doc_id=f"{category_dir}/{filename[:-3]}",
        meta=meta,
        raw_frontmatter=raw_fm,
        body=body,
        body_line_offset=body_line_offset,
    )


def _check_string_list_entries(pf: _ParsedFile, key: str, problems: _Problems) -> None:
    """Every entry in a list-shaped frontmatter field must be a STRING — an unquoted
    numeric-looking YAML scalar (``8080``, ``5174``) parses as an int and is fatal to
    the Astro docs-site build for the whole content collection, not just this page."""
    value = pf.meta.get(key)
    if not isinstance(value, list):
        return
    line = _find_key_line(pf.raw_frontmatter, key)
    for v in value:
        if not isinstance(v, str):
            problems.add(
                pf.rel,
                line,
                f"{key!r} contains a non-string entry {v!r} ({type(v).__name__}) — quote it "
                f'in YAML, e.g. "{v}"',
            )


def _check_required_keys(pf: _ParsedFile, problems: _Problems) -> None:
    for key in _REQUIRED_KEYS:
        if key not in pf.meta or pf.meta[key] in (None, ""):
            problems.add(
                pf.rel, _find_key_line(pf.raw_frontmatter, key), f"missing required key {key!r}"
            )
    if pf.category_dir == "features" and not pf.meta.get("status"):
        problems.add(
            pf.rel,
            _find_key_line(pf.raw_frontmatter, "status"),
            "a features/ page must declare 'status'",
        )
    for key in _STRING_LIST_FIELDS:
        _check_string_list_entries(pf, key, problems)


def _check_field_values(pf: _ParsedFile, problems: _Problems) -> None:
    meta, rel, raw_fm = pf.meta, pf.rel, pf.raw_frontmatter

    description = meta.get("description")
    if isinstance(description, str) and len(description) > _MAX_DESCRIPTION_CHARS:
        problems.add(
            rel,
            _find_key_line(raw_fm, "description"),
            f"description is {len(description)} chars, over the {_MAX_DESCRIPTION_CHARS}-char "
            "limit",
        )

    category = meta.get("category")
    if category is not None and category != pf.category_dir:
        problems.add(
            rel,
            _find_key_line(raw_fm, "category"),
            f"category {category!r} does not match parent directory {pf.category_dir!r}",
        )
    elif category is not None and category not in _CATEGORIES:
        problems.add(rel, _find_key_line(raw_fm, "category"), f"unknown category {category!r}")

    order = meta.get("order")
    if order is not None and (not isinstance(order, int) or isinstance(order, bool)):
        problems.add(rel, _find_key_line(raw_fm, "order"), "'order' must be an integer")

    audience = meta.get("audience")
    if isinstance(audience, list):
        for a in audience:
            if isinstance(a, str) and a not in _AUDIENCES:
                problems.add(rel, _find_key_line(raw_fm, "audience"), f"unknown audience {a!r}")
    elif audience is not None:
        problems.add(rel, _find_key_line(raw_fm, "audience"), "'audience' must be a list")

    status = meta.get("status")
    if status is not None and status not in _STATUSES:
        problems.add(rel, _find_key_line(raw_fm, "status"), f"unknown status {status!r}")


def _check_h1(pf: _ParsedFile, problems: _Problems) -> None:
    headings = _h1_lines(pf.body)
    if not headings:
        problems.add(pf.rel, 1, "missing H1 (expected exactly one leading '# Title' line)")
        return
    if len(headings) > 1:
        for _text, line in headings[1:]:
            problems.add(
                pf.rel,
                pf.body_line_offset + line,
                "duplicate H1 — exactly one '#' heading is allowed per page",
            )
    text, line = headings[0]
    title = pf.meta.get("title")
    if isinstance(title, str) and text != title.strip():
        problems.add(
            pf.rel,
            pf.body_line_offset + line,
            f"H1 {text!r} does not match frontmatter title {title!r}",
        )


# ── Cross-links (relative-.md contract, 2026-08-23 correction) ────────────────────────


def _relative_md_suggestion(from_category: str, target_doc_id: str) -> str:
    """The correctly-shaped relative link from a page in ``from_category`` to
    ``target_doc_id`` — mirrors the corpus's own two accepted forms."""
    target_category, _, target_page = target_doc_id.partition("/")
    if target_category == from_category:
        return f"{target_page}.md"
    return f"../{target_doc_id}.md"


def _check_anchor(
    pf: _ParsedFile,
    raw_target: str,
    line: int,
    target_doc_id: str,
    anchor: str,
    doc_anchors: dict[str, frozenset],
    problems: _Problems,
) -> None:
    if not anchor:
        return
    anchors = doc_anchors.get(target_doc_id, frozenset())
    if anchor in anchors:
        return
    available = ", ".join(sorted(anchors)[:8]) if anchors else "(no ## sections)"
    problems.add(
        pf.rel,
        line,
        f"internal link '{raw_target}' targets doc '{target_doc_id}', which has no '##' "
        f"section anchored '{anchor}' — available: {available}",
    )


def _check_absolute_link(
    pf: _ParsedFile,
    raw_target: str,
    path_part: str,
    line: int,
    doc_anchors: dict[str, frozenset],
    problems: _Problems,
) -> None:
    is_help = path_part.startswith("/help/")
    doc_id = (path_part[len("/help/") :] if is_help else path_part[1:]).rstrip("/")
    fix = (
        f"rewrite as a relative link: '{_relative_md_suggestion(pf.category_dir, doc_id)}'"
        if doc_id in doc_anchors
        else "rewrite as a relative '.md' link to an existing page"
    )
    kind = "absolute '/help/' link" if is_help else "absolute link"
    problems.add(
        pf.rel,
        line,
        f"{kind} '{raw_target}' is forbidden — inline cross-links must be relative — {fix}",
    )


def _check_one_inline_link(
    pf: _ParsedFile,
    raw_target: str,
    line: int,
    doc_anchors: dict[str, frozenset],
    problems: _Problems,
) -> None:
    target = raw_target.split(" ", 1)[0].strip().strip("<>")
    if not target or _URL_SCHEME_RE.match(target):
        return  # empty capture, or an external URL/mailto — out of scope

    path_part, _, anchor = target.partition("#")

    if not path_part:  # same-page anchor only, e.g. [x](#some-section)
        _check_anchor(pf, raw_target, line, pf.doc_id, anchor, doc_anchors, problems)
        return

    if path_part.startswith("/"):
        _check_absolute_link(pf, raw_target, path_part, line, doc_anchors, problems)
        return

    if not path_part.endswith(".md"):
        problems.add(
            pf.rel,
            line,
            f"internal link '{raw_target}' is missing the required '.md' extension — should "
            f"be '{path_part}.md'",
        )

    stem = path_part[: -len(".md")] if path_part.endswith(".md") else path_part
    resolved = posixpath.normpath(posixpath.join(pf.category_dir, stem))
    if resolved not in doc_anchors:
        problems.add(
            pf.rel,
            line,
            f"internal link '{raw_target}' resolves to doc_id '{resolved}', which does not exist",
        )
        return

    _check_anchor(pf, raw_target, line, resolved, anchor, doc_anchors, problems)


def _check_links(pf: _ParsedFile, doc_anchors: dict[str, frozenset], problems: _Problems) -> None:
    related = pf.meta.get("related")
    if isinstance(related, list):
        for target in related:
            if isinstance(target, str) and target not in doc_anchors:
                problems.add(
                    pf.rel,
                    _find_key_line(pf.raw_frontmatter, "related"),
                    f"related entry '{target}' is not a known doc_id",
                )

    for i, line_text in enumerate(pf.body.splitlines(), start=1):
        for is_image, raw_target in _LINK_RE.findall(line_text):
            if is_image:
                continue  # ![alt](...) — an asset reference, not a cross-link
            _check_one_inline_link(pf, raw_target, pf.body_line_offset + i, doc_anchors, problems)


def _check_duplicate_orders(files: list[_ParsedFile], problems: _Problems) -> None:
    by_category: dict[str, dict[int, list[Path]]] = {}
    for pf in files:
        order = pf.meta.get("order")
        if isinstance(order, int) and not isinstance(order, bool):
            by_category.setdefault(pf.category_dir, {}).setdefault(order, []).append(pf.rel)
    for category_dir, by_order in by_category.items():
        for order, paths in by_order.items():
            if len(paths) > 1:
                others = ", ".join(str(p) for p in paths[1:])
                problems.add(
                    paths[0],
                    1,
                    f"duplicate order={order} in category {category_dir!r}, shared with {others}",
                )


def lint_corpus(root: Path) -> list[str]:
    """Every problem found in the corpus rooted at ``root``, as ``"path:line: message"``
    strings. Empty list = clean (including an empty/missing corpus)."""
    problems = _Problems()
    parsed: list[_ParsedFile] = []
    for path in _scan_files(root):
        pf = _parse_file(root, path, problems)
        if pf is None:
            continue
        _check_required_keys(pf, problems)
        _check_field_values(pf, problems)
        parsed.append(pf)

    # Anchor computation reuses services/product_docs.py's OWN slug algorithm — so "the
    # linter says this #anchor resolves" and "get_section() actually finds it at
    # runtime" can never disagree.
    doc_anchors: dict[str, frozenset] = {}
    for pf in parsed:
        headings = product_docs._extract_h2_headings(pf.body)  # noqa: SLF001 — deliberate reuse
        anchors = product_docs._assign_anchors([h for h, _off in headings])  # noqa: SLF001
        doc_anchors[pf.doc_id] = frozenset(anchors)

    for pf in parsed:
        _check_h1(pf, problems)
        _check_links(pf, doc_anchors, problems)
    _check_duplicate_orders(parsed, problems)

    return sorted(problems)


def main() -> int:
    problems = lint_corpus(PRODUCT_DOCS_DIR)
    if not problems:
        print(f"docs_lint: clean ({PRODUCT_DOCS_DIR})")
        return 0
    for p in problems:
        print(p)
    print(f"\nFAIL: {len(problems)} problem(s) in {PRODUCT_DOCS_DIR}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
