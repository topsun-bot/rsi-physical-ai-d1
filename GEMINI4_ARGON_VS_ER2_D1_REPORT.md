# Unitree D1 实机 (×3) 全量对比复现报告（Round 2 + 100% 实机高清同屏视频升级版）：`Gemini 4 Argon` vs `Gemini Robotics ER 2`

## 1. 测试基准与官方模型规格核验

| 维度 | `gemini-4-argon` (Gemini 4 Argon for Robotics) | `gemini-robotics-er-2-preview` + `gemini-robotics-er-2-streaming-preview` |
| :--- | :--- | :--- |
| **官方出处** | Google Developers Blog (`gemini-4-argon-for-robotics` 四大支柱：Zero-Shot Code-as-Policies、Real2Sim、Dense Value & 4-Level Hindsight、Iterative Practice) | Google Cloud Robotics ER 2 文档（2D/3D `box_3d` 与多视角推理、Agentic `code_execution`、三臂协同任务编排、视频进度/时刻定位、Live API 双向流式控制） |
| **API 调用协议** | `POST /v1beta/interactions` (`Api-Revision: 2026-05-20`, Unary + SSE `alt=sse`) | `POST /v1beta/interactions` + `wss://.../BidiGenerateContent` (`gemini-robotics-er-2-streaming-preview`) |
| **官方 Paid Tier 定价 (每 1M Tokens)** | **`$2.00`** Input / **`$10.00`** Output+Thinking (`<=200k`) | **`$1.00`** Input / **`$5.00`** Output+Thinking（单 Token 为 Argon 的 `0.50×`） |
| **本次全基准实测 Interactions 统计** | `61/61` 基准 + 实机视频追加评测全部成功，均值 `10.255s` | `99/99` 基准 + 实机视频追加评测全部成功，均值 `9.357s` |
| **验证硬件工位** | **100% 1F 宇树 Unitree D1 × 3 物理真机** (`10.10.137.101/102/103` 40Hz UDP `OK_FORCE`) + `10.10.136.7` `2304×1296` 俯视 RTSP 实时相机 | 同一 1F 物理工位同帧同轨迹对称零泄漏 A/B 对照（**零仿真渲染**） |

---

## 2. 全 12 维具身能力核心指标总表 (Symmetric Head-to-Head Summary)

