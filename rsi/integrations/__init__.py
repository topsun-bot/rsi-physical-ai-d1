"""Integrations with Google Gemini for Robotics, DimOS, and UniRobotAgent."""

from __future__ import annotations

import importlib
from typing import Any

_LAZY_EXPORT_MODULES: dict[str, str] = {
    "D1CameraPerceptionModule": "rsi.integrations.dimos_d1_blueprint",
    "FleetWaypoint": "rsi.integrations.dimos_d1_blueprint",
    "GeminiArgonAgentModule": "rsi.integrations.dimos_d1_blueprint",
    "UnitreeD1FleetModule": "rsi.integrations.dimos_d1_blueprint",
    "autoconnect": "rsi.integrations.dimos_d1_blueprint",
    "build_skill_waypoints": "rsi.integrations.dimos_d1_blueprint",
    "interpolate_cosine": "rsi.integrations.dimos_d1_blueprint",
    "DEFAULT_GEMINI_API_KEY": "rsi.integrations.gemini_robotics_planner",
    "DEFAULT_GEMINI_MODEL": "rsi.integrations.gemini_robotics_planner",
    "GeminiRoboticsPlan": "rsi.integrations.gemini_robotics_planner",
    "GeminiRoboticsPlanner": "rsi.integrations.gemini_robotics_planner",
    "SpatialDetection2D": "rsi.integrations.gemini_robotics_planner",
    "ContinuousGraspAndPlugEvolver": "rsi.integrations.grasp_and_plug_evolver",
    "GraspPlugIterationResult": "rsi.integrations.grasp_and_plug_evolver",
    "WorkbenchObjectTarget": "rsi.integrations.grasp_and_plug_evolver",
    "InProcessEnvironmentTransport": "rsi.integrations.uni_robot_agent_d1",
    "UniRobotAgentD1Harness": "rsi.integrations.uni_robot_agent_d1",
    "appendix_e_least_squares_triangulate": "rsi.integrations.uni_robot_agent_d1",
    "appendix_e_table_plane_sweep": "rsi.integrations.uni_robot_agent_d1",
    "detect_colored_object_pixel": "rsi.integrations.uni_robot_agent_d1",
}

__all__ = list(_LAZY_EXPORT_MODULES.keys())


def __getattr__(attribute_name: str) -> Any:
    target_module_path = _LAZY_EXPORT_MODULES.get(attribute_name)
    if target_module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {attribute_name!r}")
    module = importlib.import_module(target_module_path)
    return getattr(module, attribute_name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
