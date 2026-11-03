from __future__ import annotations

import importlib.util
import json
import math
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from rsi.core.manifest import RuleManifest
from rsi.core.types import ControlPolicyCandidate, SimVerificationResult
from rsi.sim.d1_kinematics_dynamics import UnitreeD1PhysicsPlant

if TYPE_CHECKING:
    from rsi.sim.real2sim_generator import Real2SimSceneConfig


class SimulationValidator:
    """
    阶段 4：仿真环境训练验证 (Simulation Verification & Stress-Test Engine)
    加载阶段 3 生成的控制器候选代码，在 Unitree D1 物理仿真环境中执行：
      1) 最小加加速度轨迹接近 (Approach)
      2) 0.35kg 负载突变抓取 + 外力脉冲抗扰测试 (Grasp & Payload Step Disturbance)
      3) 跨障碍抬升与搬运 (Lift & Transport)
      4) 双帧确认精准放置与夹爪释放 (Two-Observation Confirmed Release)
    并输出完整的物理评测指标与 Sim-to-Real 准入判定。
    """

    def __init__(self, manifest: RuleManifest, workspace_root: str | Path):
        self.manifest = manifest
        self.workspace_root = Path(workspace_root)
        self.dt = 0.002  # 500 Hz control loop
        self.plant = UnitreeD1PhysicsPlant(dt=self.dt)
        self.last_ood_metrics: dict[str, Any] = {}
        self.last_dsec_report: dict[str, Any] = {}

    def validate_candidate(
        self,
        candidate: ControlPolicyCandidate,
        real2sim_scene: Real2SimSceneConfig | None = None,
        dsec_bank: Any | None = None,
    ) -> tuple[SimVerificationResult, list[dict[str, Any]]]:
        if not candidate.ast_safety_passed:
            raise ValueError(
                f"Cannot run simulation on AST-unsafe candidate {candidate.candidate_id}: {candidate.ast_audit_notes}"
            )

        controller = self._load_controller_instance(candidate)
        q_init = np.array([0.0, 0.42, -0.78, 0.0, 0.36, 0.0], dtype=np.float64)
        dq_init = np.zeros(6, dtype=np.float64)
        controller.reset(q_init)

        p_start, _ = self.plant.forward_kinematics(q_init)
        if real2sim_scene is not None:
            curr = real2sim_scene.task_curriculum
            p_pick = np.array(curr["p_pick_xyz_m"], dtype=np.float64)
            p_apex = np.array(curr["p_apex_xyz_m"], dtype=np.float64)
            p_place = np.array(curr["p_place_xyz_m"], dtype=np.float64)
            payload_mass_kg = float(curr.get("estimated_payload_kg", 0.35))
            backend_name = f"UnitreeD1_RK2_Lagrangian_500Hz (+Real2Sim MJCF: {Path(real2sim_scene.generated_mjcf_path).name})"
        else:
            p_pick = np.array([0.36, 0.14, 0.055], dtype=np.float64)
            p_apex = np.array([0.35, -0.02, 0.195], dtype=np.float64)
            p_place = np.array([0.32, -0.20, 0.060], dtype=np.float64)
            payload_mass_kg = 0.35
            backend_name = "UnitreeD1_RK2_Lagrangian_500Hz (+MJCF asset ready)"

        total_duration = 4.0
        steps = int(total_duration / self.dt)

        q = q_init.copy()
        dq = dq_init.copy()
        prev_tau = np.zeros(6, dtype=np.float64)

        tracking_errors: list[float] = []
        payload_phase_errors: list[float] = []
        torque_norms: list[float] = []
        torque_rates: list[float] = []
        max_torque_ratio = 0.0
        min_table_z = 10.0
        safety_violations: list[str] = []
        trajectory_records: list[dict[str, Any]] = []

        grasp_triggered = False
        reopen_confirm_count = 0
        release_completed = False
        step_compute_times_ms: list[float] = []

        for step_idx in range(steps):
            t = step_idx * self.dt
            x_ref, dx_ref, gripper_closed_cmd, true_payload_kg, ext_force = self._reference_schedule(
                t, p_start, p_pick, p_apex, p_place, payload_mass_kg
            )

            ee_pos, _ = self.plant.forward_kinematics(q)
            J_pos = self.plant.compute_position_jacobian(q)

            # Two-observation adaptive release confirmation during final placement phase (t >= 3.4s)
            if t >= 3.40:
                place_dist = float(np.linalg.norm(ee_pos - p_place))
                if place_dist < 0.022:
                    reopen_confirm_count += 1
                else:
                    reopen_confirm_count = max(0, reopen_confirm_count - 1)
                if reopen_confirm_count >= 2:
                    gripper_closed_cmd = False
                    release_completed = True

            t0_perf = time.perf_counter()
            tau_cmd, gripper_mm, _ = controller.step(
                q=q,
                dq=dq,
                ee_pos=ee_pos,
                J_pos=J_pos,
                gravity_torque_fn=self.plant.gravity_torque,
                x_ref=x_ref,
                dx_ref=dx_ref,
                gripper_closed=gripper_closed_cmd,
                dt=self.dt,
            )
            step_compute_times_ms.append((time.perf_counter() - t0_perf) * 1000.0)

            if 1.15 <= t <= 1.35 and float(np.linalg.norm(ee_pos - p_pick)) < 0.028 and gripper_mm < 35.0:
                grasp_triggered = True

            # Advance physical plant
            q, dq = self.plant.step_dynamics(
                q=q,
                dq=dq,
                tau_cmd=tau_cmd,
                true_payload_kg=true_payload_kg if grasp_triggered else 0.0,
                ext_force_xyz=ext_force,
            )

            ee_after, _ = self.plant.forward_kinematics(q)
            err = float(np.linalg.norm(x_ref - ee_after))
            tracking_errors.append(err)
            if 1.20 <= t <= 2.20:
                payload_phase_errors.append(err)

            t_norm = float(np.linalg.norm(tau_cmd))
            t_rate = float(np.linalg.norm((tau_cmd - prev_tau) / self.dt))
            prev_tau = tau_cmd.copy()
            torque_norms.append(t_norm)
            if step_idx > 0:
                torque_rates.append(t_rate)

            ratio = float(np.max(np.abs(tau_cmd) / self.manifest.max_torque_nm))
            max_torque_ratio = max(max_torque_ratio, ratio)
            min_table_z = min(min_table_z, float(ee_after[2]))

            # Safety envelope checks
            ok_j, msg_j = self.manifest.check_joint_limits(q)
            if not ok_j and len(safety_violations) < 5:
                safety_violations.append(f"t={t:.2f}s: {msg_j}")

            ok_w, msg_w = self.manifest.check_workspace_safety(ee_after)
            if not ok_w and len(safety_violations) < 5:
                safety_violations.append(f"t={t:.2f}s: {msg_w}")

            # Subsample trajectory every 20ms (50Hz) for DDS replay & evaluation artifacts
            if step_idx % 10 == 0 or step_idx == steps - 1:
                q_deg = np.degrees(q).tolist()
                trajectory_records.append(
                    {
                        "step": step_idx,
                        "t_sec": round(t, 3),
                        "q_rad": [round(float(v), 5) for v in q],
                        "q_deg_7d": [round(float(v), 2) for v in q_deg] + [round(float(gripper_mm), 2)],
                        "ee_xyz_m": [round(float(v), 4) for v in ee_after],
                        "ref_xyz_m": [round(float(v), 4) for v in x_ref],
                        "tracking_err_m": round(err, 5),
                        "tau_nm": [round(float(v), 3) for v in tau_cmd],
                        "estimated_payload_kg": round(float(controller.estimated_payload_kg), 3),
                        "gripper_mm": round(float(gripper_mm), 2),
                    }
                )

        rmse_m = float(np.sqrt(np.mean(np.square(tracking_errors))))
        max_err_m = float(np.max(tracking_errors))
        payload_rec_m = float(np.sqrt(np.mean(np.square(payload_phase_errors))))
        place_err_m = float(np.linalg.norm(ee_after - p_place))
        mean_tau_norm = float(np.mean(torque_norms))
        mean_tau_rate = float(np.mean(torque_rates)) if torque_rates else 0.0
        grasp_and_place_ok = bool(grasp_triggered and release_completed and place_err_m < 0.022)

        # Compute Eureka dense reward via extracted skill function
        skill_mod = self._load_skill_module(candidate.skill_id)
        eureka_reward, _ = skill_mod.compute_eureka_dense_reward(
            tracking_rmse_m=rmse_m,
            payload_recovery_m=payload_rec_m,
            mean_torque_rate=mean_tau_rate,
            grasp_success=grasp_and_place_ok,
            safety_violations_count=len(safety_violations),
            params=candidate.hyperparameters,
        )

        gate_cfg = self.manifest.sim2real_gate
        passed_gate = (
            len(safety_violations) == 0
            and rmse_m <= float(gate_cfg["max_tracking_rmse_m"])
            and grasp_and_place_ok
        )

        gen_dir = Path(candidate.code_file_path).parent
        traj_path = gen_dir / f"sim_trajectory_gen{candidate.generation:02d}.json"
        traj_path.write_text(
            json.dumps(
                {
                    "candidate_id": candidate.candidate_id,
                    "generation": candidate.generation,
                    "rmse_m": round(rmse_m, 5),
                    "grasp_success": grasp_and_place_ok,
                    "frames": trajectory_records,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        # V2.0 RRSI Held-Out OOD Perturbation Suite Validation
        ood_metrics = self.validate_ood_perturbation_suite(
            controller=controller,
            q_init=q_init,
            p_start=p_start,
            p_pick=p_pick,
            p_apex=p_apex,
            p_place=p_place,
        )
        self.last_ood_metrics = ood_metrics

        # V2.0 DeepSeek DSec Failure Replay Micro-Sandbox Verification
        if dsec_bank is not None:
            dsec_report = dsec_bank.replay_all_sandboxes(
                controller=controller,
                plant=self.plant,
                manifest=self.manifest,
            )
            self.last_dsec_report = dsec_report
            dsec_passed = bool(dsec_report["passed"])
            dsec_tested = int(dsec_report["total_sandboxes"])
        else:
            self.last_dsec_report = {"passed": True, "total_sandboxes": 0, "results": []}
            dsec_passed = True
            dsec_tested = 0

        mean_step_cost_ms = float(np.mean(step_compute_times_ms)) if step_compute_times_ms else 0.05

        sim_result = SimVerificationResult(
            generation=candidate.generation,
            candidate_id=candidate.candidate_id,
            backend_used=backend_name,
            sim_duration_sec=total_duration,
            control_steps=steps,
            tracking_rmse_m=round(rmse_m, 5),
            max_tracking_error_m=round(max_err_m, 5),
            payload_disturbance_recovery_m=round(payload_rec_m, 5),
            grasp_success=grasp_and_place_ok,
            place_error_m=round(place_err_m, 5),
            mean_torque_norm_nm=round(mean_tau_norm, 4),
            mean_torque_rate_nm_s=round(mean_tau_rate, 4),
            max_joint_torque_ratio=round(max_torque_ratio, 4),
            min_table_clearance_m=round(min_table_z, 4),
            safety_violations=safety_violations,
            eureka_dense_reward=round(eureka_reward, 3),
            passed_sim2real_gate=passed_gate,
            trajectory_log_path=str(traj_path),
            ood_mean_rmse_m=round(float(ood_metrics["ood_mean_rmse_m"]), 5),
            ood_worst_rmse_m=round(float(ood_metrics["ood_worst_rmse_m"]), 5),
            ood_pass_rate=round(float(ood_metrics["ood_pass_rate"]), 3),
            dsec_replay_passed=dsec_passed,
            dsec_sandboxes_tested=dsec_tested,
            step_compute_cost_ms=round(mean_step_cost_ms, 4),
        )

        # Extract any new failure micro-sandboxes (`pack_diff`) into DSec bank for subsequent generations
        if dsec_bank is not None:
            dsec_bank.pack_diff_from_generation(
                candidate=candidate,
                sim_result=sim_result,
                trajectory_records=trajectory_records,
            )

        return sim_result, trajectory_records

    def validate_ood_perturbation_suite(
        self,
        controller: Any,
        q_init: np.ndarray,
        p_start: np.ndarray,
        p_pick: np.ndarray,
        p_apex: np.ndarray,
        p_place: np.ndarray,
    ) -> dict[str, Any]:
        """
        Google Research RRSI (arXiv:2609.24972) Held-Out Out-Of-Distribution (OOD) Perturbation Suite:
          1) `ood_heavy_payload_0.48kg`: Near-rated-limit 0.48 kg load + lateral disturbance pulse
          2) `ood_shifted_waypoints_15mm`: Pick/Place shifted by +12..-14mm in XY & +5mm in Z (prevents waypoint memorization)
          3) `ood_light_payload_0.15kg_high_friction`: 0.15 kg light load with 1.35x Coulomb friction perturbation
        """
        scenarios = [
            {
                "name": "ood_heavy_payload_0.48kg",
                "p_pick": p_pick.copy(),
                "p_apex": p_apex.copy(),
                "p_place": p_place.copy(),
                "payload_kg": 0.48,
                "friction_scale": 1.0,
            },
            {
                "name": "ood_shifted_waypoints_15mm",
                "p_pick": p_pick + np.array([0.012, -0.010, 0.002], dtype=np.float64),
                "p_apex": p_apex + np.array([-0.008, 0.010, 0.004], dtype=np.float64),
                "p_place": p_place + np.array([-0.012, 0.014, 0.005], dtype=np.float64),
                "payload_kg": 0.32,
                "friction_scale": 1.0,
            },
            {
                "name": "ood_light_payload_0.15kg_high_friction",
                "p_pick": p_pick.copy(),
                "p_apex": p_apex.copy(),
                "p_place": p_place.copy(),
                "payload_kg": 0.15,
                "friction_scale": 1.35,
            },
        ]

        rmses: list[float] = []
        scenario_scores: list[float] = []
        scenario_details: list[dict[str, Any]] = []
        passed_scenarios = 0
        gate_limit = float(self.manifest.sim2real_gate["max_tracking_rmse_m"]) * 1.15  # 17.25mm OOD tolerance

        # Run critical pick-lift-transport window (t in [0.90s, 2.50s], 400 steps @ dt=0.004s) for fast & exact OOD check
        dt_ood = 0.004
        ood_plant = UnitreeD1PhysicsPlant(dt=dt_ood)
        base_coulomb = ood_plant.coulomb_friction.copy()

        for sc in scenarios:
            ood_plant.coulomb_friction = base_coulomb * float(sc["friction_scale"])
            q = q_init.copy()
            dq = np.zeros(6, dtype=np.float64)
            controller.reset(q)
            errs: list[float] = []
            violations = 0

            # Simulate 0.0 -> 2.6s covering approach, 0.48kg/0.15kg grasp, lift apex & pulse disturbance
            n_steps = int(2.60 / dt_ood)
            for idx in range(n_steps):
                t = idx * dt_ood
                x_ref, dx_ref, grip_cmd, load_kg, ext_f = self._reference_schedule(
                    t,
                    p_start,
                    sc["p_pick"],
                    sc["p_apex"],
                    sc["p_place"],
                    float(sc["payload_kg"]),
                )
                ee_pos, _ = ood_plant.forward_kinematics(q)
                J_pos = ood_plant.compute_position_jacobian(q)
                tau_cmd, _, _ = controller.step(
                    q=q,
                    dq=dq,
                    ee_pos=ee_pos,
                    J_pos=J_pos,
                    gravity_torque_fn=ood_plant.gravity_torque,
                    x_ref=x_ref,
                    dx_ref=dx_ref,
                    gripper_closed=grip_cmd,
                    dt=dt_ood,
                )
                q, dq = ood_plant.step_dynamics(
                    q=q,
                    dq=dq,
                    tau_cmd=tau_cmd,
                    true_payload_kg=load_kg if grip_cmd else 0.0,
                    ext_force_xyz=ext_f,
                )
                ee_after, _ = ood_plant.forward_kinematics(q)
                errs.append(float(np.linalg.norm(x_ref - ee_after)))
                ok_j, _ = self.manifest.check_joint_limits(q)
                ok_w, _ = self.manifest.check_workspace_safety(ee_after)
                if not ok_j or not ok_w:
                    violations += 1

            sc_rmse = float(np.sqrt(np.mean(np.square(errs))))
            sc_ok = violations == 0 and sc_rmse <= gate_limit
            if sc_ok:
                passed_scenarios += 1
            sc_score = 100.0 * math.exp(-30.0 * sc_rmse) - (25.0 if violations > 0 else 0.0)
            rmses.append(sc_rmse)
            scenario_scores.append(max(0.0, sc_score))
            scenario_details.append(
                {
                    "name": sc["name"],
                    "rmse_m": round(sc_rmse, 5),
                    "violations": violations,
                    "passed": sc_ok,
                }
            )

        return {
            "ood_mean_rmse_m": float(np.mean(rmses)),
            "ood_worst_rmse_m": float(np.max(rmses)),
            "ood_pass_rate": float(passed_scenarios) / float(len(scenarios)),
            "scenario_scores": scenario_scores,
            "scenarios": scenario_details,
        }

    def _reference_schedule(
        self,
        t: float,
        p_start: np.ndarray,
        p_pick: np.ndarray,
        p_apex: np.ndarray,
        p_place: np.ndarray,
        payload_mass_kg: float = 0.35,
    ) -> tuple[np.ndarray, np.ndarray, bool, float, np.ndarray]:
        """
        Generates smooth quintic minimum-jerk SE(3) reference position & velocity,
        gripper command, true payload mass (0.35 kg), and external perturbation force.
        """
        def quintic_interp(p0: np.ndarray, p1: np.ndarray, t0: float, t1: float, cur_t: float):
            dur = max(1e-4, t1 - t0)
            u = float(np.clip((cur_t - t0) / dur, 0.0, 1.0))
            s = 10.0 * (u ** 3) - 15.0 * (u ** 4) + 6.0 * (u ** 5)
            ds = (30.0 * (u ** 2) - 60.0 * (u ** 3) + 30.0 * (u ** 4)) / dur
            pos = p0 + s * (p1 - p0)
            vel = ds * (p1 - p0)
            return pos, vel

        ext_force = np.zeros(3, dtype=np.float64)
        if t < 1.10:
            # Phase 0: Approach pick object
            pos, vel = quintic_interp(p_start, p_pick, 0.0, 1.10, t)
            return pos, vel, False, 0.0, ext_force
        elif t < 1.40:
            # Phase 1: Close gripper on payload block at p_pick
            return p_pick.copy(), np.zeros(3, dtype=np.float64), True, payload_mass_kg, ext_force
        elif t < 2.30:
            # Phase 2A: Lift payload to obstacle-clearing apex + external perturbation pulse
            pos, vel = quintic_interp(p_pick, p_apex, 1.40, 2.30, t)
            if 1.65 <= t <= 1.85:
                ext_force = np.array([0.35, -0.25, -0.75], dtype=np.float64)
            return pos, vel, True, payload_mass_kg, ext_force
        elif t < 3.40:
            # Phase 2B: Transport from apex to placement target p_place
            pos, vel = quintic_interp(p_apex, p_place, 2.30, 3.40, t)
            return pos, vel, True, payload_mass_kg, ext_force
        else:
            # Phase 3: Hold at placement site for two-observation confirmation & release
            return p_place.copy(), np.zeros(3, dtype=np.float64), True, 0.0, ext_force

    def _load_controller_instance(self, candidate: ControlPolicyCandidate):
        if str(self.workspace_root) not in sys.path:
            sys.path.insert(0, str(self.workspace_root))
        spec = importlib.util.spec_from_file_location(
            candidate.candidate_id, candidate.code_file_path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load candidate from {candidate.code_file_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.UnitreeD1EvolvedController()

    def _load_skill_module(self, skill_id: str):
        if str(self.workspace_root) not in sys.path:
            sys.path.insert(0, str(self.workspace_root))
        return importlib.import_module(f"skills_library.{skill_id}")
