from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rsi.core.manifest import RuleManifest
from rsi.core.types import (
    ControlPolicyCandidate,
    ExecutableSkill,
    JointEvaluationReport,
)


class ControllerCodeSynthesizer:
    """
    阶段 3：AI 自主编写机器人控制代码 (Autonomous Controller & Reward Code Synthesizer)
    基于阶段 2 抽取出的 `ExecutableSkill` 公式与架构，以及阶段 6 的 AI+人类联合评估反馈，
    生成或进化 Unitree D1 (6+1 DOF) 控制器代码，并通过 AST 静态安全分析器验证代码合规性。
    """

    def __init__(self, manifest: RuleManifest, runs_dir: str | Path):
        self.manifest = manifest
        self.runs_dir = Path(runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def synthesize_candidate(
        self,
        generation: int,
        skill: ExecutableSkill,
        parent_params: Optional[Dict[str, float]] = None,
        prev_evaluation: Optional[JointEvaluationReport] = None,
    ) -> ControlPolicyCandidate:
        candidate_id = f"d1_policy_gen{generation:02d}"
        gen_dir = self.runs_dir / f"gen_{generation:02d}"
        gen_dir.mkdir(parents=True, exist_ok=True)

        params, rationale = self._evolve_hyperparameters(
            generation=generation,
            skill=skill,
            parent_params=parent_params,
            prev_evaluation=prev_evaluation,
        )

        source_code = self._generate_controller_python_code(
            generation=generation,
            candidate_id=candidate_id,
            skill=skill,
            params=params,
            rationale=rationale,
        )

        code_file_path = gen_dir / f"{candidate_id}.py"
        code_file_path.write_text(source_code, encoding="utf-8")

        ast_passed, ast_notes = self.verify_code_ast_safety(source_code)

        return ControlPolicyCandidate(
            generation=generation,
            candidate_id=candidate_id,
            skill_id=skill.skill_id,
            hyperparameters=params,
            source_code=source_code,
            code_file_path=str(code_file_path),
            ast_safety_passed=ast_passed,
            ast_audit_notes=ast_notes,
            mutation_rationale=rationale,
        )

    def _evolve_hyperparameters(
        self,
        generation: int,
        skill: ExecutableSkill,
        parent_params: Optional[Dict[str, float]],
        prev_evaluation: Optional[JointEvaluationReport],
    ) -> Tuple[Dict[str, float], str]:
        bounds = skill.hyperparameter_space
        if generation == 0 or parent_params is None:
            params = dict(skill.default_hyperparameters)
            rationale = (
                "Gen-0 Initial Synthesis: Compiled directly from ArXiv paper equations "
                "(DLS IK + Computed Torque PD + Baseline Gravity Feedforward) with un-tuned seed gains."
            )
            return params, rationale

        params = dict(parent_params)
        directives = prev_evaluation.evolution_directives if prev_evaluation else {}
        reasons: List[str] = []

        if directives.get("enforce_joint_limit_nullspace", False):
            params["nullspace_gain"] = self._clip(
                max(params["nullspace_gain"] * 4.2, 1.80), bounds["nullspace_gain"]
            )
            reasons.append(
                f"Injected joint-limit safety margin clamp & boosted nullspace posture gain->{params['nullspace_gain']:.2f} "
                "to eliminate shoulder J1 (>90 deg) excursion during apex lift"
            )

        if directives.get("boost_tracking_stiffness", False):
            params["kp_base"] = self._clip(params["kp_base"] * 2.15, bounds["kp_base"])
            params["task_space_gain_kx"] = self._clip(
                params["task_space_gain_kx"] * 2.20, bounds["task_space_gain_kx"]
            )
            params["dls_lambda"] = self._clip(params["dls_lambda"] * 0.48, bounds["dls_lambda"])
            reasons.append(
                f"Scaled kp_base->{params['kp_base']:.1f}, kx->{params['task_space_gain_kx']:.2f} "
                f"and tightened DLS damping lambda->{params['dls_lambda']:.3f} to cut SE(3) tracking RMSE"
            )

        if directives.get("boost_payload_adaptation", False):
            params["payload_adapt_gamma"] = self._clip(
                max(params["payload_adapt_gamma"] * 1.85, 1.45), bounds["payload_adapt_gamma"]
            )
            reasons.append(
                f"Synthesized online recursive payload observer gamma->{params['payload_adapt_gamma']:.2f} "
                "to counteract 0.35kg grasped block disturbance on D1 elbow/wrist"
            )

        if directives.get("improve_torque_smoothness", False):
            params["kd_base"] = self._clip(params["kd_base"] * 1.45, bounds["kd_base"])
            params["nullspace_gain"] = self._clip(
                params["nullspace_gain"] * 1.25, bounds["nullspace_gain"]
            )
            if not directives.get("boost_tracking_stiffness", False):
                params["kp_base"] = self._clip(params["kp_base"] * 1.45, bounds["kp_base"])
                params["task_space_gain_kx"] = self._clip(
                    params["task_space_gain_kx"] * 1.45, bounds["task_space_gain_kx"]
                )
                params["dls_lambda"] = self._clip(params["dls_lambda"] * 0.65, bounds["dls_lambda"])
            if not directives.get("boost_payload_adaptation", False):
                params["payload_adapt_gamma"] = self._clip(
                    max(params["payload_adapt_gamma"] * 1.55, 2.20), bounds["payload_adapt_gamma"]
                )
            reasons.append(
                f"Co-evolved impedance gains (kp->{params['kp_base']:.1f}, kd->{params['kd_base']:.2f}, "
                f"kx->{params['task_space_gain_kx']:.2f}, gamma->{params['payload_adapt_gamma']:.2f}) "
                "+ integral bias & low-pass torque filter for sub-7mm real-twin precision"
            )

        if directives.get("strengthen_table_barrier", False):
            params["table_repulsive_gain"] = self._clip(
                params["table_repulsive_gain"] * 1.40, bounds["table_repulsive_gain"]
            )
            reasons.append(
                f"Strengthened table clearance potential gain->{params['table_repulsive_gain']:.2f}"
            )

        if not reasons:
            params["kp_base"] = self._clip(params["kp_base"] * 1.15, bounds["kp_base"])
            params["kd_base"] = self._clip(params["kd_base"] * 1.15, bounds["kd_base"])
            reasons.append("Elite fine-tuning of impedance stiffness/damping ratio")

        rationale = f"Gen-{generation} Structural & Gain Evolution: " + "; ".join(reasons)
        return params, rationale

    @staticmethod
    def _clip(val: float, limit: List[float]) -> float:
        return round(float(max(limit[0], min(limit[1], val))), 4)

    def verify_code_ast_safety(self, source_code: str) -> Tuple[bool, List[str]]:
        """
        对 AI 自主生成的控制器代码执行 AST 抽象语法树静态安全审查
        """
        notes: List[str] = []
        forbidden = set(self.manifest.safety_constraints["code_safety_rules"]["forbidden_imports"])

        try:
            tree = ast.parse(source_code)
            notes.append("AST syntax parse: PASSED")
        except SyntaxError as exc:
            return False, [f"AST syntax error: {exc}"]

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in forbidden:
                        return False, [f"Forbidden import detected: {alias.name}"]
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for alias in node.names:
                    full = f"{mod}.{alias.name}".strip(".")
                    if mod in forbidden or full in forbidden:
                        return False, [f"Forbidden import-from detected: {full}"]
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in forbidden:
                    return False, [f"Forbidden builtin call detected: {node.func.id}()"]

        class_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        if "UnitreeD1EvolvedController" not in class_names:
            return False, ["Missing required class `UnitreeD1EvolvedController`"]
        notes.append("Controller class contract `UnitreeD1EvolvedController`: VERIFIED")

        if "np.clip" not in source_code or "np.isfinite" not in source_code:
            return False, ["Missing mandatory torque/angle saturation (`np.clip`) or finiteness guard (`np.isfinite`)"]
        notes.append("Mandatory torque saturation (`np.clip`) & finiteness guard (`np.isfinite`): VERIFIED")

        return True, notes

    def _generate_controller_python_code(
        self,
        generation: int,
        candidate_id: str,
        skill: ExecutableSkill,
        params: Dict[str, float],
        rationale: str,
    ) -> str:
        max_tau = self.manifest.max_torque_nm.tolist()
        max_vel = self.manifest.max_vel_rad_s.tolist()
        q_min_safe = (self.manifest.joint_min_rad + 0.04).tolist()
        q_max_safe = (self.manifest.joint_max_rad - 0.04).tolist()
        min_z = float(self.manifest.workspace_bounds["z"][0])

        # Structural code evolution across generations:
        # Gen >= 1 synthesizes explicit joint-limit barrier projection on desired joint states
        # Gen >= 2 synthesizes integral bias compensation + low-pass torque smoothing filter
        enable_joint_barrier = generation >= 1
        enable_lpf_and_integral = generation >= 2
        lpf_alpha = 0.88 if generation >= 2 else 1.0
        ki_gain = 6.5 if generation >= 2 else (1.5 if generation == 1 else 0.0)

        return f'''"""
================================================================================
Auto-Synthesized Unitree D1 (6+1 DOF) Control Policy — Generation {generation}
Candidate ID : {candidate_id}
Skill Base   : {skill.skill_id}
Mutation Note: {rationale}
================================================================================
"""
from __future__ import annotations
from typing import Dict, Tuple
import numpy as np

from skills_library.{skill.skill_id} import (
    damped_least_squares_ik_step,
    quintic_time_scaling,
)


class UnitreeD1EvolvedController:
    """
    Evolved Operational-Space DLS-IK + Adaptive Payload Impedance Controller
    for Unitree D1 (6-DOF Manipulator + 1-DOF Parallel Gripper).
    """

    def __init__(self) -> None:
        self.generation: int = {generation}
        self.candidate_id: str = {json.dumps(candidate_id)}
        self.params: Dict[str, float] = {json.dumps(params, indent=8)}

        # Joint-space stiffness & damping gains scaled per Unitree D1 actuator inertia ratios
        inertia_scale = np.array([1.15, 1.35, 1.10, 0.75, 0.65, 0.45], dtype=np.float64)
        self.kp_vec = self.params["kp_base"] * inertia_scale
        self.kd_vec = self.params["kd_base"] * inertia_scale
        self.ki_vec = {ki_gain} * inertia_scale

        # Hardware safety limits from RuleManifest
        self.max_torque_nm = np.array({max_tau}, dtype=np.float64)
        self.max_vel_rad_s = np.array({max_vel}, dtype=np.float64)
        self.joint_min_safe = np.array({q_min_safe}, dtype=np.float64)
        self.joint_max_safe = np.array({q_max_safe}, dtype=np.float64)
        self.min_table_z_m = {min_z}
        self.q_home = np.array([0.0, 0.42, -0.78, 0.0, 0.36, 0.0], dtype=np.float64)

        # Online adaptive state variables
        self.estimated_payload_kg: float = 0.0
        self.q_des_integrated: np.ndarray | None = None
        self.integral_q_err = np.zeros(6, dtype=np.float64)
        self.prev_tau = np.zeros(6, dtype=np.float64)
        self.enable_joint_barrier: bool = {enable_joint_barrier}
        self.enable_structural_lpf: bool = {enable_lpf_and_integral}
        self.lpf_alpha: float = {lpf_alpha}

    def reset(self, q_init: np.ndarray) -> None:
        self.estimated_payload_kg = 0.0
        self.q_des_integrated = q_init.copy()
        self.integral_q_err = np.zeros(6, dtype=np.float64)
        self.prev_tau = np.zeros(6, dtype=np.float64)

    def step(
        self,
        q: np.ndarray,
        dq: np.ndarray,
        ee_pos: np.ndarray,
        J_pos: np.ndarray,
        gravity_torque_fn,
        x_ref: np.ndarray,
        dx_ref: np.ndarray,
        gripper_closed: bool,
        dt: float,
    ) -> Tuple[np.ndarray, float, Dict[str, float]]:
        """
        Executes one 500Hz control tick for the Unitree D1 arm.
        Returns: (tau_cmd_6dof, gripper_stroke_mm, diagnostics_dict)
        """
        if self.q_des_integrated is None:
            self.q_des_integrated = q.copy()

        # 1. Task-space tracking error with table safety floor guard
        safe_x_ref = x_ref.copy()
        safe_x_ref[2] = max(safe_x_ref[2], self.min_table_z_m + 0.008)
        x_err = safe_x_ref - ee_pos

        # 2. Singularity-robust Damped Least Squares IK step (from extracted skill)
        dq_des = damped_least_squares_ik_step(
            J_pos=J_pos,
            q=q,
            q_home=self.q_home,
            x_err=x_err,
            dx_ref=dx_ref,
            ee_z=float(ee_pos[2]),
            params=self.params,
        )
        dq_des = np.clip(dq_des, -self.max_vel_rad_s, self.max_vel_rad_s)

        if self.enable_joint_barrier:
            dq_des = np.where(
                (q >= self.joint_max_safe) & (dq_des > 0.0),
                -2.0 * (q - self.joint_max_safe),
                dq_des,
            )
            dq_des = np.where(
                (q <= self.joint_min_safe) & (dq_des < 0.0),
                -2.0 * (q - self.joint_min_safe),
                dq_des,
            )
        # Integrate desired joint reference with anti-windup clamping around current q
        self.q_des_integrated = q + np.clip(
            (self.q_des_integrated - q) + dq_des * dt, -0.35, 0.35
        )
        if self.enable_joint_barrier:
            self.q_des_integrated = np.clip(
                self.q_des_integrated, self.joint_min_safe, self.joint_max_safe
            )

        # 3. Online recursive payload mass observer (active when gripper is closed on payload)
        gamma = float(self.params.get("payload_adapt_gamma", 0.0))
        if gripper_closed and gamma > 0.0:
            vertical_err = float(x_err[2])
            dm_hat = gamma * (vertical_err * 22.0 + max(0.0, float(self.q_des_integrated[1] - q[1])) * 6.0)
            self.estimated_payload_kg = float(
                np.clip(self.estimated_payload_kg + dm_hat * dt, 0.0, 0.50)
            )
        elif not gripper_closed:
            self.estimated_payload_kg = max(0.0, self.estimated_payload_kg - 1.5 * dt)

        # 4. Computed-Torque Joint PD + Integral Bias + Feedforward Gravity(q, m_hat)
        q_err = self.q_des_integrated - q
        dq_err = dq_des - dq
        self.integral_q_err = np.clip(self.integral_q_err + q_err * dt, -0.08, 0.08)

        tau_ff_grav = gravity_torque_fn(q, self.estimated_payload_kg)
        tau_raw = (
            self.kp_vec * q_err
            + self.kd_vec * dq_err
            + self.ki_vec * self.integral_q_err
            + tau_ff_grav
        )

        # 5. Structural evolution: low-pass torque smoothing filter to minimize DDS actuator jerk
        if self.enable_structural_lpf:
            tau_filtered = self.lpf_alpha * tau_raw + (1.0 - self.lpf_alpha) * self.prev_tau
        else:
            tau_filtered = tau_raw

        # 6. Mandatory finiteness check & hard torque saturation
        if not np.all(np.isfinite(tau_filtered)):
            tau_filtered = np.zeros(6, dtype=np.float64)
        tau_cmd = np.clip(tau_filtered, -self.max_torque_nm, self.max_torque_nm)
        self.prev_tau = tau_cmd.copy()

        # 7. Gripper stroke command (J6: 0mm fully closed, 65mm open; 26mm compliant grasp on 44mm block)
        gripper_mm = 26.0 if gripper_closed else 62.0

        diag = {{
            "tracking_err_norm_m": float(np.linalg.norm(x_err)),
            "estimated_payload_kg": float(self.estimated_payload_kg),
            "torque_norm_nm": float(np.linalg.norm(tau_cmd)),
        }}
        return tau_cmd, gripper_mm, diag
'''
