#!/usr/bin/env python3
"""
物理AI机器人自进化 V1.0 (RSI) — 一键运行全闭环 CLI 主入口
Usage:
  python3 run_rsi_loop.py --generations 3
  python3 run_rsi_loop.py --generations 3 --offline
  python3 run_rsi_loop.py --generations 3 --interactive-human
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Tuple

from rsi.core.types import RealDeploymentResult, SimVerificationResult
from rsi.evolution.loop_engine import RSISelfEvolutionEngine


def interactive_human_scorer(
    generation: int,
    sim_result: SimVerificationResult,
    real_result: RealDeploymentResult,
) -> Tuple[float, str]:
    print(f"\n[人类评审交互输入 Gen-{generation}]")
    print(
        f"  当前指标: 仿真RMSE={sim_result.tracking_rmse_m * 1000:.2f}mm, "
        f"抓放成功={sim_result.grasp_success}, "
        f"真机门禁={'通过' if real_result.gate_approved else '拦截'}"
    )
    raw_score = input("  请输入人类工程师评分 (0-100, 直接回车采用默认专家评分): ").strip()
    raw_comment = input("  请输入人类工程师反馈意见 (直接回车采用默认评语): ").strip()
    score = float(raw_score) if raw_score else (94.0 if real_result.gate_approved else 60.0)
    comment = raw_comment or f"人工交互确认评分: {score:.1f}"
    return score, comment


def main() -> None:
    parser = argparse.ArgumentParser(
        description="物理AI机器人自进化 V1.0 (Unitree D1) 全闭环运行器"
    )
    parser.add_argument(
        "--generations",
        type=int,
        default=3,
        help="自进化闭环迭代代数 (默认: 3)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="使用本地高置信论文/开源库种子，跳过实时外网 HTTP 请求",
    )
    parser.add_argument(
        "--interactive-human",
        action="store_true",
        help="开启命令行人工交互打分模式",
    )
    args = parser.parse_args()

    workspace_root = Path(__file__).resolve().parent
    engine = RSISelfEvolutionEngine(
        workspace_root=workspace_root,
        enable_live_network=not args.offline,
        human_scorer_callback=interactive_human_scorer if args.interactive_human else None,
    )
    engine.run_evolution_loop(
        num_generations=args.generations,
        human_approved_live_dds=False,
        verbose=True,
    )


if __name__ == "__main__":
    main()
