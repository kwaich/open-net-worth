"""Asset analysis cannot be written under a different instrument's story."""

import backend.monitoring.analyst as analyst

_analysis_matches_subject = analyst._analysis_matches_subject


def test_rejects_kestrel_dash_analysis_on_bitcoin_row():
    row = {"subject": "asset:btc", "ticker": "BTC", "payload": {"label": "Bitcoin"}}
    parsed = {
        "headline": "Kestrel Dash extends CEO contract",
        "why": "Kestrel Dash filed an ad-hoc notice about its CEO.",
    }
    assert not _analysis_matches_subject(row, parsed, ["Kestrel Dash SE"])


def test_accepts_matching_asset_analysis():
    row = {"subject": "asset:btc", "ticker": "BTC", "payload": {"label": "Bitcoin"}}
    parsed = {"headline": "Bitcoin slips on rates", "why": "BTC fell with crypto markets."}
    assert _analysis_matches_subject(row, parsed)


def test_checks_watched_asset_names_when_not_supplied(monkeypatch):
    monkeypatch.setattr(
        analyst,
        "read_watchlist",
        lambda: {"items": [{"type": "ticker", "label": "Kestrel Dash SE"}]},
    )
    row = {"subject": "asset:btc", "ticker": "BTC", "payload": {"label": "Bitcoin"}}
    parsed = {"headline": "Kestrel Dash extends CEO contract", "why": "A leadership update."}
    assert not _analysis_matches_subject(row, parsed)


def test_topic_analysis_does_not_require_asset_name():
    row = {"subject": "topic:fed", "ticker": "", "payload": {"label": "Rates"}}
    assert _analysis_matches_subject(row, {"headline": "Treasury yields rise", "why": ""})
