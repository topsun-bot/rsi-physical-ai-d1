from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class PaperSource:
    """ArXiv 论文检索结果与关键信息"""
    arxiv_id: str
    title: str
    authors: List[str]
    published: str
    summary: str
    url: str
    extracted_concepts: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RepoSource:
    """GitHub 开源仓库检索结果与关键结构"""
    full_name: str
    html_url: str
    description: str
    stars: int
    language: str
    topics: List[str] = field(default_factory=list)
    architectural_primitives: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExtractedEquation:
    """从论文/代码中抽取的数学公式及其对应的代码变量映射"""
    name: str
    latex_formula: str
    physical_meaning: str
    code_expression: str
    source_ref: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutableSkill:
    """阶段2产出：从论文公式与开源仓库结构转化而来的可执行机器人技能 (Skill)"""
    skill_id: str
    name: str
    target_robot: str
    created_at: str
    source_papers: List[PaperSource]
    source_repos: List[RepoSource]
    extracted_equations: List[ExtractedEquation]
    code_architecture: Dict[str, Any]
    hyperparameter_space: Dict[str, List[float]]
    default_hyperparameters: Dict[str, float]
    skill_module_path: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ControlPolicyCandidate:
    """阶段3产出：AI 自主编写/进化出的控制器与奖励函数候选代码"""
    generation: int
    candidate_id: str
    skill_id: str
    hyperparameters: Dict[str, float]
    source_code: str
    code_file_path: str
    ast_safety_passed: bool
    ast_audit_notes: List[str]
    mutation_rationale: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SimVerificationResult:
    """阶段4产出：仿真环境训练与多工况验证指标"""
    generation: int
    candidate_id: str
    backend_used: str
    sim_duration_sec: float
    control_steps: int
    tracking_rmse_m: float
    max_tracking_error_m: float
    payload_disturbance_recovery_m: float
    grasp_success: bool
    place_error_m: float
    mean_torque_norm_nm: float
    mean_torque_rate_nm_s: float
    max_joint_torque_ratio: float
    min_table_clearance_m: float
    safety_violations: List[str]
    eureka_dense_reward: float
    passed_sim2real_gate: bool
    trajectory_log_path: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RealDeploymentResult:
    """阶段5产出：下发 Unitree D1 实体机械臂真机实测 / DDS 安全回放验证记录"""
    generation: int
    candidate_id: str
    deployment_mode: str  # "blocked_by_gate" | "dry_run_dds_replay" | "live_cyclonedds"
    gate_approved: bool
    dds_command_topic: str
    dds_feedback_topic: str
    total_dds_frames: int
    safety_clamped_frames: int
    emergency_stop_triggered: bool
    real_tracking_rmse_m: Optional[float]
    sample_dds_commands: List[Dict[str, Any]]
    dds_packet_dump_path: str
    notes: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class JointEvaluationReport:
    """阶段6产出：AI 与人类共同打分评估及下一代进化反馈指令"""
    generation: int
    candidate_id: str
    ai_score: float
    human_score: float
    combined_score: float
    sub_scores: Dict[str, float]
    ai_diagnosis: List[str]
    human_comments: str
    evolution_directives: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GenerationRecord:
    """单代完整闭环账本记录 (Perception -> Decision -> Execution -> Evaluation -> Learning -> Evolution)"""
    generation: int
    candidate: ControlPolicyCandidate
    sim_result: SimVerificationResult
    real_result: RealDeploymentResult
    evaluation: JointEvaluationReport

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
