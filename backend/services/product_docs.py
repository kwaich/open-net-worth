"""Parse, index, and search the product documentation corpus (``docs/product/``).

The one-corpus rule (``docs/superpowers/specs/2026-08-23-product-docs-and-launch-design.md``):
``docs/product/**/*.md`` is the single source of truth. This module is the only reader of
that tree — the Lucius tools (``ai/tools/docs.py``) and the in-app Help route
(``api/product_docs.py``) both call it rather than reading the filesystem themselves. Pure,
import-clean: no store access, no network, no state beyond an in-process cache.

SECURITY (the point of this module). A ``doc_id`` arrives from a language model and from a
URL path — both untrusted. It is NEVER used to build a filesystem path. Instead, the corpus
is scanned once at index time into a dict keyed by every valid ``doc_id`` discovered on disk;
every lookup below is a plain dict ``get()`` against that closed set. A ``doc_id`` outside the
set — ``../../etc/passwd``, an absolute path, a null byte, a case-mismatched id, URL-encoded
traversal — simply is not a key, so it can never reach the filesystem and always resolves to
``NotFoundError``. A symlink discovered during the scan that resolves outside the corpus root
is dropped during scanning itself (``_scan_corpus``), so it never becomes a doc_id at all.

INDEXING. The index (entries + the search index built over them) is cached per corpus root
and invalidated on a cheap signature (every ``*.md`` path + its mtime) recomputed on every
call — so an author editing the corpus sees changes without restarting the process, at the
cost of one filesystem walk per call. Rebuilding the (more expensive) parsed index only
happens when that signature actually changes.

FAIL-CLOSED. A malformed corpus file (missing/invalid frontmatter, wrong nesting, a stray
duplicate doc_id) raises ``DocsCorpusError`` and blocks indexing of the WHOLE corpus, rather
than silently dropping the one bad page — the same fail-closed posture every other
file-backed store in this app takes on a corrupt file (ADR
``2026-07-09-jsonstore-fail-closed-on-corrupt``). The corpus linter
(``backend/scripts/docs_lint.py``) exists precisely so this never happens on a merged commit.
An EMPTY corpus (no ``*.md`` files at all, or a directory that does not exist yet) is not an
error — every function below degrades to empty results.
"""

from __future__ import annotations

import logging
import math
import re
import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from backend.core.errors import DataIntegrityError, NotFoundError
from backend.core.paths import PRODUCT_DOCS_DIR

logger = logging.getLogger(__name__)

# ── Corpus contract constants (frozen — see the design spec) ──────────────────────────

CATEGORY_TITLES: dict[str, str] = {
    "getting-started": "Getting Started",
    "concepts": "Concepts",
    "features": "Features",
    "lucius": "Lucius",
    "connectors": "Connectors",
    "operations": "Operations",
    "troubleshooting": "Troubleshooting",
    "reference": "Reference",
}
# One line per category, shown on the Help landing's "Browse the docs" grid.
# Kept BYTE-IDENTICAL to `docs-site/src/pages/index.astro`'s own `categoryBlurbs`, which is
# the same grid on the published site. The two tables are separate because docs-site is an
# Astro build that reads the markdown directly and never calls this API — the same reason
# CATEGORY_TITLES above is already duplicated there as `CATEGORIES`. If you edit one, edit
# both; `tests/test_product_docs.py` pins that every category here has a blurb.
CATEGORY_BLURBS: dict[str, str] = {
    "getting-started": "Install it, run it for the first time, add your first account.",
    "concepts": "The mental model: ledger, accounts, categories, envelopes, base currency.",
    "features": "One page per feature — budgeting, investments, planning, and more.",
    "lucius": "What the AI advisor can do, its permissions, and its memory.",
    "connectors": "Email ingest, Telegram, MCP, and price-source integrations.",
    "operations": "Backup and restore, upgrading, security and privacy, performance.",
    "troubleshooting": "Symptom-first fixes and a frequently-asked-questions page.",
    "reference": "Environment variables, ports, file layout, API surface, glossary.",
}
_CATEGORY_ORDER: tuple[str, ...] = tuple(CATEGORY_TITLES)  # canonical Layout order
_CATEGORY_RANK: dict[str, int] = {cat_id: i for i, cat_id in enumerate(_CATEGORY_ORDER)}

