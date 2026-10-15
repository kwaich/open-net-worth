"""Outlook plan-scenario + FIRE-baseline AI tools."""

from __future__ import annotations

import json
import logging
from typing import List, Optional

from pydantic import ConfigDict, Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of, schema_of_no_required

logger = logging.getLogger(__name__)


class PlanStream(ToolInput):
    label: str = Field(description="Display name, e.g. 'Rental'.")
    amount: float = Field(description="Monthly amount in the base currency.")
    age: int = Field(description="Age the stream starts.")
    endAge: Optional[int] = Field(  # noqa: N815 — camelCase is the wire contract
        default=None, description="Age the stream stops (omit = lasts for life)."
    )


class PlanEvent(ToolInput):
    label: str = Field(description="Display name, e.g. 'Home'.")
    amount: float = Field(
        description="Signed like a ledger amount: negative = one-off spend, positive = windfall."
    )
    age: int = Field(description="Age the event lands.")


class LeverInput(ToolInput):
    """The shared Monte Carlo lever block — the ONE source of truth for both tools here.

    ``extra="allow"`` is what emits ``additionalProperties: true``: the extra camelCase
    levers (atNumber, crash, weakSeq, lostDec, highInfl, shockCfg, …) are passed through
    verbatim, so forbidding them would refuse calls that work today.
    """

    model_config = ConfigDict(extra="allow")

    ret: Optional[float] = Field(
        default=None, description="Expected annual return % (0–100). E.g. 7 for 7 %."
    )
    vol: Optional[float] = Field(
        default=None, description="Annual volatility % (0–100). E.g. 15 for 15 %."
    )
    swr: Optional[float] = Field(
        default=None, description="Safe withdrawal rate % (0–100). E.g. 4 for the 4 % rule."
    )
    saving: Optional[float] = Field(
        default=None,
        description="Monthly savings amount in the user's base currency (negative = spending more than they earn).",
    )
    monthlyRetirementSpending: Optional[float] = Field(  # noqa: N815 — wire contract
        default=None,
        ge=0,
        allow_inf_nan=False,
        description="Monthly retirement spending in the user's base currency (>=0).",
    )
    lifeExp: Optional[int] = Field(  # noqa: N815 — camelCase is the wire contract
        default=None, description="Life expectancy in years (1–150)."
    )
    retireAge: Optional[int] = Field(  # noqa: N815 — camelCase is the wire contract
        default=None, description="Target retirement age (1–150)."
    )
    streams: Optional[List[PlanStream]] = Field(
        default=None,
        description=(
            "Additional income streams (max 50). camelCase field names — an off-contract "
            "key (e.g. end_age) is REFUSED with a corrective error, never coerced."
        ),
    )
    events: Optional[List[PlanEvent]] = Field(
        default=None,
        description=(
            "One-off life events (max 50). camelCase field names — an off-contract key is "
            "REFUSED with a corrective error, never coerced."
        ),
    )


class CreatePlanScenarioInput(LeverInput):
    name: str = Field(
        description="Human-readable scenario label shown in the UI (must not be empty)."
    )


# ``set_plan_baseline``'s schema stays a hand-written literal (its ``baseline`` is a
# ``oneOf[lever-object, null]`` where the explicit null CLEARS the baseline — Pydantic
# emits ``anyOf`` and the nullable collapse would drop the null branch, silently removing
# the ability to clear). It embeds the DERIVED lever block rather than a second copy of
# it, so there is still exactly one source of truth for these fields.
_LEVER_PROPS = schema_of_no_required(LeverInput)["properties"]


class ListPlanScenariosInput(ToolInput):
    """Schema-of-record for ``list_plan_scenarios`` (see ``schema_from_model``)."""


@tool(
    "list_plan_scenarios",
    description=(
        "Return all saved Outlook plan scenarios. Each scenario contains the user's "
        "assumption levers (expected return, volatility, SWR, saving, monthly retirement spending, etc.) "
        "stored for the Monte Carlo 'save & compare' feature. "
        "Call this to enumerate scenarios before updating or deleting one."
    ),
    input_schema=schema_of(ListPlanScenariosInput, tool_name="list_plan_scenarios"),
)
async def list_plan_scenarios(input_data: dict):
    from backend.services import plan_state as svc

    scenarios = svc.get_scenarios()
    logger.info("tool list_plan_scenarios: count=%d", len(scenarios))
    return json.dumps({"scenarios": scenarios}, ensure_ascii=False), None


