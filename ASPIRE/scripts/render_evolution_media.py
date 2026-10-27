#!/usr/bin/env python3
"""Render composite showcase image, side-by-side diff image, MP4 video, and animated GIF
for the ASPIRE + Unitree D1 x3 Closed-Loop Self-Evolution run.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
LOOP_DIR = REPO_ROOT / "outputs" / "evolution_loop"
RSI_RUNS_DIR = REPO_ROOT.parent / "runs"
ARTIFACT_DIR = Path("/Users/zhang/.gemini/antigravity/brain/47b96c95-11e6-4ab7-aea8-7536378282bc")


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
    return ImageFont.load_default()


def add_banner(img: Image.Image, title: str, subtitle: str, badge_color: tuple[int, int, int]) -> Image.Image:
    out = img.copy().convert("RGB")
    draw = ImageDraw.Draw(out)
    w, h = out.size
    bar_h = max(54, int(h * 0.11))
    draw.rectangle([(0, h - bar_h), (w, h)], fill=(15, 23, 42))
    draw.rectangle([(0, h - bar_h), (12, h)], fill=badge_color)
    f_title = load_font(max(16, int(bar_h * 0.38)))
    f_sub = load_font(max(13, int(bar_h * 0.28)))
    draw.text((22, h - bar_h + 6), title, fill=(255, 255, 255), font=f_title)
    draw.text((22, h - bar_h + int(bar_h * 0.52)), subtitle, fill=(148, 163, 184), font=f_sub)
    return out


def build_showcase_poster(report: dict) -> Path:
    iter1 = report["iterations"][0]
    iter2 = report["iterations"][1]

    img_base = Image.open(LOOP_DIR / "iter0_baseline.jpg").convert("RGB")
    img_it1 = Image.open(LOOP_DIR / "iter1_peak.jpg").convert("RGB")
    img_it2 = Image.open(LOOP_DIR / "iter2_peak.jpg").convert("RGB")
    img_home = Image.open(LOOP_DIR / "iter_final_home.jpg").convert("RGB")

    sam_it1_before = Image.open(LOOP_DIR / "iter1_sam3-000001-top_optical.png").convert("RGB")
    sam_it1_peak = Image.open(LOOP_DIR / "iter1_sam3-000002-top_optical.png").convert("RGB")
    sam_it2_before = Image.open(LOOP_DIR / "iter2_sam3-000001-top_optical.png").convert("RGB")
    sam_it2_peak = Image.open(LOOP_DIR / "iter2_sam3-000002-top_optical.png").convert("RGB")

    # Compute heat/diff overlay between Baseline and Iter 2 Peak
    arr_base = np.asarray(img_base, dtype=np.float32)
    arr_it2 = np.asarray(img_it2, dtype=np.float32)
    diff = np.mean(np.abs(arr_it2 - arr_base), axis=2)
    mask = (diff > 22.0).astype(np.float32)
    heat = arr_it2.copy()
    # Highlight moved pixels in bright cyan/green
    heat[:, :, 0] = np.clip(heat[:, :, 0] * (1.0 - 0.55 * mask), 0, 255)
    heat[:, :, 1] = np.clip(heat[:, :, 1] * (1.0 - 0.35 * mask) + 255.0 * 0.55 * mask, 0, 255)
    heat[:, :, 2] = np.clip(heat[:, :, 2] * (1.0 - 0.35 * mask) + 220.0 * 0.55 * mask, 0, 255)
    img_diff = Image.fromarray(heat.astype(np.uint8))
    draw_diff = ImageDraw.Draw(img_diff)
    rois = {
        "Left D1 (10.10.137.103)": (180, 340, 520, 790),
        "Center D1 (10.10.137.102)": (920, 310, 1240, 760),
        "Right D1 (10.10.137.101)": (1620, 290, 1960, 720),
    }
    f_roi = load_font(28)
    for label, (x1, y1, x2, y2) in rois.items():
        draw_diff.rectangle([(x1, y1), (x2, y2)], outline=(16, 185, 129), width=5)
        draw_diff.rectangle([(x1, y1 - 38), (x1 + 330, y1)], fill=(16, 185, 129))
        draw_diff.text((x1 + 8, y1 - 34), label, fill=(255, 255, 255), font=f_roi)

    diff_path = LOOP_DIR / "iter2_visual_displacement_diff.jpg"
    img_diff.save(diff_path, quality=92)

    # Tile dimensions for 2x4 grid + header
    tile_w, tile_h = 640, 360
    pad = 16
    header_h = 110
    canvas_w = 4 * tile_w + 5 * pad
    canvas_h = header_h + 2 * tile_h + 3 * pad

    canvas = Image.new("RGB", (canvas_w, canvas_h), (11, 15, 25))
    draw = ImageDraw.Draw(canvas)
    f_h1 = load_font(34)
    f_h2 = load_font(20)

    draw.text(
        (pad, 18),
        "物理AI机器人自进化 V1.0 — NVIDIA ASPIRE × Unitree D1 (3台真机 + SAM3 视觉闭环实测)",
        fill=(248, 250, 252),
        font=f_h1,
    )
    draw.text(
        (pad, 64),
        (
            f"感知(TP-LINK RTSP + SAM3:8114) → 决策(Code-as-Policies生成) → 执行(UnitreeD1Bus UDP 101/102/103) "
            f"→ 评估/进化: Iter1 总分 {iter1['total_score']} → Iter2 总分 {iter2['total_score']} (PASS >= 90.0)"
        ),
        fill=(56, 189, 248),
        font=f_h2,
    )

    row1_tiles = [
        add_banner(
            img_base.resize((tile_w, tile_h), Image.Resampling.LANCZOS),
            "Stage 0: 初始折叠归零态 (Baseline)",
            "J0=0°, J1=-89.8°, J2=+89.5°, Gripper=0° (闭合)",
            (148, 163, 184),
        ),
        add_banner(
            img_it1.resize((tile_w, tile_h), Image.Resampling.LANCZOS),
            f"Iter 1: v1_conservative_lift (Score {iter1['total_score']}/100)",
            f"Yaw=4.4°, Lift=15.6°, Grip=0° | ROI变化率={iter1['visual_evaluation']['mean_changed_pixel_ratio']*100:.1f}% (未达标->触发自进化)",
            (245, 158, 11),
        ),
        add_banner(
            img_it2.resize((tile_w, tile_h), Image.Resampling.LANCZOS),
            f"Iter 2: v2_moderate_yaw_and_reach (Score {iter2['total_score']}/100 PASS)",
            f"Yaw=12.5°, Lift=29.1°, Grip=49.8°(张开) | ROI变化率={iter2['visual_evaluation']['mean_changed_pixel_ratio']*100:.1f}%",
            (16, 185, 129),
        ),
        add_banner(
            img_diff.resize((tile_w, tile_h), Image.Resampling.LANCZOS),
            "Stage 4 视觉位移差分热力框 (Iter 2 vs Baseline)",
            "3台 Unitree D1 同步外摆+前伸+夹爪全开 + 结束后安全归零",
            (59, 130, 246),
        ),
    ]

    row2_tiles = [
        add_banner(
            sam_it1_before.resize((tile_w, tile_h), Image.Resampling.LANCZOS),
            "SAM3 感知 #1: Iter 1 运动前分割 (segment_text 'robotic arm')",
            "GPU 8114 实时推理分割 3 台桌面机械臂掩码与 Bounding Box",
            (139, 92, 246),
        ),
        add_banner(
            sam_it1_peak.resize((tile_w, tile_h), Image.Resampling.LANCZOS),
            "SAM3 感知 #2: Iter 1 峰值位姿分割验证",
            "检测到 3 台机械臂小幅抬升，评估器判定工作空间展开不足 (24.0分)",
            (245, 158, 11),
        ),
        add_banner(
            sam_it2_peak.resize((tile_w, tile_h), Image.Resampling.LANCZOS),
            "SAM3 感知 #3: Iter 2 进化策略峰值位姿分割 (PASS)",
            "3 台机械臂大幅外摆前伸 (#2 0.789, #3 0.715, #4 0.270) + 夹爪张开",
            (16, 185, 129),
        ),
        add_banner(
            img_home.resize((tile_w, tile_h), Image.Resampling.LANCZOS),
            "Final Stage: 闭环收敛后自动安全归零 (Safe Homing)",
            "J0=0.4°, J1=-89.8°, J2=+88.8°, Gripper=0.1° (平滑归位完成)",
            (20, 184, 166),
        ),
    ]

    for idx, tile in enumerate(row1_tiles):
        x = pad + idx * (tile_w + pad)
        y = header_h + pad
        canvas.paste(tile, (x, y))

    for idx, tile in enumerate(row2_tiles):
        x = pad + idx * (tile_w + pad)
        y = header_h + 2 * pad + tile_h
        canvas.paste(tile, (x, y))

    poster_path = LOOP_DIR / "aspire_d1_evolution_showcase.png"
    canvas.save(poster_path, optimize=True)
    return poster_path


def build_combined_videos() -> tuple[Path, Path]:
    """Concatenate iter1_video.mp4, iter2_video.mp4, and homing_video.mp4 into a single MP4 and GIF."""
    concat_list = LOOP_DIR / "concat_videos.txt"
    videos = [
        LOOP_DIR / "iter1_video.mp4",
        LOOP_DIR / "iter2_video.mp4",
        LOOP_DIR / "homing_video.mp4",
    ]
    existing = [v for v in videos if v.exists()]
    concat_list.write_text("\n".join(f"file '{v.name}'" for v in existing) + "\n", encoding="utf-8")

    out_mp4 = LOOP_DIR / "aspire_d1_self_evolution_demo.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(out_mp4),
        ],
        cwd=str(LOOP_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )

    # Also build a speedup overview GIF (fps=10, 4x speedup so the full 3-stage physical motion plays in ~8s)
    out_gif = LOOP_DIR / "aspire_d1_self_evolution_demo.gif"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(out_mp4),
            "-vf",
            "setpts=0.18*PTS,fps=10,scale=640:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer",
            "-loop",
            "0",
            str(out_gif),
        ],
        cwd=str(LOOP_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    return out_mp4, out_gif


def main() -> None:
    report = json.loads((LOOP_DIR / "evolution_report.json").read_text(encoding="utf-8"))
    poster_path = build_showcase_poster(report)
    out_mp4, out_gif = build_combined_videos()

    # Copy key media files to both /Users/zhang/Documents/RSI/runs/ and ARTIFACT_DIR
    RSI_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    files_to_export = [
        poster_path,
        LOOP_DIR / "iter0_baseline.jpg",
        LOOP_DIR / "iter1_peak.jpg",
        LOOP_DIR / "iter2_peak.jpg",
        LOOP_DIR / "iter2_visual_displacement_diff.jpg",
        LOOP_DIR / "iter1_sam3-000001-top_optical.png",
        LOOP_DIR / "iter1_sam3-000002-top_optical.png",
        LOOP_DIR / "iter2_sam3-000001-top_optical.png",
        LOOP_DIR / "iter2_sam3-000002-top_optical.png",
        LOOP_DIR / "iter_final_home.jpg",
        out_mp4,
        out_gif,
        LOOP_DIR / "iter1_video.mp4",
        LOOP_DIR / "iter2_video.mp4",
        LOOP_DIR / "homing_video.mp4",
        LOOP_DIR / "evolution_report.json",
    ]
    for src in files_to_export:
        if src.exists():
            shutil.copy2(src, RSI_RUNS_DIR / src.name)
            shutil.copy2(src, ARTIFACT_DIR / src.name)

    print(f"Poster: {poster_path}")
    print(f"Combined MP4: {out_mp4}")
    print(f"Animated GIF: {out_gif}")


if __name__ == "__main__":
    main()
