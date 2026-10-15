from backend.api.chat import ChatRequest


def test_chat_request_accepts_new_optional_fields():
    req = ChatRequest(
        messages=[{"role": "user", "content": "hi"}],
        responding_agent="lucius",
        skills=["rebalance-check"],
    )
    assert req.responding_agent == "lucius"
    assert req.skills == ["rebalance-check"]


def test_new_fields_default_to_none():
    req = ChatRequest(messages=[{"role": "user", "content": "hi"}])
    assert req.responding_agent is None
    assert req.skills == []
