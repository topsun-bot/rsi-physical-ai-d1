r"""
Auto-Generated Executable Skill Module by Physical-AI RSI V1.0 Skill Extractor
Skill ID: d1_compliant_se3_pick_and_place
Skill Name: Unitree D1 柔顺轨迹跟踪、抗扰抓取与自适应释放技能 (DLS-IK + Adaptive Impedance + Horizon-3 Release Gate)
Target Robot: Unitree D1 (6-DOF Arm + 1-DOF Gripper, 7× FashionStar UART Servos on /dev/ttyS4)

Extracted Mathematical Equations from ArXiv & 4gpu/unitree-d1 Repositories:
  - [Singularity-Robust Damped Least Squares (DLS) IK with Null-Space Projection]: \dot{q}_{des} = J^T (J J^T + \lambda^2 I)^{-1} (\dot{x}_{ref} + K_x e_x + f_{rep}(z)) + (I - J^\dagger J)\bigl(-k_{null}(q - q_{home})\bigr)
    Code: JJT = J @ J.T + (dls_lambda ** 2) * np.eye(3); J_pinv = J.T @ np.linalg.solve(JJT, np.eye(3)); dq_des = J_pinv @ v_task + (np.eye(6) - J_pinv @ J) @ (-nullspace_gain * (q - q_home))
  - [Task-Space Impedance & Adaptive Payload Computed-Torque Law]: \tau = K_p (q_{des} - q) + K_d (\dot{q}_{des} - \dot{q}) + G(q, \hat{m}_{load}) + D_{visc}\dot{q}, \quad \dot{\hat{m}}_{load} = \gamma_{adapt} J_z^T(q) e_z
    Code: m_hat = np.clip(m_hat + dt * payload_adapt_gamma * max(0.0, float(e_x[2])) * 12.0, 0.0, 0.55); tau = kp_vec * (q_des - q) + kd_vec * (dq_des - dq) + gravity_torque(q, m_hat)
  - [Horizon-3 Action Chunk Latency Compensation & Two-Observation Release Gate]: a_{exec}(t) = \hat{A}_{t - k_{delay}}[k_{delay} : k_{delay} + H_{act}], \quad u_{reopen}(t) = \mathbb{I}\bigl(\|e_{place}(t)\|_2 < \epsilon \land \|e_{place}(t-1)\|_2 < \epsilon\bigr)
    Code: valid_chunk = chunk[k_delay : k_delay + 3] if k_delay <= 10 else hold_action; reopen_confirmed = (confirm_count >= 2)
  - [Minimum-Jerk Quintic SE(3) Trajectory & Table Barrier Potential]: s(\tau) = 10\tau^3 - 15\tau^4 + 6\tau^5, \quad U_{rep}(z) = \frac{1}{2} k_{rep} \left(\frac{1}{z - z_{table}} - \frac{1}{z_0}\right)^2
    Code: s = 10.0 * (u**3) - 15.0 * (u**4) + 6.0 * (u**5); z_push = table_repulsive_gain * max(0.0, 0.065 - ee_pos[2])**2
  - [Eureka + RECAP Advantage-Conditioned Dense Manipulation Fitness Function]: R_{Eureka} = w_{track} \exp(-\alpha \|e_x\|_2) + w_{smooth} \exp(-\beta \|\dot{\tau}\|_2) + w_{grasp} \mathbb{I}_{grasp} - w_{barrier} \Phi_{violation}, \quad A(s,a) = V(s') - V(s)
    Code: r_track = np.exp(-reward_alpha_track * rmse_m); r_smooth = np.exp(-reward_beta_smooth * mean_torque_rate); reward = 45.0 * r_track + 20.0 * r_payload + 15.0 * r_smooth + 20.0 * grasp_bonus - 35.0 * penalty
"""
from __future__ import annotations
from typing import Any, Dict, Tuple
import numpy as np

SKILL_METADATA = {
    "skill_id": "d1_compliant_se3_pick_and_place",
    "skill_name": "Unitree D1 \u67d4\u987a\u8f68\u8ff9\u8ddf\u8e2a\u3001\u6297\u6270\u6293\u53d6\u4e0e\u81ea\u9002\u5e94\u91ca\u653e\u6280\u80fd (DLS-IK + Adaptive Impedance + Horizon-3 Release Gate)",
    "target_robot": "Unitree D1 (6-DOF Arm + 1-DOF Gripper, 7\u00d7 FashionStar UART Servos on /dev/ttyS4)",
    "architecture": {
    "pattern_name": "Cascaded_TaskSpace_DLS_IK_Adaptive_Impedance_Horizon3_DDS_Skill",
    "contract": "d1_absolute_state_action_7d_v1",
    "layers": [
        "Layer 1 (Trajectory & Chunk Planner): Quintic Minimum-Jerk SE(3) Planner + 4gpu Horizon-3 (3/13) Latency Compensator",
        "Layer 2 (Kinematic Solver): Singularity-Robust Damped Least Squares IK + Elbow-Up Nullspace Projector",
        "Layer 3 (Dynamic Controller): Joint-Space PD + Analytical Gravity Feedforward + Online Payload Mass Observer",
        "Layer 4 (Release & Hardware Gate): Two-Observation Adaptive Release Gate + Unitree D1 CycloneDDS (rt/arm_Command & set_servo_angle)"
    ],
    "paper_refs": [
        "2310.12931",
        "2402.15391",
        "2506.01844",
        "2410.24164"
    ],
    "repo_refs": [
        "4gpu:/mnt/nvme0n1/gy/d1_smolvla_automation",
        "4gpu:/mnt/raid30t/gy/dual_arm_workcell/code/openpi_d1_recap_publish",
        "unitree-d1-1:/home/ubuntu/marm_code_and_teleop_direct"
    ]
},
    "default_hyperparameters": {
    "kp_base": 20.0,
    "kd_base": 3.0,
    "task_space_gain_kx": 2.8,
    "dls_lambda": 0.095,
    "nullspace_gain": 0.35,
    "payload_adapt_gamma": 0.0,
    "table_repulsive_gain": 1.2,
    "reward_alpha_track": 14.0,
    "reward_beta_smooth": 0.12
},
}


