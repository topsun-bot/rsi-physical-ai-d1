"""
================================================================================
Auto-Synthesized Unitree D1 (6+1 DOF) Control Policy — Generation 2
Candidate ID : d1_policy_gen02
Skill Base   : d1_compliant_se3_pick_and_place
Mutation Note: Gen-2 Structural & Gain Evolution: Elite fine-tuning of impedance stiffness/damping ratio
================================================================================
"""
from __future__ import annotations
from typing import Dict, Tuple
import numpy as np

from skills_library.d1_compliant_se3_pick_and_place import (
    damped_least_squares_ik_step,
    quintic_time_scaling,
)


class UnitreeD1EvolvedController:
    """
    Evolved Operational-Space DLS-IK + Adaptive Payload Impedance Controller
    for Unitree D1 (6-DOF Manipulator + 1-DOF Parallel Gripper).
    """

    def __init__(self) -> None:
        self.generation: int = 2
        self.candidate_id: str = "d1_policy_gen02"
        self.params: Dict[str, float] = {
        "kp_base": 49.45,
        "kd_base": 5.0025,
        "task_space_gain_kx": 6.16,
        "dls_lambda": 0.0456,
        "nullspace_gain": 0.4375,
        "payload_adapt_gamma": 1.45,
        "table_repulsive_gain": 1.68,
        "reward_alpha_track": 14.0,
        "reward_beta_smooth": 0.12
}

        # Joint-space stiffness & damping gains scaled per Unitree D1 actuator inertia ratios
        inertia_scale = np.array([1.15, 1.35, 1.10, 0.75, 0.65, 0.45], dtype=np.float64)
        self.kp_vec = self.params["kp_base"] * inertia_scale
        self.kd_vec = self.params["kd_base"] * inertia_scale
        self.ki_vec = 6.5 * inertia_scale

        # Hardware safety limits from RuleManifest
        self.max_torque_nm = np.array([8.5, 8.5, 6.0, 4.0, 3.0, 2.0], dtype=np.float64)
        self.max_vel_rad_s = np.array([2.5, 2.2, 2.5, 3.0, 3.0, 3.5], dtype=np.float64)
        self.joint_min_safe = np.array([-2.316194490192345, -1.5307963267948965, -2.316194490192345, -2.316194490192345, -1.5307963267948965, -2.316194490192345], dtype=np.float64)
        self.joint_max_safe = np.array([2.316194490192345, 1.5307963267948965, 2.316194490192345, 2.316194490192345, 1.5307963267948965, 2.316194490192345], dtype=np.float64)
        self.min_table_z_m = 0.025
        self.q_home = np.array([0.0, 0.42, -0.78, 0.0, 0.36, 0.0], dtype=np.float64)

        # Online adaptive state variables
        self.estimated_payload_kg: float = 0.0
        self.q_des_integrated: np.ndarray | None = None
        self.integral_q_err = np.zeros(6, dtype=np.float64)
        self.prev_tau = np.zeros(6, dtype=np.float64)
        self.enable_joint_barrier: bool = True
        self.enable_structural_lpf: bool = True
        self.lpf_alpha: float = 0.88

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

        diag = {
            "tracking_err_norm_m": float(np.linalg.norm(x_err)),
            "estimated_payload_kg": float(self.estimated_payload_kg),
            "torque_norm_nm": float(np.linalg.norm(tau_cmd)),
        }
        return tau_cmd, gripper_mm, diag
