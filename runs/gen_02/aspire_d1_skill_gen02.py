"""
Auto-Exported ASPIRE `cap_harness` (`yam_real/unitree-d1`) Closed-Loop Skill
Candidate: d1_policy_gen02 (Generation 2)
Target Station: unitree-d1 (right arm: d1://10.10.137.101:19001, left arm: d1://10.10.137.102:19001)
"""
import numpy as np

task = get_task_context()
state_before = get_robot_state()
obs_before = get_observation()
home_q = state_before.joint_positions["right"].copy()

# 1. Approach & Compliant Grasp at Pick Target
wp_pick = np.array([0.37171, 0.90822, -1.73502, 0.00536, 0.25061, 0.0], dtype=float)
open_gripper(arm="right")
move_pick = move_to_joints(wp_pick, arm="right")
grip_close = close_gripper(arm="right")
state_pick = get_robot_state()

# 2. Lift 0.35kg Payload via Obstacle-Clearing Apex
wp_apex = np.array([-0.05937, 1.39284, -1.85817, -9e-05, 0.20997, 0.0], dtype=float)
move_apex = move_to_joints(wp_apex, arm="right")
obs_mid = get_observation()

# 3. Transport to Goal & Two-Observation Confirmed Release
wp_place = np.array([-0.55791, 0.96026, -1.75957, -0.00534, 0.16263, 0.0], dtype=float)
move_place = move_to_joints(wp_place, arm="right")
obs_pre_release_1 = get_observation()
obs_pre_release_2 = get_observation()
grip_open = open_gripper(arm="right")

# 4. Return to Home Pose
move_home = move_to_joints(home_q, arm="right")
state_after = get_robot_state()
obs_after = get_observation()

result = {
    "candidate_id": "d1_policy_gen02",
    "generation": 2,
    "task": task,
    "move_pick_ok": bool(move_pick.converged),
    "move_apex_ok": bool(move_apex.converged),
    "move_place_ok": bool(move_place.converged),
    "move_home_ok": bool(move_home.converged),
    "obs_views": list(obs_after.views.keys()),
}
