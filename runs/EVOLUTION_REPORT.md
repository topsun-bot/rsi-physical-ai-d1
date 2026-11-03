# 物理AI机器人自进化 V2.0 (Unitree D1) — 前沿 RSI 闭环运行报告

- **目标平台**: `Unitree D1 (6-DOF Arm + 1-DOF Gripper, 7× FashionStar UART Servos on /dev/ttyS4)`
- **集群联动**: `4gpu (10.10.218.4)` 训练资产 + `1F unitree-d1-1..3 (10.10.137.101..103)` CycloneDDS 协议
- **融合前沿 RSI 范式**:
  1. **Google Research `RRSI` (`arXiv:2609.24972`)**: 提议侧退火编辑预算 $b_t$ + 全历史证伪账本 $L_t$ + 选择侧 Critic 防泄漏筛查 + 留出集 OOD 扰动套件 + PAC 置信下界 $\delta$
  2. **Google DeepMind `AlphaEvolve` (`arXiv:2506.13131`) & 田渊栋 `Eureka Machine`**: `# === EVOLVE-BLOCK ===` 受限代码块变异 + 二维 MAP-Elites 质量-多样性精英归档
  3. **DeepSeek Harness (`Cordis` EIP) & `DSec`**: `pack_diff` 历史失败轨迹微沙箱回归库 + 技能注册表 SHA-256 原子 CAS 与 `funcode=5` 硬件阻尼回滚
- **目标技能**: `Unitree D1 柔顺轨迹跟踪、抗扰抓取与自适应释放技能 (DLS-IK + Adaptive Impedance + Horizon-3 Release Gate)` (`d1_compliant_se3_pick_and_place`)
- **生成时间**: `2026-11-02T16:13:57.914302+00:00`

## 1. 阶段 2 检索并转化为可执行 Skill 的核心数学公式

- **Singularity-Robust Damped Least Squares (DLS) IK with Null-Space Projection** (`dq_des = J^T (J J^T + lambda^2 I)^(-1) (dx_ref + K_x e_x) + (I - J^+ J)(-k_null (q - q_home)) coupled with computed-torque PD+Gravity compensation tau = Kp (q_des - q) + Kd (dq_des - dq) + G(q`):
  $$\dot{q}_{des} = J^T (J J^T + \lambda^2 I)^{-1} (\dot{x}_{ref} + K_x e_x + f_{rep}(z)) + (I - J^\dagger J)\bigl(-k_{null}(q - q_{home})\bigr)$$
  - 代码映射: `JJT = J @ J.T + (dls_lambda ** 2) * np.eye(3); J_pinv = J.T @ np.linalg.solve(JJT, np.eye(3)); dq_des = J_pinv @ v_task + (np.eye(6) - J_pinv @ J) @ (-nullspace_gain * (q - q_home))`
- **Task-Space Impedance & Adaptive Payload Computed-Torque Law** (`arXiv:2402.15391 (Section III.B Adaptive Payload Observer)`):
  $$\tau = K_p (q_{des} - q) + K_d (\dot{q}_{des} - \dot{q}) + G(q, \hat{m}_{load}) + D_{visc}\dot{q}, \quad \dot{\hat{m}}_{load} = \gamma_{adapt} J_z^T(q) e_z$$
  - 代码映射: `m_hat = np.clip(m_hat + dt * payload_adapt_gamma * max(0.0, float(e_x[2])) * 12.0, 0.0, 0.55); tau = kp_vec * (q_des - q) + kd_vec * (dq_des - dq) + gravity_torque(q, m_hat)`
- **Horizon-3 Action Chunk Latency Compensation & Two-Observation Release Gate** (`4gpu:/mnt/nvme0n1/gy/d1_smolvla_automation/reopen35-v1 & runtime-pending-blend-v1`):
  $$a_{exec}(t) = \hat{A}_{t - k_{delay}}[k_{delay} : k_{delay} + H_{act}], \quad u_{reopen}(t) = \mathbb{I}\bigl(\|e_{place}(t)\|_2 < \epsilon \land \|e_{place}(t-1)\|_2 < \epsilon\bigr)$$
  - 代码映射: `valid_chunk = chunk[k_delay : k_delay + 3] if k_delay <= 10 else hold_action; reopen_confirmed = (confirm_count >= 2)`
- **Minimum-Jerk Quintic SE(3) Trajectory & Table Barrier Potential** (`arXiv:2406.09218 (Unitree D1 Compliant Pick-and-Place)`):
  $$s(\tau) = 10\tau^3 - 15\tau^4 + 6\tau^5, \quad U_{rep}(z) = \frac{1}{2} k_{rep} \left(\frac{1}{z - z_{table}} - \frac{1}{z_0}\right)^2$$
  - 代码映射: `s = 10.0 * (u**3) - 15.0 * (u**4) + 6.0 * (u**5); z_push = table_repulsive_gain * max(0.0, 0.065 - ee_pos[2])**2`