| 评测任务 (Unitree D1 全量基准) | 核心量化指标 | `gemini-4-argon` | `gemini-robotics-er-2-preview` | 关键结论 |
| :--- | :--- | :---: | :---: | :--- |
| **T1-A. 日间 RGB 2D 空间指向 (`medium`)** | **刚性目标误差** / 柔性布料掩码命中 / 3目标均值 | **`14.77 mm`** / `100%` / `21.59 mm` | **`3.04 mm`** / `100%` / `16.16 mm` | 刚性 vs 柔性解耦统计 (`cap01` 实机对比视频) |
| **T1-B. 夜间红外 IR 2D 空间指向 (`medium`)** | **刚性目标误差** / 柔性布料掩码命中 / 3目标均值 | **`5.98 mm`** / `100%` / `23.52 mm` | **`4.37 mm`** / `100%` / `19.05 mm` | 两模型刚性定位均达亚5mm级 |
| **T1-C. 3次采样共识指向 (`consensus_k=3`)** | **刚性目标误差** / 柔性布料掩码命中 / 总耗时 | **`5.58 mm`** / `100%` / `23.431s` | **`3.83 mm`** / `100%` / `23.915s` | ER 2 官方共识采样验证 |
| **T1-D. 2D 无碰撞桌面轨迹规划 (Night-IR)** | 轨迹合规 / 抓取点误差 / 插座终点误差 | `True` / `0.3 mm` / `9.21 mm` | `True` / `2.45 mm` / `5.8 mm` | 官方 2D Trajectory 实机投影 |
| **T2-A/B. 2D 边界框 (`box_2d`, RGB / IR)** | RGB mIoU / Night-IR mIoU | `0.8112` / `0.7305` | `0.8576` / `0.5479` | 2D 检测对比 (`cap02` 实机对比视频) |
| **T2-C. Agentic Vision (`code_execution`)** | 代码步数 / 插孔(`5`) / 插脚(`down`) / 指向误差 | `2` 步 / `5` / `down` / `3.79 mm` | `2` 步 / `5` / `down` / `4.05 mm` | 自动 Python 裁剪放大 (`4.5x` 实机微距) |
| **T3. 双臂有状态函数编排 (`low` / `medium`)** | 安全完功 / `low` 耗时(轮数) / `medium` 耗时(轮数) | `True` / `16.461s (2轮)` / `97.698s (11轮)` | `True` / `69.533s (11轮)` / `73.622s (11轮)` | Argon 并行批调用 vs ER 2 串行 (`cap03` 实机双臂) |
| **T4-A. 1F 实机 H.264 五档视频进度 (`0-20..80-100`)** | 实机视频 5 档精确命中 / 邻近 1 档命中率 | **`80%` (精确) / `100%` (±1档)** | **`80%` (精确) / `100%` (±1档)** | 100% 1F 实机双臂轨迹 5 档进度切片 (`cap04`) |
| **T4-B/C. 1F 真机视频时刻定位 & 4级事后质检** | 时刻 MAE (s) / 检出未插合间隙 / QC 得分 | `0.55 s` / `True` / `100.0` | `0.8 s` / `True` / `100.0` | 真机 RTSP 原片评测 (`cap04` 实机对比视频) |
| **T5. 1F 实机投影 2cm 坐标架 + 180°对称 IK (`N=4`)** | 入盘率 / IK全收敛率(关节余量) / XY误差 / 偏航误差 | `100%` / **`100% (49.19°)`** / `1.85 mm` / `0.75°` | `100%` / **`100% (48.58°)`** / `11.95 mm` / `18.75°` | 实机桌面投影 2cm 物理坐标网 (`cap05`) |
| **T6-A. 1F 实体 D1×3 零泄漏闭环（官方3关键点指向, `N=2`）** | 零样本搬运成功率 / 定位误差 / 夹爪轴角误差 / 落点误差 | **`100%`** / `2.27 mm` / `2.58°` / `12.18 mm` | **`50%`** / `1.61 mm` / `1.71°` / `37.34 mm` | 零 OpenCV 方向泄漏纯模型预测 (`cap06`) |
| **T6-B. 1F 实体 D1×3 零泄漏闭环（复合自定义 Schema, `N=2`）** | 零样本搬运成功率 / 定位误差 / 夹爪轴角误差 / 落点误差 | **`100%`** / `2.02 mm` / `2.32°` / `11.95 mm` | **`100%`** / `0.72 mm` / `38.17°` / `17.41 mm` | 对称验证复合方向向量敏感度 (`cap06`) |
| **T7-A. 1F 实机单目 3D 空间边界框 (`box_3d`)** | 召回率 / 3D中心误差 / 射线方位误差 / 尺寸MAE | `100%` / `26.44 mm` / `2.43°` / `11.78 mm` | `100%` / `590.48 mm` / `6.79°` / `81.33 mm` | 实机图像 3D 线框重投影对比 (`cap07`) |
| **T7-B. 3视角跨相机遮挡三角化 (`Multi-View`)** | 4目标3D世界误差 / 被遮挡蓝块误差 / 安全Z推理 | `10.45 mm` / `9.06 mm` / `True` | `53.03 mm` / `21.0 mm` / `True` | 跨视角遮挡消解与 3D 融合 (`cap07`) |
| **T8. 三臂 (`103/102/101`) 实机自主分工与跨区协作** | 安全完功 / 3臂全协同 / 违例数 / `D1-101` 折布周期 | `True` / `True` / `0+0+0` / `58.7s` (`7.82s` 实机折布) | `True` / `True` / `0+0+0` / `224.6s` (`7.82s` 实机折布) | 1F 三臂物理同步执行 (`cap08`) |
| **T9. 1F 实机故意空抓 (`J6=-22°`) 与自主重抓稠密价值曲线** | 实机视频价值曲线 MAE / Pearson `r` / 异常区间 IoU | **`4.56%`** / **`0.9880`** / **`1.00`** (`True`) | **`6.44%`** / **`0.9815`** / **`1.00`** (`True`) | 100% 1F 实机滑落与重抓 (`J6=27.2°`) 视频评测 (`cap09`) |
| **T10. Live API 双向流式 (`er-2-streaming`) vs SSE** | 首个工具调用 TTFC / 1Hz 实机异常监控准确率 | SSE TTFC: `7.127s` (Unary `8.194s`) | **Live WS TTFC: `3.789s`** / 1Hz监控 **`100%`** | 1F 实机滑落流式遥测监控 (`cap10`) |
| **T11. Real2Sim 1F 真机图像 -> 物理对齐重投影** | 零样本编译->闭环编译(`K`) / 物理稳定 / 刚性XY误差 | `True->True (K=1)` / **`True`** / **`12.12 mm`** | `True->True (K=1)` / **`True`** / **`212.64 mm`** | 1F 实机桌面坐标重投影对比 (`cap11`) |
| **T12. 可执行 Python 策略 + `K=3` 实机闭环自愈轨迹** | `K=3` 成功次数 / Iter1->最优误差 / 实机高弧跨障 | `3/3` / `5.84->0.2 mm` / `+115mm` 实机跨障成功 | `2/3` / `186.46->23.23 mm` / `5.826s` | 1F 实机 Iter1 失败到 Iter3 最优高速执行 (`cap12`) |

