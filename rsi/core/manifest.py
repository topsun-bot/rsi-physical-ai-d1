from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Tuple
import yaml
import numpy as np


class RuleManifest:
    """
    阶段 1：人类设定规则、技能、约束与工具集 (Human-in-the-Loop Rule & Constraint Manifest)
    负责加载 `configs/d1_manifest.yaml` 并向后续各阶段提供强类型的物理护栏与权限校验。
    """

    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)
        if not self.config_path.exists():
            raise FileNotFoundError(f"Manifest file not found: {self.config_path}")
        with open(self.config_path, "r", encoding="utf-8") as f:
            self.raw: Dict[str, Any] = yaml.safe_load(f)

        self.robot_spec: Dict[str, Any] = self.raw["robot_spec"]
        self.safety_constraints: Dict[str, Any] = self.raw["safety_constraints"]
        self.target_skills: List[Dict[str, Any]] = self.raw["target_skills"]
        self.authorized_tools: Dict[str, Any] = self.raw["authorized_tools"]

        # Precompute arm joint position limits in radians (6 revolute joints)
        deg_limits = self.safety_constraints["joint_position_limits_deg"]
        self.joint_min_rad = np.array(
            [math.radians(deg_limits[f"joint{i}"][0]) for i in range(6)],
            dtype=np.float64,
        )
        self.joint_max_rad = np.array(
            [math.radians(deg_limits[f"joint{i}"][1]) for i in range(6)],
            dtype=np.float64,
        )
        self.gripper_limits_mm = tuple(deg_limits["joint6_gripper"])
        self.max_vel_rad_s = np.array(
            self.safety_constraints["max_joint_velocity_rad_s"][:6],
            dtype=np.float64,
        )
        self.max_torque_nm = np.array(
            self.safety_constraints["max_joint_torque_nm"][:6],
            dtype=np.float64,
        )
        self.workspace_bounds = self.safety_constraints["workspace_bounds_m"]
        self.sim2real_gate = self.safety_constraints["sim_to_real_gate"]

    def get_primary_skill(self) -> Dict[str, Any]:
        return self.target_skills[0]

    def check_joint_limits(self, q_rad: np.ndarray) -> Tuple[bool, str]:
        """校验 6 轴关节角是否处于人类设定的安全包络内"""
        below = q_rad < (self.joint_min_rad - 1e-3)
        above = q_rad > (self.joint_max_rad + 1e-3)
        if np.any(below) or np.any(above):
            violated = np.where(below | above)[0].tolist()
            return False, f"Joint limit violation on joints {violated}: q={np.round(np.degrees(q_rad), 1)} deg"
        return True, "OK"

    def check_workspace_safety(self, ee_pos_xyz: np.ndarray) -> Tuple[bool, str]:
        """校验末端执行器笛卡尔坐标是否违反桌面防碰撞高度或最大工作半径"""
        x, y, z = float(ee_pos_xyz[0]), float(ee_pos_xyz[1]), float(ee_pos_xyz[2])
        wb = self.workspace_bounds
        if z < wb["z"][0]:
            return False, f"Table collision hazard: z={z:.4f}m < min_z={wb['z'][0]}m"
        if z > wb["z"][1]:
            return False, f"Height ceiling violation: z={z:.4f}m > max_z={wb['z'][1]}m"
        radial = math.sqrt(x * x + y * y + z * z)
        if radial > self.robot_spec["max_reach_with_gripper_m"]:
            return False, f"Reach envelope violation: r={radial:.4f}m"
        return True, "OK"

    def clamp_torque(self, tau_nm: np.ndarray) -> Tuple[np.ndarray, bool]:
        """对关节力矩执行硬限幅并返回是否触发限幅"""
        clamped = np.clip(tau_nm, -self.max_torque_nm, self.max_torque_nm)
        was_clamped = not np.allclose(clamped, tau_nm, atol=1e-4)
        return clamped, was_clamped

    def summary_banner(self) -> str:
        skill = self.get_primary_skill()
        return (
            f"Robot: {self.robot_spec['model']} ({self.robot_spec['dof_arm']}+{self.robot_spec['dof_gripper']} DOF) | "
            f"Skill: {skill['skill_id']} | "
            f"DDS Topic: {self.robot_spec['dds_protocol']['command_topic']} | "
            f"Min Table Z: {self.workspace_bounds['z'][0]}m | "
            f"Sim2Real Gate Score >= {self.sim2real_gate['min_sim_total_score']}"
        )
