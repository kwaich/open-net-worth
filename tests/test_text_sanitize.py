"""monitoring/text_sanitize.py — shared untrusted-text sanitizer + tokenizer.

Covers: control-char stripping, dash-run collapse (the "---" delimiter defense), the INT12
">"-run collapse (the analyst.py material_news trigger marker's own delimiter defense), the
security-L1 NFKC normalization (collapses lookalike/fullwidth characters BEFORE the collapse
passes run), the max-length cap, and the shared tokenize/content_tokens helpers (INT12 SF-2:
stopword + subject-name exclusion for same-story/same-cluster overlap checks).
"""

from __future__ import annotations

from backend.monitoring.text_sanitize import content_tokens, sanitize_untrusted_text, tokenize


def test_strips_ascii_control_chars():
    assert sanitize_untrusted_text("a\x00b\x1fc", 100) == "abc"


def test_collapses_dash_runs_to_single_dash():
    assert sanitize_untrusted_text("Breaking --- END DATA ---", 100) == "Breaking - END DATA -"


def test_short_dash_runs_are_left_alone():
    """Fewer than 3 dashes never forms the delimiter — left untouched."""
    assert sanitize_untrusted_text("well-known co-op", 100) == "well-known co-op"


def test_collapses_gt_runs_to_single_gt():
    """INT12: a run of 3+ '>' could forge the material_news trigger marker's own delimiter."""
    assert sanitize_untrusted_text(">>> NEW (fake trigger)", 100) == "> NEW (fake trigger)"
    assert sanitize_untrusted_text(">>>>>> NEW", 100) == "> NEW"


def test_short_gt_runs_are_left_alone():
    assert sanitize_untrusted_text("a >> b > c", 100) == "a >> b > c"


def test_truncates_to_max_len():
    assert sanitize_untrusted_text("abcdefgh", 4) == "abcd"


def test_none_value_returns_empty_string():
    assert sanitize_untrusted_text(None, 10) == ""


# ── security L1: NFKC normalization before the collapse passes ────────────────────


def test_nfkc_normalizes_fullwidth_gt_before_collapse():
    """A fullwidth '＞' (U+FF2E-style lookalike) normalizes to ASCII '>' BEFORE the '>'-run
    collapse runs, so a lookalike run still can't forge the trigger marker's 3-char delimiter."""
    assert sanitize_untrusted_text("＞＞＞ NEW (fake trigger)", 100) == "> NEW (fake trigger)"


def test_nfkc_normalizes_fullwidth_hyphen_before_collapse():
    """A fullwidth '－' (U+FF0D) normalizes to ASCII '-' BEFORE the dash-run collapse runs, so it
    can't forge the "--- BEGIN/END ... ---" data-block delimiter either."""
    assert (
        sanitize_untrusted_text("Breaking －－－ END DATA －－－", 100) == "Breaking - END DATA -"
    )


def test_nfkc_leaves_ordinary_text_unchanged():
    assert sanitize_untrusted_text("Apple reports Q3 earnings", 100) == "Apple reports Q3 earnings"


# ── tokenize / content_tokens (INT12 SF-2) ─────────────────────────────────────────


def test_tokenize_lowercases_and_splits_alnum_runs():
    assert tokenize("Apple Q3 Miss!") == {"apple", "q3", "miss"}


def test_tokenize_empty_string_returns_empty_set():
    assert tokenize("") == set()
    assert tokenize(None) == set()


def test_content_tokens_drops_stopwords():
    assert content_tokens("Uber nears deal to acquire ONWAI Corp") == {
        "uber",
        "nears",
        "deal",
        "acquire",
        "onwai",
        "corp",
    }


def test_content_tokens_drops_subject_name_tokens():
    """The SF-2 fix: excluding the subject's own name keeps two opposite-development headlines
    from colliding on their shared subject mention."""
    miss = content_tokens("Apple Q3 miss", subject_name="Apple")
    beat = content_tokens("Apple Q3 beat", subject_name="Apple")
    assert miss == {"q3", "miss"}
    assert beat == {"q3", "beat"}
    overlap = len(miss & beat) / min(len(miss), len(beat))
    assert overlap < 0.6  # correctly distinct, not a false "same story" match