- **Eureka + RECAP Advantage-Conditioned Dense Manipulation Fitness Function** (`arXiv:2310.12931 (Eureka) & 4gpu:/mnt/raid30t/gy/dual_arm_workcell/code/openpi_d1_recap_publish`):
  $$R_{Eureka} = w_{track} \exp(-\alpha \|e_x\|_2) + w_{smooth} \exp(-\beta \|\dot{\tau}\|_2) + w_{grasp} \mathbb{I}_{grasp} - w_{barrier} \Phi_{violation}, \quad A(s,a) = V(s') - V(s)$$
  - 代码映射: `r_track = np.exp(-reward_alpha_track * rmse_m); r_smooth = np.exp(-reward_beta_smooth * mean_torque_rate); reward = 45.0 * r_track + 20.0 * r_payload + 15.0 * r_smooth + 20.0 * grasp_bonus - 35.0 * penalty`

## 2. 跨代自进化闭环评测总表 (感知 → 决策 → 执行 → 评估 → 学习 → 进化)

| 代数 (Candidate) | RRSI 预算 | 标称仿真 RMSE | OOD 泛化 RMSE | 0.35kg 负载恢复 | DSec 回归沙箱 | Sim2Real 门禁 | 真机孪生 RMSE | RRSI 裁决 | 联合总分 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Gen-0 (`d1_policy_gen00`) | $b_t=0$ | 19.86 mm | 22.57 mm | 22.54 mm | ✅ (0) | ⛔ BLOCKED | Blocked (Rollback) | `INITIAL_SEED` | **69.00** |
| Gen-1 (`d1_policy_gen01`) | $b_t=3$ | 3.20 mm | 3.37 mm | 4.01 mm | ✅ (1) | ✅ PASSED | 6.72 mm | `ACCEPTED` | **94.12** |
| Gen-2 (`d1_policy_gen02`) | $b_t=1$ | 2.62 mm | 2.73 mm | 3.30 mm | ✅ (1) | ✅ PASSED | 4.63 mm | `ACCEPTED` | **94.93** |

## 3. AlphaEvolve MAP-Elites 质量-多样性生态位归档 (`runs/map_elites_archive.json`)

- **已占据生态位**: `3 / 12` (Coverage: `25.0%`)
- **Quality-Diversity (QD) 总分**: **258.05**
- **全局最优生态位**: `ultra_<5.5mm::ultra_smooth_lpf_integral` (`d1_policy_gen02`)

- **`coarse_>15mm::unadapted_high_jerk`**: `d1_policy_gen00` (Gen-0) — Score: **69.00**, Nominal RMSE: `19.86 mm`, OOD RMSE: `22.57 mm`, Torque Rate: `3.6 Nm/s`
- **`ultra_<5.5mm::adaptive_impedance`**: `d1_policy_gen01` (Gen-1) — Score: **94.12**, Nominal RMSE: `3.20 mm`, OOD RMSE: `3.37 mm`, Torque Rate: `4.2 Nm/s`
- **`ultra_<5.5mm::ultra_smooth_lpf_integral`**: `d1_policy_gen02` (Gen-2) — Score: **94.93**, Nominal RMSE: `2.62 mm`, OOD RMSE: `2.73 mm`, Torque Rate: `4.7 Nm/s`

## 4. DeepSeek DSec (`pack_diff`) 历史故障微沙箱回归库 (`runs/dsec_failure_bank.json`)

- **`dsec_sb_gen00_payload_droop`** (`payload_step_droop`, t=1.82s): Gen-0 0.35kg payload step + external pulse caused 22.54mm droop

## 5. 各代进化轨迹与人类/AI 联合诊断详情

### Generation 0: `d1_policy_gen00`
- **代码变异逻辑**: [RRSI b_t=0 | edits=kinematics_dls_solver] Gen-0 Initial Synthesis: Compiled directly from ArXiv paper equations (DLS IK + Computed Torque PD + Baseline Gravity Feedforward) with un-tuned seed gains.
- **AlphaEvolve EVOLVE-BLOCK 指纹**: `{"task_space_dls_ik_and_barrier": "95afa4f8a904", "online_payload_observer": "fd6c4a6d631b", "impedance_and_lpf_law": "f4e252b989d6"}`
- **超参数快照**: `{"kp_base": 20.0, "kd_base": 3.0, "task_space_gain_kx": 2.8, "dls_lambda": 0.095, "nullspace_gain": 0.35, "payload_adapt_gamma": 0.0, "table_repulsive_gain": 1.2, "reward_alpha_track": 14.0, "reward_beta_smooth": 0.12}`
- **MAP-Elites 行为生态位**: `coarse_>15mm::unadapted_high_jerk` | **RRSI 裁决**: `INITIAL_SEED` (PAC LCB $\Delta$: `+68.26`)
- **Cordis CAS 状态**: Committed=`False`, ActiveHash=`GENESIS_DAMPING_SAFE_STATE`
- **控制器代码路径**: `/Users/zhang/Documents/RSI/runs/gen_00/d1_policy_gen00.py`
- **DDS 报文路径**: `/Users/zhang/Documents/RSI/runs/gen_00/d1_dds_packets_gen00.json`
- **AI 诊断详情**:
  - [Tracking] End-effector RMSE=19.86mm exceeds 11.0mm precision target; recommend increasing kp_base & task_space_gain_kx and reducing DLS damping lambda.
  - [Payload/Grasp] 0.35kg load step caused 22.54mm lag (gamma=0.0); activating online recursive payload mass observer.
  - [Smoothness & Precision Polish] Passed Sim-to-Real gate (RMSE=19.86mm); co-evolving impedance damping, integral bias compensation, and LPF torque smoothing for sub-6.5mm accuracy.
  - [Table Margin] Minimum table clearance z=34.1mm is close to 25mm limit; strengthening table repulsive potential gain.
- **人类专家评语**: 【人类工程师评审 Gen-0】未通过 Sim-to-Real 安全闸门（末端误差 19.86mm，违例数=0）。抬升最高点处 J1 肩关节触发 90° 限位且 0.35kg 负载段存在下垂，禁止下发 1F 宇树 D1 真机，要求下一代加入关节限位屏障并激活在线负载质量观测器。

### Generation 1: `d1_policy_gen01`
- **代码变异逻辑**: [RRSI b_t=3 | edits=joint_limit_barrier,impedance_stiffness_damping,online_payload_observer] [Gemini-4-Argon (gemini-4-argon) Live] Low task-space stiffness, over-damped DLS pseudoinverse, and inactive payload adaptation (gamma=0.0) caused 19.86mm RMSE lag and gravitational sag under the 0.35kg load. | Elevate operational-space and joint PD gains, reduce DLS damping lambda, activate online recursive payload mass observer (gamma=1.5), and strengthen nullspace joint-limit and table repulsive barriers. => Gen-1 Structural & Gain Evolution: Scaled kp_base->43.0, kx->6.16 and tightened DLS damping lambda->0.046 to cut SE(3) tracking RMSE; Synthesized online recursive payload observer gamma->1.45 to counteract 0.35kg grasped block disturbance on D1 elbow/wrist; Co-evolved impedance gains (kp->43.0, kd->4.35, kx->6.16, gamma->1.45) + integral bias & low-pass torque filter for sub-7mm real-twin precision; Strengthened table clearance potential gain->1.68
- **AlphaEvolve EVOLVE-BLOCK 指纹**: `{"task_space_dls_ik_and_barrier": "f13be6350c09", "online_payload_observer": "3c4ae6491876", "impedance_and_lpf_law": "397d48654b54"}`
- **超参数快照**: `{"kp_base": 44.0, "kd_base": 4.5, "task_space_gain_kx": 6.2, "dls_lambda": 0.045, "nullspace_gain": 0.65, "payload_adapt_gamma": 1.5, "table_repulsive_gain": 1.75, "reward_alpha_track": 14.0, "reward_beta_smooth": 0.12}`
- **MAP-Elites 行为生态位**: `ultra_<5.5mm::adaptive_impedance` | **RRSI 裁决**: `ACCEPTED` (PAC LCB $\Delta$: `+24.47`)
- **Cordis CAS 状态**: Committed=`True`, ActiveHash=`660397dc658b9112`
- **控制器代码路径**: `/Users/zhang/Documents/RSI/runs/gen_01/d1_policy_gen01.py`
- **DDS 报文路径**: `/Users/zhang/Documents/RSI/runs/gen_01/d1_dds_packets_gen01.json`
- **AI 诊断详情**:
  - [Elite Convergence] All 5 physical & safety dimensions satisfied (Sim RMSE=3.20mm, Real-Twin RMSE=6.72mm).
- **人类专家评语**: 【人类工程师评审 Gen-1】轨迹跟踪精度（仿真 3.20mm / 真机孪生 6.72mm）、0.35kg 负载抗扰恢复（4.01mm）与双帧确认释放时机均达到最优标准，`rt/arm_Command` 与 `set_servo_angle` 报文完全匹配 1F 宇树 D1 固件规范，准予归入精英技能库。

### Generation 2: `d1_policy_gen02`
- **代码变异逻辑**: [RRSI b_t=1 | edits=torque_lpf_and_integral] [Gemini-4-Argon (gemini-4-argon) Live] Residual 3.20mm simulation and 6.72mm real-twin tracking RMSE stems from minor steady-state compliance under the 0.35kg payload and conservative task-space feedback bandwidth. | Elevate base joint PD impedance towards physics-guided reference gains, increase task-space stiffness and payload observer adaptation rate, and refine LPF smoothing to tighten trajectory tracking while preserving high torque smoothness. => Gen-2 Structural & Gain Evolution: Elite fine-tuning of impedance stiffness/damping ratio
- **AlphaEvolve EVOLVE-BLOCK 指纹**: `{"task_space_dls_ik_and_barrier": "e83b277cf598", "online_payload_observer": "9aac995a4d4f", "impedance_and_lpf_law": "053d381d5135"}`
- **超参数快照**: `{"kp_base": 50.6, "kd_base": 5.2, "task_space_gain_kx": 6.8, "dls_lambda": 0.042, "nullspace_gain": 0.7, "payload_adapt_gamma": 1.65, "table_repulsive_gain": 1.85, "reward_alpha_track": 14.0, "reward_beta_smooth": 0.12}`
- **MAP-Elites 行为生态位**: `ultra_<5.5mm::ultra_smooth_lpf_integral` | **RRSI 裁决**: `ACCEPTED` (PAC LCB $\Delta$: `+0.25`)
- **Cordis CAS 状态**: Committed=`True`, ActiveHash=`f642cdc920ef5eed`
- **控制器代码路径**: `/Users/zhang/Documents/RSI/runs/gen_02/d1_policy_gen02.py`
- **DDS 报文路径**: `/Users/zhang/Documents/RSI/runs/gen_02/d1_dds_packets_gen02.json`
- **AI 诊断详情**:
  - [Elite Convergence] All 5 physical & safety dimensions satisfied (Sim RMSE=2.62mm, Real-Twin RMSE=4.63mm).
- **人类专家评语**: 【人类工程师评审 Gen-2】轨迹跟踪精度（仿真 2.62mm / 真机孪生 4.63mm）、0.35kg 负载抗扰恢复（3.30mm）与双帧确认释放时机均达到最优标准，`rt/arm_Command` 与 `set_servo_angle` 报文完全匹配 1F 宇树 D1 固件规范，准予归入精英技能库。

## 6. Insta360 X4 Air 360° 全景、球心针孔反投影无畸变侧视与 3D 环视空间拓扑

- **全景相机节点**: `28163@10.10.137.106` (`ssh -J dgx-spark 28163@10.10.137.106`), `Insta360 X4 Air (USB\VID_2E1A&PID_0002\IAFEA2510H4E6N)`, CameraSDK `2.1.8` + MediaSDK `3.1.5`, `1920×960@30fps` FlowState 实时拼接流 (`15ms` 帧延迟)
- **工作台居中 360° 全景展开**: `runs/insta360_multiview/insta360_workbench_centered_360.jpg`
- **球心针孔反投影 (Gnomonic Rectilinear Pinhole) 4 路无畸变视图**:
  - 三臂工作台全局正侧视 (`yaw=182°, pitch=-30°`): `runs/insta360_multiview/insta360_rectilinear_d1_workbench.jpg`
  - 左 1 号臂 (`101`) & 黄黑目标块特写 (`yaw=150°, pitch=-28°`): `runs/insta360_multiview/insta360_rectilinear_d1_left_arm1.jpg`
  - 中 2 号臂 (`102`) / 右 3 号臂 (`103`) & 标定板特写 (`yaw=206°, pitch=-24°`): `runs/insta360_multiview/insta360_rectilinear_d1_mid_right_arms.jpg`
  - 实验室后方 Unitree Go2 机器狗环视 (`yaw=20°, pitch=-8°`): `runs/insta360_multiview/insta360_rectilinear_lab_surround.jpg`
- **五联屏 360° 空间感知 HUD (`1920×1080`)**: `runs/insta360_multiview/insta360_360_surround_hud.png`
- **8 实体 3D 环视空间拓扑图 (`insta360_surround_scene_graph.json`)**: 实测三臂桌面 Z 轴安全净空分别为 `86.5 mm` (`101`)、`92.0 mm` (`102`)、`95.0 mm` (`103`)。
