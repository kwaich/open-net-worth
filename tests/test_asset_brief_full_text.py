"""The full-text pass also harvests each article's og:image (owner ask 2026-09-22: Perplexity-
style story cards need a picture; the HTML is already being downloaded for the text, so the
image is free) and writes it back onto the evidence row so the cards get it on every later
read — not only in the one refresh that fetched it."""

from __future__ import annotations

import asyncio

import backend.monitoring.asset_brief as ab


class _Reader:
    async def fetch_page_with_meta(self, url):
        return ("A long enough article body. " * 40, "https://cdn.example.com/hero.jpg")


def test_full_text_pass_attaches_the_og_image_and_writes_it_back(monkeypatch):
    monkeypatch.setattr(ab, "get_search_provider", lambda: _Reader())
    monkeypatch.setattr(ab, "assert_public_url", lambda url: None)
    written = []
    monkeypatch.setattr(
        ab, "attach_evidence_image", lambda url, image: written.append((url, image))
    )
    items = [
        {"url": "https://news.example.com/a", "snippet": "short", "image": None},
        {
            "url": "https://news.example.com/b",
            "snippet": "short",
            "image": "https://already.example.com/x.jpg",
        },
    ]
    asyncio.run(ab._fetch_full_text(items))
    assert items[0]["image"] == "https://cdn.example.com/hero.jpg"
    assert items[1]["image"] == "https://already.example.com/x.jpg"  # an existing image is kept
    assert written == [("https://news.example.com/a", "https://cdn.example.com/hero.jpg")]


class _WallReader:
    async def fetch_page_with_meta(self, url):
        # A bot-wall/consent page: the text fails _looks_blocked, and its og:image is the
        # site's own logo — never the article's picture.
        return (
            "Please enable cookies. Access denied. " * 20,
            "https://s.yimg.com/yahoo-finance-logo.png",
        )


def test_a_blocked_page_never_donates_its_logo_as_the_article_image(monkeypatch):
    monkeypatch.setattr(ab, "get_search_provider", lambda: _WallReader())
    monkeypatch.setattr(ab, "assert_public_url", lambda url: None)
    monkeypatch.setattr(ab, "_looks_blocked", lambda text: True)
    written = []
    monkeypatch.setattr(
        ab, "attach_evidence_image", lambda url, image: written.append((url, image))
    )
    items = [
        {
            "url": "https://finance.yahoo.com/news/a",
            "snippet": "a real rss snippet long enough to keep " * 3,
            "image": None,
        }
    ]
    asyncio.run(ab._fetch_full_text(items))
    assert items[0]["image"] is None
    assert written == []
