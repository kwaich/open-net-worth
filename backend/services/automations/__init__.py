"""Canonical Automation service surface."""

from .activity import automation_activity
from .runs import run_automation, run_due_automations
from .service import apply_automation_change

__all__ = [
    "apply_automation_change",
    "automation_activity",
    "run_automation",
    "run_due_automations",
]