@tool(
    "create_plan_scenario",
    description=(
        "Save a new Outlook plan scenario — a named set of Monte Carlo assumption "
        "levers (return, volatility, SWR, saving, monthly retirement spending, retirement age, life "
        "expectancy, income streams, life events). The server assigns the id. "
        "All numeric lever fields are optional; omitted fields fall back to the "
        "user's FIRE profile settings at simulation time. Extra camelCase fields "
        "(atNumber, crash, weakSeq, lostDec, highInfl, shockCfg, …) are passed "
        "through verbatim."
    ),
    input_schema=schema_of(CreatePlanScenarioInput, tool_name="create_plan_scenario"),
    mutates=("plan",),
)
async def create_plan_scenario(input_data: dict):
    from backend.core.errors import OpenNetWorthError
    from backend.services import plan_state as svc

    try:
        result = svc.add_scenario(input_data)
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    logger.info("tool create_plan_scenario: id=%s name=%s", result.get("id"), result.get("name"))
    return json.dumps({"ok": True, "scenario": result}, ensure_ascii=False), None


class _LeverStreamInput(ToolInput):
    """Nested ``streams[]`` item — mirrors ``_LEVER_PROPS['streams']['items']``."""

    label: str = Field(
        description=_LEVER_PROPS["streams"]["items"]["properties"]["label"]["description"]
    )
    amount: float = Field(
        description=_LEVER_PROPS["streams"]["items"]["properties"]["amount"]["description"]
    )
    age: int = Field(
        description=_LEVER_PROPS["streams"]["items"]["properties"]["age"]["description"]
    )
    endAge: Optional[int] = Field(
        default=None,
        description=_LEVER_PROPS["streams"]["items"]["properties"]["endAge"]["description"],
    )


class _LeverEventInput(ToolInput):
    """Nested ``events[]`` item — mirrors ``_LEVER_PROPS['events']['items']``."""

    label: str = Field(
        description=_LEVER_PROPS["events"]["items"]["properties"]["label"]["description"]
    )
    amount: float = Field(
        description=_LEVER_PROPS["events"]["items"]["properties"]["amount"]["description"]
    )
    age: int = Field(
        description=_LEVER_PROPS["events"]["items"]["properties"]["age"]["description"]
    )


class UpdatePlanScenarioInput(ToolInput):
    """Schema-of-record for ``update_plan_scenario`` (see ``schema_from_model``).

    Mirrors ``_LEVER_PROPS`` (also used, as a raw dict, by the still-literal
    ``create_plan_scenario``/``set_plan_baseline``) field-for-field — descriptions are
    pulled from that same dict so the two can never drift apart in wording.
    """

    scenario_id: str = Field(
        description="The server-assigned id of the scenario to replace (from list_plan_scenarios)."
    )
    name: str = Field(description="New scenario name (must not be empty).")
    ret: Optional[float] = Field(default=None, description=_LEVER_PROPS["ret"]["description"])
    vol: Optional[float] = Field(default=None, description=_LEVER_PROPS["vol"]["description"])
    swr: Optional[float] = Field(default=None, description=_LEVER_PROPS["swr"]["description"])
    saving: Optional[float] = Field(default=None, description=_LEVER_PROPS["saving"]["description"])
    monthlyRetirementSpending: Optional[float] = Field(  # noqa: N815 — wire contract
        default=None,
        ge=0,
        allow_inf_nan=False,
        description=_LEVER_PROPS["monthlyRetirementSpending"]["description"],
    )
    lifeExp: Optional[int] = Field(default=None, description=_LEVER_PROPS["lifeExp"]["description"])
    retireAge: Optional[int] = Field(
        default=None, description=_LEVER_PROPS["retireAge"]["description"]
    )
    streams: Optional[List[_LeverStreamInput]] = Field(
        default=None, description=_LEVER_PROPS["streams"]["description"]
    )
    events: Optional[List[_LeverEventInput]] = Field(
        default=None, description=_LEVER_PROPS["events"]["description"]
    )


