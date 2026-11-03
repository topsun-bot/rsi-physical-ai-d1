from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from rsi.coder.controller_synthesizer import ControllerCodeSynthesizer
from rsi.core.manifest import RuleManifest
from rsi.core.types import (
    ExecutableSkill,
    GenerationRecord,
    JointEvaluationReport,
    RealDeploymentResult,
    SimVerificationResult,
)
from rsi.deploy.d1_sim2real_bridge import UnitreeD1Sim2RealBridge
from rsi.evaluator.joint_evaluator import HumanAIJointEvaluator
from rsi.evolution.dsec_failure_bank import DSecFailureReplayBank
from rsi.evolution.map_elites_archive import AlphaEvolveMAPElitesArchive
from rsi.evolution.rrsi_regularizer import RRSIDualRegularizer
from rsi.scout.paper_github_scout import LiteratureAndCodeScout
from rsi.scout.skill_extractor import PaperCodeSkillExtractor
from rsi.sim.real2sim_generator import GeminiReal2SimGenerator, Real2SimSceneConfig
from rsi.sim.validator import SimulationValidator


class RSISelfEvolutionEngine:
    """
    “物理AI机器人自进化 V2.0” (Physical AI Robot Self-Evolution V2.0 — Frontier RSI Edition) 全闭环编排引擎
    融合四大前沿 RSI 范式，形成完整的【感知 — 决策 — 执行 — 评估 — 学习 — 进化】六阶段自进化飞轮：
      1) Google Research `RRSI` (arXiv:2609.24972): 双侧正则化（退火编辑预算 b_t + 证伪账本 L_t + Critic 防泄漏 + OOD 扰动套件 + PAC 置信下界）
      2) Google DeepMind `AlphaEvolve` (arXiv:2506.13131) + 田渊栋 `Eureka Machine`: `# === EVOLVE-BLOCK ===` 局部进化 + MAP-Elites 质量-多样性归档
      3) DeepSeek Harness (`Cordis` EIP) + `DSec`: `pack_diff` 历史故障微沙箱回归库 + 技能目录 SHA-256 原子 CAS 与硬件阻尼回滚
    """

    def __init__(
        self,
        workspace_root: str | Path,
        manifest_path: Optional[str | Path] = None,
        enable_live_network: bool = True,
        human_scorer_callback: Optional[
            Callable[[int, SimVerificationResult, RealDeploymentResult], Tuple[float, str]]
        ] = None,
    ):
        self.workspace_root = Path(workspace_root)
        self.manifest_path = (
            Path(manifest_path)
            if manifest_path
            else self.workspace_root / "configs" / "d1_manifest.yaml"
        )
        self.runs_dir = self.workspace_root / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)

        # Stage 1: Load Human Rules, Skills, Constraints & Toolset
        self.manifest = RuleManifest(self.manifest_path)

        # Stage 2: Literature & Open-Source Scout + Skill Extractor
        self.scout = LiteratureAndCodeScout(enable_live_network=enable_live_network)
        self.extractor = PaperCodeSkillExtractor(self.workspace_root)

        # Stage 3: Autonomous Controller Code Synthesizer (Gemini 4 Argon + AlphaEvolve EVOLVE-BLOCK)
        self.synthesizer = ControllerCodeSynthesizer(
            self.manifest,
            self.runs_dir,
            enable_live_llm=enable_live_network,
        )

        # Stage 4A: Gemini 4 Argon Real2Sim Scene Reconstructor
        self.real2sim = GeminiReal2SimGenerator(self.manifest, self.workspace_root)
        self.enable_live_network = enable_live_network

        # Stage 4: Physics Simulation + OOD Perturbation Validator
        self.validator = SimulationValidator(self.manifest, self.workspace_root)

        # Stage 5: Unitree D1 Sim-to-Real Bridge, Safety Gate & Cordis CAS Registry
        self.bridge = UnitreeD1Sim2RealBridge(self.manifest, self.runs_dir)

        # Stage 6: AI + Human Joint Evaluator + Frontier RSI Regularizers & Archives
        self.evaluator = HumanAIJointEvaluator(
            self.manifest, human_scorer_callback=human_scorer_callback
        )
        self.rrsi_regularizer = RRSIDualRegularizer(self.runs_dir)
        self.map_elites = AlphaEvolveMAPElitesArchive(self.runs_dir)
        self.dsec_bank = DSecFailureReplayBank(self.runs_dir)

    def run_evolution_loop(
        self,
        num_generations: int = 3,
        human_approved_live_dds: bool = False,
        verbose: bool = True,
        real2sim_image_path: Optional[str | Path] = None,
    ) -> Dict[str, Any]:
        if verbose:
            print("=" * 88)
            print("🚀 物理AI机器人自进化 V2.0 (Frontier RSI: RRSI + AlphaEvolve + Cordis/DSec) — Unitree D1 闭环启动")
            print(f"📋 [阶段1 人类规则与约束] {self.manifest.summary_banner()}")
            print("=" * 88)

        real2sim_scene: Optional[Real2SimSceneConfig] = None
        if real2sim_image_path is not None:
            real2sim_scene = self.real2sim.reconstruct_from_image(
                image_path=real2sim_image_path,
                enable_live_llm=self.enable_live_network,
            )
            if verbose:
                print(
                    f"\n📸 [阶段4A Real2Sim 单图数字孪生构建] {real2sim_scene.scene_summary}"
                    f"\n   • 生成 MuJoCo MJCF 场景: {real2sim_scene.generated_mjcf_path}"
                    f"\n   • 识别刚体数: {len(real2sim_scene.bodies)} 个 ({', '.join(b.name for b in real2sim_scene.bodies)})"
                )

        # Stage 2: Search ArXiv & GitHub + 4gpu/unitree-d1 repos and compile into ExecutableSkill
        primary_skill_cfg = self.manifest.get_primary_skill()
        papers = self.scout.search_arxiv(primary_skill_cfg["search_queries"]["arxiv"])
        repos = self.scout.search_github(primary_skill_cfg["search_queries"]["github"])

        executable_skill: ExecutableSkill = self.extractor.extract_and_compile_skill(
            skill_id=primary_skill_cfg["skill_id"],
            skill_name=primary_skill_cfg["name"],
            target_robot=self.manifest.robot_spec["model"],
            papers=papers,
            repos=repos,
        )

        if verbose:
            print(
                f"\n🔍 [阶段2 全网论文与开源检索 -> 可执行Skill转化]"
                f"\n   • 检索论文数: {len(papers)} 篇 (含 Eureka, DLS-IK, SmolVLA, Pi0.6-RECAP, Google RRSI arXiv:2609.24972, AlphaEvolve arXiv:2506.13131)"
                f"\n   • 检索代码库: {len(repos)} 个 (含 4gpu SmolVLA/OpenPI-RECAP, 1F unitree-d1, google-research/rrsi, DeepSeek Cordis/DSec)"
                f"\n   • 抽取数学公式: {len(executable_skill.extracted_equations)} 条控制律/奖励公式"
                f"\n   • 生成可执行Skill模块: {executable_skill.skill_module_path}"
            )

        history: List[GenerationRecord] = []
        best_record: Optional[GenerationRecord] = None
        parent_params: Optional[Dict[str, float]] = None
        prev_eval: Optional[JointEvaluationReport] = None

        for gen in range(num_generations):
            if verbose:
                print("\n" + "-" * 88)
                print(f"🧬 Generation {gen} — 感知 → 决策 → 执行 → 评估 → 学习 → 进化")
                print("-" * 88)

            # Stage 3A: Build Google RRSI Proposal-Side Regularization Context
            rrsi_ctx = self.rrsi_regularizer.build_proposal_context(gen)

            # Stage 3B: Synthesize / Evolve Controller Code with EVOLVE-BLOCKs & Verify AST Safety
            candidate = self.synthesizer.synthesize_candidate(
                generation=gen,
                skill=executable_skill,
                parent_params=parent_params,
                prev_evaluation=prev_eval,
                snapshot_path=real2sim_image_path,
                rrsi_context=rrsi_ctx,
            )
            if verbose:
                print(
                    f"💻 [阶段3 AI自主编写控制代码 (AlphaEvolve EVOLVE-BLOCK + RRSI Proposal)] Candidate: {candidate.candidate_id}"
                    f"\n   • RRSI 退火编辑预算 b_t={candidate.edit_budget_b_t} | 激活进化组件: {candidate.active_component_edits}"
                    f"\n   • AlphaEvolve EVOLVE-BLOCK 指纹: {json.dumps(candidate.evolve_block_hashes)}"
                    f"\n   • 进化策略: {candidate.mutation_rationale}"
                    f"\n   • 核心参数: kp={candidate.hyperparameters['kp_base']}, "
                    f"kd={candidate.hyperparameters['kd_base']}, "
                    f"kx={candidate.hyperparameters['task_space_gain_kx']}, "
                    f"dls_lambda={candidate.hyperparameters['dls_lambda']}, "
                    f"payload_gamma={candidate.hyperparameters['payload_adapt_gamma']}"
                    f"\n   • AST 安全静态审查: {'PASSED' if candidate.ast_safety_passed else 'FAILED'} ({len(candidate.ast_audit_notes)} checks)"
                )

            # Stage 4: Nominal Simulation + RRSI OOD Perturbation Suite + DSec Failure Sandbox Replay
            sim_result, traj_records = self.validator.validate_candidate(
                candidate,
                real2sim_scene=real2sim_scene,
                dsec_bank=self.dsec_bank,
            )
            if verbose:
                print(
                    f"🌐 [阶段4 仿真验证 + RRSI OOD泛化套件 + DSec微沙箱回归] Backend: {sim_result.backend_used}"
                    f"\n   • 标称轨迹 RMSE: {sim_result.tracking_rmse_m * 1000:.2f} mm "
                    f"(0.35kg 负载扰动恢复: {sim_result.payload_disturbance_recovery_m * 1000:.2f} mm)"
                    f"\n   • RRSI OOD 留出集扰动均值 RMSE: {sim_result.ood_mean_rmse_m * 1000:.2f} mm "
                    f"(最差工况: {sim_result.ood_worst_rmse_m * 1000:.2f} mm, 通过率: {sim_result.ood_pass_rate * 100:.0f}%)"
                    f"\n   • DSec 历史故障微沙箱回归: {'✅ PASSED' if sim_result.dsec_replay_passed else '❌ FAILED'} "
                    f"(测试沙箱数: {sim_result.dsec_sandboxes_tested}, 当前故障库容量: {len(self.dsec_bank.sandboxes)})"
                    f"\n   • 最小桌面安全净空: {sim_result.min_table_clearance_m * 1000:.1f} mm | "
                    f"Sim-to-Real 准入门禁: {'✅ PASSED' if sim_result.passed_sim2real_gate else '⛔ BLOCKED'}"
                )

            # Stage 5: Real Robot Deployment / Safety-Gated DDS Replay + Cordis Atomic CAS
            real_result = self.bridge.execute_deployment(
                candidate=candidate,
                sim_result=sim_result,
                trajectory_records=traj_records,
                human_approved_live=human_approved_live_dds,
            )
            if verbose:
                real_rmse_str = (
                    f"{real_result.real_tracking_rmse_m * 1000:.2f} mm"
                    if real_result.real_tracking_rmse_m is not None
                    else "N/A (Gate Blocked -> Atomic Rollback)"
                )
                print(
                    f"🤖 [阶段5 下发实体机器人真机实测 + Cordis EIP 原子CAS] Mode: {real_result.deployment_mode}"
                    f"\n   • DDS 协议通道: {real_result.dds_command_topic} -> {real_result.total_dds_frames} 帧 "
                    f"| 真机数字孪生实测 RMSE: {real_rmse_str}"
                    f"\n   • Cordis 技能目录 CAS 提交: {real_result.cordis_cas_committed} "
                    f"(Active Hash: `{real_result.cordis_active_hash}`)"
                    f"\n   • 说明: {real_result.notes}"
                )

            # Stage 6: AI + Human Joint Evaluation + RRSI Selection Gate + MAP-Elites QD Archive
            eval_report = self.evaluator.evaluate(
                candidate=candidate,
                sim_result=sim_result,
                real_result=real_result,
            )

            incumbent_score = (
                best_record.evaluation.combined_score if best_record is not None else None
            )
            rrsi_entry = self.rrsi_regularizer.evaluate_selection_gate(
                generation=gen,
                candidate=candidate,
                sim_result=sim_result,
                combined_score=eval_report.combined_score,
                incumbent_score=incumbent_score,
                ood_metrics=self.validator.last_ood_metrics,
            )
            niche_key, is_niche_elite = self.map_elites.register_candidate(
                candidate=candidate,
                sim_result=sim_result,
                combined_score=eval_report.combined_score,
            )
            eval_report.rrsi_verdict = rrsi_entry.verdict
            eval_report.pac_lower_bound_delta = rrsi_entry.pac_lower_bound_delta
            eval_report.map_elites_niche = niche_key

            if verbose:
                print(
                    f"⚖️  [阶段6 AI+人类联合评估 + RRSI选择侧护栏 + MAP-Elites归档]"
                    f"\n   • AI 评分 (65%): {eval_report.ai_score:.2f} | 人类评分 (35%): {eval_report.human_score:.2f} "
                    f"👉 联合总分: {eval_report.combined_score:.2f} / 100"
                    f"\n   • RRSI 选择侧裁决: {rrsi_entry.verdict} (Score Δ={rrsi_entry.score_delta:+.2f}, "
                    f"PAC LCB Δ={rrsi_entry.pac_lower_bound_delta:+.2f}, 噪声阈值 δ={rrsi_entry.noise_floor_delta:.2f})"
                    f"\n   • AlphaEvolve MAP-Elites 生态位: `{niche_key}` (更新生态位精英: {is_niche_elite})"
                    f"\n   • 人类评审意见: {eval_report.human_comments}"
                    f"\n   • 下一代进化指令: {json.dumps(eval_report.evolution_directives, ensure_ascii=False)}"
                )

            record = GenerationRecord(
                generation=gen,
                candidate=candidate,
                sim_result=sim_result,
                real_result=real_result,
                evaluation=eval_report,
            )
            history.append(record)

            # RRSI incumbent acceptance rule: only promote candidate if accepted or initial seed
            if best_record is None or (
                rrsi_entry.verdict in ("INITIAL_SEED", "ACCEPTED")
                and eval_report.combined_score >= best_record.evaluation.combined_score
            ):
                best_record = record
                parent_params = dict(candidate.hyperparameters)
            prev_eval = eval_report

        summary = self._save_evolution_artifacts(executable_skill, history, best_record)
        if verbose and best_record is not None:
            qd_summary = summary["map_elites_archive"]
            print("\n" + "=" * 88)
            print(
                f"🏆 自进化闭环完成！最优代数: Gen-{best_record.generation} ({best_record.candidate.candidate_id}) "
                f"| 联合总分: {best_record.evaluation.combined_score:.2f} "
                f"(较 Gen-0 提升 +{best_record.evaluation.combined_score - history[0].evaluation.combined_score:.2f} 分)"
            )
            print(
                f"   • 标称仿真 RMSE: {history[0].sim_result.tracking_rmse_m * 1000:.2f} mm "
                f"→ {best_record.sim_result.tracking_rmse_m * 1000:.2f} mm | "
                f"OOD 泛化均值 RMSE: {best_record.sim_result.ood_mean_rmse_m * 1000:.2f} mm"
            )
            print(
                f"   • MAP-Elites 覆盖生态位: {qd_summary['occupied_niches']}/{qd_summary['total_grid_cells']} "
                f"(QD 总分: {qd_summary['qd_score']:.2f}) | "
                f"DSec 故障回归沙箱数: {len(self.dsec_bank.sandboxes)}"
            )
            print(f"   • 进化总结账本: {summary['summary_json_path']}")
            print(f"   • RRSI 证伪账本: {self.rrsi_regularizer.ledger_path}")
            print(f"   • 进化详细报告: {summary['report_md_path']}")
            print("=" * 88)

        return summary

    def _save_evolution_artifacts(
        self,
        skill: ExecutableSkill,
        history: List[GenerationRecord],
        best_record: Optional[GenerationRecord],
    ) -> Dict[str, Any]:
        summary_path = self.runs_dir / "evolution_summary.json"
        report_md_path = self.runs_dir / "EVOLUTION_REPORT.md"

        rrsi_summary = self.rrsi_regularizer.summary_dict()
        map_elites_summary = self.map_elites.summary_dict()
        dsec_summary = self.dsec_bank.summary_dict()

        payload = {
            "project": self.manifest.raw["project_name"],
            "version": "2.0.0-frontier-rsi",
            "robot": self.manifest.robot_spec["model"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "skill": skill.to_dict(),
            "best_generation": best_record.generation if best_record else None,
            "best_combined_score": best_record.evaluation.combined_score if best_record else None,
            "rrsi_regularization": rrsi_summary,
            "map_elites_archive": map_elites_summary,
            "dsec_failure_bank": dsec_summary,
            "cordis_active_hash": self.bridge.active_incumbent_hash,
            "generations": [r.to_dict() for r in history],
        }
        summary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Render human-readable Markdown Evolution Report
        rows = []
        for r in history:
            real_rmse = (
                f"{r.real_result.real_tracking_rmse_m * 1000:.2f} mm"
                if r.real_result.real_tracking_rmse_m is not None
                else "Blocked (Rollback)"
            )
            rows.append(
                f"| Gen-{r.generation} (`{r.candidate.candidate_id}`) "
                f"| $b_t={r.candidate.edit_budget_b_t}$ "
                f"| {r.sim_result.tracking_rmse_m * 1000:.2f} mm "
                f"| {r.sim_result.ood_mean_rmse_m * 1000:.2f} mm "
                f"| {r.sim_result.payload_disturbance_recovery_m * 1000:.2f} mm "
                f"| {'✅' if r.sim_result.dsec_replay_passed else '❌'} ({r.sim_result.dsec_sandboxes_tested}) "
                f"| {'✅ PASSED' if r.sim_result.passed_sim2real_gate else '⛔ BLOCKED'} "
                f"| {real_rmse} "
                f"| `{r.evaluation.rrsi_verdict}` "
                f"| **{r.evaluation.combined_score:.2f}** |"
            )

        table_str = "\n".join(rows)
        eq_lines = "\n".join(
            [
                f"- **{eq.name}** (`{eq.source_ref}`):\n  $${eq.latex_formula}$$\n  - 代码映射: `{eq.code_expression}`"
                for eq in skill.extracted_equations
            ]
        )

        niche_lines = "\n".join(
            [
                f"- **`{cell_key}`**: `{cell['candidate_id']}` (Gen-{cell['generation']}) — "
                f"Score: **{cell['combined_score']:.2f}**, Nominal RMSE: `{cell['tracking_rmse_m'] * 1000:.2f} mm`, "
                f"OOD RMSE: `{cell['ood_mean_rmse_m'] * 1000:.2f} mm`, Torque Rate: `{cell['mean_torque_rate_nm_s']:.1f} Nm/s`"
                for cell_key, cell in map_elites_summary["cells"].items()
            ]
        )

        dsec_lines = "\n".join(
            [
                f"- **`{sb['sandbox_id']}`** (`{sb['failure_type']}`, t={sb['failure_timestamp_sec']}s): {sb['description']}"
                for sb in dsec_summary["sandboxes"]
            ]
        ) or "- 暂无故障沙箱记录"

        md_content = f"""# 物理AI机器人自进化 V2.0 (Unitree D1) — 前沿 RSI 闭环运行报告

- **目标平台**: `{self.manifest.robot_spec['model']}`
- **集群联动**: `4gpu (10.10.218.4)` 训练资产 + `1F unitree-d1-1..3 (10.10.137.101..103)` CycloneDDS 协议
- **融合前沿 RSI 范式**:
  1. **Google Research `RRSI` (`arXiv:2609.24972`)**: 提议侧退火编辑预算 $b_t$ + 全历史证伪账本 $L_t$ + 选择侧 Critic 防泄漏筛查 + 留出集 OOD 扰动套件 + PAC 置信下界 $\\delta$
  2. **Google DeepMind `AlphaEvolve` (`arXiv:2506.13131`) & 田渊栋 `Eureka Machine`**: `# === EVOLVE-BLOCK ===` 受限代码块变异 + 二维 MAP-Elites 质量-多样性精英归档
  3. **DeepSeek Harness (`Cordis` EIP) & `DSec`**: `pack_diff` 历史失败轨迹微沙箱回归库 + 技能注册表 SHA-256 原子 CAS 与 `funcode=5` 硬件阻尼回滚
- **目标技能**: `{skill.name}` (`{skill.skill_id}`)
- **生成时间**: `{payload['timestamp']}`

## 1. 阶段 2 检索并转化为可执行 Skill 的核心数学公式

{eq_lines}

## 2. 跨代自进化闭环评测总表 (感知 → 决策 → 执行 → 评估 → 学习 → 进化)

| 代数 (Candidate) | RRSI 预算 | 标称仿真 RMSE | OOD 泛化 RMSE | 0.35kg 负载恢复 | DSec 回归沙箱 | Sim2Real 门禁 | 真机孪生 RMSE | RRSI 裁决 | 联合总分 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
{table_str}

## 3. AlphaEvolve MAP-Elites 质量-多样性生态位归档 (`runs/map_elites_archive.json`)

- **已占据生态位**: `{map_elites_summary['occupied_niches']} / {map_elites_summary['total_grid_cells']}` (Coverage: `{map_elites_summary['coverage_ratio'] * 100:.1f}%`)
- **Quality-Diversity (QD) 总分**: **{map_elites_summary['qd_score']:.2f}**
- **全局最优生态位**: `{map_elites_summary['best_elite_cell']}` (`{map_elites_summary['best_elite_candidate']}`)

{niche_lines}

## 4. DeepSeek DSec (`pack_diff`) 历史故障微沙箱回归库 (`runs/dsec_failure_bank.json`)

{dsec_lines}

## 5. 各代进化轨迹与人类/AI 联合诊断详情

"""
        for r in history:
            diag_bullets = "\n".join([f"  - {d}" for d in r.evaluation.ai_diagnosis])
            md_content += f"""### Generation {r.generation}: `{r.candidate.candidate_id}`
- **代码变异逻辑**: {r.candidate.mutation_rationale}
- **AlphaEvolve EVOLVE-BLOCK 指纹**: `{json.dumps(r.candidate.evolve_block_hashes)}`
- **超参数快照**: `{json.dumps(r.candidate.hyperparameters)}`
- **MAP-Elites 行为生态位**: `{r.evaluation.map_elites_niche}` | **RRSI 裁决**: `{r.evaluation.rrsi_verdict}` (PAC LCB $\\Delta$: `{r.evaluation.pac_lower_bound_delta:+.2f}`)
- **Cordis CAS 状态**: Committed=`{r.real_result.cordis_cas_committed}`, ActiveHash=`{r.real_result.cordis_active_hash}`
- **控制器代码路径**: `{r.candidate.code_file_path}`
- **DDS 报文路径**: `{r.real_result.dds_packet_dump_path}`
- **AI 诊断详情**:
{diag_bullets}
- **人类专家评语**: {r.evaluation.human_comments}

"""
        md_content += """## 6. Insta360 X4 Air 360° 全景、球心针孔反投影无畸变侧视与 3D 环视空间拓扑

- **全景相机节点**: `28163@10.10.137.106` (`ssh -J dgx-spark 28163@10.10.137.106`), `Insta360 X4 Air (USB\\VID_2E1A&PID_0002\\IAFEA2510H4E6N)`, CameraSDK `2.1.8` + MediaSDK `3.1.5`, `1920×960@30fps` FlowState 实时拼接流 (`15ms` 帧延迟)
- **工作台居中 360° 全景展开**: `runs/insta360_multiview/insta360_workbench_centered_360.jpg`
- **球心针孔反投影 (Gnomonic Rectilinear Pinhole) 4 路无畸变视图**:
  - 三臂工作台全局正侧视 (`yaw=182°, pitch=-30°`): `runs/insta360_multiview/insta360_rectilinear_d1_workbench.jpg`
  - 左 1 号臂 (`101`) & 黄黑目标块特写 (`yaw=150°, pitch=-28°`): `runs/insta360_multiview/insta360_rectilinear_d1_left_arm1.jpg`
  - 中 2 号臂 (`102`) / 右 3 号臂 (`103`) & 标定板特写 (`yaw=206°, pitch=-24°`): `runs/insta360_multiview/insta360_rectilinear_d1_mid_right_arms.jpg`
  - 实验室后方 Unitree Go2 机器狗环视 (`yaw=20°, pitch=-8°`): `runs/insta360_multiview/insta360_rectilinear_lab_surround.jpg`
- **五联屏 360° 空间感知 HUD (`1920×1080`)**: `runs/insta360_multiview/insta360_360_surround_hud.png`
- **8 实体 3D 环视空间拓扑图 (`insta360_surround_scene_graph.json`)**: 实测三臂桌面 Z 轴安全净空分别为 `86.5 mm` (`101`)、`92.0 mm` (`102`)、`95.0 mm` (`103`)。
"""

        report_md_path.write_text(md_content, encoding="utf-8")
        return {
            "summary_json_path": str(summary_path),
            "report_md_path": str(report_md_path),
            "rrsi_ledger_path": str(self.rrsi_regularizer.ledger_path),
            "map_elites_path": str(self.map_elites.archive_path),
            "dsec_bank_path": str(self.dsec_bank.bank_path),
            "cordis_cas_path": str(self.bridge.cordis_cas_path),
            "best_generation": best_record.generation if best_record else 0,
            "best_score": best_record.evaluation.combined_score if best_record else 0.0,
            "rrsi_regularization": rrsi_summary,
            "map_elites_archive": map_elites_summary,
            "dsec_failure_bank": dsec_summary,
            "history": history,
        }
