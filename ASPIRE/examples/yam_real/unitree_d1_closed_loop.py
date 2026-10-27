task = get_task_context()
state_before = get_robot_state()
obs_before = get_observation()

home_left = [0.0, -1.56, 1.55, 0.0, 0.10, 0.0]
home_right = [-0.06, -1.56, 1.56, 0.0, 0.12, 0.0]

# Waypoint 1: Sweep yaw across while elevated and close grippers
wp2_left = [-0.25, -0.65, 0.65, 0.0, 0.35, 0.0]
wp2_right = [0.25, -0.65, 0.65, 0.0, 0.35, 0.0]
move_2l = move_to_joints(wp2_left, arm="left")
move_2r = move_to_joints(wp2_right, arm="right")
close_l = close_gripper(arm="left")
close_r = close_gripper(arm="right")
state_wp2 = get_robot_state()
obs_mid = get_observation()

# Waypoint 2: Smoothly return both arms to resting folded home pose
move_hl = move_to_joints(home_left, arm="left")
move_hr = move_to_joints(home_right, arm="right")
state_after = get_robot_state()
obs_after = get_observation()

result = {
    "task": task,
    "left_before_rad": state_before.joint_positions["left"].tolist(),
    "right_before_rad": state_before.joint_positions["right"].tolist(),
    "left_wp2_rad": state_wp2.joint_positions["left"].tolist(),
    "right_wp2_rad": state_wp2.joint_positions["right"].tolist(),
    "left_after_rad": state_after.joint_positions["left"].tolist(),
    "right_after_rad": state_after.joint_positions["right"].tolist(),
    "move_sweep_ok": bool(move_2l.ok and move_2r.ok),
    "gripper_close_ok": bool(close_l.ok and close_r.ok),
    "move_home_ok": bool(move_hl.ok and move_hr.ok),
}