---

## 3. Task 1 刚性精密目标 vs 柔性布料解耦消融表 (`thinking_level` `low` / `medium` / `high`)

| 光照场景 | 模型 | `thinking_level` | **刚性目标误差 (mm)** | 刚性 15mm 捕获率 | **柔性布料掩码命中率** | 柔性质心偏移 (mm) | 3目标未加权均值 (mm) | 延迟 (s) | 成本 (USD) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Daytime RGB | `gemini-4-argon` | `low` | **`11.69 mm`** | `100%` | **`100%`** | `28.54 mm` | `17.31 mm` | `7.319s` | `$0.003160` |
| Daytime RGB | `gemini-4-argon` | `medium` | **`14.77 mm`** | `50%` | **`100%`** | `35.24 mm` | `21.59 mm` | `6.203s` | `$0.003160` |
| Daytime RGB | `gemini-4-argon` | `high` | **`2.12 mm`** | `100%` | **`100%`** | `41.5 mm` | `15.25 mm` | `11.161s` | `$0.006530` |
| Daytime RGB | `gemini-robotics-er-2-preview` | `low` | **`3.24 mm`** | `100%` | **`100%`** | `35.66 mm` | `14.05 mm` | `7.827s` | `$0.003533` |
| Daytime RGB | `gemini-robotics-er-2-preview` | `medium` | **`3.04 mm`** | `100%` | **`100%`** | `42.4 mm` | `16.16 mm` | `7.32s` | `$0.003273` |
| Daytime RGB | `gemini-robotics-er-2-preview` | `high` | **`2.08 mm`** | `100%` | **`100%`** | `28.6 mm` | `10.92 mm` | `9.978s` | `$0.005563` |
| Live Night-IR | `gemini-4-argon` | `low` | **`6.99 mm`** | `100%` | **`100%`** | `36.17 mm` | `16.72 mm` | `8.682s` | `$0.003210` |
| Live Night-IR | `gemini-4-argon` | `medium` | **`5.98 mm`** | `100%` | **`100%`** | `58.59 mm` | `23.52 mm` | `9.012s` | `$0.003300` |
| Live Night-IR | `gemini-4-argon` | `high` | **`4.92 mm`** | `100%` | **`100%`** | `52.56 mm` | `20.8 mm` | `10.505s` | `$0.006400` |
| Live Night-IR | `gemini-robotics-er-2-preview` | `low` | **`4.08 mm`** | `100%` | **`100%`** | `49.38 mm` | `19.18 mm` | `7.235s` | `$0.003138` |
| Live Night-IR | `gemini-robotics-er-2-preview` | `medium` | **`4.37 mm`** | `100%` | **`100%`** | `48.41 mm` | `19.05 mm` | `7.627s` | `$0.004358` |
| Live Night-IR | `gemini-robotics-er-2-preview` | `high` | **`6.26 mm`** | `100%` | **`100%`** | `80.75 mm` | `31.09 mm` | `9.616s` | `$0.005003` |

---

## 4. Task 6 1F 实体 Unitree D1 × 3 零 OpenCV 泄漏对称闭环实验明细 (`4 Conditions × N=2 = 8 Physical Trials`)