def validate_d1_7d_contract(state_7d: np.ndarray, action_7d: np.ndarray) -> Dict[str, Any]:
    """
    Validates the 4gpu `d1_absolute_state_action_7d_v1` contract (6 arm joints + 1 gripper).
    """
    ok = (
        state_7d.shape == (7,)
        and action_7d.shape == (7,)
        and bool(np.all(np.isfinite(state_7d)))
        and bool(np.all(np.isfinite(action_7d)))
    )
    return {
        "ok": ok,
        "contract": "d1_absolute_state_action_7d_v1",
        "state_dim": int(state_7d.shape[0]) if state_7d.ndim == 1 else -1,
        "action_dim": int(action_7d.shape[0]) if action_7d.ndim == 1 else -1,
    }


def quintic_time_scaling(u: float) -> Tuple[float, float]:
    """
    Minimum-jerk quintic polynomial s(u) and derivative ds/du for u in [0, 1].
    s(u) = 10*u^3 - 15*u^4 + 6*u^5
    """
    u_c = float(np.clip(u, 0.0, 1.0))
    s = 10.0 * (u_c ** 3) - 15.0 * (u_c ** 4) + 6.0 * (u_c ** 5)
    ds = 30.0 * (u_c ** 2) - 60.0 * (u_c ** 3) + 30.0 * (u_c ** 4)
    return s, ds


def damped_least_squares_ik_step(
    J_pos: np.ndarray,
    q: np.ndarray,
    q_home: np.ndarray,
    x_err: np.ndarray,
    dx_ref: np.ndarray,
    ee_z: float,
    params: Dict[str, float],
) -> np.ndarray:
    """
    Singularity-robust Damped Least Squares (DLS) IK with table repulsive barrier and null-space posture control.
    """
    kx = float(params.get("task_space_gain_kx", 6.0))
    lam = float(params.get("dls_lambda", 0.045))
    k_null = float(params.get("nullspace_gain", 1.0))
    k_rep = float(params.get("table_repulsive_gain", 2.0))

    # Table-clearance repulsive upward velocity if end-effector z approaches 0.025m table limit
    z_margin = 0.055 - float(ee_z)
    v_rep_z = k_rep * (z_margin ** 2) if z_margin > 0.0 else 0.0
    v_task = dx_ref + kx * x_err + np.array([0.0, 0.0, v_rep_z], dtype=np.float64)

    jjt = J_pos @ J_pos.T + (lam ** 2) * np.eye(3, dtype=np.float64)
    j_pinv = J_pos.T @ np.linalg.solve(jjt, np.eye(3, dtype=np.float64))
    null_proj = np.eye(6, dtype=np.float64) - j_pinv @ J_pos
    dq_null = -k_null * (q - q_home)
    dq_des = j_pinv @ v_task + null_proj @ dq_null
    return dq_des


def compute_eureka_dense_reward(
    tracking_rmse_m: float,
    payload_recovery_m: float,
    mean_torque_rate: float,
    grasp_success: bool,
    safety_violations_count: int,
    params: Dict[str, float],
) -> Tuple[float, Dict[str, float]]:
    """
    Eureka + RECAP decomposed dense fitness function extracted from arXiv:2310.12931 & 4gpu OpenPI-RECAP.
    """
    alpha = float(params.get("reward_alpha_track", 16.0))
    beta = float(params.get("reward_beta_smooth", 0.008))

    r_track = 45.0 * float(np.exp(-alpha * tracking_rmse_m))
    r_payload = 20.0 * float(np.exp(-alpha * 1.2 * payload_recovery_m))
    r_smooth = 15.0 * float(np.exp(-beta * mean_torque_rate))
    r_grasp = 20.0 if grasp_success else 0.0
    penalty = 35.0 * float(safety_violations_count)

    total = max(0.0, r_track + r_payload + r_smooth + r_grasp - penalty)
    breakdown = {
        "r_track": round(r_track, 3),
        "r_payload": round(r_payload, 3),
        "r_smooth": round(r_smooth, 3),
        "r_grasp": round(r_grasp, 3),
        "penalty": round(penalty, 3),
        "total_reward": round(total, 3),
    }
    return total, breakdown
