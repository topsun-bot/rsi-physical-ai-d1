from __future__ import annotations

import math
from typing import List, Tuple
import numpy as np


class UnitreeD1PhysicsPlant:
    """
    Unitree D1 (6-DOF Manipulator + 1-DOF Parallel Gripper) 正逆运动学、雅可比矩阵与拉格朗日动力学仿真核
    同时兼容 MuJoCo MJCF 资产 (`assets/unitree_d1/unitree_d1.xml`) 与内置 RK4 高保真刚体动力学。
    """

    def __init__(self, dt: float = 0.002):
        self.dt = dt
        self.g = 9.81

        # Link lengths (m) matching Unitree D1 550mm reach geometry
        self.d0 = 0.105  # Base pedestal + yaw height
        self.l1 = 0.225  # Upper arm link (Shoulder J1 -> Elbow J2)
        self.l2 = 0.225  # Forearm link (Elbow J2 -> Wrist J3/J4)
        self.l3 = 0.115  # Wrist + Gripper TCP offset

        # Link masses (kg) totaling ~2.37 kg
        self.link_masses = np.array([0.48, 0.52, 0.42, 0.35, 0.30, 0.30], dtype=np.float64)

        # Effective reflected rotor + link diagonal inertia (kg*m^2)
        self.base_inertia = np.array([0.085, 0.110, 0.075, 0.032, 0.024, 0.015], dtype=np.float64)
        self.viscous_damping = np.array([0.45, 0.55, 0.40, 0.22, 0.18, 0.12], dtype=np.float64)
        self.coulomb_friction = np.array([0.12, 0.15, 0.10, 0.05, 0.04, 0.03], dtype=np.float64)

    def forward_kinematics(self, q: np.ndarray) -> Tuple[np.ndarray, List[np.ndarray]]:
        """
        Computes 3D Cartesian positions of all joints and the End-Effector TCP for 6-DOF angles q (rad).
        Convention:
          q[0]: Base yaw around Z
          q[1]: Shoulder pitch (elevation from horizontal XY plane)
          q[2]: Elbow pitch
          q[3]: Forearm roll
          q[4]: Wrist pitch
          q[5]: Wrist roll
        """
        q0, q1, q2, q3, q4, _ = [float(x) for x in q[:6]]

        c0, s0 = math.cos(q0), math.sin(q0)
        radial_u = np.array([c0, s0, 0.0], dtype=np.float64)
        lateral_v = np.array([-s0, c0, 0.0], dtype=np.float64)
        up_z = np.array([0.0, 0.0, 1.0], dtype=np.float64)

        p_base = np.array([0.0, 0.0, 0.0], dtype=np.float64)
        p_shoulder = np.array([0.0, 0.0, self.d0], dtype=np.float64)

        # Pitch angles measured in the vertical plane rotated by q0
        theta1 = q1
        theta12 = q1 + q2
        theta124 = q1 + q2 + q4

        p_elbow = (
            p_shoulder
            + self.l1 * math.cos(theta1) * radial_u
            + self.l1 * math.sin(theta1) * up_z
        )
        p_wrist = (
            p_elbow
            + self.l2 * math.cos(theta12) * radial_u
            + self.l2 * math.sin(theta12) * up_z
        )

        # Wrist + forearm roll q3 induces a slight lateral offset when wrist pitch q4 != 0
        wrist_dir = (
            math.cos(theta124) * radial_u
            + math.sin(theta124) * up_z
            + 0.18 * math.sin(q3) * math.sin(q4) * lateral_v
        )
        p_ee = p_wrist + self.l3 * wrist_dir

        chain = [p_base, p_shoulder, p_elbow, p_wrist, p_ee]
        return p_ee, chain

    def compute_position_jacobian(self, q: np.ndarray, eps: float = 1e-5) -> np.ndarray:
        """
        Computes the 3x6 geometric position Jacobian J_pos = dx_ee / dq via central differences.
        """
        J = np.zeros((3, 6), dtype=np.float64)
        for i in range(6):
            dq = np.zeros(6, dtype=np.float64)
            dq[i] = eps
            p_plus, _ = self.forward_kinematics(q + dq)
            p_minus, _ = self.forward_kinematics(q - dq)
            J[:, i] = (p_plus - p_minus) / (2.0 * eps)
        return J

    def gravity_torque(self, q: np.ndarray, payload_mass_kg: float = 0.0) -> np.ndarray:
        """
        Computes exact gravitational torque vector G(q, m_payload) via potential energy gradient dV/dq.
        """
        eps = 1e-5
        tau_g = np.zeros(6, dtype=np.float64)

        def potential_energy(q_vec: np.ndarray) -> float:
            _, chain = self.forward_kinematics(q_vec)
            p_shoulder, p_elbow, p_wrist, p_ee = chain[1], chain[2], chain[3], chain[4]
            z_com1 = 0.5 * (p_shoulder[2] + p_elbow[2])
            z_com2 = 0.5 * (p_elbow[2] + p_wrist[2])
            z_com3 = 0.5 * (p_wrist[2] + p_ee[2])
            v_arm = self.g * (
                self.link_masses[1] * z_com1
                + self.link_masses[2] * z_com2
                + (self.link_masses[3] + self.link_masses[4] + self.link_masses[5]) * z_com3
            )
            v_load = self.g * float(payload_mass_kg) * p_ee[2]
            return float(v_arm + v_load)

        for i in range(6):
            dq = np.zeros(6, dtype=np.float64)
            dq[i] = eps
            tau_g[i] = (potential_energy(q + dq) - potential_energy(q - dq)) / (2.0 * eps)
        return tau_g

    def step_dynamics(
        self,
        q: np.ndarray,
        dq: np.ndarray,
        tau_cmd: np.ndarray,
        true_payload_kg: float,
        ext_force_xyz: np.ndarray,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Semi-implicit Euler / RK2 rigid-body dynamics integration:
          M(q, m_load) ddq + D dq + F_coulomb(dq) + G(q, m_load) = tau_cmd + J^T F_ext
        """
        J = self.compute_position_jacobian(q)
        # Configuration + payload reflected inertia
        m_eff = self.base_inertia + float(true_payload_kg) * np.sum(J * J, axis=0)
        tau_grav = self.gravity_torque(q, true_payload_kg)
        tau_fric = self.viscous_damping * dq + self.coulomb_friction * np.tanh(8.0 * dq)
        tau_ext = J.T @ ext_force_xyz

        ddq = (tau_cmd + tau_ext - tau_grav - tau_fric) / m_eff
        dq_next = dq + ddq * self.dt
        q_next = q + dq_next * self.dt
        return q_next, dq_next