_AUDIENCES = ("user", "operator", "developer")
_STATUSES = ("stable", "beta", "coming-soon")
_REQUIRED_KEYS = ("title", "description", "category", "order", "audience", "status")
_MAX_DESCRIPTION_CHARS = 160


class DocsCorpusError(DataIntegrityError):
    """A ``docs/product/`` markdown file's frontmatter is missing/invalid, or the corpus
    layout violates the frozen contract (nesting depth, category/directory mismatch,
    duplicate doc_id). Names the offending file (repo-relative) and the problem in the
    message. Fail-closed by design — see the module docstring."""


# ── Public shapes ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Section:
    anchor: str
    heading: str
    char_offset: int  # into the doc's markdown BODY (post-frontmatter)


@dataclass(frozen=True)
class DocMeta:
    doc_id: str
    title: str
    description: str
    category: str
    order: int
    audience: tuple[str, ...]
    status: str
    related: tuple[str, ...]
    keywords: tuple[str, ...]
    sections: tuple[Section, ...]


@dataclass(frozen=True)
class Doc(DocMeta):
    markdown: str  # full body, post-frontmatter


@dataclass(frozen=True)
class CategoryInfo:
    id: str
    title: str
    order: int
    description: str


@dataclass(frozen=True)
class SearchHit:
    doc_id: str
    title: str
    category: str
    status: str
    anchor: Optional[str]
    heading: Optional[str]
    snippet: str
    score: float


@dataclass(frozen=True)
class SearchResult:
    hits: tuple[SearchHit, ...]
    showing: int
    total: int


# ── Frontmatter parse + validation ─────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?(.*)$", re.DOTALL)


def _parse_frontmatter(text: str, rel_path: str) -> tuple[dict, str]:
    m = _FRONTMATTER_RE.match(text.lstrip("﻿"))
    if not m:
        raise DocsCorpusError(
            f"{rel_path}: missing YAML frontmatter (expected a leading '---' block)"
        )
    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError as exc:
        raise DocsCorpusError(f"{rel_path}: invalid YAML frontmatter ({exc})") from exc
    if not isinstance(meta, dict):
        raise DocsCorpusError(f"{rel_path}: frontmatter must be a YAML mapping")
    return meta, m.group(2).lstrip("\n")


def _require_str(meta: dict, key: str, rel_path: str, *, max_chars: Optional[int] = None) -> str:
    value = meta[key]
    if not isinstance(value, str) or not value.strip():
        raise DocsCorpusError(f"{rel_path}: frontmatter {key!r} must be a non-empty string")
    if max_chars is not None and len(value) > max_chars:
        # A style guideline (search-snippet length), not structure: warn and trim (``_fit``),
        # never refuse the corpus. At 161 chars one page once took search_docs offline
        # (2026-10-03). The linter (backend/scripts/docs_lint.py) still flags it at commit time.
        logger.warning(
            "product_docs: %s %r is %d chars, over the %d-char guideline",
            rel_path,
            key,
            len(value),
            max_chars,
        )
    return value


def _fit(value: str, max_chars: int) -> str:
    """Trim an over-long guideline field to ``max_chars``, ending in an ellipsis."""
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "…"


def _require_str_list(meta: dict, key: str, rel_path: str, *, allow_empty: bool) -> list[str]:
    value = meta.get(key) if allow_empty else meta[key]
    value = value or ([] if allow_empty else value)
    if not isinstance(value, list) or (not allow_empty and not value):
        empty_note = "a list of strings" if allow_empty else "a non-empty list"
        raise DocsCorpusError(f"{rel_path}: frontmatter {key!r} must be {empty_note}")
    if not all(isinstance(v, str) for v in value):
        raise DocsCorpusError(f"{rel_path}: frontmatter {key!r} must be a list of strings")
    return value


