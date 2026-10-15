"""The wire shape of a problem report — what the preview shows and what is sent, verbatim.

The limits mirror the receiving endpoint (onw-cloud ``services/portal``); a report the portal
would refuse is refused here first, with the same words. Nothing in this model rewrites a
field: the user's edited preview must be the exact payload that leaves the machine.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ReportMode = Literal["desktop", "web", "server"]

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ProblemReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(max_length=5000)
    contact_email: str | None = Field(default=None, max_length=254)
    app_version: str = Field(min_length=1, max_length=64)
    platform: str = Field(min_length=1, max_length=128)
    mode: ReportMode
    screen: str = Field(default="", max_length=256)
    error: str | None = Field(default=None, max_length=4000)
    logs: str | None = Field(default=None, max_length=65536)

    @field_validator("description")
    @classmethod
    def _described(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Describe what went wrong.")
        return value

    @field_validator("contact_email")
    @classmethod
    def _contact(cls, value: str | None) -> str | None:
        if value is not None and not _EMAIL.match(value):
            raise ValueError("That email address doesn't look right.")
        return value


class ProblemReportDraftRequest(BaseModel):
    """What the page knows when it opens the report: the error it is reporting and where it is."""

    model_config = ConfigDict(extra="forbid")

    error: str | None = Field(default=None, max_length=4000)
    screen: str = Field(default="", max_length=256)
    include_logs: bool = True
