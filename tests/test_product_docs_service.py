"""Unit tests for backend/services/product_docs.py — parse, index, search, sections.

Isolated to tests/fixtures/product_docs/ (a small, hand-authored, deterministic corpus)
and, for malformed-corpus/invalidation cases, to tmp_path. Never touches the real
docs/product/ tree, which three other agents are writing concurrently — see the module
docstring in backend/services/product_docs.py.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import backend.services.product_docs as product_docs

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "product_docs"


@pytest.fixture(autouse=True)
def fixture_corpus(monkeypatch):
    monkeypatch.setattr(product_docs, "PRODUCT_DOCS_DIR", FIXTURE_DIR)


# ── list_docs / list_categories ─────────────────────────────────────────────────────


def test_list_docs_returns_every_doc():
    docs = product_docs.list_docs()
    assert {d.doc_id for d in docs} == {
        "getting-started/install",
        "features/budgeting",
        "features/envelope-groups",
        "troubleshooting/prices-not-updating",
    }


def test_list_docs_filters_by_category():
    docs = product_docs.list_docs(category="features")
    assert [d.doc_id for d in docs] == ["features/budgeting", "features/envelope-groups"]


def test_list_docs_sorts_by_canonical_category_order_not_alphabetical():
    # 'features' < 'getting-started' alphabetically, but getting-started comes FIRST in
    # the corpus contract's Layout order — the sidebar must follow that, not ASCII.
    docs = product_docs.list_docs()
    assert [d.doc_id for d in docs] == [
        "getting-started/install",
        "features/budgeting",
        "features/envelope-groups",
        "troubleshooting/prices-not-updating",
    ]


def test_list_categories_uses_fixed_layout_order_and_omits_empty_ones():
    cats = product_docs.list_categories()
    assert [(c.id, c.title, c.order) for c in cats] == [
        ("getting-started", "Getting Started", 10),
        ("features", "Features", 30),
        ("troubleshooting", "Troubleshooting", 70),
    ]
    # concepts/lucius/connectors/operations/reference have zero fixture docs — omitted.
    assert "concepts" not in {c.id for c in cats}


# ── get_doc ──────────────────────────────────────────────────────────────────────────


def test_get_doc_returns_meta_and_markdown():
    doc = product_docs.get_doc("features/budgeting")
    assert doc.title == "Budgeting with envelopes"
    assert doc.category == "features"
    assert doc.status == "stable"
    assert doc.related == ("features/envelope-groups",)
    assert doc.keywords == ("budget", "envelope", "assign", "zero-based")
    assert doc.audience == ("user",)
    assert "Give every dollar a job" in doc.markdown
    assert [s.anchor for s in doc.sections] == ["assigning-money", "rollover"]


def test_get_doc_unknown_id_raises_not_found():
    with pytest.raises(product_docs.NotFoundError):
        product_docs.get_doc("features/does-not-exist")


def test_get_doc_reports_coming_soon_status():
    doc = product_docs.get_doc("features/envelope-groups")
    assert doc.status == "coming-soon"


# ── get_section ──────────────────────────────────────────────────────────────────────


def test_get_section_returns_the_block_through_the_next_heading():
    text = product_docs.get_section("features/budgeting", "assigning-money")
    assert text.startswith("## Assigning money")
    assert "Ready to Assign" in text
    assert "Rollover" not in text  # stops before the next ## heading


def test_get_section_last_section_reads_to_end_of_doc():
    text = product_docs.get_section("features/budgeting", "rollover")
    assert text.startswith("## Rollover")
    assert text.rstrip().endswith("your choice, per envelope.")


def test_get_section_unknown_anchor_raises_not_found():
    with pytest.raises(product_docs.NotFoundError):
        product_docs.get_section("features/budgeting", "no-such-section")


def test_get_section_unknown_doc_raises_not_found():
    with pytest.raises(product_docs.NotFoundError):
        product_docs.get_section("features/does-not-exist", "anything")


def test_duplicate_headings_get_a_dash_suffix_like_github():
    doc = product_docs.get_doc("getting-started/install")
    anchors = [s.anchor for s in doc.sections]
    assert anchors == ["prerequisites", "running-the-backend", "running-the-backend-1"]
    # And each anchor is independently readable via get_section.
    first = product_docs.get_section("getting-started/install", "running-the-backend")
    second = product_docs.get_section("getting-started/install", "running-the-backend-1")
    assert first != second
    assert "uvicorn" in first
    assert "second, identical heading" in second


def test_assign_anchors_avoids_collision_between_a_suffix_and_a_later_literal_heading():
    # "## Foo", "## Foo", "## Foo 1": the second "Foo" naturally wants "foo-1", but so
    # does the THIRD heading's own literal slug — without a genuinely-unused-candidate
    # loop, both would collide on "foo-1" and get_section() would silently return the
    # wrong section for one of them. Matches github-slugger's own behavior exactly.
    anchors = product_docs._assign_anchors(["Foo", "Foo", "Foo 1"])  # noqa: SLF001
    assert anchors == ["foo", "foo-1", "foo-1-1"]
    assert len(set(anchors)) == len(anchors)  # every anchor is unique


# ── search ───────────────────────────────────────────────────────────────────────────


def test_search_finds_relevant_docs_and_excludes_irrelevant_ones():
    result = product_docs.search("envelope")
    assert result.total == 2
    assert result.showing == 2
    assert {h.doc_id for h in result.hits} == {"features/budgeting", "features/envelope-groups"}
    # Deterministic ranking, pinned: shorter/denser envelope-groups outranks budgeting.
    assert [h.doc_id for h in result.hits] == ["features/envelope-groups", "features/budgeting"]
    assert result.hits[0].score >= result.hits[1].score


def test_search_hit_carries_title_category_status_and_a_snippet():
    result = product_docs.search("prices")
    top = result.hits[0]
    assert top.doc_id == "troubleshooting/prices-not-updating"
    assert top.title == "Prices are not updating"
    assert top.category == "troubleshooting"
    assert top.status == "stable"
    assert "prices" in top.snippet.lower()


def test_search_section_and_heading_point_at_the_matching_block():
    result = product_docs.search("prices", limit=10)
    install_hit = next(h for h in result.hits if h.doc_id == "getting-started/install")
    assert install_hit.anchor == "prerequisites"
    assert install_hit.heading == "Prerequisites"


def test_search_respects_category_filter():
    result = product_docs.search("prices", category="features")
    assert result.total == 0
    assert result.hits == ()


def test_search_no_match_returns_empty_result():
    # A single unbroken nonsense token — no underscores/spaces that would tokenize into
    # a real word (e.g. 'no') that happens to appear somewhere in the fixture corpus.
    result = product_docs.search("zzqxvthisisnotarealword")
    assert result == product_docs.SearchResult(hits=(), showing=0, total=0)


def test_search_empty_query_returns_empty_result():
    result = product_docs.search("")
    assert result.total == 0
    assert result.showing == 0


def test_search_limit_caps_showing_but_reports_true_total():
    result = product_docs.search("envelope", limit=1)
    assert result.showing == 1
    assert result.total == 2


# ── malformed corpus (fail-closed) ──────────────────────────────────────────────────


def _write_doc(
    root: Path, category: str, name: str, frontmatter: str, body: str = "content\n"
) -> None:
    d = root / category
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(f"---\n{frontmatter}\n---\n{body}")


_GOOD_FM = (
    "title: T\ndescription: D\ncategory: features\norder: 10\naudience: [user]\nstatus: stable"
)


def test_missing_required_key_raises_docs_corpus_error(tmp_path, monkeypatch):
    monkeypatch.setattr(product_docs, "PRODUCT_DOCS_DIR", tmp_path)
    _write_doc(
        tmp_path,
        "features",
        "bad",
        "title: T\ndescription: D\ncategory: features\norder: 10\naudience: [user]",
    )
    with pytest.raises(product_docs.DocsCorpusError, match="status"):
        product_docs.list_docs()


def test_description_over_160_chars_warns_and_still_indexes(tmp_path, monkeypatch, caplog):
    # A guideline, not structure: one long summary once took search_docs offline.
    monkeypatch.setattr(product_docs, "PRODUCT_DOCS_DIR", tmp_path)
    long_desc = "x" * 161
    _write_doc(
        tmp_path,
        "features",
        "long",
        f"title: T\ndescription: {long_desc}\ncategory: features\norder: 10\naudience: [user]\nstatus: stable",
    )
    with caplog.at_level("WARNING"):
        docs = product_docs.list_docs()
    assert "160-char guideline" in caplog.text
    (meta,) = [d for d in docs if d.doc_id == "features/long"]
    assert len(meta.description) == 160 and meta.description.endswith("…")


def test_category_mismatching_directory_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(product_docs, "PRODUCT_DOCS_DIR", tmp_path)
    _write_doc(
        tmp_path,
        "features",
        "bad",
        "title: T\ndescription: D\ncategory: troubleshooting\norder: 10\naudience: [user]\nstatus: stable",
    )
    with pytest.raises(product_docs.DocsCorpusError, match="does not match"):
        product_docs.list_docs()


def test_invalid_status_value_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(product_docs, "PRODUCT_DOCS_DIR", tmp_path)
    _write_doc(
        tmp_path,
        "features",
        "bad",
        "title: T\ndescription: D\ncategory: features\norder: 10\naudience: [user]\nstatus: not-a-real-status",
    )
    with pytest.raises(product_docs.DocsCorpusError, match="unknown status"):
        product_docs.list_docs()


def test_nested_doc_deeper_than_one_category_level_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(product_docs, "PRODUCT_DOCS_DIR", tmp_path)
    _write_doc(tmp_path / "features", "nested", "bad", _GOOD_FM)
    with pytest.raises(product_docs.DocsCorpusError, match="exactly one category directory"):
        product_docs.list_docs()


def test_missing_frontmatter_block_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(product_docs, "PRODUCT_DOCS_DIR", tmp_path)
    d = tmp_path / "features"
    d.mkdir()
    (d / "bad.md").write_text("# No frontmatter at all\n")
    with pytest.raises(product_docs.DocsCorpusError, match="frontmatter"):
        product_docs.list_docs()


# ── OSError on read never leaks an absolute filesystem path (security review) ───────
# A bare PermissionError/FileNotFoundError's str() carries the full path it failed on
# — which, surfaced verbatim to the model via assistant.py's generic exception catch
# (json.dumps({"error": str(exc)})), leaks the operator's OS username through their
# home directory. Both call sites (the mtime signature and the actual page read) must
# re-raise DocsCorpusError with the repo-relative name only, matching every other
# corpus-parsing failure above.


def test_read_text_oserror_is_reraised_repo_relative_only(tmp_path, monkeypatch):
    _write_doc(tmp_path, "features", "secret", _GOOD_FM)
    target = tmp_path / "features" / "secret.md"
    # Stub _scan_corpus (its own is_file()/resolve(strict=True) calls are unrelated
    # to this unit) so only _build_entries' own read_text() call is exercised.
    monkeypatch.setattr(product_docs, "_scan_corpus", lambda root: [target])

    def fake_read_text(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    with pytest.raises(product_docs.DocsCorpusError) as exc_info:
        product_docs._build_entries(tmp_path)

    message = str(exc_info.value)
    assert "features/secret" in message
    assert str(target) not in message
    assert str(tmp_path) not in message


def test_stat_oserror_is_reraised_repo_relative_only(tmp_path, monkeypatch):
    _write_doc(tmp_path, "features", "secret", _GOOD_FM)
    target = tmp_path / "features" / "secret.md"
    monkeypatch.setattr(product_docs, "_scan_corpus", lambda root: [target])

    def fake_stat(self, *args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", str(self))

    monkeypatch.setattr(Path, "stat", fake_stat)

    with pytest.raises(product_docs.DocsCorpusError) as exc_info:
        product_docs._quick_signature(tmp_path)

    message = str(exc_info.value)
    assert "features/secret" in message
    assert str(target) not in message
    assert str(tmp_path) not in message


# ── empty / missing corpus ──────────────────────────────────────────────────────────


def test_empty_corpus_degrades_to_empty_results(tmp_path, monkeypatch):
    monkeypatch.setattr(product_docs, "PRODUCT_DOCS_DIR", tmp_path / "does-not-exist")
    assert product_docs.list_docs() == []
    assert product_docs.list_categories() == []
    assert product_docs.search("anything") == product_docs.SearchResult(hits=(), showing=0, total=0)
    with pytest.raises(product_docs.NotFoundError):
        product_docs.get_doc("anything/anything")


# ── index caching / invalidation ────────────────────────────────────────────────────


def test_index_picks_up_a_newly_added_file_without_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(product_docs, "PRODUCT_DOCS_DIR", tmp_path)
    _write_doc(tmp_path, "features", "one", _GOOD_FM)
    assert len(product_docs.list_docs()) == 1

    _write_doc(tmp_path, "features", "two", _GOOD_FM)
    assert len(product_docs.list_docs()) == 2


def test_index_picks_up_an_edited_file_without_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(product_docs, "PRODUCT_DOCS_DIR", tmp_path)
    _write_doc(tmp_path, "features", "one", _GOOD_FM)
    assert product_docs.get_doc("features/one").title == "T"

    edited_fm = _GOOD_FM.replace("title: T", "title: Edited Title")
    path = tmp_path / "features" / "one.md"
    path.write_text(f"---\n{edited_fm}\n---\ncontent\n")
    # Force a distinguishable mtime regardless of filesystem clock resolution.
    future = time.time() + 5
    os.utime(path, (future, future))

    assert product_docs.get_doc("features/one").title == "Edited Title"


def test_every_category_has_a_landing_blurb():
    """The Help landing renders one card per category and shows its blurb underneath.

    A category with no entry here would render a titled card with an empty body — so this
    pins the two tables together rather than letting one drift ahead of the other. The same
    copy lives in `docs-site/src/pages/index.astro` (`categoryBlurbs`) for the published
    site, which cannot read this module: it is an Astro build with no Python in it.
    """
    assert set(product_docs.CATEGORY_BLURBS) == set(product_docs.CATEGORY_TITLES)
    for cat_id, blurb in product_docs.CATEGORY_BLURBS.items():
        assert blurb.strip(), f"{cat_id} has an empty landing blurb"
