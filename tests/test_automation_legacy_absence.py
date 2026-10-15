"""The unified Automation cutover must not expose zombie legacy write surfaces."""

from fastapi import routing as fastapi_routing

from backend.ai.tools import registered_tool_names
from backend.main import app


def test_application_does_not_mount_legacy_automation_routes() -> None:
    # FastAPI >=0.141 defers `include_router` into lazy `_IncludedRouter` wrappers,
    # so `app.routes` is no longer a flat list of objects with `.path` — walk it via
    # the same `iter_route_contexts` flattening helper FastAPI's own OpenAPI
    # generator uses (fastapi/openapi/utils.py) to recover the effective path set.
    paths = {
        ctx.path for ctx in fastapi_routing.iter_route_contexts(app.routes) if ctx.path is not None
    }
    assert not any(path.startswith("/api/rules") for path in paths)
    assert not any(path.startswith("/api/scheduled-tasks") for path in paths)
    assert "/api/automations" in paths


def test_model_cannot_call_legacy_automation_tools() -> None:
    names = set(registered_tool_names())
    assert "create_automation" in names
    assert not names.intersection(
        {
            "apply_rules_to_history",
            "cancel_scheduled_task",
            "create_rules",
            "delete_rules",
            "list_rules",
            "list_scheduled_tasks",
            "run_scheduled_task_now",
            "schedule_task",
            "test_rules",
            "update_rules",
            "update_scheduled_task",
        }
    )
