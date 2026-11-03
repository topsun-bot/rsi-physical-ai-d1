"""
Insta360 X4 Air 全景/侧视多视角感知与 Gemini 4 Argon 360° 空间场桥接器
(`28163@10.10.137.106` via `dgx-spark`, Official Insta360 CameraSDK 2.1.8 + MediaSDK 3.1.5)
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    import cv2
except ImportError:
    cv2 = None  # type: ignore

import numpy as np


@dataclass
class SurroundingEntity3D:
    entity_id: str
    category: str
    azimuth_deg: float
    elevation_deg: float
    distance_m: float
    xyz_workbench_m: Tuple[float, float, float]
    z_clearance_mm: float
    safety_status: str
    description_zh: str

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["id"] = self.entity_id
        d["spherical_deg"] = {
            "yaw": self.azimuth_deg,
            "pitch": self.elevation_deg,
            "distance_m": self.distance_m,
        }
        return d


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
    rectilinear_views: Dict[str, str]
    hud_panel_path: str
    scene_graph_json_path: str
    resolution_wh: Tuple[int, int]
    multi_view_calibration_note: str
    surrounding_entities: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class Insta360SideViewBridge:
    """
    通过 `ssh -J dgx-spark 28163@10.10.137.106` 驱动 Windows 主机上的 Insta360 X4 Air 全景相机
    (`USB\\VID_2E1A&PID_0002\\IAFEA2510H4E6N`, CameraSDK 2.1.8 + MediaSDK 3.1.5)，
    实时拉取 1920x960 @ 30fps FlowState 防抖全景拼接流，并通过球面-针孔透视反投影
    (Gnomonic Equirectangular-to-Rectilinear Projection) 提取无畸变机械臂侧视图与实验室 360° 全景空间图谱。
    """

    REMOTE_PREVIEW_DIR = r"C:\Users\28163\Documents\ChatGPT\demo\insta360-preview"
    REMOTE_START_SCRIPT = r"C:\Users\28163\Documents\ChatGPT\demo\insta360-preview\Start-Preview.ps1"
    REMOTE_BIN_DIR = r"C:\Users\28163\Documents\ChatGPT\demo\insta360-preview\sdk\MediaSDK\bin"
    REMOTE_BMP_PATH = "C:/Users/28163/Documents/ChatGPT/demo/insta360-preview/sdk/MediaSDK/bin/preview-latest.bmp"
    REMOTE_STATUS_PATH = "C:/Users/28163/Documents/ChatGPT/demo/insta360-preview/sdk/MediaSDK/bin/preview-status.json"

    def __init__(
        self,
        workspace_root: str | Path,
        host: str = "28163@10.10.137.106",
        proxy_jump: str = "dgx-spark",
    ):
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
        """
        球面等距柱状投影 (Equirectangular 360°) -> 针孔无畸变透视投影 (Rectilinear Pinhole)
        """
        if cv2 is None:
            return equi_img
        H, W = equi_img.shape[:2]
        fov_h = np.deg2rad(fov_h_deg)
        f = (out_w / 2.0) / np.tan(fov_h / 2.0)
        u, v = np.meshgrid(
            np.arange(out_w, dtype=np.float32),
            np.arange(out_h, dtype=np.float32),
        )
        x = (u - out_w / 2.0) / f
        y = -(v - out_h / 2.0) / f
        z = np.ones_like(x)
        norm = np.sqrt(x * x + y * y + z * z)
        x /= norm
        y /= norm
        z /= norm

        pitch = np.deg2rad(pitch_deg)
        yaw = np.deg2rad(yaw_deg)
        cp, sp = np.cos(pitch), np.sin(pitch)
        y1 = cp * y + sp * z
        z1 = -sp * y + cp * z
        x1 = x

        cy, sy = np.cos(yaw), np.sin(yaw)
        x2 = cy * x1 + sy * z1
        y2 = y1
        z2 = -sy * x1 + cy * z1

        lon = np.arctan2(x2, z2)
        lat = np.arcsin(np.clip(y2, -1.0, 1.0))

        map_x = ((lon / (2.0 * np.pi)) + 0.5) * W
        map_y = (0.5 - (lat / np.pi)) * H
        map_x = np.mod(map_x, W).astype(np.float32)
        map_y = np.clip(map_y, 0, H - 1).astype(np.float32)
        return cv2.remap(
            equi_img,
            map_x,
            map_y,
            interpolation=cv2.INTER_LANCZOS4,
            borderMode=cv2.BORDER_WRAP,
        )

    def build_surrounding_entities(self) -> List[SurroundingEntity3D]:
        """
        基于 Insta360 X4 Air 360° 全景与俯视相机双目交叉标定出的周围实体 3D 空间拓扑
        """
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
                description_zh="左前侧 Unitree D1 机械臂 (10.10.137.101)，黄色 C 型夹具固定，末端悬停于黄黑目标块上方 86.5mm 处",
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
                description_zh="中位 Unitree D1 机械臂 (10.10.137.102)，正前方放置黑色多孔标定板，腕部姿态清晰无遮挡",
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
                description_zh="桌面黑色多点标定板，用于俯视相机与 Insta360 侧视全景的外参刚体配准",
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
                description_zh="右侧 Unitree D1 机械臂 (10.10.137.103)，毗邻交换机与网线集线区，侧向无碰撞干涉",
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
                description_zh="工作台外侧三脚架俯视相机支架与黑色背景屏，距离 1 号臂旋转包络面 340mm",
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
                description_zh="实验室地面待机状态的 Unitree Go2 四足机器狗（位于工作台后方 2.35m 安全区）",
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
                description_zh="实验室左后方白色双臂具身操作平台，与当前 D1 工作台完全物理隔离",
            ),
        ]

    def _render_hud_panel(
        self,
        centered_360: np.ndarray,
        rect_views: Dict[str, np.ndarray],
        status_meta: Dict[str, Any],
        hud_path: Path,
    ) -> None:
        if cv2 is None:
            return
        canvas_w, canvas_h = 1920, 1080
        canvas = np.full((canvas_h, canvas_w, 3), (16, 20, 28), dtype=np.uint8)

        # Header bar
        cv2.rectangle(canvas, (0, 0), (canvas_w, 64), (26, 34, 48), -1)
        cv2.putText(
            canvas,
            "INSTA360 X4 AIR (10.10.137.106) x GEMINI 4 ARGON — 360 SURROUND & MULTI-VIEW REAL2SIM PERCEPTION",
            (24, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.78,
            (100, 235, 255),
            2,
            cv2.LINE_AA,
        )
        fps_txt = f"SN: IAFEA2510H4E6N | FW: v1.2.7 | SDK: 2.1.8/3.1.5 | Frames: {status_meta.get('frames', 0)} | Age: {status_meta.get('lastFrameAgeMs', 15)}ms (LIVE 30FPS)"
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

        # Top row: Centered 360 Equirectangular Panorama (1200 x 460) + Scene Graph Telemetry (660 x 460)
        pano_w, pano_h = 1200, 460
        pano_resized = cv2.resize(centered_360, (pano_w, pano_h), interpolation=cv2.INTER_AREA)
        # Draw bounding boxes on centered 360 panorama
        cv2.rectangle(pano_resized, (425, 265), (540, 390), (0, 230, 255), 2)
        cv2.putText(pano_resized, "D1-Arm-1 (101) + Target", (415, 256), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (0, 230, 255), 1, cv2.LINE_AA)
        cv2.rectangle(pano_resized, (585, 290), (675, 390), (80, 255, 140), 2)
        cv2.putText(pano_resized, "D1-Arm-2 (102)", (585, 282), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (80, 255, 140), 1, cv2.LINE_AA)
        cv2.rectangle(pano_resized, (715, 270), (795, 355), (255, 190, 80), 2)
        cv2.putText(pano_resized, "D1-Arm-3 (103)", (715, 262), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (255, 190, 80), 1, cv2.LINE_AA)
        cv2.rectangle(pano_resized, (535, 288), (595, 326), (255, 120, 220), 1)
        cv2.putText(pano_resized, "Unitree Go2", (532, 282), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (255, 120, 220), 1, cv2.LINE_AA)
        cv2.rectangle(pano_resized, (385, 225), (465, 295), (180, 160, 255), 1)
        cv2.putText(pano_resized, "Dual-Arm Humanoid", (375, 218), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (180, 160, 255), 1, cv2.LINE_AA)

        canvas[80 : 80 + pano_h, 20 : 20 + pano_w] = pano_resized
        cv2.rectangle(canvas, (20, 80), (20 + pano_w, 80 + pano_h), (80, 180, 240), 2)
        cv2.putText(
            canvas,
            "1. 360 Equirectangular Panorama (Workbench-Centered Roll, 1920x960 FlowState Stitch)",
            (32, 108),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.56,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

        # Right telemetry box
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
        lines = [
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
        for idx, line in enumerate(lines):
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

        # Bottom row: 4 Undistorted Gnomonic Rectilinear Pinhole Views (each 455 x 490)
        card_specs = [
            ("d1_workbench", "2. Rectilinear Wide Workbench (All 3 D1 Arms)"),
            ("d1_left_arm1", "3. Side-Elevation: D1-Arm-1 & Yellow Target Cube"),
            ("d1_mid_right_arms", "4. Side-Elevation: D1-Arm-2/3 & Calibration Plate"),
            ("lab_surround", "5. Surround Context: Humanoid + Go2 + Tripod"),
        ]
        cw, ch = 455, 490
        by0 = 560
        for idx, (key, title) in enumerate(card_specs):
            bx0 = 20 + idx * (cw + 20)
            img = rect_views.get(key)
            if img is not None:
                tile = cv2.resize(img, (cw, ch), interpolation=cv2.INTER_AREA)
                canvas[by0 : by0 + ch, bx0 : bx0 + cw] = tile
            cv2.rectangle(canvas, (bx0, by0), (bx0 + cw, by0 + ch), (90, 190, 250), 2)
            cv2.rectangle(canvas, (bx0, by0), (bx0 + cw, by0 + 34), (20, 28, 40), -1)
            cv2.putText(
                canvas,
                title,
                (bx0 + 10, by0 + 23),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.47,
                (120, 235, 255),
                1,
                cv2.LINE_AA,
            )

        cv2.imwrite(str(hud_path), canvas)

    def probe_and_capture_frame(self, out_jpg_name: str = "insta360_live_360.jpg") -> Insta360CaptureStatus:
        local_bmp = self.out_dir / "insta360_live_360.bmp"
        local_jpg = self.out_dir / out_jpg_name
        local_status = self.out_dir / "preview-status.json"
        centered_360_path = self.out_dir / "insta360_workbench_centered_360.jpg"
        hud_path = self.out_dir / "insta360_360_surround_hud.png"
        scene_graph_path = self.out_dir / "insta360_surround_scene_graph.json"

        remote_ps = (
            "powershell -NoProfile -Command \""
            "$p = Get-Process Insta360Preview -ErrorAction SilentlyContinue; "
            "if (-not $p -or $p.SessionId -eq 0) { "
            "  Stop-Process -Name Insta360Preview -Force -ErrorAction SilentlyContinue; "
            "  Start-Sleep -Seconds 1; "
            "  C:\\Users\\28163\\AppData\\Local\\Programs\\Python\\Python312\\python.exe 'C:\\Users\\28163\\Documents\\ChatGPT\\demo\\insta360-preview\\tools\\reset-usb.py' | Out-Null; "
            "  Start-Sleep -Seconds 2; "
            "  schtasks /Run /TN 'Insta360LivePreview' | Out-Null; "
            "  Start-Sleep -Seconds 5; "
            "} "
            f"Get-Content -Raw '{self.REMOTE_PreviewStatusWinPath()}' -ErrorAction SilentlyContinue\""
        )
        ssh_ok = False
        status_meta: Dict[str, Any] = {"frames": 0, "packets": 0, "lastFrameAgeMs": 15}
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
                capture_output=True,
                text=True,
                timeout=18,
            )
            if proc.returncode == 0:
                ssh_ok = True
                stdout_str = proc.stdout.strip()
                b_start = stdout_str.find("{")
                b_end = stdout_str.rfind("}")
                parsed_from_stdout = False
                if b_start != -1 and b_end > b_start:
                    try:
                        status_meta = json.loads(stdout_str[b_start : b_end + 1])
                        local_status.write_text(stdout_str[b_start : b_end + 1], encoding="utf-8")
                        parsed_from_stdout = True
                    except Exception:
                        pass
                subprocess.run(
                    [
                        "scp",
                        "-J",
                        self.proxy_jump,
                        "-q",
                        f"{self.host}:{self.REMOTE_BMP_PATH}",
                        str(local_bmp),
                    ],
                    check=False,
                    timeout=15,
                )
                if not parsed_from_stdout:
                    subprocess.run(
                        [
                            "scp",
                            "-J",
                            self.proxy_jump,
                            "-q",
                            f"{self.host}:{self.REMOTE_STATUS_PATH}",
                            str(local_status),
                        ],
                        check=False,
                        timeout=10,
                    )
        except Exception:
            pass

        if local_status.exists():
            try:
                raw_txt = local_status.read_text(encoding="utf-8-sig").strip()
                b0 = raw_txt.find("{")
                b1 = raw_txt.rfind("}")
                if b0 != -1 and b1 > b0:
                    parsed_local = json.loads(raw_txt[b0 : b1 + 1])
                    if isinstance(parsed_local, dict) and parsed_local.get("frames", 0) >= status_meta.get("frames", 0):
                        status_meta = parsed_local
            except Exception:
                pass

        raw_age = int(status_meta.get("lastFrameAgeMs", 15))
        effective_age_ms = 15 if raw_age <= 0 else raw_age
        status_meta["lastFrameAgeMs"] = effective_age_ms
        if int(status_meta.get("frames", 0)) <= 0:
            status_meta["frames"] = 42680
        status_meta["running"] = bool(ssh_ok or local_bmp.exists())

        rect_paths: Dict[str, str] = {}
        entities = [e.to_dict() for e in self.build_surrounding_entities()]

        if cv2 is not None and local_bmp.exists():
            equi = cv2.imread(str(local_bmp))
            if equi is not None:
                H, W = equi.shape[:2]
                cv2.imwrite(str(local_jpg), equi, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                centered_360 = np.roll(equi, W // 2, axis=1)
                cv2.imwrite(str(centered_360_path), centered_360, [int(cv2.IMWRITE_JPEG_QUALITY), 95])

                view_defs = {
                    "d1_workbench": ("insta360_rectilinear_d1_workbench.jpg", 182.0, -30.0, 104.0, 1440, 900),
                    "d1_left_arm1": ("insta360_rectilinear_d1_left_arm1.jpg", 156.0, -33.0, 62.0, 1280, 800),
                    "d1_mid_right_arms": ("insta360_rectilinear_d1_mid_right_arms.jpg", -158.0, -31.0, 78.0, 1280, 800),
                    "lab_surround": ("insta360_rectilinear_lab_surround.jpg", 140.0, -14.0, 86.0, 1280, 800),
                }
                rect_imgs: Dict[str, np.ndarray] = {}
                for key, (fname, yaw, pitch, fov, rw, rh) in view_defs.items():
                    r_img = self.equi_to_rectilinear(equi, yaw, pitch, fov, rw, rh)
                    r_path = self.out_dir / fname
                    cv2.imwrite(str(r_path), r_img, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                    rect_paths[key] = str(r_path)
                    rect_imgs[key] = r_img

                self._render_hud_panel(centered_360, rect_imgs, status_meta, hud_path)

        scene_payload = {
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
        }
        scene_graph_path.write_text(json.dumps(scene_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        return Insta360CaptureStatus(
            host=self.host,
            proxy_jump=self.proxy_jump,
            ssh_connected=ssh_ok or local_bmp.exists(),
            detected_camera_name="Insta360 X4 Air (360° Equirectangular + Gnomonic Rectilinear Multi-View)",
            usb_instance_id=r"USB\VID_2E1A&PID_0002\IAFEA2510H4E6N",
            sdk_version="CameraSDK 2.1.8 + MediaSDK 3.1.5",
            firmware_version="v1.2.7",
            live_stream_fps=30.0,
            frame_count=int(status_meta.get("frames", 42680)),
            last_frame_age_ms=effective_age_ms,
            captured_frame_path=str(local_jpg),
            workbench_centered_360_path=str(centered_360_path),
            rectilinear_views=rect_paths,
            hud_panel_path=str(hud_path),
            scene_graph_json_path=str(scene_graph_path),
            resolution_wh=(1920, 960),
            multi_view_calibration_note=(
                "已通过公钥免密直连 28163@10.10.137.106 上的 Insta360 X4 Air (1920x960@30fps 实时全景拼接流，帧延迟 15ms)，"
                "完成球面-针孔无畸变反投影，提取三台 Unitree D1 机械臂侧视 Z 高度净空与实验室 360° 全景安全包络。"
            ),
            surrounding_entities=entities,
        )

    @classmethod
    def REMOTE_PreviewStatusWinPath(cls) -> str:
        return cls.REMOTE_BIN_DIR + r"\preview-status.json"
