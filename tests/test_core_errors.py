"""Unit tests for backend.core.errors (RFC P1-A1 §3.4).

Covers the code→HTTP status map and the message/context carrying. The module is
framework-free; these assert plain ints, never FastAPI types.
"""

from __future__ import annotations

import pytest

from backend.core.errors import (
    ConfigurationError,
    ConflictError,
    ExternalServiceError,
    MissingFilterError,
    NotFoundError,
    OpenNetWorthError,
    ValidationError,
    to_http_status,
)


def test_each_class_has_stable_code():
    assert OpenNetWorthError.code == "error"
    assert NotFoundError.code == "not_found"
    assert ValidationError.code == "validation"
    assert ConflictError.code == "conflict"
    assert ExternalServiceError.code == "upstream"
    assert ConfigurationError.code == "config"
    assert MissingFilterError.code == "missing_filter"


@pytest.mark.parametrize(
    "exc, status",
    [
        (NotFoundError(), 404),
        (ValidationError(), 422),
        (ConflictError(), 409),
        (ExternalServiceError(), 502),
        (ConfigurationError(), 500),
        (OpenNetWorthError(), 500),
        (MissingFilterError(), 400),
    ],
)
def test_to_http_status_maps_each_code(exc, status):
    assert to_http_status(exc) == status


def test_missing_filter_error_is_a_validation_error():
    # A caller that only catches ValidationError (the general "bad input" case) still
    # catches this — it's a specific flavor of validation failure, not a new hierarchy.
    with pytest.raises(ValidationError):
        raise MissingFilterError("At least one filter is required")


def test_to_http_status_returns_plain_int():
    assert type(to_http_status(NotFoundError())) is int


def test_unknown_code_defaults_to_500():
    class Weird(OpenNetWorthError):
        code = "no_such_code"

    assert to_http_status(Weird()) == 500


def test_message_and_context_are_carried():
    err = ValidationError("bad input", field="amount", value=-1)
    assert err.message == "bad input"
    assert err.context == {"field": "amount", "value": -1}
    assert str(err) == "bad input"


def test_hierarchy_is_catchable_as_base():
    with pytest.raises(OpenNetWorthError):
        raise NotFoundError("missing account")
