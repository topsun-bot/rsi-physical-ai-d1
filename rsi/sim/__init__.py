"""RSI simulation package with lazy exports to keep submodules decoupled."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .d1_kinematics_dynamics import UnitreeD1PhysicsPlant
    from .validator import SimulationValidator

__all__ = ["SimulationValidator", "UnitreeD1PhysicsPlant"]


def __getattr__(attribute_name: str) -> Any:
    if attribute_name == "UnitreeD1PhysicsPlant":
        from .d1_kinematics_dynamics import UnitreeD1PhysicsPlant

        return UnitreeD1PhysicsPlant
    if attribute_name == "SimulationValidator":
        from .validator import SimulationValidator

        return SimulationValidator
    raise AttributeError(f"module {__name__!r} has no attribute {attribute_name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
