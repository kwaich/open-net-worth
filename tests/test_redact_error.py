"""core.redact_error — credentials quoted inside an error string are masked before storage."""

from __future__ import annotations

import pytest

from backend.core.redact_error import redact_error


@pytest.mark.parametrize(
    "raw, expected",
    [
        (
            "HTTPSConnectionPool: https://abc123:s3cret@bridge.simplefin.org/simplefin/accounts",
            "HTTPSConnectionPool: https://***@bridge.simplefin.org/simplefin/accounts",
        ),
        (
            "connect failed for postgresql://u:pw@db:5432/x",
            "connect failed for postgresql://***@db:5432/x",
        ),
        ("bad url https://user@example.com/path", "bad url https://***@example.com/path"),
        # a password containing '@' is still consumed up to the host
        ("https://u:p@ss@host/x", "https://***@host/x"),
    ],
)
def test_url_userinfo_is_masked(raw, expected):
    assert redact_error(raw) == expected


@pytest.mark.parametrize(
    "name",
    [
        "token",
        "access_token",
        "api_key",
        "apikey",
        "X-Api-Key",
        "client_secret",
        "password",
        "passwd",
        "access_url",
    ],
)
def test_query_values_named_like_a_credential_are_masked(name):
    out = redact_error(
        f"GET https://api.example.com/v1/items?limit=5&{name}=abc123XYZ&page=2 failed"
    )
    assert "abc123XYZ" not in out
    assert f"{name}=***" in out
    assert "limit=5" in out and "page=2" in out


def test_name_matching_is_case_insensitive():
    assert redact_error("?API_KEY=zzz") == "?API_KEY=***"


def test_a_value_stops_at_the_next_delimiter():
    out = redact_error('request (token=abc123) failed, token=def456"; retry')
    assert out == 'request (token=***) failed, token=***"; retry'


def test_an_email_address_is_not_userinfo():
    text = "mailbox alice@example.com refused the login"
    assert redact_error(text) == text


def test_a_url_with_an_at_sign_in_its_path_keeps_its_host():
    text = "GET https://example.com/users/@me failed"
    assert redact_error(text) == text


def test_ordinary_text_and_unrelated_params_are_untouched():
    text = "HTTP 401 from https://api.example.com/v1/items?limit=5&sort=name"
    assert redact_error(text) == text


def test_empty_value_is_left_alone():
    assert redact_error("token= was empty") == "token= was empty"


def test_redaction_is_idempotent():
    once = redact_error("https://u:pw@h/x?api_key=abc&token=def")
    assert redact_error(once) == once
    assert "pw" not in once and "abc" not in once and "def" not in once


# ── backstop shapes: headers, JSON bodies, signed URLs, opaque path tokens ───────────────────


@pytest.mark.parametrize(
    "raw, secret",
    [
        ("sent Authorization: Bearer sk-live-abcdef123456 to host", "sk-live-abcdef123456"),
        ("401 for authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
        ("retry with Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig failed", "eyJhbGciOiJIUzI1NiJ9"),
        ('{"error": "bad", "api_key": "AKIA1234567890"}', "AKIA1234567890"),  # gitleaks:allow
        ("{'client_secret': 'shh-its-secret', 'ok': 1}", "shh-its-secret"),
        ('{"password":"hunter2hunter2"}', "hunter2hunter2"),
        ('{"access_token": "ya29.a0AfH6SMB"}', "ya29.a0AfH6SMB"),  # gitleaks:allow
    ],
)
def test_headers_and_json_bodies_are_masked(raw, secret):
    out = redact_error(raw)
    assert secret not in out
    assert "***" in out


def test_json_shape_is_preserved_around_the_masked_value():
    assert redact_error('{"api_key": "abc123", "n": 1}') == '{"api_key": "***", "n": 1}'
    assert redact_error("{'token': 'abc'}") == "{'token': '***'}"


@pytest.mark.parametrize("name", ["sig", "signature", "code", "sas"])
def test_signature_query_params_are_masked(name):
    out = redact_error(f"GET https://api.example.com/v1/x?a=1&{name}=Zm9vYmFy&b=2 failed")
    assert "Zm9vYmFy" not in out
    assert f"{name}=***" in out
    assert "a=1" in out and "b=2" in out


def test_a_plain_code_in_prose_is_not_a_query_param():
    text = "upstream replied with code=404 and no body"
    assert redact_error(text) == text


def test_userinfo_whose_password_contains_a_slash_is_masked():
    out = redact_error("connect failed for https://alice:pa/ss/word@bridge.example.com/x")
    assert out == "connect failed for https://***@bridge.example.com/x"


def test_a_host_and_port_is_not_mistaken_for_userinfo():
    text = "GET http://localhost:8080/users/x?email=a@b.com failed"
    assert redact_error(text) == text


def test_a_zapier_style_token_in_the_path_is_masked():
    token = "Zk9aN3pTQ1JvM2xYd0Y5dTZ1Rm5hS2ZP"  # gitleaks:allow (fake token fixture)
    out = redact_error(f"cannot reach https://actions.zapier.com/api/mcp/s/{token}/mcp: timed out")
    assert token not in out
    assert out == "cannot reach https://actions.zapier.com/api/mcp/s/***/mcp: timed out"


def test_a_discord_webhook_path_is_masked():
    token = "abCDef1234567890ghIJkl-MNopQR_stuvWX0123456789yzABcdEFghIJ"  # gitleaks:allow (fake token fixture)
    out = redact_error(
        f"POST https://discord.com/api/webhooks/123456789012345678/{token} returned 400"
    )
    assert token not in out
    assert "/api/webhooks/123456789012345678/***" in out


def test_ordinary_long_path_words_are_left_alone():
    # long but no digits, and a short id with digits: neither is an opaque token
    text = "GET https://example.com/internationalization-and-localization/v2 failed"
    assert redact_error(text) == text


def test_the_new_shapes_are_idempotent():
    raw = (
        'Authorization: Bearer abcdef123456 {"token": "xyz"} '
        "https://h/api/mcp/s/Zk9aN3pTQ1JvM2xYd0Y5dTZ1/mcp?sig=abc&code=def"
    )
    once = redact_error(raw)
    assert redact_error(once) == once