def _validate_frontmatter(meta: dict, rel_path: str, expected_category: str) -> None:
    for key in _REQUIRED_KEYS:
        if key not in meta or meta[key] in (None, ""):
            raise DocsCorpusError(f"{rel_path}: frontmatter is missing required key {key!r}")

    _require_str(meta, "title", rel_path)
    _require_str(meta, "description", rel_path, max_chars=_MAX_DESCRIPTION_CHARS)

    category = meta["category"]
    if category != expected_category:
        raise DocsCorpusError(
            f"{rel_path}: frontmatter 'category' ({category!r}) does not match its "
            f"parent directory ({expected_category!r})"
        )
    if category not in CATEGORY_TITLES:
        raise DocsCorpusError(
            f"{rel_path}: unknown category {category!r} — expected one of {sorted(CATEGORY_TITLES)}"
        )

    order = meta["order"]
    if not isinstance(order, int) or isinstance(order, bool):
        raise DocsCorpusError(f"{rel_path}: frontmatter 'order' must be an integer")

    for a in _require_str_list(meta, "audience", rel_path, allow_empty=False):
        if a not in _AUDIENCES:
            raise DocsCorpusError(
                f"{rel_path}: unknown audience {a!r} — expected one of {_AUDIENCES}"
            )

    status = meta["status"]
    if status not in _STATUSES:
        raise DocsCorpusError(
            f"{rel_path}: unknown status {status!r} — expected one of {_STATUSES}"
        )

    _require_str_list(meta, "related", rel_path, allow_empty=True)
    _require_str_list(meta, "keywords", rel_path, allow_empty=True)


# ── Heading / anchor extraction (GitHub-style slugs) ───────────────────────────────────

_H2_RE = re.compile(r"^##[ \t]+(.*\S)[ \t]*$")
_FENCE_RE = re.compile(r"^(```+|~~~+)")
# github-slugger's two-step rule, which Astro/Starlight also uses. Step 1 REMOVES
# punctuation outright (an apostrophe must vanish, not become a separator); step 2 turns
# EACH space into its own "-" — runs are NOT collapsed (see _slugify's docstring).
# Hyphens, underscores and unicode letters survive.
_SLUG_STRIP_RE = re.compile(r"[^\w\s-]", re.UNICODE)


def _slugify(heading: str) -> str:
    """GitHub heading slug, matching ``github-slugger`` — which is what Astro/Starlight
    uses to mint the published site's heading ids.

    The rule that matters and is easy to get wrong: punctuation is REMOVED, not turned
    into a separator. ``What's excluded`` is ``whats-excluded``, never
    ``what-s-excluded``. Only whitespace becomes ``-``.

    This function is the single source of truth for anchors across all three consumers
    (the AI tool's ``read_doc(section=...)``, the in-app Help TOC, and the corpus
    linter). If it disagrees with the static site's slugger, an author writes a link
    that works in one place and 404s in the other — and the linter confidently reports
    the wrong one as broken.
    """
    text = _SLUG_STRIP_RE.sub("", heading.strip().lower())
    # Each space becomes its own "-": github-slugger does NOT collapse runs and does NOT
    # trim. "Option A - one command" (em dash) loses the dash but keeps BOTH spaces
    # around it, so the real anchor is "option-a--one-command" with two hyphens. Collapsing
    # or trimming here silently desynchronises every anchor from the published site.
    return text.replace(" ", "-")


def _assign_anchors(headings: list[str]) -> list[str]:
    """Duplicate headings get a -1/-2/... suffix, matching GitHub's own behavior — the
    first occurrence of a slug stays bare.

    Matches ``github-slugger`` exactly, including its edge case: the suffix loop keeps
    incrementing until the candidate is a genuinely UNUSED anchor, not just an unused
    base slug. Without that loop, ``## Foo``, ``## Foo``, ``## Foo 1`` would anchor both
    the second ``Foo`` AND the literal ``Foo 1`` heading to ``foo-1`` — a real collision,
    since the counter-driven suffix of one heading can coincide with another heading's
    own natural slug.
    """
    used: set[str] = set()
    counters: dict[str, int] = {}
    anchors = []
    for h in headings:
        base = _slugify(h)
        candidate = base
        while candidate in used:
            counters[base] = counters.get(base, 0) + 1
            candidate = f"{base}-{counters[base]}"
        used.add(candidate)
        anchors.append(candidate)
    return anchors


def _extract_h2_headings(body: str) -> list[tuple[str, int]]:
    """``(heading text, char offset into body)`` for every ``##`` heading, skipping any
    that fall inside a fenced code block."""
    results: list[tuple[str, int]] = []
    in_fence = False
    fence_marker = ""
    offset = 0
    for line in body.splitlines(keepends=True):
        stripped = line.strip()
        fence = _FENCE_RE.match(stripped)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence, fence_marker = True, marker[:3]
            elif marker[:3] == fence_marker:
                in_fence, fence_marker = False, ""
        elif not in_fence:
            m = _H2_RE.match(line.rstrip("\n").rstrip("\r"))
            if m:
                results.append((m.group(1).strip(), offset))
        offset += len(line)
    return results


