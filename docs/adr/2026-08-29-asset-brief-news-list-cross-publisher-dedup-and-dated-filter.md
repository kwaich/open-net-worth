# 2026-08-29 — Asset Brief news list: cross-publisher dedup, drop undated filler, tiered timestamps

**Status:** accepted (owner-directed, 2026-08-29, real screenshots evidenced); **amended
2026-08-29** (same day, real live repro) — see "Correction" below; **a second, related fix added
2026-08-29** (same day, different root cause) — see "Related fix" below.
**Where:** `backend/monitoring/asset_brief.py` (`_has_known_publish_date`,
`_is_generic_reference_title`, `_filter_and_collapse_news`, `_looks_garbled`,
`_mark_extraction_failed_if_thin`, `_drop_failed_extractions`, `_fetch_full_text`,
`_synthesize_news_summaries`, `_synthesize_facts_peers`, `_do_refresh`),
`frontend/src/utils/format.ts` (`newsTime`), `frontend/src/components/intel/NewsCardGrid.tsx`,
`frontend/src/components/intel/LatestDevelopments.tsx`, `tests/test_asset_brief_refresh.py`,
`frontend/src/utils/format.test.ts`

## Context

The Asset Detail page's "Stories & Analysis" / "Latest Developments" sections
(`asset_brief.py`'s `news` list) had three confirmed problems, compared directly against a
Perplexity-style reference the owner supplied:

1. **Visible duplicate.** The same insider-sale story (Dr. Johannes Bruder, Kestrel Dash,
   2,728 shares @ EUR 37.07456763) appeared 3 times, sourced as `marketscreener.com`,
   `tradingview.com`, and the bare label `"TradingView"`. `core.news.dedupe_stories` — the
   existing dedup already running in this pipeline — is deliberately SAME-PUBLISHER-ONLY (its
   own module docstring: "different outlets covering one event are distinct stories"), and its
   publisher match is an exact casefolded string match on `source`/`domain`. None of the three
   labels above equal each other as strings (`"tradingview.com"` != `"tradingview"`), so
   `dedupe_stories` structurally could not collapse any of the three — confirmed by a direct
   test (`test_dedupe_stories_alone_cannot_collapse_the_bruder_shape`) that runs it, unmodified,
   over the exact repro shape and gets 3 items back.
2. **Generic filler cards** ("Kestrel Dash Company Overview", "Kestrel Dash Stock Price and
   Performance Data") diluted real news slots. These are evergreen company-profile/quote pages;
   `_fetch_news_items`'s own comments already noted that general web search (vs. news verticals)
   "returns generic quote/overview pages" and that such hits often carry no `published_date` at
   all.
3. **Vague relative timestamps** ("yesterday", "1w ago", "2w ago") where the reference showed
   exact dates ("Aug 28, 2026") for anything not genuinely recent.

## Decision

### 1. Cross-publisher near-duplicate collapse — reuse, don't reimplement

`_filter_and_collapse_news` (new, factored out of `_do_refresh`) runs the assembled news list
through `backend.monitoring.analyst_prompt.cluster_by_content_overlap` — the SAME
publisher-agnostic, content-token-overlap clustering (0.6 threshold) `analyst.py`'s own
evidence-read path (`_read_and_collapse_evidence`) already uses to collapse syndicated copies
before a synthesis prompt is built (INT14b). This runs as an ADDITIVE stage AFTER the existing
`dedupe_stories` pass, not a replacement for it — `dedupe_stories` is left completely untouched,
including its other call sites (`analyst.py`, `services/asset_detail.py`); nothing about its
same-publisher-only design changes.

Deliberately NOT `collapse_to_distinct_stories` (the existing wrapper around
`cluster_by_content_overlap`): that wrapper caps output at `_MAX_DISTINCT_STORIES = 8`, a bound
that exists to control LLM prompt token cost, not to limit how many distinct stories a person can
page through in "View all". Calling the un-capped `cluster_by_content_overlap` directly and
taking each cluster's own first-seen item (`[cluster[0] for cluster in clusters]`) — rather than
`collapse_to_distinct_stories`'s own "keep the newest representative, then re-sort by recency"
policy — was the deliberate choice here: the news list entering this stage is already sorted by
`_news_sort_key` (tier + recency), so "first item in each cluster" is exactly the
highest-priority representative in THAT ordering, mirroring `dedupe_stories`'s own "keep first
occurrence, preserve order" contract instead of introducing a second, different selection rule.

Regression-tested against the exact reported shape: 3 items, same core fact/numbers, 3
string-distinct source labels — collapses to 1 (`test_filter_and_collapse_news_collapses_
bruder_duplicate_to_one_card`), and two genuinely different stories about the same subject stay
separate (`test_filter_and_collapse_news_keeps_genuinely_distinct_stories`).

### 2. Drop items with no parseable `published_at` — the missing-date signal, not title text

`_has_known_publish_date` drops any item whose `published_at` doesn't parse to a real date/age
at all, reusing `_recency_hours`'s own existing undated/unparseable sentinel (`>= 1e9`) — not a
second date parser, and never a title-text pattern match (fragile, English-specific; "Company
Overview" in one language tells you nothing about the same content in another).

This is deliberately a DIFFERENT question from the pre-existing `_is_stale_news`, which treats
an undated item as *unprovably old* and explicitly KEEPS it (see its own test,
`test_is_stale_news_keeps_undated_pages`: "A quote/reference page with no date can't be proven
old → kept... so providers that omit dates don't get their whole feed nuked"). `_is_stale_news`
and its behavior are UNCHANGED — this is a new, separate filter stage in the news-LIST assembly
specifically, answering "does this look like a real, dated development at all," not "is it
provably old."

**Checked for the dateless-but-real-news risk the owner asked about, before shipping:** every
evergreen reference host this pipeline already special-cases (Wikipedia, Investopedia) carries a
relative-but-parseable age string ("7 years ago"), not an absent one — `_MAX_NEWS_AGE_HOURS`'s
own exemption for those hosts is about being provably OLD, not about being undated, so this new
filter doesn't touch them. `_fetch_news_items`'s own code comments (news verticals lead with
real dates; general web search returns the undated quote/overview pages) and this file's own
existing test fixture (`https://finance.yahoo.com/quote/ONWAI.DE` with `published_at: ""`) both
independently point the same way: an absent date in this pipeline's real evidence is
overwhelmingly the quote/overview-page case this fix targets, not a genuine news source that
happens to omit dates. No live-traffic sample was available to confirm this at scale; if a
dateless-but-real-news case turns out to be common in practice, it should surface as fewer real
stories in "Latest Developments" than expected, and would call for narrowing this filter to
specific known-dateless-but-legitimate hosts rather than reverting it outright.

### 3. Tiered timestamp: relative under 24h, absolute date beyond that

`frontend/src/utils/format.ts::newsTime` — new, alongside the existing `timeAgo` (kept, used
elsewhere: connector "last synced" chips, price "as of" labels, the Activity panel — none of
which this change touches). Under 24h old, delegates to `timeAgo` unchanged ("8h ago" reads as
more useful than a bare date at that scale); 24h or older, renders via the existing `fDate`
helper with explicit `{ month: 'short', day: 'numeric', year: 'numeric' }` options ("Aug 28,
2026"), matching the reference. No existing tiered relative/absolute helper was found elsewhere
in the codebase to reuse (`fDate` is pure-absolute, `timeAgo` is pure-relative) — `newsTime`
composes the two rather than duplicating either's date math.

Wired into both `NewsCardGrid.tsx` and `LatestDevelopments.tsx`'s timestamp render (the two
consumers of `asset_brief.py`'s `news` list) — both previously called `timeAgo` directly.

## Consequences

- The exact reported repro (Dr. Bruder insider sale, 3 near-identical source labels) collapses
  to 1 card. Verified with a named regression test using the real reported numbers/shares, not
  just an abstract clustering case.
- A quote/overview page with no publish date no longer occupies a Stories & Analysis / Latest
  Developments slot. `_is_stale_news`'s own, deliberately different "unprovably old" tolerance
  for undated items is unchanged for whatever else still reads it.
- News timestamps under 24h stay relative; everything older reads as an exact date instead of
  "1w ago"/"2w ago" fuzz.
- Test fixtures in `tests/test_asset_brief_refresh.py` that used templated placeholder titles
  differing only by a trailing digit (`"RSS story {i}"`, `"Cached story {i:02d}"`, and the
  default `_stub_provider()` pair `"Test headline"`/`"Another headline"`) were found to
  false-positive as cross-publisher duplicates once this stage ran — not a production bug, but
  an artifact of unrealistic test data (real headlines never repeat this much boilerplate
  verbatim; the `"Test headline"` collision was specifically caused by `_make_inst()`'s "Test
  Co" fixture name being stripped as the subject's own name, leaving only "headline" as the
  differentiating token). Fixed by giving those fixtures genuinely content-distinct titles
  (`_DISTINCT_STORY_TITLES`, a 40-entry bank verified pairwise-non-clustering) rather than
  weakening the production clustering pass.

## What this does NOT fix

- No cross-LANGUAGE or cross-currency-notation dedup beyond what content-token overlap already
  catches (e.g. "EUR 37.07" vs "€37.07" tokenize differently enough that they may not always
  cluster) — same accepted residual risk `cluster_by_content_overlap`'s own docstring already
  flags (SF-2: a cheap heuristic, not semantic understanding).
- The dateless-but-real-news risk flagged above is checked against code/comments/fixtures, not
  against a live production sample — see the note in Decision §2.

## Correction (2026-08-29, same day) — the date-only heuristic had a real gap

Decision §2's own text flagged the uncertainty explicitly ("No live-traffic sample was available
to confirm this at scale"). It's now confirmed to matter, live, the same day: after this ADR
shipped, the owner refreshed KSTL's Asset Brief and **"Kestrel Dash Company Overview"
(reuters.com) was still showing — now WITH a real-looking "19h ago" timestamp attached**. So
some evergreen reference/quote pages DO get a timestamp after all — evidently a search
provider's page-fetch/crawl time, not a true article-publish date — which defeats
`_has_known_publish_date` on its own for exactly the class of filler card this ADR set out to
remove.

**What closes the gap:** a SECOND, narrower filter stage, `_is_generic_reference_title`, runs
immediately after `_has_known_publish_date` inside `_filter_and_collapse_news` (both stages run
unconditionally; neither replaces the other). It matches on the title's TEXT after all — the
thing §2 explicitly rejected as "fragile, English-specific" for the general case — but scoped
much more tightly than a plain pattern match: a small, explicit, hand-curated list of confirmed
generic-template title ENDINGS (`_GENERIC_REFERENCE_TITLE_ENDINGS` — "company overview", "stock
price and performance data", "stock profile and overview", "stock quote", "quote & history",
bare trailing "overview"), matched as a case-insensitive SUFFIX only (never substring-anywhere),
after first stripping a trailing " - SourceName" attribution tail search providers commonly
append. Suffix-anchoring is the deliberate false-positive control: a real headline is unique
prose about one event and essentially never happens to *end* with one of these exact templated
phrases, even though it might contain a word like "overview" or "quote" mid-title.

One entry needed a judgment call: "Kestrel Dash SE (KSTL.DE) Stock Price, News, Quote &
History - Yahoo Finance" (seen live) contains the word "News" mid-title, which reads at first
glance like it might describe real news content. It was still added to the list — this exact
string is Yahoo Finance's FIXED quote-page title template, rendered identically for every quote
page regardless of whether any actual news exists that day; "News" here is a static section
label baked into the page layout, not prose reporting an event. Suffix-matching on the tail
after "- Yahoo Finance" is stripped ("...Quote & History") is what actually catches it, so the
mid-title "News" token never enters the decision.

Verified directly against the exact confirmed real strings from this investigation — dropped:
"Kestrel Dash Company Overview", "Kestrel Dash Stock Price and Performance Data", "Delivery
Hero SE Stock Profile and Overview", the Yahoo Finance quote-page title above
(`test_is_generic_reference_title_drops_confirmed_real_examples`); and the exact live-repro
shape with a real-looking date attached, proving the gap is closed
(`test_filter_and_collapse_news_drops_generic_titled_card_even_with_a_known_date`). Verified NOT
dropped — real genuine headlines seen live this session ("Uber Formally Launches €41.50 Takeover
Offer for Kestrel Dash", "Kestrel Dash Major Shareholder Reduces Voting Rights Position",
"Berenberg Reaffirms Buy Rating on Kestrel Dash", "Kestrel Dash Raises Full-Year 2026
Guidance", "Kestrel Dash Stock Faces Valuation Headwinds as Losses Widen") —
`test_is_generic_reference_title_keeps_genuine_headlines`; plus a direct substring-vs-suffix
false-positive check (`test_is_generic_reference_title_is_suffix_only_not_substring_anywhere`).

`_has_known_publish_date` itself, and `_filter_and_collapse_news`'s cross-publisher clustering
call, are UNCHANGED by this correction — this is purely an additional filter stage alongside
both.

## Related fix (2026-08-29, same day) — a failed extraction is narrated as news, not dropped

**A different root cause from both fixes above.** The two filters above identify a
successfully-fetched item that's real but not real NEWS (a quote/overview page). This bug is
about an item whose fetch never landed real content AT ALL — the model was handed nothing (or
garbage) and, with no instruction otherwise, narrated that fact as if it were the story. Owner
confirmed live on a brand-new ticker's first-ever Brief refresh (no prior evidence, so the
refresh is forced onto the live-search fallback — `_fetch_news_items`/`_fetch_full_text`):
several fetched URLs came back unreadable (binary data, JS-rendered shells, empty extraction),
and the news_summaries synthesis produced cards like "Kestrel Dash SE Quote Page on Yahoo
Finance" — "...but the fetched content was unreadable binary data with no extractable facts" —
and similar hedged non-answers for a Google Finance data page, an Investor Relations homepage, a
StockAnalysis overview, and a Financial Reports page ("though no specific figures were
extracted", "no specific content was retrievable from the excerpt").

**Two gaps, both in `_fetch_full_text`/`_fetch_one`:**

1. `_looks_blocked`'s length + known-bot-wall-marker check doesn't catch a response that's long
   enough (≥400 chars) and marker-free but still isn't real article prose — a mis-decoded binary
   body (`plain_get_fetch` decodes with `errors="replace"`, flooding the text with U+FFFD) or a
   JS-only page's raw-HTML/script fallback (trafilatura found too little to extract, so the raw
   markup/script soup is returned verbatim). This is exactly what let the Yahoo Finance item's
   garbage overwrite a snippet the model then dutifully described as "unreadable."
2. Even where the existing "keep the original snippet on failure" degrade-gracefully contract
   already applied (the other four examples), nothing ever checked whether that fallback snippet
   itself had anything in it. A quote/overview page's own search-result `content` field is
   commonly empty (many search engines return no description for that result shape), so a failed
   fetch left the item with literally nothing — yet it still reached the synthesis prompt.

**Decision — two-part fix, deliberately narrow:**

- `_looks_garbled(text)` (new, alongside `_looks_blocked`): a second, independent gate on the
  freshly-fetched text before it's trusted to overwrite the snippet. Two signals, either
  disqualifying: (a) the U+FFFD replacement-character density exceeds 2% (mis-decoded binary),
  or (b) structural/code punctuation (`<>{}[]();=/`) exceeds 10% of the sample OR letters+
  whitespace fall under 55% (markup/script soup, or a numeric-data-widget dump with almost no
  prose). Thresholds were picked by measuring real prose vs. a constructed HTML/script-soup
  sample vs. a constructed numeric-quote-widget dump (see the module comment above the
  constants) — not guessed. `_fetch_one` now checks `_looks_blocked(text) or _looks_garbled(text)`
  before overwriting the snippet; garbled text is treated exactly like a blocked page (rejected,
  original snippet kept).
- `_mark_extraction_failed_if_thin` + `_drop_failed_extractions` (new): whenever a fetch attempt
  doesn't land (SSRF refusal, exception/timeout, or the blocked-or-garbled check above), the item
  is flagged `extraction_failed=True` **only if what it's left with is genuinely empty** after
  stripping whitespace — an emptiness check, deliberately not a length heuristic. `_do_refresh`
  drops every flagged item, via the new `_drop_failed_extractions`, BEFORE `_filter_and_collapse_
  news` runs and before either synthesis call is built, so a failed-extraction item never
  participates in cross-publisher clustering and never reaches a prompt.

**Why emptiness, not a length threshold:** this codebase's own existing test fixtures rely on
short-but-real placeholder content surviving a fetch failure unmodified (`test_failing_fetch_
keeps_original_snippet`, `test_unsafe_url_keeps_original_snippet`, and several `_do_refresh`-
level fixtures using one-to-three-word `content` fields like `"new"`/`"ctx"`/`"context"`) — that
"a fetch failure keeps the item, however short its original snippet" contract predates this fix
and is deliberately preserved. Trying a blanket short-content threshold (a first draft used 40
chars) broke roughly a dozen of those existing tests by dropping items the codebase's own
fixtures treat as legitimately-thin-but-real. Emptiness is the one boundary that catches the
confirmed "nothing was retrievable" failure shape without that regression — verified by running
the full existing suite (all green) before and after narrowing the threshold.

**Defense-in-depth (belt-and-suspenders, matching this file's own style elsewhere for guarding
against a model doing something undesired with bad input):** both `_synthesize_news_summaries`'s
and `_synthesize_facts_peers`'s prompts (they share `_render_news_block`) gained one explicit
rule each — if a result's content is empty/unreadable/clearly-not-real, omit it rather than
describing the extraction failure. The pre-filter is the real fix; this is a backstop for
whatever content shape the filter doesn't anticipate.

**Verified:** `_looks_garbled` unit tests against a replacement-char flood, constructed HTML/JS
soup vs. real prose (including a deliberately punctuation-heavy real article, to check the
structural-density threshold doesn't false-positive), and a numeric quote-widget dump;
`_mark_extraction_failed_if_thin`/`_drop_failed_extractions` unit tests; an end-to-end regression
(`test_failed_extraction_item_never_reaches_synthesis_prompt_or_persisted_news`) that mirrors the
confirmed live shape — a quote-page URL with empty search content and a garbled fetch, alongside
one genuine article — and asserts the failed item is absent from the persisted `news` list AND
never appears in either synthesis prompt's captured text, while the genuine article is
unaffected. Full existing suite (115 tests in `test_asset_brief_refresh.py` +
`test_asset_brief_fence.py`) green throughout.

**Deliberately out of scope:** `_filter_and_collapse_news`'s two filters
(`_has_known_publish_date`, `_is_generic_reference_title`) and its cross-publisher clustering
call are untouched by this fix — this is an independent, earlier pipeline stage
(`_drop_failed_extractions` runs before it) addressing a structurally different question ("did
the fetch land anything at all" vs. "is what landed real news").

## Agent provenance

- **Decided by:** implementer agent (Claude), continuing `feat/claims-ledger`.
- **Lane / branch:** `feat/claims-ledger`, worktree `worktrees/
  claims-ledger` off `main`.
- **Alternatives weighed:** modifying `dedupe_stories` itself to also match cross-publisher
  (rejected — it has other call sites with a deliberate same-publisher-only design; ADR scope
  was to add a stage to `asset_brief.py`'s own pipeline, not change shared, intentional
  behavior); using `collapse_to_distinct_stories`'s wrapper directly (rejected — its
  `_MAX_DISTINCT_STORIES` cap is a token-cost bound for an LLM prompt, wrong semantics for a
  user-facing "View all" list); title-text pattern matching for filler detection (rejected per
  the owner's explicit instruction — fragile, English-specific; the missing-date signal is
  general and reuses existing parsing).
- **Driving artifacts:** the owner's real screenshots (Dr. Bruder 3x duplicate, filler cards,
  relative-only timestamps) compared against a Perplexity-style reference; this repo's own
  `_fetch_news_items`/`_is_stale_news` code comments and existing test
  (`test_is_stale_news_keeps_undated_pages`) as the basis for the dateless-item risk assessment.
- **Why this way (one line):** reuse the exact primitive already proven for this class of
  problem (`cluster_by_content_overlap`) rather than inventing a second dedup heuristic, and
  prefer a general structural signal (missing date) over a fragile textual one (title pattern)
  wherever the task allowed a choice.