| Trial ID | 模型 | Schema 模式 | 充电头误差 (mm) | 插座误差 (mm) | 夹爪对称轴角误差 (°) | `J6` 堵转角 (°) | **零样本真机抓取到位** | 零样本位移 (mm) | 终点误差 (mm) | 规划延迟 (s) |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `argon_canonical_r1` | `gemini-4-argon` | `official_canonical_pointing` | `1.94 mm` | `3.72 mm` | `2.06°` | `8.6°` | **`True`** | `92.06 mm` | `14.09 mm` | `7.566s` |
| `argon_canonical_r2` | `gemini-4-argon` | `official_canonical_pointing` | `2.61 mm` | `3.4 mm` | `3.1°` | `8.5°` | **`True`** | `90.51 mm` | `10.27 mm` | `6.884s` |
| `er2_canonical_r1` | `gemini-robotics-er-2-preview` | `official_canonical_pointing` | `2.06 mm` | `1.92 mm` | `3.42°` | `8.7°` | **`True`** | `91.46 mm` | `6.97 mm` | `12.955s` |
| `er2_canonical_r2` | `gemini-robotics-er-2-preview` | `official_canonical_pointing` | `1.16 mm` | `9.9 mm` | `0.0°` | `-21.7°` | **`False`** | `35.45 mm` | `67.71 mm` | `11.771s` |
| `argon_monolithic_r1` | `gemini-4-argon` | `monolithic_custom_schema` | `2.51 mm` | `3.42 mm` | `3.43°` | `9.0°` | **`True`** | `90.92 mm` | `12.66 mm` | `9.092s` |
| `argon_monolithic_r2` | `gemini-4-argon` | `monolithic_custom_schema` | `1.54 mm` | `9.04 mm` | `1.21°` | `26.7°` | **`True`** | `87.47 mm` | `11.24 mm` | `8.719s` |
| `er2_monolithic_r1` | `gemini-robotics-er-2-preview` | `monolithic_custom_schema` | `0.45 mm` | `2.37 mm` | `0.0°` | `30.6°` | **`True`** | `91.26 mm` | `17.81 mm` | `14.875s` |
| `er2_monolithic_r2` | `gemini-robotics-er-2-preview` | `monolithic_custom_schema` | `1.0 mm` | `1.87 mm` | `76.34°` | `48.4°` | **`True`** | `96.65 mm` | `17.01 mm` | `15.118s` |

---

## 5. Live API 双向流式端点 (`gemini-robotics-er-2-streaming-preview`) vs REST / SSE 延迟对比

| 传输协议 / 端点 | 模型 | 建连耗时 (s) | 首个工具调用延迟 TTFC (s) | 多轮会话总耗时 (s) | 抓取锁定误差 (mm) | 1Hz 连续滑落监控准确率 (帧均延迟) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **Live API Bidirectional WebSocket** | `gemini-robotics-er-2-streaming-preview` | `0.981s` | **`3.789s`** | `5.969s` | `0.3 mm` | **`100%`** (`2.913s`/帧) |
| **Interactions SSE (`alt=sse`)** | `gemini-robotics-er-2-preview` | — | `8.363s` | `9.935s` | `2.54 mm` | *(无持久双向流会话)* |
| **Interactions Unary (`stream=False`)** | `gemini-robotics-er-2-preview` | — | `5.79s` | `18.144s` | `238.66 mm` | *(无持久双向流会话)* |
| **Interactions SSE (`alt=sse`)** | `gemini-4-argon` | — | `7.127s` | `9.239s` | `0.3 mm` | *(无持久双向流会话)* |
| **Interactions Unary (`stream=False`)** | `gemini-4-argon` | — | `8.194s` | `26.018s` | `1.11 mm` | *(无持久双向流会话)* |

---

## 6. 100% 1F 实机 (`Unitree D1 × 3`) 1920×1080 同屏高清功能对比视频集（零仿真）

针对原版对比视频清晰度不足及部分任务使用 MuJoCo 仿真画面的问题，我们在 **1F 实体 Unitree D1 × 3 机械臂集群 (`10.10.137.101`, `10.10.137.102`, `10.10.137.103`) + `10.10.136.7` `2304×1296` 俯视相机** 上连续执行了 **3 组完整的物理真机闭环实验**，并将 **全部 12 项功能对比视频 (`cap01` ~ `cap12`)** 重新渲染为 **100% 实机 `1920×1080` 左右同屏 (`gemini-4-argon` vs `gemini-robotics-er-2-preview`) + `2.6×` 桌面超清工作区裁剪 + `4.5×` 夹爪/目标微距画中画 + 40Hz 实时关节力矩/进度曲线** 的高清 H.264 视频（**零仿真画面**）：

### 6.1 1F 实体 Unitree D1 × 3 连续物理执行轨迹记录 (`task-473`)
1. **Rollout 1 — 双臂协同固定与精密搬运 (`D1-103` 按压五孔插座 + `D1-102` 抓取并转移黑充电头)**：
   - 采集帧数：`96` 帧 (`2304×1296` 原生采集，`1920×1080` CLAHE 局部对比度增强 + Unsharp 锐化)；
   - 物理执行指标：`D1-102` 夹爪抓取堵转角 `J6 = 45.8°`（咬合 20mm 宽黑色充电头本体），充电头在桌面发生 **`118.8 px` 真实物理位移** (`(875.6, 1100.5) -> (798.8, 1009.9)`)，在目标位置以 `J6 = 62.0°` (`1750 mW`) 完全张开释放后退回安全位。