# ── Corpus scan (the security boundary) ─────────────────────────────────────────────────


def _scan_corpus(root: Path) -> list[Path]:
    """Every ``*.md`` file under ``root``, EXCLUDING anything whose resolved (symlinks
    followed) location falls outside ``root`` — the one place a corpus-authored symlink
    could otherwise be used to smuggle an out-of-tree file into the doc_id set. A broken
    symlink is skipped, never raised."""
    if not root.exists():
        return []
    try:
        root_resolved = root.resolve(strict=True)
    except OSError:
        return []
    found: list[Path] = []
    for path in sorted(root.rglob("*.md")):
        if not path.is_file():
            continue
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue  # broken symlink target — skip rather than crash
        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            continue  # resolves OUTSIDE the corpus root — refuse (symlink escape)
        found.append(path)
    return found


def _quick_signature(root: Path) -> tuple[tuple[str, float], ...]:
    """Cheap invalidation key: every scanned file's path + mtime. No parsing."""
    sig: list[tuple[str, float]] = []
    for p in _scan_corpus(root):
        try:
            mtime = p.stat().st_mtime
        except OSError as exc:
            # A raw OSError's str() carries the full absolute path (leaking the OS
            # username via the home directory) — re-raise repo-relative only, same
            # as every other corpus-parsing failure (security review).
            raise DocsCorpusError(
                f"{p.relative_to(root).as_posix()}: {exc.strerror or exc}"
            ) from exc
        sig.append((str(p), mtime))
    return tuple(sorted(sig))


@dataclass(frozen=True)
class _CorpusEntry:
    meta: DocMeta
    markdown: str


def _build_entries(root: Path) -> dict[str, "_CorpusEntry"]:
    entries: dict[str, _CorpusEntry] = {}
    for path in _scan_corpus(root):
        rel = path.relative_to(root)
        if len(rel.parts) != 2:
            raise DocsCorpusError(
                f"{rel.as_posix()}: a doc must live directly under exactly one category "
                "directory (docs/product/<category>/<page>.md)"
            )
        category_dir, filename = rel.parts
        doc_id = f"{category_dir}/{filename[:-3]}"  # strip '.md' (guaranteed by rglob)

        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            # Same repo-relative-only rule as every other failure in this function — a
            # raw OSError's str() carries the full absolute path (security review).
            raise DocsCorpusError(f"{rel.as_posix()}: {exc.strerror or exc}") from exc
        meta_raw, body = _parse_frontmatter(text, rel.as_posix())
        _validate_frontmatter(meta_raw, rel.as_posix(), category_dir)

        headings = _extract_h2_headings(body)
        anchors = _assign_anchors([h for h, _off in headings])
        sections = tuple(
            Section(anchor=a, heading=h, char_offset=off) for a, (h, off) in zip(anchors, headings)
        )

        meta = DocMeta(
            doc_id=doc_id,
            title=meta_raw["title"].strip(),
            description=_fit(meta_raw["description"].strip(), _MAX_DESCRIPTION_CHARS),
            category=category_dir,
            order=int(meta_raw["order"]),
            audience=tuple(meta_raw["audience"]),
            status=meta_raw["status"],
            related=tuple(meta_raw.get("related") or []),
            keywords=tuple(meta_raw.get("keywords") or []),
            sections=sections,
        )
        if doc_id in entries:
            raise DocsCorpusError(f"{rel.as_posix()}: duplicate doc_id {doc_id!r}")
        entries[doc_id] = _CorpusEntry(meta=meta, markdown=body)
    return entries


# ── Search index (deterministic BM25-ish, per-field weights) ──────────────────────────
# No embeddings, no network, no new dependency — classic BM25 computed independently per
# field (title/description/keywords/headings/body), then combined with explicit field
# weights. k1/b are the standard textbook defaults; weights favor a match in the title or
# an author-declared keyword over an incidental mention in the body.

_FIELD_WEIGHTS: dict[str, float] = {
    "title": 5.0,
    "description": 3.0,
    "keywords": 4.0,
    "headings": 2.0,
    "body": 1.0,
}
_BM25_K1 = 1.5
_BM25_B = 0.75

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass(frozen=True)
class _FieldStats:
    doc_freq: dict[str, int]  # term -> number of docs with >=1 occurrence in this field
    lengths: dict[str, int]  # doc_id -> token count in this field
    avg_len: float


