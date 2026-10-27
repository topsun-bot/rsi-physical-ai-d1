# 物理AI机器人自进化 V1.0 (`Physical-AI-RSI-Unitree-D1`)

基于 **Unitree D1 (6-DOF 机械臂 + 1-DOF 平行夹爪)** 与 **`4gpu (10.10.218.4)` + `1F unitree-d1-1..3 (10.10.137.101..103)`** 真实资产落地的 **“感知—决策—执行—评估—学习—进化”** 六阶段递归自进化（RSI）工程框架。

## 六阶段全闭环架构

1. **阶段 1 — 人类设定规则、技能、约束与工具集** ([`configs/d1_manifest.yaml`](configs/d1_manifest.yaml), [`rsi/core/manifest.py`](rsi/core/manifest.py))
   - 定义 Unitree D1 7D 状态-动作契约 (`d1_absolute_state_action_7d_v1`)、6+1 轴角度/角速度/力矩硬限幅、桌面防碰撞高度红线 ($z \ge 25\text{ mm}$)、AST 代码静态禁用名单及 Sim-to-Real 准入门槛（RMSE $\le 15.0\text{ mm}$ 且零安全违例）。
2. **阶段 2 — AI 全网检索最新论文与开源方案 → 提取公式与代码结构转化为可执行 Skill** ([`rsi/scout/paper_github_scout.py`](rsi/scout/paper_github_scout.py), [`rsi/scout/skill_extractor.py`](rsi/scout/skill_extractor.py))
   - 实时检索 ArXiv 与 GitHub，融合 `4gpu` 上沉淀的 SmolVLA (`reopen35-v1`)、ACT、OpenPI-RECAP 方案及 `unitree-d1-1` 板载固件架构，自动抽取 5 条核心数学公式并编译为独立可调用的 Python 技能模块 [`skills_library/d1_compliant_se3_pick_and_place.py`](skills_library/d1_compliant_se3_pick_and_place.py)，同步注册至 [`configs/skills_catalog.json`](configs/skills_catalog.json)。
3. **阶段 3 — AI 自主编写/进化机器人控制代码** ([`rsi/coder/controller_synthesizer.py`](rsi/coder/controller_synthesizer.py))
   - 根据提取的 `ExecutableSkill` 与上一代联合评估的进化指令，自主合成并变异控制器代码 (`runs/gen_XX/d1_policy_genXX.py`)，执行 AST 抽象语法树静态安全审查。
4. **阶段 4 — 仿真环境训练与多工况抗扰验证** ([`rsi/sim/d1_kinematics_dynamics.py`](rsi/sim/d1_kinematics_dynamics.py), [`rsi/sim/validator.py`](rsi/sim/validator.py), [`assets/unitree_d1/unitree_d1.xml`](assets/unitree_d1/unitree_d1.xml))
   - 在 500 Hz 物理仿真核中执行“五次多项式接近 → `0.35 kg` 负载突变抓取 + 外力扰动冲击 → 跨障搬运 → 双帧观测确认释放”全流程验证。
5. **阶段 5 — 下发实体机器人真机实测与安全闸门** ([`rsi/deploy/d1_sim2real_bridge.py`](rsi/deploy/d1_sim2real_bridge.py))
   - 未达标策略由 Sim-to-Real Gate 直接拦截；达标策略自动生成与 1F `unitree-d1-1..3` 板载 `marm_communication_node` 及 `teleop_direct` 完全兼容的 `rt/arm_Command` (`funcode=2` 7轴控制 + `funcode=5` 阻尼卸力) 与 `set_servo_angle` (`SetServoAngle_`) 报文包，并在含编码器量化与串口延迟的真机数字孪生核中实测。
6. **阶段 6 — AI 与人类共同打分评估并反馈迭代** ([`rsi/evaluator/joint_evaluator.py`](rsi/evaluator/joint_evaluator.py), [`rsi/evolution/loop_engine.py`](rsi/evolution/loop_engine.py))
   - 按 `AI (65%) + 人类 (35%)` 联合评分，自动生成根因诊断与下一代结构/超参进化指令 (`evolution_directives`)。

---

## 快速启动

### 1. 一键运行 3 代完整自进化闭环（含 ArXiv/GitHub 实时检索）
```bash
python3 run_rsi_loop.py --generations 3
```

### 2. 离线确定性模式运行（使用内置高置信论文与 `4gpu` / `unitree-d1` 种子库）
```bash
python3 run_rsi_loop.py --generations 3 --offline
```

### 3. 开启命令行人工交互打分模式
```bash
python3 run_rsi_loop.py --generations 3 --interactive-human
```

### 4. 运行自动化单元测试与闭环回归套件
```bash
python3 -m unittest discover -s tests -v
```

---

## 实测三代自进化效果对比（[`runs/EVOLUTION_REPORT.md`](runs/EVOLUTION_REPORT.md)）

| 代数 (Candidate) | 仿真轨迹 RMSE | 0.35kg 负载恢复误差 | 抓放成功 | Sim2Real 安全门禁 | 真机数字孪生 RMSE | AI 评分 (65%) | 人类评分 (35%) | 联合总分 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Gen-0** (`d1_policy_gen00`) | `19.43 mm` | `22.68 mm` | ✅ | ⛔ **BLOCKED** (>15mm) | Blocked (Gate) | `76.09` | `56.28` | **`69.16`** |
| **Gen-1** (`d1_policy_gen01`) | `3.09 mm` | `4.06 mm` | ✅ | ✅ **PASSED** | `6.56 mm` | `93.70` | `95.00` | **`94.16`** |
| **Gen-2** (`d1_policy_gen02`) | **`2.86 mm`** | **`3.61 mm`** | ✅ | ✅ **PASSED** | **`4.83 mm`** | **`94.56`** | **`95.00`** | **`94.71`** |