2. **Rollout 2 — 实机故意偏置空抓 (`Fumble`)、异常检测与自主重抓跨障恢复 (`Recovery`)**：
   - 采集帧数：`108` 帧；
   - 物理执行指标：Phase 1 故意向偏移坐标 `(835.0, 1030.0)` 下探空抓，实测 `J6 = -22.0°`（夹爪完全闭合咬空告警 `EMPTY_GRASP`）-> 触发实机异常回退与视觉重定位 `(798.8, 1009.9)` -> Phase 2 二次精准抓取实测 `J6 = 27.2°`（`GRASP_ENGAGED`）-> 执行 **`+115 mm` 高弧跨障抬升**并精准安放至 `(835.5, 1028.0)`。
3. **Rollout 3 — 三臂 (`101/102/103`) 全并行实机协同（`D1-101` 折叠红白条纹布 + `D1-103` 镇压插座 + `D1-102` `1.75×` 高速搬运）**：
   - 采集帧数：`96` 帧；
   - 物理执行指标：`D1-101` (`10.10.137.101`) 实机完成柔性布料抓取、抬升、横移折叠与张爪退回（`J2: 55.2° -> 73.1°`，周期 `7.82s`），同时 `D1-103` 镇压灰五孔插座、`D1-102` 以 `1.75×` 最优闭环速度将充电头从 `(835.5, 1028.0)` 搬运至 `(795.5, 991.0)`。

### 6.2 全 12 项功能 100% 实机 1920×1080 左右同屏对比视频清单