@tool(
    "update_plan_scenario",
    description=(
        "Replace the levers of an existing Outlook plan scenario. "
        "All fields in the body REPLACE the stored scenario (a full PUT, not a patch). "
        "Call list_plan_scenarios first to obtain the scenario_id. "
        "Raises an error if the scenario is not found."
    ),
    input_schema=schema_of(UpdatePlanScenarioInput, tool_name="update_plan_scenario"),
    mutates=("plan",),
)
async def update_plan_scenario(input_data: dict):
    from backend.core.errors import OpenNetWorthError
    from backend.services import plan_state as svc

    scenario_id = input_data.get("scenario_id", "")
    body = {k: v for k, v in input_data.items() if k != "scenario_id"}
    try:
        result = svc.update_scenario(scenario_id, body)
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    logger.info("tool update_plan_scenario: id=%s name=%s", result.get("id"), result.get("name"))
    return json.dumps({"ok": True, "scenario": result}, ensure_ascii=False), None


class DeletePlanScenarioInput(ToolInput):
    """Schema-of-record for ``delete_plan_scenario`` (see ``schema_from_model``)."""

    scenario_id: str = Field(description="The server-assigned id of the scenario to delete.")


@tool(
    "delete_plan_scenario",
    description=(
        "Permanently delete an Outlook plan scenario by its id. "
        "Call list_plan_scenarios first to confirm the scenario_id. "
        "Raises an error if the scenario is not found."
    ),
    input_schema=schema_of(DeletePlanScenarioInput, tool_name="delete_plan_scenario"),
    mutates=("plan",),
)
async def delete_plan_scenario(input_data: dict):
    from backend.core.errors import OpenNetWorthError
    from backend.services import plan_state as svc

    scenario_id = input_data.get("scenario_id", "")
    try:
        svc.delete_scenario(scenario_id)
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    logger.info("tool delete_plan_scenario: id=%s", scenario_id)
    return json.dumps({"ok": True, "deleted_id": scenario_id}, ensure_ascii=False), None


class GetPlanBaselineInput(ToolInput):
    """Schema-of-record for ``get_plan_baseline`` (see ``schema_from_model``)."""


@tool(
    "get_plan_baseline",
    description=(
        "Return the FIRE baseline override — the user's 'My FIRE Plan' lever set that "
        "anchors the Outlook tab's primary projection. Includes 'events': life events "
        "(a car purchase, house purchase, or other one-off future expense/windfall modeled "
        "as a HYPOTHETICAL what-if in the FIRE plan — NOT a real tracked obligation; for "
        "those use get_commitments instead) — and 'streams': additional income streams. "
        "Returns null when no override is set (the simulation falls back to FIRE profile "
        "defaults). Call this before set_plan_baseline to inspect the current state."
    ),
    input_schema=schema_of(GetPlanBaselineInput, tool_name="get_plan_baseline"),
)
async def get_plan_baseline(input_data: dict):
    from backend.services import plan_state as svc

    baseline = svc.get_baseline()
    logger.info("tool get_plan_baseline: is_set=%s", baseline is not None)
    return json.dumps({"baseline": baseline}, ensure_ascii=False), None


@tool(
    "set_plan_baseline",
    description=(
        "Store or clear the FIRE baseline override. "
        "Pass a lever object to set a baseline (same numeric fields as a scenario: "
        "ret, vol, swr, saving, monthlyRetirementSpending, lifeExp, retireAge, streams, events, …). "
        "Pass null to clear the baseline and revert to FIRE profile defaults. "
        "Numeric levers are range-validated before saving."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "baseline": {
                "description": (
                    "The lever object to store as the FIRE baseline, or null to clear it. "
                    "Fields: ret (return %), vol (volatility %), swr (SWR %), "
                    "saving (monthly save), monthlyRetirementSpending, lifeExp, retireAge, "
                    "streams (array), events (array). All optional."
                ),
                "oneOf": [
                    {
                        "type": "object",
                        "additionalProperties": True,
                        "properties": {k: v for k, v in _LEVER_PROPS.items()},
                    },
                    {"type": "null"},
                ],
            },
        },
        "required": ["baseline"],
    },
    mutates=("plan",),
)
async def set_plan_baseline(input_data: dict):
    from backend.core.errors import OpenNetWorthError
    from backend.services import plan_state as svc

    obj = input_data.get("baseline")
    if obj == {}:
        obj = None
    try:
        result = svc.set_baseline(obj)
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    logger.info("tool set_plan_baseline: cleared=%s", result is None)
    return json.dumps({"ok": True, "baseline": result}, ensure_ascii=False), None
