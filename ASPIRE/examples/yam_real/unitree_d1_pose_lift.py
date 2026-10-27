state_before = get_robot_state()
obs_before = get_observation()

# Prominent, safe elevated pose on BOTH Unitree D1 arms (left=10.10.137.102 center-foreground, right=10.10.137.101)
lift_left = [0.25, -0.65, 0.65, 0.0, 0.35, 0.0]
lift_right = [-0.35, -0.65, 0.65, 0.0, 0.35, 0.0]

move_l = move_to_joints(lift_left, arm="left")
open_l = open_gripper(arm="left")
move_r = move_to_joints(lift_right, arm="right")
open_r = open_gripper(arm="right")

state_after = get_robot_state()
obs_after = get_observation()

result = {
    "left_before_rad": state_before.joint_positions["left"].tolist(),
    "right_before_rad": state_before.joint_positions["right"].tolist(),
    "left_after_rad": state_after.joint_positions["left"].tolist(),
    "right_after_rad": state_after.joint_positions["right"].tolist(),
    "move_left_ok": bool(move_l.ok),
    "move_right_ok": bool(move_r.ok),
    "gripper_left_ok": bool(open_l.ok),
    "gripper_right_ok": bool(open_r.ok),
}
