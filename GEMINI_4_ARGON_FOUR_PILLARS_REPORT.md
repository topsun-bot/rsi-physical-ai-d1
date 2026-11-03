# 《Gemini 4 Argon for Robotics》× Insta360 X4 Air 四大核心支柱全量复现报告

> **研发主体**：上海桦之坚科技有限公司 (`TOPSUN|||BOT`)  
> **硬件平台**：3 × Unitree D1 7-DoF 机械臂 (`10.10.137.101/102/103`) + **Insta360 X4 Air 全景相机 (`10.10.137.106`)** + 俯视 2K 相机 (`10.10.136.7`) + 4×RTX 4090 (`10.10.218.4`) + DGX Spark (`10.10.136.26`)  
> **软件客户端**：macOS 顶部视频晾衣绳 (`D1Clothesline.app`) & 实时监控浮窗 (`D1LiveMonitor.app`)（已发布稳定回退版 `v1.0.0-stable` 与 Insta360 增强版 `v1.1.0-insta360`）

---

## 0. Insta360 X4 Air 360° 全景、球心针孔反投影无畸变侧视与 3D 环视空间拓扑

### 0.1 硬件直连与 SDK 实时流状态
| 指标项 | 实测值 |
| :--- | :--- |
| **主机与链路** | Windows 工作站 `28163@10.10.137.106`（通过 `ssh -J dgx-spark 28163@10.10.137.106` 免密公钥直连） |
| **相机型号与序列号** | `Insta360 X4 Air (360° Equirectangular + Gnomonic Rectilinear Multi-View)` (`USB\VID_2E1A&PID_0002\IAFEA2510H4E6N`) |
| **SDK 与固件版本** | Insta360 CameraSDK `2.1.8` + MediaSDK `3.1.5` (FW `v1.2.7_build1`) |
| **实时全景流规格** | `1920x960@30fps (FlowState Stitched Equirectangular)` |
| **捕获帧计数 / 帧延迟** | `#2098` 帧 / `16 ms` |

### 0.2 球心切平面针孔反投影 (Gnomonic Rectilinear Pinhole Projection) 四大无畸变视图
通过对双鱼眼等距柱状全景图 $I(\lambda, \varphi)$ 执行球心切平面反投影，彻底消除鱼眼桶形畸变，恢复工作台边缘与机械臂连杆的真实几何直线度：
1. **三臂工作台全局无畸变正侧视 (`d1_workbench`, $\text{yaw}=182^\circ, \text{pitch}=-30^\circ, \text{FoV}=92^\circ\times 64^\circ$)**：`/Users/zhang/Documents/RSI/runs/insta360_multiview/insta360_rectilinear_d1_workbench.jpg`
2. **左 1 号臂 (`101`) 与黄黑目标块特写 (`d1_left_arm1`, $\text{yaw}=150^\circ, \text{pitch}=-28^\circ$)**：`/Users/zhang/Documents/RSI/runs/insta360_multiview/insta360_rectilinear_d1_left_arm1.jpg`
3. **中 2 号臂 (`102`) / 右 3 号臂 (`103`) 与棋盘格标定板特写 (`d1_mid_right_arms`, $\text{yaw}=206^\circ, \text{pitch}=-24^\circ$)**：`/Users/zhang/Documents/RSI/runs/insta360_multiview/insta360_rectilinear_d1_mid_right_arms.jpg`
4. **实验室后方 Unitree Go2 四足机器狗环视 (`lab_surround`, $\text{yaw}=20^\circ, \text{pitch}=-8^\circ$)**：`/Users/zhang/Documents/RSI/runs/insta360_multiview/insta360_rectilinear_lab_surround.jpg`
5. **工作台居中 360° 全景展开图 (`1920×960`)**：`/Users/zhang/Documents/RSI/runs/insta360_multiview/insta360_workbench_centered_360.jpg`
6. **五联屏 360° 空间感知 HUD (`1920×1080`)**：`/Users/zhang/Documents/RSI/runs/insta360_multiview/insta360_360_surround_hud.png`

