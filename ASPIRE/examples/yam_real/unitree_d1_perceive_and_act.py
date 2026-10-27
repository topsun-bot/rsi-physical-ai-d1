task = get_task_context()
state_before = get_robot_state()

# Step 1: Perceive the 1F physical workbench via TP-LINK RTSP camera + SAM3 on GPU
seg_before = segment_text("top", "robotic arm")
seg_dog = segment_text("top", "robot dog")

before_boxes = [
    {"score": float(s.score), "box_xyxy": [float(v) for v in s.box_xyxy]}
    for s in seg_before.segmentations
    if s.box_xyxy is not None
]
dog_boxes = [
    {"score": float(s.score), "box_xyxy": [float(v) for v in s.box_xyxy]}
    for s in seg_dog.segmentations
    if s.box_xyxy is not None
]

# Step 2: Conditioned on visual perception (SAM3 detecting the robotic arms), execute 3-arm overhead reach & salute
if seg_before.ok:
    lift_left = [0.38, -0.85, 0.55, 0.25, 0.35, 0.0]
    lift_right = [-0.38, -0.85, 0.55, -0.25, 0.35, 0.0]
    move_l = move_to_joints(lift_left, arm="left")
    move_r = move_to_joints(lift_right, arm="right")
    open_l = open_gripper(arm="left")
    open_r = open_gripper(arm="right")
    state_peak = get_robot_state()

    # Step 3: Re-perceive with SAM3 at the elevated peak to verify physical pose change in overhead camera
    seg_peak = segment_text("top", "robotic arm")
    seg_gripper_peak = segment_text("top", "robot gripper")
    peak_boxes = [
        {"score": float(s.score), "box_xyxy": [float(v) for v in s.box_xyxy]}
        for s in seg_peak.segmentations
        if s.box_xyxy is not None
    ]

    # Step 4: Close grippers and return smoothly to resting home pose
    close_l = close_gripper(arm="left")
    close_r = close_gripper(arm="right")
    home_left = [0.0, -1.56, 1.54, 0.0, 0.08, 0.0]
    home_right = [-0.05, -1.56, 1.56, 0.0, 0.12, 0.0]
    home_l = move_to_joints(home_left, arm="left")
    home_r = move_to_joints(home_right, arm="right")
else:
    move_l = None
    move_r = None
    home_l = None
    home_r = None
    state_peak = state_before
    peak_boxes = []

state_after = get_robot_state()
seg_after = segment_text("top", "robotic arm")
after_boxes = [
    {"score": float(s.score), "box_xyxy": [float(v) for v in s.box_xyxy]}
    for s in seg_after.segmentations
    if s.box_xyxy is not None
]

result = {
    "sam3_arm_detected_before": bool(seg_before.ok),
    "sam3_arm_count_before": len(before_boxes),
    "sam3_arm_boxes_before": before_boxes,
    "sam3_dog_boxes": dog_boxes,
    "sam3_arm_boxes_peak": peak_boxes,
    "sam3_arm_boxes_after": after_boxes,
    "left_peak_rad": state_peak.joint_positions["left"].tolist(),
    "right_peak_rad": state_peak.joint_positions["right"].tolist(),
    "left_after_rad": state_after.joint_positions["left"].tolist(),
    "right_after_rad": state_after.joint_positions["right"].tolist(),
    "motion_ok": bool(move_l is not None and move_l.ok and move_r.ok and home_l.ok and home_r.ok),
}