@dataclass(frozen=True)
class _SearchIndex:
    entries: dict[str, _CorpusEntry]
    field_tokens: dict[str, dict[str, list[str]]]  # field -> doc_id -> tokens
    field_stats: dict[str, _FieldStats]
    n_docs: int


def _build_search_index(entries: dict[str, _CorpusEntry]) -> _SearchIndex:
    field_tokens: dict[str, dict[str, list[str]]] = {f: {} for f in _FIELD_WEIGHTS}
    for doc_id, entry in entries.items():
        m = entry.meta
        field_tokens["title"][doc_id] = _tokenize(m.title)
        field_tokens["description"][doc_id] = _tokenize(m.description)
        field_tokens["keywords"][doc_id] = _tokenize(" ".join(m.keywords))
        field_tokens["headings"][doc_id] = _tokenize(" ".join(s.heading for s in m.sections))
        field_tokens["body"][doc_id] = _tokenize(entry.markdown)

    n_docs = len(entries)
    field_stats: dict[str, _FieldStats] = {}
    for field, per_doc in field_tokens.items():
        lengths = {doc_id: len(tokens) for doc_id, tokens in per_doc.items()}
        doc_freq: dict[str, int] = {}
        for tokens in per_doc.values():
            for t in set(tokens):
                doc_freq[t] = doc_freq.get(t, 0) + 1
        avg_len = (sum(lengths.values()) / n_docs) if n_docs else 0.0
        field_stats[field] = _FieldStats(doc_freq=doc_freq, lengths=lengths, avg_len=avg_len)

    return _SearchIndex(
        entries=entries, field_tokens=field_tokens, field_stats=field_stats, n_docs=n_docs
    )


def _bm25_term_score(term: str, tf: int, length: int, stats: _FieldStats, n_docs: int) -> float:
    if tf == 0:
        return 0.0
    df = stats.doc_freq.get(term, 0)
    idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
    norm = length / stats.avg_len if stats.avg_len else 1.0
    denom = tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * norm)
    return idf * (tf * (_BM25_K1 + 1)) / denom


_SNIPPET_RADIUS = 80


def _find_section_for_offset(sections: tuple[Section, ...], offset: int) -> Optional[Section]:
    match: Optional[Section] = None
    for s in sections:
        if s.char_offset <= offset:
            match = s
        else:
            break
    return match


def _snippet_around(text: str, positions: list[int]) -> str:
    if not positions:
        return text[:160].strip()
    pos = positions[0]
    start = max(0, pos - _SNIPPET_RADIUS)
    end = min(len(text), pos + _SNIPPET_RADIUS)
    snippet = " ".join(text[start:end].split())
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{snippet}{suffix}"


def _best_section_and_snippet(
    entry: _CorpusEntry, terms: list[str]
) -> tuple[Optional[str], Optional[str], str]:
    body_lower = entry.markdown.lower()
    positions = sorted(idx for idx in (body_lower.find(t) for t in terms) if idx != -1)
    if not positions:
        return None, None, entry.meta.description
    section = _find_section_for_offset(entry.meta.sections, positions[0])
    snippet = _snippet_around(entry.markdown, positions)
    if section is not None:
        return section.anchor, section.heading, snippet
    return None, None, snippet


# ── Cache (mtime-invalidated) ────────────────────────────────────────────────────────

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[tuple[tuple[str, float], ...], _SearchIndex]] = {}


def _get_search_index(root: Path) -> _SearchIndex:
    key = str(root)
    sig = _quick_signature(root)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None and cached[0] == sig:
            return cached[1]
    entries = _build_entries(root)  # raises DocsCorpusError on a malformed corpus
    index = _build_search_index(entries)
    with _CACHE_LOCK:
        _CACHE[key] = (sig, index)
    return index


def _get_entries(root: Path) -> dict[str, _CorpusEntry]:
    return _get_search_index(root).entries


# ── Public API ──────────────────────────────────────────────────────────────────────


def list_docs(category: Optional[str] = None) -> list[DocMeta]:
    """Every doc, optionally filtered to one category, sorted by (category's canonical
    Layout rank, frontmatter order, doc_id) — the sidebar order the corpus contract
    defines. NOT alphabetical by category — 'features' must not sort before
    'getting-started' just because 'f' < 'g'."""
    entries = _get_entries(PRODUCT_DOCS_DIR)
    metas = [e.meta for e in entries.values()]
    if category is not None:
        metas = [m for m in metas if m.category == category]
    metas.sort(
        key=lambda m: (_CATEGORY_RANK.get(m.category, len(_CATEGORY_ORDER)), m.order, m.doc_id)
    )
    return metas