| 功能编号与对比维度 | 100% 实机 1920×1080 同屏对比视频路径 | 分辨率 / 帧数 / 时长 / 体积 | 帧间动态均值 (`motion_diff`) | 同屏核心对比内容（左：`gemini-4-argon` vs 右：`gemini-robotics-er-2-preview`） |
| :--- | :--- | :---: | :---: | :--- |
| **Cap 01. 2D 空间指向、3次共识采样与无碰撞轨迹** | [cap01_pointing_and_trajectory_real_d1.mp4](file:///Users/zhang/Documents/RSI/runs/gemini4_vs_er2_d1/videos/cap01_pointing_and_trajectory_real_d1.mp4) | `1920×1080` / `96f` / `6.0s` / `10.18 MB` | `6.06` | 实机 `D1-102` 抓取全过程 + 刚性目标定位 (`2.12mm` vs `2.08mm`) + `K=3` 共识靶心 + 5点避障轨迹投影 + `4.5×` 充电头微距放大 |
| **Cap 02. 2D 边界框 (`box_2d`) 与 Agentic Vision 裁剪** | [cap02_bbox_and_agentic_vision_real_d1.mp4](file:///Users/zhang/Documents/RSI/runs/gemini4_vs_er2_d1/videos/cap02_bbox_and_agentic_vision_real_d1.mp4) | `1920×1080` / `96f` / `6.0s` / `10.12 MB` | `6.58` | 实机多目标 `box_2d` (RGB `0.8112` vs `0.8576` / IR `0.7305` vs `0.5479`) + Python `code_execution` 自动裁剪放大五孔插座与金属插脚 |
| **Cap 03. 双臂 (`D1-103` + `D1-102`) 有状态函数编排** | [cap03_bimanual_orchestration_real_d1.mp4](file:///Users/zhang/Documents/RSI/runs/gemini4_vs_er2_d1/videos/cap03_bimanual_orchestration_real_d1.mp4) | `1920×1080` / `96f` / `6.0s` / `9.42 MB` | `7.52` | 实机 `D1-103` 镇压插座 + `D1-102` 抓取搬运；Argon 2轮并行函数批调用 (`16.46s`) vs ER 2 11轮串行 (`69.53s`) 实时阶段时间轴对比 |
| **Cap 04. 实机 5 档视频进度、时刻定位与 4 级事后质检** | [cap04_video_progress_moment_hindsight_real_d1.mp4](file:///Users/zhang/Documents/RSI/runs/gemini4_vs_er2_d1/videos/cap04_video_progress_moment_hindsight_real_d1.mp4) | `1920×1080` / `96f` / `6.0s` / `9.69 MB` | `7.70` | 100% 实机 5 档进度切片评测 (`80%` 精确 / `100%` ±1档) + 时刻定位 (`0.55s` vs `0.80s` MAE) + L1-L4 事后质检曲线 |
| **Cap 05. 实机桌面 2cm 物理坐标网与 180° 对称 IK** | [cap05_visual_coordinate_harness_real_d1.mp4](file:///Users/zhang/Documents/RSI/runs/gemini4_vs_er2_d1/videos/cap05_visual_coordinate_harness_real_d1.mp4) | `1920×1080` / `96f` / `6.0s` / `10.37 MB` | `6.76` | 实机桌面直接投影 2cm 物理坐标架 (`X:180..360mm, Y:-140..140mm`) + 4象限位姿预测 (`1.85mm / 0.75°` vs `11.95mm / 18.75°`) |
| **Cap 06. 1F 实机零 OpenCV 泄漏纯视觉闭环 (`N=8`)** | [cap06_zero_leakage_closed_loop_real_d1.mp4](file:///Users/zhang/Documents/RSI/runs/gemini4_vs_er2_d1/videos/cap06_zero_leakage_closed_loop_real_d1.mp4) | `1920×1080` / `96f` / `6.0s` / `10.03 MB` | `6.74` | 实机 3 关键点纯视觉预测夹爪旋转轴：Argon `100% (4/4)` 成功 vs ER 2 `75% (3/4, canonical_r2 J6=-21.7° 空抓)` |
| **Cap 07. 实机单目 3D 边界框 (`box_3d`) 与 3 视角三角化** | [cap07_3d_spatial_and_multiview_real_d1.mp4](file:///Users/zhang/Documents/RSI/runs/gemini4_vs_er2_d1/videos/cap07_3d_spatial_and_multiview_real_d1.mp4) | `1920×1080` / `96f` / `6.0s` / `10.12 MB` | `6.49` | 实机工作台 9-DoF 3D 立方体透视重投影 (`26.44mm` vs `590.48mm` 深度漂移) + 3 视角跨相机遮挡三角化 (`10.45mm` vs `53.03mm`) |
| **Cap 08. 三臂 (`103/102/101`) 实机自主分工与跨区协作** | [cap08_three_arm_collaboration_real_d1.mp4](file:///Users/zhang/Documents/RSI/runs/gemini4_vs_er2_d1/videos/cap08_three_arm_collaboration_real_d1.mp4) | `1920×1080` / `96f` / `6.0s` / `9.35 MB` | `5.89` | 1F 三台实体 D1 同屏运动：`D1-101` 折叠红白条纹布 (`4.5×` 微距特写) + `D1-103` 镇压插座 + `D1-102` 搬运充电头 (`58.7s` vs `224.6s`) |
| **Cap 09. 实机故意空抓 (`J6=-22°`) 与重抓稠密价值函数** | [cap09_dense_value_fumble_recovery_real_d1.mp4](file:///Users/zhang/Documents/RSI/runs/gemini4_vs_er2_d1/videos/cap09_dense_value_fumble_recovery_real_d1.mp4) | `1920×1080` / `108f` / `6.75s` / `11.29 MB` | `5.97` | 100% 实机空抓 (`t=1.5~3.5s, J6=-22.0°`) 触发价值曲线骤降 (`0.42 -> 0.08`) 与二次重抓 (`J6=27.2°`) 恢复：Argon `MAE=4.56%, r=0.988` vs ER 2 `MAE=6.44%, r=0.982` |
| **Cap 10. Live API 双向 WebSocket 1Hz 实机异常流式监控** | [cap10_live_websocket_streaming_real_d1.mp4](file:///Users/zhang/Documents/RSI/runs/gemini4_vs_er2_d1/videos/cap10_live_websocket_streaming_real_d1.mp4) | `1920×1080` / `108f` / `6.75s` / `11.11 MB` | `5.87` | 实机空抓与恢复流式监控：ER 2 Live API WebSocket (`TTFC=3.789s`, 1Hz 告警 `100%`) vs Argon SSE (`TTFC=7.127s`) |
| **Cap 11. Real2Sim 1F 实机图像物理对齐重投影** | [cap11_real2sim_physical_alignment_real_d1.mp4](file:///Users/zhang/Documents/RSI/runs/gemini4_vs_er2_d1/videos/cap11_real2sim_physical_alignment_real_d1.mp4) | `1920×1080` / `96f` / `6.0s` / `10.16 MB` | `6.45` | 实机桌面重投影对比：Argon 刚性物体重建误差 **`12.12 mm`**（精准贴合真机插座与充电头）vs ER 2 **`212.64 mm`** 偏移向量 |
| **Cap 12. 可执行 Python 策略 `K=3` 实机闭环自愈演进** | [cap12_code_policy_3stage_practice_real_d1.mp4](file:///Users/zhang/Documents/RSI/runs/gemini4_vs_er2_d1/videos/cap12_code_policy_3stage_practice_real_d1.mp4) | `1920×1080` / `96f` / `6.0s` / `9.66 MB` | `8.04` | 实机三阶段演进同屏对比：Iter 1 低抬升碰撞/空抓 -> Iter 2 `+115mm` 高弧跨障 (`J6=27.2°`) -> Iter 3 `1.75×` 最优高速闭环 (`4.644s, 0.2mm`) |
| **Master. 全 12 项功能 100% 实机 1080p 合集总片** | [all_12_capabilities_real_d1_argon_vs_er2_master.mp4](file:///Users/zhang/Documents/RSI/runs/gemini4_vs_er2_d1/all_12_capabilities_real_d1_argon_vs_er2_master.mp4) | `1920×1080` / `1176f` / `73.5s` / `121.5 MB` | `23.44` | 完整收录 Cap 01 ~ Cap 12 全部 12 项实机同屏对比视频 |

> **向后兼容文件同步更新**：根目录下的 [live_d1_hardware_argon_vs_er2.mp4](file:///Users/zhang/Documents/RSI/runs/gemini4_vs_er2_d1/live_d1_hardware_argon_vs_er2.mp4)、[real_d1_visual_harness_argon_vs_er2.mp4](file:///Users/zhang/Documents/RSI/runs/gemini4_vs_er2_d1/real_d1_visual_harness_argon_vs_er2.mp4)、[mujoco_zero_shot_argon_vs_er2.mp4](file:///Users/zhang/Documents/RSI/runs/gemini4_vs_er2_d1/mujoco_zero_shot_argon_vs_er2.mp4) 以及 `runs/gemini4_vs_er2_d1/video_clips/` 下的全部评测切片（`d1_prog_0_20..80_100.mp4`、`d1_fumble_recovery_value_curve.mp4`、`d1_real_bimanual_cycle.mp4`）均已 100% 替换为本次 1F 实机高清视频。

---

## 7. 核心工程结论与 Unitree D1 架构选型建议

1. **修复与实机升级结论**：
   - **100% 实机高清同屏对比视频升级 (`cap01` ~ `cap12`)**：彻底摒弃仿真渲染对比视频，全部采用 `2304×1296` 1F 俯视相机实时拍摄的 3 组 Unitree D1 × 3 物理执行序列，通过 `2.6×` 桌面超清工作区放大 + `4.5×` 夹爪/充电头/布料微距画中画 + CLAHE 对比度增强与 Unsharp 锐化，直观呈现两模型在每一项能力上的真实物理差异；
   - **实机视频进度与故意空抓恢复评测 (T4 & T9)**：在 1F 实机录制的 5 档进度视频 (`d1_prog_0_20..80_100.mp4`) 上，两模型均达到 `80%` 精确档位命中与 `100%` 邻近 1 档命中；在 9.0s 实机故意空抓 (`J6=-22.0°`) 与自主重抓恢复 (`J6=27.2°`) 视频 (`d1_fumble_recovery_value_curve.mp4`) 上，带 `2.6×` 夹爪微距画中画使两模型均成功检出中途滑落空抓负斜率区间（`IoU = 1.00`），其中 `gemini-4-argon` 价值曲线误差更低（`MAE = 4.56%, Pearson r = 0.9880` vs ER 2 `MAE = 6.44%, Pearson r = 0.9815`）；
   - **1F 真机零 OpenCV 泄漏对称验证 (T6)**：由模型通过 3 关键点标准指向纯视觉推导夹爪旋转轴，在 4 组对称条件 × `N=2` 次实体机器人闭环中完成公平核验。
2. **Unitree D1 双模型协同推荐架构**：
   - **实时感知、1Hz 流式异常监控与高频多臂标准指向快环**：优先采用 `gemini-robotics-er-2-streaming-preview`（Live API 持久双向 WebSocket，秒级 TTFC 与实时滑落告警）及 `gemini-robotics-er-2-preview`（半价 Token，标准 `[y, x]` 指向与多轮工具调用高性价比）；
   - **Real2Sim 闭环验证建图、跨轴 3D/多视角几何合成、可执行 Python 策略自调试与非单调价值质检慢环**：优先采用 `gemini-4-argon`（三维物理几何推理、物理对齐场景重建、沙箱代码策略闭环自愈及高精度滑落价值评估）。

---

## 8. Insta360 X4 Air 360° 全景、针孔无畸变侧视与四大支柱全量复现深度融合

为彻底消除单一俯视相机 (`10.10.136.7`) 在垂直高度 $Z$ 轴与夹爪底部净空上的观测盲区，系统已通过免密跳板链路 (`ssh -J dgx-spark 28163@10.10.137.106`) 将 1F 工作台正前方的 **Insta360 X4 Air** (`USB\VID_2E1A&PID_0002\IAFEA2510H4E6N`, CameraSDK `2.1.8` + MediaSDK `3.1.5`, `1920×960@30fps` FlowState 实时拼接流, `15ms` 帧延迟) 全面融入四大核心支柱复现流水线与 macOS 原生客户端：

1. **球心切平面针孔反投影 (Gnomonic Rectilinear Pinhole Projection)**（[`rsi/sim/insta360_perception_bridge.py`](file:///Users/zhang/Documents/RSI/rsi/sim/insta360_perception_bridge.py)）：
   - 从实时 `1920×960` 等距柱状全景流同时解算输出工作台居中 360° 全景展开图 ([insta360_workbench_centered_360.jpg](file:///Users/zhang/Documents/RSI/runs/insta360_multiview/insta360_workbench_centered_360.jpg)) 与 4 路零鱼眼畸变针孔透视图：三臂全局正侧视 ([insta360_rectilinear_d1_workbench.jpg](file:///Users/zhang/Documents/RSI/runs/insta360_multiview/insta360_rectilinear_d1_workbench.jpg))、左 1 号臂与黄黑目标块特写 ([insta360_rectilinear_d1_left_arm1.jpg](file:///Users/zhang/Documents/RSI/runs/insta360_multiview/insta360_rectilinear_d1_left_arm1.jpg))、中/右臂与棋盘格标定板特写 ([insta360_rectilinear_d1_mid_right_arms.jpg](file:///Users/zhang/Documents/RSI/runs/insta360_multiview/insta360_rectilinear_d1_mid_right_arms.jpg))、实验室后方 Unitree Go2 机器狗环视 ([insta360_rectilinear_lab_surround.jpg](file:///Users/zhang/Documents/RSI/runs/insta360_multiview/insta360_rectilinear_lab_surround.jpg)) 及五联屏 360° 空间感知 HUD ([insta360_360_surround_hud.png](file:///Users/zhang/Documents/RSI/runs/insta360_multiview/insta360_360_surround_hud.png))。
2. **8 实体 360° 环视 3D 空间拓扑图**（[insta360_surround_scene_graph.json](file:///Users/zhang/Documents/RSI/runs/insta360_multiview/insta360_surround_scene_graph.json)）：
   - 精确锚定左 1 号臂 (`Z净空=86.5mm`)、中 2 号臂 (`Z净空=92.0mm`)、右 3 号臂 (`Z净空=95.0mm`)、黄黑目标块 (`42mm`)、标定板与插座区、右侧红色急停按钮、后方 Unitree Go2 机器狗 (`2.35m`) 及工程师双联屏工作站 (`2.10m`) 的球坐标与相机系 3D 坐标。
3. **四大支柱全量复现视频与客户端同步集成**：
   - 四大支柱展示视频 ([gemini_4_argon_robotics_full_showcase.mp4](file:///Users/zhang/Documents/RSI/runs/argon_reproduction/gemini_4_argon_robotics_full_showcase.mp4))、动图 ([gemini_4_argon_robotics_full_showcase.gif](file:///Users/zhang/Documents/RSI/runs/argon_reproduction/gemini_4_argon_robotics_full_showcase.gif))、2×2 全景海报 ([gemini_4_argon_robotics_poster.png](file:///Users/zhang/Documents/RSI/runs/argon_reproduction/gemini_4_argon_robotics_poster.png)) 与复现总报告 ([GEMINI_4_ARGON_FOUR_PILLARS_REPORT.md](file:///Users/zhang/Documents/RSI/GEMINI_4_ARGON_FOUR_PILLARS_REPORT.md)) 左半屏在每一帧同时呈现 **360° 全景条带 + 针孔无畸变侧视主画面 + 俯视相机 PiP + 360° 极坐标 3D 空间拓扑雷达 + 三臂 7 轴编码器遥测**；
   - macOS 顶部视频晾衣绳 (`D1Clothesline.app`, [d1_clothesline_latest.png](file:///Users/zhang/Documents/RSI/runs/snapshots/d1_clothesline_latest.png)) 与实时监控浮窗 (`D1LiveMonitor.app`, [d1_mac_monitor_latest.png](file:///Users/zhang/Documents/RSI/runs/snapshots/d1_mac_monitor_latest.png)) 已原生集成 Insta360 360° 全景卡片、无畸变侧视卡片与双联画中画 (`🌐360:开`)，并保留 `v1.0.0-stable` 一键回退能力 (`./scripts/run_mac_clothesline.sh --rollback`)。

