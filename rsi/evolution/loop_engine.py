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
from rsi.scout.paper_github_scout import LiteratureAndCodeScout
from rsi.scout.skill_extractor import PaperCodeSkillExtractor
from rsi.sim.validator import SimulationValidator


class RSISelfEvolutionEngine:
    """
    “物理AI机器人自进化 V1.0” (Physical AI Robot Self-Evolution V1.0) 全闭环编排引擎
    形成完整的：【感知 — 决策 — 执行 — 评估 — 学习 — 进化】六阶段自进化飞轮。
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

        # Stage 3: Autonomous Controller Code Synthesizer
        self.synthesizer = ControllerCodeSynthesizer(self.manifest, self.runs_dir)

        # Stage 4: Physics Simulation Validator
        self.validator = SimulationValidator(self.manifest, self.workspace_root)

        # Stage 5: Unitree D1 Sim-to-Real Bridge & Safety Gate
        self.bridge = UnitreeD1Sim2RealBridge(self.manifest, self.runs_dir)

        # Stage 6: AI + Human Joint Evaluator
        self.evaluator = HumanAIJointEvaluator(
            self.manifest, human_scorer_callback=human_scorer_callback
        )

    def run_evolution_loop(
        self,
        num_generations: int = 3,
        human_approved_live_dds: bool = False,
        verbose: bool = True,
    ) -> Dict[str, Any]:
        if verbose:
            print("=" * 88)
            print("🚀 物理AI机器人自进化 V1.0 (RSI) — Unitree D1 闭环启动")
            print(f"📋 [阶段1 人类规则与约束] {self.manifest.summary_banner()}")
            print("=" * 88)

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
                f"\n   • 检索论文数: {len(papers)} 篇 (含 Eureka arXiv:2310.12931, DLS-IK arXiv:2402.15391, SmolVLA arXiv:2506.01844, Pi0.6-RECAP arXiv:2410.24164)"
                f"\n   • 检索代码库: {len(repos)} 个 (含 4gpu SmolVLA/OpenPI-RECAP 工程与 1F unitree-d1 固件库)"
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

            # Stage 3: Synthesize / Evolve Controller Code & Verify AST Safety
            candidate = self.synthesizer.synthesize_candidate(
                generation=gen,
                skill=executable_skill,
                parent_params=parent_params,
                prev_evaluation=prev_eval,
            )
            if verbose:
                print(
                    f"💻 [阶段3 AI自主编写控制代码] Candidate: {candidate.candidate_id}"
                    f"\n   • 进化策略: {candidate.mutation_rationale}"
                    f"\n   • 核心参数: kp={candidate.hyperparameters['kp_base']}, "
                    f"kd={candidate.hyperparameters['kd_base']}, "
                    f"kx={candidate.hyperparameters['task_space_gain_kx']}, "
                    f"dls_lambda={candidate.hyperparameters['dls_lambda']}, "
                    f"payload_gamma={candidate.hyperparameters['payload_adapt_gamma']}"
                    f"\n   • AST 安全静态审查: {'PASSED' if candidate.ast_safety_passed else 'FAILED'} ({len(candidate.ast_audit_notes)} checks)"
                )

            # Stage 4: Simulation Verification
            sim_result, traj_records = self.validator.validate_candidate(candidate)
            if verbose:
                print(
                    f"🌐 [阶段4 仿真环境训练验证] Backend: {sim_result.backend_used}"
                    f"\n   • 轨迹跟踪误差 (RMSE): {sim_result.tracking_rmse_m * 1000:.2f} mm "
                    f"(0.35kg 负载扰动段恢复误差: {sim_result.payload_disturbance_recovery_m * 1000:.2f} mm)"
                    f"\n   • 抗扰抓放闭环成功: {sim_result.grasp_success} (放置终点误差: {sim_result.place_error_m * 1000:.2f} mm)"
                    f"\n   • 最小桌面安全净空: {sim_result.min_table_clearance_m * 1000:.1f} mm (硬红线 >= 25.0 mm)"
                    f"\n   • Eureka 密集奖励得分: {sim_result.eureka_dense_reward:.2f} | "
                    f"Sim-to-Real 准入门禁: {'✅ PASSED' if sim_result.passed_sim2real_gate else '⛔ BLOCKED'}"
                )

            # Stage 5: Real Robot Deployment / Safety-Gated DDS Replay
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
                    else "N/A (Gate Blocked)"
                )
                print(
                    f"🤖 [阶段5 下发实体机器人真机实测] Mode: {real_result.deployment_mode}"
                    f"\n   • DDS 协议通道: {real_result.dds_command_topic} -> {real_result.total_dds_frames} 帧 "
                    f"| 真机数字孪生实测 RMSE: {real_rmse_str}"
                    f"\n   • 说明: {real_result.notes}"
                )

            # Stage 6: AI + Human Joint Evaluation & Feedback
            eval_report = self.evaluator.evaluate(
                candidate=candidate,
                sim_result=sim_result,
                real_result=real_result,
            )
            if verbose:
                print(
                    f"⚖️  [阶段6 AI与人类共同打分评估与反馈迭代]"
                    f"\n   • AI 评分 (权重 65%): {eval_report.ai_score:.2f} | "
                    f"人类评分 (权重 35%): {eval_report.human_score:.2f} "
                    f"👉 联合总分: {eval_report.combined_score:.2f} / 100"
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

            if best_record is None or eval_report.combined_score >= best_record.evaluation.combined_score:
                best_record = record
                parent_params = dict(candidate.hyperparameters)
            prev_eval = eval_report

        summary = self._save_evolution_artifacts(executable_skill, history, best_record)
        if verbose and best_record is not None:
            print("\n" + "=" * 88)
            print(
                f"🏆 自进化闭环完成！最优代数: Gen-{best_record.generation} ({best_record.candidate.candidate_id}) "
                f"| 联合总分: {best_record.evaluation.combined_score:.2f} "
                f"(较 Gen-0 提升 +{best_record.evaluation.combined_score - history[0].evaluation.combined_score:.2f} 分)"
            )
            print(
                f"   • 仿真 RMSE: {history[0].sim_result.tracking_rmse_m * 1000:.2f} mm "
                f"→ {best_record.sim_result.tracking_rmse_m * 1000:.2f} mm"
            )
            print(f"   • 进化总结账本: {summary['summary_json_path']}")
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

        payload = {
            "project": self.manifest.raw["project_name"],
            "robot": self.manifest.robot_spec["model"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "skill": skill.to_dict(),
            "best_generation": best_record.generation if best_record else None,
            "best_combined_score": best_record.evaluation.combined_score if best_record else None,
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
                else "Blocked (Gate)"
            )
            rows.append(
                f"| Gen-{r.generation} (`{r.candidate.candidate_id}`) "
                f"| {r.sim_result.tracking_rmse_m * 1000:.2f} mm "
                f"| {r.sim_result.payload_disturbance_recovery_m * 1000:.2f} mm "
                f"| {'✅' if r.sim_result.grasp_success else '❌'} "
                f"| {'✅ PASSED' if r.sim_result.passed_sim2real_gate else '⛔ BLOCKED'} "
                f"| {real_rmse} "
                f"| {r.evaluation.ai_score:.2f} "
                f"| {r.evaluation.human_score:.2f} "
                f"| **{r.evaluation.combined_score:.2f}** |"
            )

        table_str = "\n".join(rows)
        eq_lines = "\n".join(
            [
                f"- **{eq.name}** (`{eq.source_ref}`):\n  $${eq.latex_formula}$$\n  - 代码映射: `{eq.code_expression}`"
                for eq in skill.extracted_equations
            ]
        )

        md_content = f"""# 物理AI机器人自进化 V1.0 (Unitree D1) — 闭环运行报告

