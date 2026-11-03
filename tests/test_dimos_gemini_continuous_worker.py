"""Unit & integration tests for DimOS Reactive Blueprints, Gemini 4 Argon Planner,
and the 24/7 Unitree D1 Multi-Skill Continuous Worker (Cloth Folding, Dance, Gripper Open/Close).
"""

from __future__ import annotations

import unittest

from rsi.integrations.dimos_d1_blueprint import (
    D1CameraPerceptionModule,
    GeminiArgonAgentModule,
    UnitreeD1FleetModule,
    autoconnect,
    build_skill_waypoints,
    interpolate_cosine,
)
from rsi.integrations.gemini_robotics_planner import GeminiRoboticsPlanner
from rsi.integrations.grasp_and_plug_evolver import ContinuousGraspAndPlugEvolver
from scripts.d1_live_hub import D1ContinuousWorkHub, clamp_joints


class TestDimOSGeminiContinuousWorker(unittest.TestCase):
    def test_dimos_reactive_blueprint_autoconnect_and_skills(self) -> None:
        cam = D1CameraPerceptionModule()
        agent = GeminiArgonAgentModule()
        fleet = UnitreeD1FleetModule()
        # Wire stream ports
        cam.color_image.subscribe(agent._on_image)
        agent.skill_command.subscribe(fleet._on_skill)

        for skill_name in ("cloth_folding", "cyber_dance", "gripper_workout", "pick_relay", "surface_wipe"):
            cam.publish_frame_meta({"requested_skill": skill_name})

        self.assertEqual(
            fleet.executed_skills,
            ["cloth_folding", "cyber_dance", "gripper_workout", "pick_relay", "surface_wipe"],
        )
        self.assertIsNotNone(fleet.joint_telemetry.last_message)
        self.assertEqual(fleet.joint_telemetry.last_message["status"], "STREAMING_TO_3_ARMS")

    def test_all_5_skills_respect_d1_joint_and_gripper_safety_limits(self) -> None:
        for skill_name in ("cloth_folding", "cyber_dance", "gripper_workout", "pick_relay", "surface_wipe"):
            waypoints = build_skill_waypoints(skill_name)
            self.assertGreaterEqual(len(waypoints), 3)
            for wp in waypoints:
                for deg_7 in (wp.left_103_deg, wp.center_102_deg, wp.right_101_deg):
                    self.assertEqual(len(deg_7), 7)
                    clamped = clamp_joints(deg_7)
                    self.assertEqual(clamped, deg_7)

    def test_gemini_robotics_planner_spatial_output(self) -> None:
        planner = GeminiRoboticsPlanner(timeout_s=2.5)
        plan = planner.plan_workbench_skills()
        self.assertEqual(len(plan.detections_2d), 4)
        self.assertIn("cloth_folding", plan.skill_sequence)
        self.assertIn("cyber_dance", plan.skill_sequence)

    def test_continuous_work_hub_gripper_and_skill_execution_dry_run(self) -> None:
        hub = D1ContinuousWorkHub(enable_hardware=False, continuous_start=False)
        hub.set_grippers_angle(50.0, target_arm="all")
        for k in ("left_103", "center_102", "right_101"):
            self.assertEqual(hub.measured_joints[k][6], 50.0)
        hub.set_grippers_angle(0.0, target_arm="left_103")
        self.assertEqual(hub.measured_joints["left_103"][6], 0.0)

        interp = interpolate_cosine([0.0] * 7, [20.0] * 7, 0.5)
        self.assertAlmostEqual(interp[0], 10.0, places=1)

    def test_grasp_and_plug_evolver_closed_loop(self) -> None:
        evolver = ContinuousGraspAndPlugEvolver()
        for it in range(1, 4):
            wps = evolver.propose_next_waypoints(it - 1)
            for key, deg_7 in wps.items():
                self.assertEqual(len(deg_7), 7, msg=f"Invalid DOF in {key}")
                self.assertEqual(clamp_joints(deg_7), deg_7, msg=f"Out of safety limits in {key}")
            res = evolver.evaluate_attempt(
                iteration=it,
                waypoints=wps,
                enc_102_grasp=[-22.0, 28.7, 8.9, 0.4, 35.9, 0.0, 7.3],
                enc_102_socket=[41.5, 30.8, -0.5, -0.3, 43.7, -0.1, 7.8],
                enc_103_push=[-36.1, 27.8, -2.4, 0.2, 47.6, 0.0, 0.0],
                plug_diff=6.8 + it * 1.5,
                socket_diff=7.2 + it * 1.6,
                tripod_diff=8.4 + it * 1.2,
                gemini_note="Gemini closed-loop socket alignment",
                snapshot_path=f"/tmp/iter_{it}.jpg",
            )
            self.assertTrue(res.grasp_contact_detected)
            self.assertGreaterEqual(res.total_evolution_score, 75.0)
        gp_waypoints = build_skill_waypoints("grasp_and_plug_loop", iteration=2)
        self.assertEqual(len(gp_waypoints), 5)


if __name__ == "__main__":
    unittest.main()
