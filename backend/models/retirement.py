"""The Outlook's lever vocabulary — what a retirement what-if can change.

The same camelCase keys ``plan_state`` stores for the baseline and for saved scenarios and
``create_plan_scenario`` / ``set_plan_baseline`` take, so the screen, the saved plan and Lucius
all talk about one set of levers. Every field is optional: anything left out is the SAVED plan's
value (``services.retirement_plan``), which is how a call with no overrides reproduces the
screen exactly.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

# Bounds are abuse guards, not product rules — the sliders and plan_state are tighter.
MAX_LEVER_ITEMS = 50
_Money = Field(default=None, allow_inf_nan=False, ge=-1e12, le=1e12)
_Age = Field(default=None, allow_inf_nan=False, ge=0, le=150)
_Pct = Field(default=None, allow_inf_nan=False, ge=0, le=100)


class StreamLever(BaseModel):
    """Guaranteed income (CPF LIFE, a pension, rent): ``amount`` a month from ``age`` until
    ``endAge`` (exclusive; omit for life)."""

    model_config = ConfigDict(extra="ignore")

    label: str = ""
    amount: float = Field(allow_inf_nan=False, ge=0, le=1e9)
    age: float = Field(allow_inf_nan=False, ge=0, le=150)
    endAge: Optional[float] = Field(  # noqa: N815 — camelCase is the wire contract
        default=None, allow_inf_nan=False, ge=0, le=150
    )


class EventLever(BaseModel):
    """A one-off cash flow at ``age``: negative = spend, positive = windfall (a ledger sign)."""

    model_config = ConfigDict(extra="ignore")

    id: str = ""
    label: str = ""
    amount: float = Field(allow_inf_nan=False, ge=-1e12, le=1e12)
    age: float = Field(allow_inf_nan=False, ge=0, le=150)


class ShockWindows(BaseModel):
    """Per-shock age overrides; an absent one uses the shock's default window."""

    model_config = ConfigDict(extra="ignore")

    crashAge: Optional[float] = _Age  # noqa: N815
    weakFrom: Optional[float] = _Age  # noqa: N815
    weakTo: Optional[float] = _Age  # noqa: N815
    lostFrom: Optional[float] = _Age  # noqa: N815
    lostTo: Optional[float] = _Age  # noqa: N815
    inflFrom: Optional[float] = _Age  # noqa: N815
    inflTo: Optional[float] = _Age  # noqa: N815


class OutlookLevers(BaseModel):
    model_config = ConfigDict(extra="ignore")

    saving: Optional[float] = _Money
    ret: Optional[float] = _Pct
    vol: Optional[float] = _Pct
    monthlyRetirementSpending: Optional[float] = _Money  # noqa: N815
    swr: Optional[float] = Field(default=None, allow_inf_nan=False, gt=0, le=100)
    lifeExp: Optional[float] = _Age  # noqa: N815
    retireAge: Optional[float] = _Age  # noqa: N815
    streams: Optional[List[StreamLever]] = Field(default=None, max_length=MAX_LEVER_ITEMS)
    events: Optional[List[EventLever]] = Field(default=None, max_length=MAX_LEVER_ITEMS)
    crash: Optional[bool] = None
    weakSeq: Optional[bool] = None  # noqa: N815
    lostDec: Optional[bool] = None  # noqa: N815
    highInfl: Optional[bool] = None  # noqa: N815
    shockCfg: Optional[ShockWindows] = None  # noqa: N815
    startNW: Optional[float] = _Money  # noqa: N815 — a "what if I start from X" override
