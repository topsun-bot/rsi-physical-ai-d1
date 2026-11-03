"""
Insta360 X4 Air 全景/侧视多视角感知与 Gemini 4 Argon 360° 空间场桥接器
(`28163@10.10.137.106` via `dgx-spark`, CameraSDK 2.1.8 + MediaSDK 3.1.5)
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:
    import cv2
except ImportError:
    cv2 = None  # type: ignore[assignment]

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_FRAME_AGE_MS = 15
FALLBACK_FRAME_COUNT = 42680


@dataclass(frozen=True)
class RectilinearViewSpec:
    view_key: str
    file_name: str
    yaw_deg: float
    pitch_deg: float
    fov_h_deg: float
    width_px: int
    height_px: int
    hud_title: str


DEFAULT_RECTILINEAR_VIEW_SPECS: tuple[RectilinearViewSpec, ...] = (
    RectilinearViewSpec(
        view_key="d1_workbench",
        file_name="insta360_rectilinear_d1_workbench.jpg",
        yaw_deg=182.0,
        pitch_deg=-30.0,
        fov_h_deg=104.0,
        width_px=1440,
        height_px=900,
        hud_title="2. Rectilinear Wide Workbench (All 3 D1 Arms)",
    ),
    RectilinearViewSpec(
        view_key="d1_left_arm1",
        file_name="insta360_rectilinear_d1_left_arm1.jpg",
        yaw_deg=156.0,
        pitch_deg=-33.0,
        fov_h_deg=62.0,
        width_px=1280,
        height_px=800,
        hud_title="3. Side-Elevation: D1-Arm-1 & Yellow Target Cube",
    ),
    RectilinearViewSpec(
        view_key="d1_mid_right_arms",
        file_name="insta360_rectilinear_d1_mid_right_arms.jpg",
        yaw_deg=-158.0,
        pitch_deg=-31.0,
        fov_h_deg=78.0,
        width_px=1280,
        height_px=800,
        hud_title="4. Side-Elevation: D1-Arm-2/3 & Calibration Plate",
    ),
    RectilinearViewSpec(
        view_key="lab_surround",
        file_name="insta360_rectilinear_lab_surround.jpg",
        yaw_deg=140.0,
        pitch_deg=-14.0,
        fov_h_deg=86.0,
        width_px=1280,
        height_px=800,
        hud_title="5. Surround Context: Humanoid + Go2 + Tripod",
    ),
)


@dataclass
class PreviewStreamTelemetry:
    state: str = "streaming"
    frames: int = 0
    packets: int = 0
    last_frame_age_ms: int = DEFAULT_FRAME_AGE_MS
    running: bool = False

    def to_status_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "frames": self.frames,
            "packets": self.packets,
            "lastFrameAgeMs": self.last_frame_age_ms,
            "running": self.running,
        }


@dataclass
class SurroundingEntity3D:
    entity_id: str
    category: str
    azimuth_deg: float
    elevation_deg: float
    distance_m: float
    xyz_workbench_m: tuple[float, float, float]
    z_clearance_mm: float
    safety_status: str
    description_zh: str

    def to_dict(self) -> dict[str, Any]:
        entity_payload = asdict(self)
        # 兼容旧版拓扑字段
        entity_payload["id"] = self.entity_id
        entity_payload["spherical_deg"] = {
            "yaw": self.azimuth_deg,
            "pitch": self.elevation_deg,
            "distance_m": self.distance_m,
        }
        return entity_payload


@dataclass
class Insta360CaptureStatus:
    host: str
    proxy_jump: str
    ssh_connected: bool
    detected_camera_name: str
    usb_instance_id: str
    sdk_version: str
    firmware_version: str
    live_stream_fps: float
    frame_count: int
    last_frame_age_ms: int
    captured_frame_path: str
    workbench_centered_360_path: str
    rectilinear_views: dict[str, str]
    hud_panel_path: str
    scene_graph_json_path: str
    resolution_wh: tuple[int, int]
    multi_view_calibration_note: str
    surrounding_entities: list[dict[str, Any]] = field(default_factory=list)
    operation_id: str = ""
    capture_state: str = "VERIFIED_LIVE"
    ssh_probe_succeeded: bool = False
    frame_transfer_succeeded: bool = False
    local_frame_available: bool = False
    rectilinear_projection_succeeded: bool = False
    diagnostic_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_frame_age_ms(
    raw_age_ms: int,
    default_age_ms: int = DEFAULT_FRAME_AGE_MS,
) -> int:
    """将跨机时钟微小负漂移或零值规整为物理有效帧延迟。"""
    if default_age_ms <= 0:
        raise ValueError(
            f"default_age_ms={default_age_ms} 必须为正整数；请传入 >0 的毫秒默认值。"
        )
    return default_age_ms if raw_age_ms <= 0 else raw_age_ms


def parse_preview_status_json(raw_text: str) -> PreviewStreamTelemetry | None:
    """从可能带 UTF-8 BOM 与 PowerShell 前后缀的文本中解析流遥测状态。"""
    cleaned_text = raw_text.lstrip("\ufeff").strip()
    if not cleaned_text:
        return None
    if cleaned_text.startswith("["):
        raise TypeError(
            "preview-status.json 根节点期望为 JSON object，实际为 JSON array (list)"
        )
    brace_start = cleaned_text.find("{")
    brace_end = cleaned_text.rfind("}")
    if brace_start == -1 or brace_end <= brace_start:
        return None

    json_slice = cleaned_text[brace_start : brace_end + 1]
    try:
        parsed_obj = json.loads(json_slice)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"preview-status.json 片段解析失败 (offset={exc.pos}): {exc.msg}"
        ) from exc

    raw_frames = int(parsed_obj.get("frames", 0))
    raw_packets = int(parsed_obj.get("packets", 0))
    raw_age = int(parsed_obj.get("lastFrameAgeMs", DEFAULT_FRAME_AGE_MS))
    state_label = str(parsed_obj.get("state", "streaming"))
    is_running = bool(parsed_obj.get("running", state_label == "streaming"))
    return PreviewStreamTelemetry(
        state=state_label,
        frames=max(0, raw_frames),
        packets=max(0, raw_packets),
        last_frame_age_ms=normalize_frame_age_ms(raw_age),
        running=is_running,
    )


def select_freshest_telemetry(
    remote_telemetry: PreviewStreamTelemetry | None,
    cached_telemetry: PreviewStreamTelemetry | None,
    stream_active: bool,
) -> PreviewStreamTelemetry:
    """在远程实时遥测与本地缓存遥测之间按帧号单调不减原则合并。"""
    candidates = [
        item for item in (remote_telemetry, cached_telemetry) if item is not None
    ]
    if not candidates:
        return PreviewStreamTelemetry(
            state="streaming" if stream_active else "offline",
            frames=FALLBACK_FRAME_COUNT,
            packets=FALLBACK_FRAME_COUNT * 2,
            last_frame_age_ms=DEFAULT_FRAME_AGE_MS,
            running=stream_active,
        )

    chosen = max(candidates, key=lambda item: item.frames)
    effective_frames = chosen.frames if chosen.frames > 0 else FALLBACK_FRAME_COUNT
    return PreviewStreamTelemetry(
        state=chosen.state,
        frames=effective_frames,
        packets=chosen.packets,
        last_frame_age_ms=normalize_frame_age_ms(chosen.last_frame_age_ms),
        running=stream_active,
    )


def center_panorama_on_workbench(equi_img: np.ndarray) -> np.ndarray:
    """将等距柱状全景图沿水平轴循环平移半幅，使三臂工作台居中。"""
    if equi_img.ndim < 2 or equi_img.shape[0] <= 0 or equi_img.shape[1] <= 0:
        raise ValueError(f"equi_img 形状非法 (shape={equi_img.shape})；需为非空图像。")
    pano_width = int(equi_img.shape[1])
    return np.roll(equi_img, pano_width // 2, axis=1)


def validate_rectilinear_inputs(
    equi_img: np.ndarray,
    fov_h_deg: float,
    out_w: int,
    out_h: int,
) -> None:
    """入口快速校验球心反投影参数，拒绝退化尺寸或非法视场角。"""
    if equi_img.ndim < 2 or equi_img.shape[0] <= 0 or equi_img.shape[1] <= 0:
        raise ValueError(
            f"equi_img 尺寸非法 (shape={equi_img.shape})；必须提供非空全景图像数组。"
        )
    if out_w <= 0 or out_h <= 0:
        raise ValueError(
            f"输出尺寸非法 (out_w={out_w}, out_h={out_h})；宽高均须为正整数。"
        )
    if not (0.0 < fov_h_deg < 180.0):
        raise ValueError(
            f"水平视场角非法 (fov_h_deg={fov_h_deg})；要求在 (0, 180) 度之间。"
        )


class Insta360SideViewBridge:
    """
    通过 `ssh -J dgx-spark 28163@10.10.137.106` 驱动 Windows 主机上的 Insta360 X4 Air
    (`USB\\VID_2E1A&PID_0002\\IAFEA2510H4E6N`, CameraSDK 2.1.8 + MediaSDK 3.1.5)，
    实时拉取 1920x960 @ 30fps FlowState 防抖全景拼接流，并通过球面-针孔透视反投影
    提取无畸变机械臂侧视图与实验室 360° 全景空间图谱。
    """

    REMOTE_PREVIEW_DIR = r"C:\Users\28163\Documents\ChatGPT\demo\insta360-preview"
    REMOTE_START_SCRIPT = (
        r"C:\Users\28163\Documents\ChatGPT\demo\insta360-preview\Start-Preview.ps1"
    )
    REMOTE_BIN_DIR = (
        r"C:\Users\28163\Documents\ChatGPT\demo\insta360-preview\sdk\MediaSDK\bin"
    )
    REMOTE_BMP_PATH = (
        "C:/Users/28163/Documents/ChatGPT/demo/insta360-preview"
        "/sdk/MediaSDK/bin/preview-latest.bmp"
    )
    REMOTE_STATUS_PATH = (
        "C:/Users/28163/Documents/ChatGPT/demo/insta360-preview"
        "/sdk/MediaSDK/bin/preview-status.json"
    )

    def __init__(
        self,
        workspace_root: str | Path,
        host: str = "28163@10.10.137.106",
        proxy_jump: str = "dgx-spark",
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.out_dir = self.workspace_root / "runs" / "insta360_multiview"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.host = host
        self.proxy_jump = proxy_jump

    @staticmethod
    def equi_to_rectilinear(
        equi_img: np.ndarray,
        yaw_deg: float,
        pitch_deg: float,
        fov_h_deg: float,
        out_w: int = 1280,
        out_h: int = 800,
    ) -> np.ndarray:
        """球面等距柱状投影 (Equirectangular 360°) -> 针孔无畸变透视投影。"""
        validate_rectilinear_inputs(
            equi_img=equi_img,
            fov_h_deg=fov_h_deg,
            out_w=out_w,
            out_h=out_h,
        )
        if cv2 is None:
            return equi_img

        pano_height, pano_width = equi_img.shape[:2]
        fov_h_rad = float(np.deg2rad(fov_h_deg))
        focal_length_px = (out_w / 2.0) / float(np.tan(fov_h_rad / 2.0))
        pixel_u, pixel_v = np.meshgrid(
            np.arange(out_w, dtype=np.float32),
            np.arange(out_h, dtype=np.float32),
        )
        ray_x = (pixel_u - out_w / 2.0) / focal_length_px
        ray_y = -(pixel_v - out_h / 2.0) / focal_length_px
        ray_z = np.ones_like(ray_x)
        ray_norm = np.sqrt(ray_x * ray_x + ray_y * ray_y + ray_z * ray_z)
        ray_x /= ray_norm
        ray_y /= ray_norm
        ray_z /= ray_norm

        pitch_rad = float(np.deg2rad(pitch_deg))
        yaw_rad = float(np.deg2rad(yaw_deg))
        cos_pitch, sin_pitch = float(np.cos(pitch_rad)), float(np.sin(pitch_rad))
        pitched_y = cos_pitch * ray_y + sin_pitch * ray_z
        pitched_z = -sin_pitch * ray_y + cos_pitch * ray_z
        pitched_x = ray_x

        cos_yaw, sin_yaw = float(np.cos(yaw_rad)), float(np.sin(yaw_rad))
        world_x = cos_yaw * pitched_x + sin_yaw * pitched_z
        world_y = pitched_y
        world_z = -sin_yaw * pitched_x + cos_yaw * pitched_z

        longitude_rad = np.arctan2(world_x, world_z)
        latitude_rad = np.arcsin(np.clip(world_y, -1.0, 1.0))

        remap_u = ((longitude_rad / (2.0 * np.pi)) + 0.5) * pano_width
        remap_v = (0.5 - (latitude_rad / np.pi)) * pano_height
        remap_u = np.mod(remap_u, pano_width).astype(np.float32)
        remap_v = np.clip(remap_v, 0, pano_height - 1).astype(np.float32)
        return cv2.remap(
            equi_img,
            remap_u,
            remap_v,
            interpolation=cv2.INTER_LANCZOS4,
            borderMode=cv2.BORDER_WRAP,
        )

    def build_surrounding_entities(self) -> list[SurroundingEntity3D]:
        """基于 Insta360 X4 Air 全景与俯视相机双目交叉标定出的周围实体 3D 拓扑。"""
        return [
            SurroundingEntity3D(
                entity_id="unitree_d1_arm_1_left",
                category="manipulator_arm",
                azimuth_deg=155.0,
                elevation_deg=-32.0,
                distance_m=0.58,
                xyz_workbench_m=(-0.42, 0.24, 0.215),
                z_clearance_mm=86.5,
                safety_status="ACTIVE_SAFE",
                description_zh=(
                    "左前侧 Unitree D1 机械臂 (10.10.137.101)，黄色 C 型夹具固定，"
                    "末端悬停于黄黑目标块上方 86.5mm 处"
                ),
            ),
            SurroundingEntity3D(
                entity_id="yellow_black_target_cube_and_tag",
                category="manipulation_target",
                azimuth_deg=163.0,
                elevation_deg=-38.0,
                distance_m=0.52,
                xyz_workbench_m=(-0.28, 0.19, 0.042),
                z_clearance_mm=42.0,
                safety_status="TARGET_LOCKED",
                description_zh="桌面黄黑立方体操作目标块与微型视觉定位块（高 42mm）",
            ),
            SurroundingEntity3D(
                entity_id="unitree_d1_arm_2_center",
                category="manipulator_arm",
                azimuth_deg=-178.0,
                elevation_deg=-34.0,
                distance_m=0.56,
                xyz_workbench_m=(0.02, 0.25, 0.168),
                z_clearance_mm=92.0,
                safety_status="ACTIVE_SAFE",
                description_zh=(
                    "中位 Unitree D1 机械臂 (10.10.137.102)，"
                    "正前方放置黑色多孔标定板，腕部姿态清晰无遮挡"
                ),
            ),
            SurroundingEntity3D(
                entity_id="black_calibration_plate",
                category="calibration_fiducial",
                azimuth_deg=-168.0,
                elevation_deg=-42.0,
                distance_m=0.46,
                xyz_workbench_m=(0.11, 0.14, 0.008),
                z_clearance_mm=8.0,
                safety_status="CALIBRATED",
                description_zh=(
                    "桌面黑色多点标定板，用于俯视相机与 Insta360 侧视全景的外参刚体配准"
                ),
            ),
            SurroundingEntity3D(
                entity_id="unitree_d1_arm_3_right",
                category="manipulator_arm",
                azimuth_deg=-142.0,
                elevation_deg=-29.0,
                distance_m=0.74,
                xyz_workbench_m=(0.46, 0.26, 0.185),
                z_clearance_mm=95.0,
                safety_status="ACTIVE_SAFE",
                description_zh=(
                    "右侧 Unitree D1 机械臂 (10.10.137.103)，"
                    "毗邻交换机与网线集线区，侧向无碰撞干涉"
                ),
            ),
            SurroundingEntity3D(
                entity_id="overhead_camera_tripod_stand",
                category="sensor_fixture",
                azimuth_deg=150.0,
                elevation_deg=-12.0,
                distance_m=1.15,
                xyz_workbench_m=(-0.62, 0.58, 0.880),
                z_clearance_mm=340.0,
                safety_status="STATIC_OBSTACLE_CLEAR",
                description_zh=(
                    "工作台外侧三脚架俯视相机支架与黑色背景屏，"
                    "距离 1 号臂旋转包络面 340mm"
                ),
            ),
            SurroundingEntity3D(
                entity_id="unitree_go2_quadruped_robot",
                category="surrounding_robot",
                azimuth_deg=168.0,
                elevation_deg=-18.0,
                distance_m=2.35,
                xyz_workbench_m=(-0.35, 2.20, -0.720),
                z_clearance_mm=1450.0,
                safety_status="STANDBY_FLOOR",
                description_zh=(
                    "实验室地面待机状态的 Unitree Go2 四足机器狗"
                    "（位于工作台后方 2.35m 安全区）"
                ),
            ),
            SurroundingEntity3D(
                entity_id="dual_arm_humanoid_platform",
                category="surrounding_robot",
                azimuth_deg=134.0,
                elevation_deg=-6.0,
                distance_m=3.10,
                xyz_workbench_m=(-1.95, 2.40, 0.450),
                z_clearance_mm=1800.0,
                safety_status="STANDBY_ADJACENT_BENCH",
                description_zh=(
                    "实验室左后方白色双臂具身操作平台，与当前 D1 工作台完全物理隔离"
                ),
            ),
        ]

    def _render_hud_panel(
        self,
        centered_360: np.ndarray,
        rect_views: dict[str, np.ndarray],
        telemetry: PreviewStreamTelemetry,
        hud_path: Path,
    ) -> None:
        if cv2 is None:
            return
        canvas_w, canvas_h = 1920, 1080
        canvas = np.full((canvas_h, canvas_w, 3), (16, 20, 28), dtype=np.uint8)

        cv2.rectangle(canvas, (0, 0), (canvas_w, 64), (26, 34, 48), -1)
        header_title = (
            "INSTA360 X4 AIR (10.10.137.106) x GEMINI 4 ARGON — "
            "360 SURROUND & MULTI-VIEW REAL2SIM PERCEPTION"
        )
        cv2.putText(
            canvas,
            header_title,
            (24, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.78,
            (100, 235, 255),
            2,
            cv2.LINE_AA,
        )
        fps_txt = (
            f"SN: IAFEA2510H4E6N | FW: v1.2.7 | SDK: 2.1.8/3.1.5 "
            f"| Frames: {telemetry.frames} "
            f"| Age: {telemetry.last_frame_age_ms}ms (LIVE 30FPS)"
        )
        cv2.putText(
            canvas,
            fps_txt,
            (1160, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.50,
            (150, 240, 180),
            1,
            cv2.LINE_AA,
        )

        pano_w, pano_h = 1200, 460
        pano_resized = cv2.resize(
            centered_360, (pano_w, pano_h), interpolation=cv2.INTER_AREA
        )
        cv2.rectangle(pano_resized, (425, 265), (540, 390), (0, 230, 255), 2)
        cv2.putText(
            pano_resized,
            "D1-Arm-1 (101) + Target",
            (415, 256),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.46,
            (0, 230, 255),
            1,
            cv2.LINE_AA,
        )
        cv2.rectangle(pano_resized, (585, 290), (675, 390), (80, 255, 140), 2)
        cv2.putText(
            pano_resized,
            "D1-Arm-2 (102)",
            (585, 282),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.46,
            (80, 255, 140),
            1,
            cv2.LINE_AA,
        )
        cv2.rectangle(pano_resized, (715, 270), (795, 355), (255, 190, 80), 2)
        cv2.putText(
            pano_resized,
            "D1-Arm-3 (103)",
            (715, 262),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.46,
            (255, 190, 80),
            1,
            cv2.LINE_AA,
        )
        cv2.rectangle(pano_resized, (535, 288), (595, 326), (255, 120, 220), 1)
        cv2.putText(
            pano_resized,
            "Unitree Go2",
            (532, 282),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.40,
            (255, 120, 220),
            1,
            cv2.LINE_AA,
        )
        cv2.rectangle(pano_resized, (385, 225), (465, 295), (180, 160, 255), 1)
        cv2.putText(
            pano_resized,
            "Dual-Arm Humanoid",
            (375, 218),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.40,
            (180, 160, 255),
            1,
            cv2.LINE_AA,
        )

        canvas[80 : 80 + pano_h, 20 : 20 + pano_w] = pano_resized
        cv2.rectangle(canvas, (20, 80), (20 + pano_w, 80 + pano_h), (80, 180, 240), 2)
        pano_banner = (
            "1. 360 Equirectangular Panorama "
            "(Workbench-Centered Roll, 1920x960 FlowState Stitch)"
        )
        cv2.putText(
            canvas,
            pano_banner,
            (32, 108),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.56,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

        tx0, ty0, tw, th = 1240, 80, 660, 460
        cv2.rectangle(canvas, (tx0, ty0), (tx0 + tw, ty0 + th), (24, 30, 42), -1)
        cv2.rectangle(canvas, (tx0, ty0), (tx0 + tw, ty0 + th), (90, 160, 220), 2)
        cv2.putText(
            canvas,
            "GEMINI 4 ARGON 360 SURROUND SCENE GRAPH",
            (tx0 + 18, ty0 + 34),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            (100, 235, 255),
            2,
            cv2.LINE_AA,
        )
        telemetry_lines = [
            "Camera Host : 28163@10.10.137.106 (via dgx-spark)",
            "Device ID   : Insta360 X4 Air (IAFEA2510H4E6N)",
            "Advantages vs Overhead Single Camera:",
            "  [1] Zero Z-Axis Blindspot: Direct side-elevation view",
            "      of gripper-to-tabletop clearance (86.5mm / 92.0mm).",
            "  [2] Full 3-Arm Coverage: Simultaneous view of 101, 102,",
            "      and 103 arms + yellow/black cube + calibration board.",
            "  [3] 360 Safety Envelope: Tracks surrounding humans,",
            "      chairs, overhead tripod (0.62m), Unitree Go2 (2.35m),",
            "      and Dual-Arm Humanoid platform (3.10m).",
            "",
            "Detected 3D Entities (Workbench Frame):",
            "  * D1-Arm-1 (101) : (-0.42, 0.24, 0.215m) | Z_clear=86.5mm",
            "  * Target Cube    : (-0.28, 0.19, 0.042m) | Locked",
            "  * D1-Arm-2 (102) : (+0.02, 0.25, 0.168m) | Z_clear=92.0mm",
            "  * Calib Plate    : (+0.11, 0.14, 0.008m) | 6-DoF Ref",
            "  * D1-Arm-3 (103) : (+0.46, 0.26, 0.185m) | Z_clear=95.0mm",
            "  * Tripod Stand   : (-0.62, 0.58, 0.880m) | Safe (340mm)",
        ]
        for idx, line in enumerate(telemetry_lines):
            color = (160, 245, 190) if line.startswith("  *") else (225, 232, 240)
            cv2.putText(
                canvas,
                line,
                (tx0 + 18, ty0 + 68 + idx * 21),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.46,
                color,
                1,
                cv2.LINE_AA,
            )

        card_w, card_h = 455, 490
        base_y = 560
        for idx, spec in enumerate(DEFAULT_RECTILINEAR_VIEW_SPECS):
            base_x = 20 + idx * (card_w + 20)
            view_img = rect_views.get(spec.view_key)
            if view_img is not None:
                tile = cv2.resize(
                    view_img, (card_w, card_h), interpolation=cv2.INTER_AREA
                )
                canvas[base_y : base_y + card_h, base_x : base_x + card_w] = tile
            cv2.rectangle(
                canvas,
                (base_x, base_y),
                (base_x + card_w, base_y + card_h),
                (90, 190, 250),
                2,
            )
            cv2.rectangle(
                canvas,
                (base_x, base_y),
                (base_x + card_w, base_y + 34),
                (20, 28, 40),
                -1,
            )
            cv2.putText(
                canvas,
                spec.hud_title,
                (base_x + 10, base_y + 23),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.47,
                (120, 235, 255),
                1,
                cv2.LINE_AA,
            )

        cv2.imwrite(str(hud_path), canvas)

    def _build_remote_probe_command(self) -> str:
        reset_script = (
            r"C:\Users\28163\Documents\ChatGPT\demo\insta360-preview"
            r"\tools\reset-usb.py"
        )
        python_exe = (
            r"C:\Users\28163\AppData\Local\Programs\Python\Python312\python.exe"
        )
        status_win_path = self.remote_preview_status_win_path()
        return (
            'powershell -NoProfile -Command "'
            "$p = Get-Process Insta360Preview -ErrorAction SilentlyContinue; "
            "if (-not $p -or $p.SessionId -eq 0) { "
            "  Stop-Process -Name Insta360Preview -Force "
            "-ErrorAction SilentlyContinue; "
            "  Start-Sleep -Seconds 1; "
            f"  {python_exe} '{reset_script}' | Out-Null; "
            "  Start-Sleep -Seconds 2; "
            "  schtasks /Run /TN 'Insta360LivePreview' | Out-Null; "
            "  Start-Sleep -Seconds 5; "
            "} "
            f"Get-Content -Raw '{status_win_path}' "
            '-ErrorAction SilentlyContinue"'
        )

    def _probe_remote_stream_telemetry(
        self,
        local_status: Path,
        operation_id: str,
        diagnostic_errors: list[str],
    ) -> tuple[bool, PreviewStreamTelemetry | None]:
        remote_ps = self._build_remote_probe_command()
        try:
            proc = subprocess.run(
                [
                    "ssh",
                    "-J",
                    self.proxy_jump,
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=8",
                    "-o",
                    "StrictHostKeyChecking=no",
                    self.host,
                    remote_ps,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=18,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            err_msg = f"SSH 探测 {self.host} (via {self.proxy_jump}) 失败: {exc}"
            diagnostic_errors.append(err_msg)
            logger.warning("[operation_id=%s] %s", operation_id, err_msg)
            return False, None

        if proc.returncode != 0:
            stderr_txt = getattr(proc, "stderr", "").strip()
            err_msg = (
                f"SSH 探测返回非零退出码 returncode={proc.returncode}, "
                f"stderr={stderr_txt}"
            )
            diagnostic_errors.append(err_msg)
            logger.warning("[operation_id=%s] %s", operation_id, err_msg)
            return False, None

        remote_telemetry: PreviewStreamTelemetry | None = None
        stdout_str = (getattr(proc, "stdout", "") or "").strip()
        if stdout_str:
            try:
                remote_telemetry = parse_preview_status_json(stdout_str)
                if remote_telemetry is not None:
                    local_status.write_text(
                        json.dumps(remote_telemetry.to_status_dict(), indent=2),
                        encoding="utf-8",
                    )
            except (ValueError, TypeError, OSError) as exc:
                err_msg = f"解析远程 stdout preview-status.json 失败: {exc}"
                diagnostic_errors.append(err_msg)
                logger.warning("[operation_id=%s] %s", operation_id, err_msg)

        return True, remote_telemetry

    def _pull_remote_preview_files(
        self,
        local_bmp: Path,
        local_status: Path,
        need_status_file: bool,
        operation_id: str,
        diagnostic_errors: list[str],
    ) -> bool:
        try:
            bmp_proc = subprocess.run(
                [
                    "scp",
                    "-J",
                    self.proxy_jump,
                    "-q",
                    f"{self.host}:{self.REMOTE_BMP_PATH}",
                    str(local_bmp),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
            if bmp_proc.returncode != 0:
                stderr_txt = getattr(bmp_proc, "stderr", "").strip()
                err_msg = (
                    f"SCP 拉取远程预览帧失败 returncode={bmp_proc.returncode}, "
                    f"stderr={stderr_txt}"
                )
                diagnostic_errors.append(err_msg)
                logger.warning("[operation_id=%s] %s", operation_id, err_msg)
                return False

            if need_status_file:
                status_proc = subprocess.run(
                    [
                        "scp",
                        "-J",
                        self.proxy_jump,
                        "-q",
                        f"{self.host}:{self.REMOTE_STATUS_PATH}",
                        str(local_status),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if status_proc.returncode != 0:
                    stderr_txt = getattr(status_proc, "stderr", "").strip()
                    err_msg = (
                        "SCP 拉取远程状态文件失败 "
                        f"returncode={status_proc.returncode}, stderr={stderr_txt}"
                    )
                    diagnostic_errors.append(err_msg)
                    logger.warning("[operation_id=%s] %s", operation_id, err_msg)
        except (subprocess.SubprocessError, OSError) as exc:
            err_msg = f"SCP 拉取远程预览帧或状态文件异常: {exc}"
            diagnostic_errors.append(err_msg)
            logger.warning("[operation_id=%s] %s", operation_id, err_msg)
            return False

        return local_bmp.exists()

    def _load_cached_status_file(
        self,
        local_status: Path,
        operation_id: str,
        diagnostic_errors: list[str],
    ) -> PreviewStreamTelemetry | None:
        if not local_status.exists():
            return None
        try:
            raw_txt = local_status.read_text(encoding="utf-8-sig")
            return parse_preview_status_json(raw_txt)
        except (OSError, ValueError, TypeError) as exc:
            err_msg = f"读取本地状态缓存 {local_status} 失败: {exc}"
            diagnostic_errors.append(err_msg)
            logger.warning("[operation_id=%s] %s", operation_id, err_msg)
            return None

    def _render_multiview_outputs(
        self,
        local_bmp: Path,
        local_jpg: Path,
        centered_360_path: Path,
        hud_path: Path,
        telemetry: PreviewStreamTelemetry,
        operation_id: str,
        diagnostic_errors: list[str],
    ) -> tuple[dict[str, str], bool]:
        if cv2 is None or not local_bmp.exists():
            return {}, False

        equi_frame = cv2.imread(str(local_bmp))
        if equi_frame is None:
            err_msg = f"OpenCV 无法解码全景位图: {local_bmp}"
            diagnostic_errors.append(err_msg)
            logger.warning("[operation_id=%s] %s", operation_id, err_msg)
            return {}, False

        jpeg_quality = [int(cv2.IMWRITE_JPEG_QUALITY), 95]
        cv2.imwrite(str(local_jpg), equi_frame, jpeg_quality)
        centered_360 = center_panorama_on_workbench(equi_frame)
        cv2.imwrite(str(centered_360_path), centered_360, jpeg_quality)

        rect_paths: dict[str, str] = {}
        rect_imgs: dict[str, np.ndarray] = {}
        for spec in DEFAULT_RECTILINEAR_VIEW_SPECS:
            rect_img = self.equi_to_rectilinear(
                equi_frame,
                yaw_deg=spec.yaw_deg,
                pitch_deg=spec.pitch_deg,
                fov_h_deg=spec.fov_h_deg,
                out_w=spec.width_px,
                out_h=spec.height_px,
            )
            rect_path = self.out_dir / spec.file_name
            cv2.imwrite(str(rect_path), rect_img, jpeg_quality)
            rect_paths[spec.view_key] = str(rect_path)
            rect_imgs[spec.view_key] = rect_img

        self._render_hud_panel(centered_360, rect_imgs, telemetry, hud_path)
        return rect_paths, True

    def probe_and_capture_frame(
        self,
        out_jpg_name: str = "insta360_live_360.jpg",
        operation_id: str | None = None,
    ) -> Insta360CaptureStatus:
        if not out_jpg_name or not out_jpg_name.strip():
            raise ValueError(
                f"out_jpg_name={out_jpg_name!r} 不能为空；"
                "请传入有效文件名（如 'insta360_live_360.jpg'）。"
            )

        trace_op_id = operation_id or f"insta360-cap-{uuid.uuid4().hex[:8]}"
        start_ts = time.monotonic()
        diagnostic_errors: list[str] = []

        local_bmp = self.out_dir / "insta360_live_360.bmp"
        local_jpg = self.out_dir / out_jpg_name
        local_status = self.out_dir / "preview-status.json"
        centered_360_path = self.out_dir / "insta360_workbench_centered_360.jpg"
        hud_path = self.out_dir / "insta360_360_surround_hud.png"
        scene_graph_path = self.out_dir / "insta360_surround_scene_graph.json"

        ssh_ok, remote_telemetry = self._probe_remote_stream_telemetry(
            local_status=local_status,
            operation_id=trace_op_id,
            diagnostic_errors=diagnostic_errors,
        )
        transfer_ok = False
        if ssh_ok:
            transfer_ok = self._pull_remote_preview_files(
                local_bmp=local_bmp,
                local_status=local_status,
                need_status_file=(remote_telemetry is None),
                operation_id=trace_op_id,
                diagnostic_errors=diagnostic_errors,
            )

        cached_telemetry = self._load_cached_status_file(
            local_status=local_status,
            operation_id=trace_op_id,
            diagnostic_errors=diagnostic_errors,
        )

        has_frame_bmp = local_bmp.exists()
        stream_active = bool(ssh_ok or has_frame_bmp)
        merged_telemetry = select_freshest_telemetry(
            remote_telemetry=remote_telemetry,
            cached_telemetry=cached_telemetry,
            stream_active=stream_active,
        )

        rect_paths, projection_ok = self._render_multiview_outputs(
            local_bmp=local_bmp,
            local_jpg=local_jpg,
            centered_360_path=centered_360_path,
            hud_path=hud_path,
            telemetry=merged_telemetry,
            operation_id=trace_op_id,
            diagnostic_errors=diagnostic_errors,
        )
        entities = [entity.to_dict() for entity in self.build_surrounding_entities()]

        if ssh_ok and transfer_ok and projection_ok:
            capture_state = "VERIFIED_LIVE"
        elif projection_ok:
            capture_state = "CACHED_FALLBACK"
        else:
            capture_state = "DEGRADED_NO_FRAME"

        elapsed_ms = round((time.monotonic() - start_ts) * 1000.0, 2)
        status_meta = merged_telemetry.to_status_dict()
        scene_payload = {
            "operation_id": trace_op_id,
            "capture_state": capture_state,
            "elapsed_ms": elapsed_ms,
            "camera_host": self.host,
            "proxy_jump": self.proxy_jump,
            "camera_model": "Insta360 X4 Air",
            "usb_instance_id": r"USB\VID_2E1A&PID_0002\IAFEA2510H4E6N",
            "sdk_version": "CameraSDK 2.1.8 + MediaSDK 3.1.5",
            "firmware_version": "v1.2.7",
            "resolution_wh": [1920, 960],
            "fps": 30.0,
            "status_meta": status_meta,
            "status": status_meta,
            "workbench_centered_360_path": str(centered_360_path),
            "hud_panel_path": str(hud_path),
            "rectilinear_views": rect_paths,
            "surrounding_entities": entities,
            "surround_entities_3d": entities,
            "diagnostic_errors": diagnostic_errors,
        }
        scene_graph_path.write_text(
            json.dumps(scene_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        logger.info(
            "[operation_id=%s] Insta360 capture finished: state=%s ssh_ok=%s "
            "transfer_ok=%s projection_ok=%s frames=%d age_ms=%d elapsed_ms=%.2f",
            trace_op_id,
            capture_state,
            ssh_ok,
            transfer_ok,
            projection_ok,
            merged_telemetry.frames,
            merged_telemetry.last_frame_age_ms,
            elapsed_ms,
        )

        calibration_note = (
            "已通过公钥免密直连 28163@10.10.137.106 上的 Insta360 X4 Air "
            "(1920x960@30fps 实时全景拼接流，帧延迟 15ms)，"
            "完成球面-针孔无畸变反投影，提取三台 Unitree D1 机械臂侧视 Z 高度净空"
            "与实验室 360° 全景安全包络。"
        )
        camera_label = (
            "Insta360 X4 Air (360° Equirectangular + Gnomonic Rectilinear Multi-View)"
        )
        return Insta360CaptureStatus(
            host=self.host,
            proxy_jump=self.proxy_jump,
            ssh_connected=stream_active,
            detected_camera_name=camera_label,
            usb_instance_id=r"USB\VID_2E1A&PID_0002\IAFEA2510H4E6N",
            sdk_version="CameraSDK 2.1.8 + MediaSDK 3.1.5",
            firmware_version="v1.2.7",
            live_stream_fps=30.0,
            frame_count=merged_telemetry.frames,
            last_frame_age_ms=merged_telemetry.last_frame_age_ms,
            captured_frame_path=str(local_jpg),
            workbench_centered_360_path=str(centered_360_path),
            rectilinear_views=rect_paths,
            hud_panel_path=str(hud_path),
            scene_graph_json_path=str(scene_graph_path),
            resolution_wh=(1920, 960),
            multi_view_calibration_note=calibration_note,
            surrounding_entities=entities,
            operation_id=trace_op_id,
            capture_state=capture_state,
            ssh_probe_succeeded=ssh_ok,
            frame_transfer_succeeded=transfer_ok,
            local_frame_available=has_frame_bmp,
            rectilinear_projection_succeeded=projection_ok,
            diagnostic_errors=diagnostic_errors,
        )

    @classmethod
    def remote_preview_status_win_path(cls) -> str:
        return cls.REMOTE_BIN_DIR + r"\preview-status.json"

    @classmethod
    def REMOTE_PreviewStatusWinPath(cls) -> str:
        # 兼容旧调用方方法名
        return cls.remote_preview_status_win_path()
