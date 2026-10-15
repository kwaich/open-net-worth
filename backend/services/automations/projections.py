"""Human-readable projections for private deterministic Automation executors."""

from __future__ import annotations

import json


def describe_lucius_transaction_event(trigger: dict, spec: dict) -> str:
    """Render the durable structured transaction-event spec as Lucius instructions."""
    action = dict(spec.get("set") or {})
    priority = int(spec.get("priority", 100))
    stop = bool(spec.get("stop", False))
    override = bool(spec.get("override_existing", False))
    when = dict(trigger.get("when") or {})
    return (
        "This Automation handles the transaction event supplied with the run. Re-check the "
        "match conditions below against that transaction and take no action if any condition "
        "does not match. Otherwise call update_transactions for only that transaction and "
        "apply exactly the transformation below. A category replaces an existing category "
        f"only when override_existing is true ({str(override).lower()}); tags are additive "
        "without duplicates; note_append and memo_append append once; route_to_account uses "
        "the ordinary validated account move. Do not modify any other field or transaction. "
        f"Execution order is priority {priority} with stop={str(stop).lower()}; those values "
        "coordinate matching Automations and do not authorize extra writes.\n\n"
        "Match conditions:\n"
        f"{json.dumps(when, ensure_ascii=False, indent=2, sort_keys=True)}\n\n"
        "Transformation:\n"
        f"{json.dumps(action, ensure_ascii=False, indent=2, sort_keys=True)}"
    )


def describe_transaction_transform(automation: dict) -> str:
    """Describe a private transaction transform without exposing executor vocabulary."""
    when = (automation.get("trigger") or {}).get("when") or {}
    execution = automation.get("execution") or {}
    action = execution.get("set") or {}
    conditions: list[str] = []
    actions: list[str] = []

    for key, label in (
        ("description_contains", "description contains"),
        ("description_regex", "description matches"),
        ("memo_contains", "memo contains"),
        ("memo_regex", "memo matches"),
        ("account_id", "account is"),
        ("kind", "kind is"),
        ("direction", "direction is"),
        ("currency", "currency is"),
    ):
        value = when.get(key)
        if value:
            conditions.append(f"{label} {value!r}")
    for key, label in (
        ("keywords", "description contains any of"),
        ("memo_keywords", "memo contains any of"),
    ):
        values = [str(value) for value in (when.get(key) or []) if str(value).strip()]
        if values:
            conditions.append(f"{label} {', '.join(values)}")
    if when.get("min_amount") is not None:
        conditions.append(f"amount is at least {when['min_amount']}")
    if when.get("max_amount") is not None:
        conditions.append(f"amount is at most {when['max_amount']}")

    if action.get("category"):
        category = action["category"]
        try:
            from backend.db.category_store import CategoryStore

            category = (CategoryStore().get(category) or {}).get("label") or category
        except Exception:  # display fallback must not block a referential rewrite
            pass
        actions.append(f"set category to {category}")
    if action.get("tags"):
        actions.append(f"add tags {', '.join(str(tag) for tag in action['tags'])}")
    if action.get("note_append"):
        actions.append(f"append {action['note_append']!r} to the description")
    if action.get("memo_append"):
        actions.append(f"append {action['memo_append']!r} to the memo")
    if action.get("route_to_account"):
        actions.append(f"route statement rows to account {action['route_to_account']}")

    condition_text = " and ".join(conditions) or "the transaction matches"
    action_text = ", then ".join(actions) or "leave it unchanged"
    return f"When {condition_text}, {action_text}."
