"""An unpriced model's $0 is "unmeasured", and the turn says so (price_known)."""

from backend.ai.assistant import _price_known


def test_price_known_for_listed_unknown_and_local_models():
    assert _price_known("gpt-6.1-sol", "") is True
    assert _price_known("made-up-model-without-a-price", "") is False
    assert _price_known("llama3", "http://localhost:11434") is True


def test_a_cost_the_endpoint_reported_counts_as_known_for_an_untabled_model():
    from backend.ai.assistant import _turn_price_known

    alias = "~vendor/unlisted-model-latest"
    assert _turn_price_known(alias, "https://openrouter.ai/api/v1", 2, 2) is True
    assert _turn_price_known(alias, "https://openrouter.ai/api/v1", 2, 1) is False
    assert _turn_price_known(alias, "https://openrouter.ai/api/v1", 0, 0) is False