def list_categories() -> list[CategoryInfo]:
    """Categories actually present in the corpus, in the corpus contract's fixed Layout
    order (Getting Started -> ... -> Reference) — never alphabetical, so the sidebar
    always reads as the onboarding journey the layout was designed around. A category
    with zero pages is omitted rather than shown empty."""
    present = {m.category for m in list_docs()}
    return [
        CategoryInfo(
            id=cat_id,
            title=CATEGORY_TITLES[cat_id],
            order=(i + 1) * 10,
            description=CATEGORY_BLURBS.get(cat_id, ""),
        )
        for i, cat_id in enumerate(_CATEGORY_ORDER)
        if cat_id in present
    ]


def get_doc(doc_id: str) -> Doc:
    """One doc by id. ``doc_id`` is resolved against the corpus-scanned id set ONLY — see
    the module docstring's SECURITY note. Raises ``NotFoundError`` for anything else."""
    entries = _get_entries(PRODUCT_DOCS_DIR)
    entry = entries.get(doc_id)
    if entry is None:
        raise NotFoundError(f"no such doc: {doc_id!r}", doc_id=doc_id)
    m = entry.meta
    return Doc(
        doc_id=m.doc_id,
        title=m.title,
        description=m.description,
        category=m.category,
        order=m.order,
        audience=m.audience,
        status=m.status,
        related=m.related,
        keywords=m.keywords,
        sections=m.sections,
        markdown=entry.markdown,
    )


def get_section(doc_id: str, anchor: str) -> str:
    """The ``##`` block for ``anchor`` (heading line through — but not including — the
    next ``##``, or end of doc). Raises ``NotFoundError`` for an unknown doc_id OR an
    unknown anchor within a known doc."""
    entries = _get_entries(PRODUCT_DOCS_DIR)
    entry = entries.get(doc_id)
    if entry is None:
        raise NotFoundError(f"no such doc: {doc_id!r}", doc_id=doc_id)
    sections = entry.meta.sections
    idx = next((i for i, s in enumerate(sections) if s.anchor == anchor), None)
    if idx is None:
        raise NotFoundError(
            f"no such section {anchor!r} in {doc_id!r}",
            doc_id=doc_id,
            anchor=anchor,
            available=[s.anchor for s in sections],
        )
    start = sections[idx].char_offset
    end = sections[idx + 1].char_offset if idx + 1 < len(sections) else len(entry.markdown)
    return entry.markdown[start:end].strip("\n")


def search(query: str, category: Optional[str] = None, limit: int = 5) -> SearchResult:
    """Ranked hits over title/description/keywords/headings/body (explicit field
    weights, BM25 per field). Deterministic: ties break on doc_id ascending. Reports
    ``showing``/``total`` so a caller always knows whether it saw everything."""
    index = _get_search_index(PRODUCT_DOCS_DIR)
    terms = _tokenize(query)
    candidate_ids = [
        doc_id
        for doc_id, entry in index.entries.items()
        if category is None or entry.meta.category == category
    ]
    if not terms or not candidate_ids:
        return SearchResult(hits=(), showing=0, total=0)

    scored: list[tuple[float, str]] = []
    for doc_id in candidate_ids:
        score = 0.0
        for field, weight in _FIELD_WEIGHTS.items():
            stats = index.field_stats[field]
            tokens = index.field_tokens[field].get(doc_id, [])
            length = stats.lengths.get(doc_id, 0)
            counts = Counter(tokens)
            for t in terms:
                score += weight * _bm25_term_score(t, counts.get(t, 0), length, stats, index.n_docs)
        if score > 0:
            scored.append((score, doc_id))

    scored.sort(key=lambda x: (-x[0], x[1]))
    total = len(scored)
    page = scored[:limit] if limit and limit > 0 else scored

    hits = []
    for score, doc_id in page:
        entry = index.entries[doc_id]
        anchor, heading, snippet = _best_section_and_snippet(entry, terms)
        hits.append(
            SearchHit(
                doc_id=doc_id,
                title=entry.meta.title,
                category=entry.meta.category,
                status=entry.meta.status,
                anchor=anchor,
                heading=heading,
                snippet=snippet,
                score=round(score, 4),
            )
        )
    return SearchResult(hits=tuple(hits), showing=len(hits), total=total)
