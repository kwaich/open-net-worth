"""Tool-surface arms — each a projection of the shared ``ops`` registry."""

from __future__ import annotations

from backend.evals.tool_surface.arms.arm_a import ArmA
from backend.evals.tool_surface.arms.arm_a_real import ArmAReal
from backend.evals.tool_surface.arms.arm_b import ArmB
from backend.evals.tool_surface.arms.arm_c import ArmC
from backend.evals.tool_surface.arms.base import Arm, DispatchTrace

#: Arms compared by default. ``A-real`` is a fidelity control, not a candidate, so it
#: is opt-in via ``--arms``.
DEFAULT_ARMS = ("A", "B", "C")

ARM_CLASSES: dict[str, type[Arm]] = {
    "A": ArmA,
    "B": ArmB,
    "C": ArmC,
    "A-real": ArmAReal,
}


def build_arm(spec, decoys: list[str] | None = None) -> Arm:
    """Instantiate the arm named by ``spec.surface`` (or a bare surface key)."""
    surface = getattr(spec, "surface", spec)
    cls = ARM_CLASSES.get(surface)
    if cls is None:
        raise KeyError(f"unknown surface {surface!r} — choose from {sorted(ARM_CLASSES)}")
    if isinstance(spec, str):
        return cls(decoys=decoys)
    return cls(decoys=decoys, spec=spec)


__all__ = [
    "Arm",
    "DispatchTrace",
    "ArmA",
    "ArmB",
    "ArmC",
    "ArmAReal",
    "ARM_CLASSES",
    "DEFAULT_ARMS",
    "build_arm",
]
