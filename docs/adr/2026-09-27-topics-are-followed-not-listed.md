---
title: Topics are followed on their own; a watchlist holds tickers only
status: Accepted
date: 2026-09-27
deciders: repo owner ("the additional intelligence, watchlist supporting both tickers and topics, tabs focusing purely on watchlist pulse or topics is very confusing UX") + Claude (implementation)
---

# 2026-09-27. Topics are followed on their own; a watchlist holds tickers only

## Context

Intel's tabs became the user's watchlists (My holdings · each list · a topics tab). Three ideas
then overlapped:

- **A watchlist held two kinds of thing.** A list could hold both tickers and topics, and
  Manage watchlists gave every list a Tickers sub-tab and a Topics sub-tab.
- **Tabs were two kinds of thing.** A list tab was a ticker pulse, while the last tab was a
  feed of topic and macro stories.
- **A special list glued the two together.** "Follow a topic" filed topics in an
  "Intelligence" list, later renamed "Macro & topics". It existed only so the topics tab could
  find them, and it showed up as a confusing extra list in Manage watchlists.

Nothing else depended on the coupling. News monitoring (`rss_poller`, `intel_agent`,
`news_screen`, `rss_poller_hot`, `topic_analyst`) selects topics by `type` and `monitor_news`
and never reads list membership — though only `type == "topic"`: a followed `company` fell
into the ticker branch and, having no instrument, got no fast-lane coverage. Every reader now
asks `is_topic(item)`, which covers both. The per-list `muted` flag is stored but read nowhere.

## Decision

**Split by kind.**

- **A watchlist holds tickers only.** A ticker item still needs at least one list, as before.
- **A topic or company item is followed on its own** and always has `watchlist_ids == []`.
  `WatchlistStore.insert` / `update_by_id` refuse to give one a list.
  - Following the same topic twice (case-insensitive value) is refused with `ConflictError`.
  - `find_by_value` takes a `type`, so a topic named "AMD" never answers for the ticker AMD.
- **Adding a topic:**
  - REST `POST /api/watchlist/items` and Lucius's `add_watchlist_items` need no watchlist.
  - One that is sent anyway is ignored, and the reply carries a `notice` saying so. It is not
    in `note`, which is the item's own field.
- **Removing a topic** always unfollows it, even when the call is scoped to a watchlist (REST
  `DELETE …?watchlist_id=`, Lucius's `remove_watchlist_items`).
- **Intel's tabs:** My holdings · each watchlist · **Topics & macro**.
  - A list tab takes stories on its tickers only.
  - Topics & macro takes topic and macro stories, and anything no list claims.
  - The key stays `macro`, so old links still work.
- **Manage watchlists:** a list shows tickers only. **All monitors → Topics** is the one place
  that lists every topic.
- **Migration `202609271300_topics_leave_watchlists`:**
  - detaches every topic and company item;
  - merges duplicate topics (the same value followed from two lists was two rows);
  - deletes the topics-only list ("Macro & topics", "Macro and topics" or "Intelligence") once
    it holds no tickers;
  - keeps a list with one of those names that still holds tickers, as an ordinary list — named
    "Intelligence" again if 1200 had renamed it, so it can't pass for the Topics & macro tab.
  - The earlier `202609271200_rename_intelligence_watchlist` is now a no-op but stays in the
    chain: it may already have run at boot, and removing a revision that has run would leave
    the database ahead of the code.
- **A restored export is normalised the same way** (`watchlist_topics.normalize_topics`, run by
  `investments_io.import_watchlist`), so an older backup never brings topics back into lists.

## Consequences

- **One concept per surface.** A watchlist is tickers; a topic is something you follow; each
  Intel tab is one kind of view.
- **Topics can no longer be grouped by list.** The only grouping is "every topic you follow". If
  per-theme grouping of topics is ever wanted, it should be designed as its own concept (topic
  tags), not by reusing watchlists.
- **Downgrade can't restore which list a topic was filed in.** Older code reads `[]` fine; it
  only refused `[]` on write.
