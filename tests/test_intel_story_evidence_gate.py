"""The scan only publishes market stories backed by evidence it actually gathered."""

from backend.monitoring.intel_agent import _reviewed_stories

URL = "https://news.example/report"


def _story(title: str, *, ticker: str = "MSFT", status: str = "active") -> dict:
    return {
        "type": "asset",
        "label": "Microsoft Corporation",
        "ticker": ticker,
        "status": status,
        "summary": "A material change.",
        "action_items": ["Review position"],
        "new_timeline_event": {
            "title": "New development",
            "sources": [{"title": title, "url": URL}],
        },
    }


def test_rejects_ticker_story_citing_unrelated_headline():
    assert _reviewed_stories([_story("Webb telescope spots distant planet")], {URL: ""}) == []


def test_rejects_source_not_in_collected_evidence():
    assert _reviewed_stories([_story("Microsoft expands Azure capacity")], {}) == []


def test_ignores_malformed_source_url():
    story = _story("Microsoft expands Azure capacity")
    story["new_timeline_event"]["sources"].insert(0, {"title": "Bad URL", "url": []})
    accepted = _reviewed_stories([story], {URL: ""})
    assert accepted[0]["new_timeline_event"]["sources"] == [
        {"title": "Microsoft expands Azure capacity", "url": URL}
    ]


def test_accepts_collected_on_topic_source():
    accepted = _reviewed_stories([_story("Microsoft expands Azure capacity")], {URL: ""})
    assert len(accepted) == 1


def test_downgrades_no_action_decision_to_monitoring():
    story = _story("Microsoft expands Azure capacity", status="action_needed")
    story["action_items"] = ["No action needed"]
    accepted = _reviewed_stories([story], {URL: ""})
    assert accepted[0]["status"] == "monitoring"