- **目标平台**: `{self.manifest.robot_spec['model']}`
- **集群联动**: `4gpu (10.10.218.4)` 训练资产 + `1F unitree-d1-1..3 (10.10.137.101..103)` CycloneDDS 协议
- **目标技能**: `{skill.name}` (`{skill.skill_id}`)
- **生成时间**: `{payload['timestamp']}`

## 1. 阶段 2 检索并转化为可执行 Skill 的核心数学公式

{eq_lines}

## 2. 跨代自进化闭环评测总表 (感知 → 决策 → 执行 → 评估 → 学习 → 进化)

| 代数 (Candidate) | 仿真轨迹 RMSE | 0.35kg 负载恢复误差 | 抓放成功 | Sim2Real 安全门禁 | 真机数字孪生 RMSE | AI 评分 (65%) | 人类评分 (35%) | 联合总分 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
{table_str}

## 3. 各代进化轨迹与人类/AI 联合诊断详情

"""
        for r in history:
            diag_bullets = "\n".join([f"  - {d}" for d in r.evaluation.ai_diagnosis])
            md_content += f"""### Generation {r.generation}: `{r.candidate.candidate_id}`
- **代码变异逻辑**: {r.candidate.mutation_rationale}
- **超参数快照**: `{json.dumps(r.candidate.hyperparameters)}`
- **控制器代码路径**: `{r.candidate.code_file_path}`
- **DDS 报文路径**: `{r.real_result.dds_packet_dump_path}`
- **AI 诊断详情**:
{diag_bullets}
- **人类专家评语**: {r.evaluation.human_comments}

"""

        report_md_path.write_text(md_content, encoding="utf-8")
        return {
            "summary_json_path": str(summary_path),
            "report_md_path": str(report_md_path),
            "best_generation": best_record.generation if best_record else 0,
            "best_score": best_record.evaluation.combined_score if best_record else 0.0,
            "history": history,
        }
