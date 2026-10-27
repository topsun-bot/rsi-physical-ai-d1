from __future__ import annotations

import json
import unittest
from pathlib import Path
import numpy as np

from rsi.coder.controller_synthesizer import ControllerCodeSynthesizer
from rsi.core.manifest import RuleManifest
from rsi.evolution.loop_engine import RSISelfEvolutionEngine
from rsi.scout.paper_github_scout import LiteratureAndCodeScout
from rsi.scout.skill_extractor import PaperCodeSkillExtractor
from rsi.sim.d1_kinematics_dynamics import UnitreeD1PhysicsPlant


WORKSPACE_ROOT = Path(__file__).resolve().parent.parent


class TestPhysicalAIRSIClosedLoop(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = RuleManifest(WORKSPACE_ROOT / "configs" / "d1_manifest.yaml")

    def test_stage1_manifest_and_safety_envelopes(self) -> None:
        self.assertEqual(self.manifest.robot_spec["dof_arm"], 6)
        self.assertEqual(self.manifest.robot_spec["dof_gripper"], 1)
        self.assertEqual(
            self.manifest.robot_spec["state_action_contract"],
            "d1_absolute_state_action_7d_v1",
        )
        # Valid joint configuration
        q_ok = np.zeros(6, dtype=np.float64)
        ok, _ = self.manifest.check_joint_limits(q_ok)
        self.assertTrue(ok)

        # Violating joint configuration (joint1 > 90 deg)
        q_bad = np.array([0.0, 1.80, 0.0, 0.0, 0.0, 0.0], dtype=np.float64)
        ok_bad, msg = self.manifest.check_joint_limits(q_bad)
        self.assertFalse(ok_bad)
        self.assertIn("Joint limit violation", msg)

        # Table collision check (z < 0.025m)
        ok_ws, msg_ws = self.manifest.check_workspace_safety(np.array([0.30, 0.10, 0.010]))
        self.assertFalse(ok_ws)
        self.assertIn("Table collision hazard", msg_ws)

    def test_stage2_paper_repo_scout_and_skill_extraction(self) -> None:
        scout = LiteratureAndCodeScout(enable_live_network=False)
        extractor = PaperCodeSkillExtractor(WORKSPACE_ROOT)
        skill_cfg = self.manifest.get_primary_skill()

        papers = scout.search_arxiv(skill_cfg["search_queries"]["arxiv"])
        repos = scout.search_github(skill_cfg["search_queries"]["github"])
        self.assertGreaterEqual(len(papers), 4)
        self.assertGreaterEqual(len(repos), 3)

        skill = extractor.extract_and_compile_skill(
            skill_id=skill_cfg["skill_id"],
            skill_name=skill_cfg["name"],
            target_robot=self.manifest.robot_spec["model"],
            papers=papers,
            repos=repos,
        )
        self.assertEqual(len(skill.extracted_equations), 5)
        self.assertTrue(Path(skill.skill_module_path).exists())

    def test_stage3_ast_safety_guard_rejects_malicious_code(self) -> None:
        synthesizer = ControllerCodeSynthesizer(self.manifest, WORKSPACE_ROOT / "runs")
        unsafe_code = "import subprocess\nsubprocess.Popen(['rm', '-rf', '/'])\n"
        passed, notes = synthesizer.verify_code_ast_safety(unsafe_code)
        self.assertFalse(passed)
        self.assertIn("Forbidden import detected: subprocess", notes[0])

    def test_stage4_d1_forward_kinematics_and_jacobian(self) -> None:
        plant = UnitreeD1PhysicsPlant(dt=0.002)
        q = np.array([0.0, 0.42, -0.78, 0.0, 0.36, 0.0], dtype=np.float64)
        ee_pos, chain = plant.forward_kinematics(q)
        self.assertEqual(ee_pos.shape, (3,))
        self.assertEqual(len(chain), 5)
        J = plant.compute_position_jacobian(q)
        self.assertEqual(J.shape, (3, 6))
        tau_g = plant.gravity_torque(q, payload_mass_kg=0.35)
        self.assertEqual(tau_g.shape, (6,))
        self.assertTrue(np.all(np.isfinite(tau_g)))

    def test_full_6_stage_self_evolution_loop_improvement(self) -> None:
        engine = RSISelfEvolutionEngine(
            workspace_root=WORKSPACE_ROOT,
            enable_live_network=False,
        )
        summary = engine.run_evolution_loop(
            num_generations=3,
            human_approved_live_dds=False,
            verbose=False,
        )
        history = summary["history"]
        self.assertEqual(len(history), 3)

        # Gen 0 should be blocked by the 15mm Sim-to-Real gate; Gen 1 & Gen 2 must pass and improve score
        self.assertFalse(history[0].sim_result.passed_sim2real_gate)
        self.assertTrue(history[1].sim_result.passed_sim2real_gate)
        self.assertTrue(history[2].sim_result.passed_sim2real_gate)
        self.assertGreater(
            history[2].evaluation.combined_score,
            history[0].evaluation.combined_score + 20.0,
        )
        self.assertLess(
            history[2].sim_result.tracking_rmse_m,
            history[0].sim_result.tracking_rmse_m * 0.25,
        )

        # Verify generated CycloneDDS packets match unitree-d1-1 `rt/arm_Command` schema
        dds_dump = json.loads(
            Path(history[2].real_result.dds_packet_dump_path).read_text(encoding="utf-8")
        )
        self.assertEqual(dds_dump["state_action_contract"], "d1_absolute_state_action_7d_v1")
        self.assertEqual(len(dds_dump["packets"]), 201)
        sample_cmd = dds_dump["packets"][0]["parsed_command"]
        self.assertEqual(sample_cmd["address"], 1)
        self.assertEqual(sample_cmd["funcode"], 2)
        for idx in range(7):
            self.assertIn(f"angle{idx}", sample_cmd["data"])


if __name__ == "__main__":
    unittest.main()