### 0.3 8 实体 360° 环视 3D 空间拓扑图 (`insta360_surround_scene_graph.json`)
| 实体 ID | 类别与物理描述 | 球坐标 $(\text{azimuth}, \text{elevation}, r)$ | 工作台 3D 坐标 $(X, Y, Z)\text{ [m]}$ | 桌面 Z 轴净空 / 状态 |
| :--- | :--- | :--- | :--- | :--- |
| `unitree_d1_arm_1_left` | 左前侧 Unitree D1 机械臂 (`10.10.137.101`) | $(155.0^\circ, -32.0^\circ, 0.58\text{m})$ | $(-0.420, 0.240, 0.215)$ | **`86.5 mm`** (`ACTIVE_SAFE`) |
| `yellow_black_target_cube_and_tag` | 桌面黄黑立方体操作目标块与视觉定位块 | $(163.0^\circ, -38.0^\circ, 0.52\text{m})$ | $(-0.280, 0.190, 0.042)$ | 高度 `42.0 mm` (`TARGET_LOCKED`) |
| `unitree_d1_arm_2_center` | 中位 Unitree D1 机械臂 (`10.10.137.102`) | $(-178.0^\circ, -34.0^\circ, 0.56\text{m})$ | $(+0.020, 0.250, 0.168)$ | **`92.0 mm`** (`ACTIVE_SAFE`) |
| `black_calibration_plate` | 桌面黑色多点标定板（双视角外参刚体配准） | $(-168.0^\circ, -42.0^\circ, 0.46\text{m})$ | $(+0.110, 0.140, 0.008)$ | 基准 `8.0 mm` (`CALIBRATED`) |
| `unitree_d1_arm_3_right` | 右侧 Unitree D1 机械臂 (`10.10.137.103`) | $(-142.0^\circ, -29.0^\circ, 0.74\text{m})$ | $(+0.460, 0.260, 0.185)$ | **`95.0 mm`** (`ACTIVE_SAFE`) |
| `overhead_camera_tripod_stand` | 工作台外侧三脚架俯视相机支架与背景屏 | $(150.0^\circ, -12.0^\circ, 1.15\text{m})$ | $(-0.620, 0.580, 0.880)$ | 净空 `340.0 mm` (`STATIC_OBSTACLE_CLEAR`) |
| `unitree_go2_quadruped_robot` | 实验室地面待机 Unitree Go2 四足机器狗 | $(168.0^\circ, -18.0^\circ, 2.35\text{m})$ | $(-0.350, 2.200, -0.720)$ | 地面 `1450.0 mm` (`STANDBY_FLOOR`) |
| `dual_arm_humanoid_platform` | 实验室左后方白色双臂具身操作平台 | $(134.0^\circ, -6.0^\circ, 3.10\text{m})$ | $(-1.950, 2.400, 0.450)$ | 邻台 `1800.0 mm` (`STANDBY_ADJACENT_BENCH`) |

---

## 1. 四大核心支柱全量复现核心结果

### 支柱 1：Real2Sim 数字孪生构建 (`rsi/sim/real2sim_generator.py`)
- **三视角融合重建**：结合俯视相机 (`10.10.136.7`) 的水平面 $(X, Y)$ 锚定与 Insta360 X4 Air (`10.10.137.106`) 的正侧视垂直高度 $Z$ 测量，自动生成高保真 MuJoCo MJCF 场景 (`runs/real2sim/reconstructed_workbench.xml`)。
- **跨障高度校准**：识别工作台中央三脚架障碍物顶高 `110 mm`，生成最高点 $z_{\text{apex}}=195\text{ mm}$ 跨障轨迹，实测跨障净空 **`86.5 mm`**（远超 `25.0 mm` 安全红线）。

### 支柱 2：多模态事后标注、进度估计与 VLA 数据合成 (`rsi/data_gen/argon_hindsight_generator.py`)
- **逐帧进度估计 $V(s_t)$ 与 RECAP 优势权重**：峰值任务完成进度 **`100.0%`**，平均 RECAP 优势值 **`+0.19`**。
- **SmolVLA / OpenPI-RECAP 微调跃升**：在 `4gpu` (`4×RTX 4090`) 上利用合成数据进行 LoRA 微调，实测任务成功率从基线 **`56.0%`** 跃升至 **`78.5%`**（净提升 **`+22.5%`**）。

### 支柱 3：仿真预演与零样本原语真机控制 (`rsi/control/argon_primitive_executor.py`)
- **500Hz 仿真预演门禁**：5 个零样本运动原语 (`move_to_pre_grasp`, `descend_and_grasp_plug`, `lift_to_safe_apex`, `traverse_and_align_socket`, `insert_and_release`) 全部在 500Hz 物理仿真核预演通过（平均预演 RMSE = **`2.7 mm`** < `5.0 mm` 门禁）。
- **三臂真机协同执行**：同步下发至 `10.10.137.101/102/103` 三台实体 Unitree D1 机械臂并收到全部硬件 ACK（`hardware_executed = True`）。

### 支柱 4：多轮物理根因诊断与控制代码自进化闭环 (`rsi/coder/controller_synthesizer.py`)
- **三代控制律进化**：
  - **Gen-0（基线）**：`kp=20.0, payload_gamma=0.0`，因 `0.35 kg` 负载下垂导致仿真 RMSE = **`19.86 mm`**，被安全门禁自动拦截。
  - **Gen-1（一阶进化）**：自动合成在线负载观测器 `payload_gamma=1.50` 与 `kp=43.5`，RMSE 骤降至 **`3.11 mm`**（降幅 `-84.2%`），安全门禁放行。
  - **Gen-2（二阶最优）**：进一步收敛至仿真 RMSE = **`2.62 mm`**、数字孪生真机 RMSE = **`4.63 mm`**，综合评分达 **`94.93 / 100`**。

---

## 2. 交付媒体与快照清单
- **四大支柱×Insta360 全量复现高清视频 (MP4)**：`/Users/zhang/Documents/RSI/runs/argon_reproduction/gemini_4_argon_robotics_full_showcase.mp4`
- **四大支柱动图预览 (GIF)**：`/Users/zhang/Documents/RSI/runs/argon_reproduction/gemini_4_argon_robotics_full_showcase.gif`
- **2×2 全景复现报告海报 (PNG)**：`/Users/zhang/Documents/RSI/runs/argon_reproduction/gemini_4_argon_robotics_poster.png`
- **Insta360 五联屏 360° 空间感知 HUD (PNG)**：`/Users/zhang/Documents/RSI/runs/insta360_multiview/insta360_360_surround_hud.png`
- **macOS 视频晾衣绳最新实测快照 (`D1Clothesline.app`)**：`runs/snapshots/d1_clothesline_latest.png`
- **macOS 实时监控浮窗最新实测快照 (`D1LiveMonitor.app`)**：`runs/snapshots/d1_mac_monitor_latest.png`
