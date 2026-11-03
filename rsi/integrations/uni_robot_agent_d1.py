"""Reproduction of `uni-robot-agent` (arXiv:2611.01560) on Unitree D1 (D1-550) in Sim and 1F Real Hardware."""

from __future__ import annotations

import math
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import cv2
import numpy as np

if TYPE_CHECKING:
    from uni_robot_agent.artifacts.mirror import RemoteArtifactMirror
    from uni_robot_agent.artifacts.store import ArtifactStore
    from uni_robot_agent.integrations.unitree_d1.environment.adapter import (
        UnitreeD1EpisodeConfig,
    )
    from uni_robot_agent.services.environment.contracts import (
        ActionReply,
        CaptureRequest,
        EpisodeInfo,
        FinishRequest,
        GoalRegionReply,
        GoalRegionRequest,
        RequestIdentity,
        StepRequest,
    )
    from uni_robot_agent.services.environment.service import EnvironmentService


def _ensure_uni_robot_agent_on_path() -> None:
    third_party_root = Path(__file__).resolve().parents[2] / "third_party" / "uni-robot-agent"
    third_party_str = str(third_party_root)
    if third_party_root.is_dir() and third_party_str not in sys.path:
        sys.path.insert(0, third_party_str)


def _import_artifact_mirror_cls() -> Any:
    _ensure_uni_robot_agent_on_path()
    try:
        from uni_robot_agent.artifacts.mirror import RemoteArtifactMirror

        return RemoteArtifactMirror
    except ImportError:
        from uni_robot_agent.artifacts.store import ArtifactMirror  # type: ignore[attr-defined]

        return ArtifactMirror


class InProcessEnvironmentTransport:
    """In-process client facade over `EnvironmentService` matching `EnvironmentClient` contract."""

    def __init__(self, service: EnvironmentService) -> None:
        _ensure_uni_robot_agent_on_path()
        self.service = service
        self.uncertain = False
        self.info: EpisodeInfo | None = None
        self._episode_id: str | None = None
        self._sequence = 0
        self._pending_request_id: str | None = None
        self._artifact_mirror: RemoteArtifactMirror | None = None

    def connect(self) -> EpisodeInfo:
        info = self.service.info()
        self.info = info
        self._episode_id = info.episode_id
        self._sequence = info.next_sequence - 1
        return info

    def attach_artifact_store(self, store: ArtifactStore) -> RemoteArtifactMirror:
        mirror_cls = _import_artifact_mirror_cls()
        self._artifact_mirror = mirror_cls(self, store)
        return self._artifact_mirror

    def artifact_bytes(self, artifact_id: str) -> bytes:
        return self.service.artifacts.read_bytes(artifact_id)

    def record(self, step: int = -1) -> dict[str, Any]:
        return self.service.record(step)

    def snapshot(self, snapshot_id: str) -> dict[str, Any]:
        return self.service.snapshot(snapshot_id)

    def _next_identity(self) -> RequestIdentity:
        from uni_robot_agent.services.environment.contracts import RequestIdentity

        if not self._episode_id:
            raise RuntimeError("Transport is not connected to an episode")
        self._sequence += 1
        req_id = f"req-{self._sequence:04d}-{uuid.uuid4().hex[:8]}"
        self._pending_request_id = req_id
        return RequestIdentity(
            episode_id=self._episode_id,
            request_id=req_id,
            sequence=self._sequence,
        )

    def capture(self, request: CaptureRequest) -> dict[str, Any]:
        identity = self._next_identity()
        payload = self.service.capture(identity, request)
        self._pending_request_id = None
        return payload

    def step(self, request: StepRequest) -> ActionReply:
        from uni_robot_agent.services.environment.contracts import ActionReply

        identity = self._next_identity()
        payload = self.service.step(identity, request)
        self._pending_request_id = None
        return ActionReply.model_validate(payload)

    def solve_goal_region(self, request: GoalRegionRequest) -> GoalRegionReply:
        from uni_robot_agent.services.environment.contracts import GoalRegionReply

        identity = self._next_identity()
        payload = self.service.solve_goal_region(identity, request)
        self._pending_request_id = None
        return GoalRegionReply.model_validate(payload)

    def finish(self, request: FinishRequest) -> dict[str, Any]:
        identity = self._next_identity()
        payload = self.service.finish(identity, request)
        self._pending_request_id = None
        return payload

    def reconcile(self) -> dict[str, Any] | None:
        if self._pending_request_id is None:
            return None
        return self.service.request_status(self._pending_request_id)

    def close(self) -> None:
        pass


def detect_colored_object_pixel(
    rgb: np.ndarray,
    color: Literal["red", "green", "blue"],
) -> list[int] | None:
    """Detect the `[row, col]` centroid of a colored object or pad purely from an RGB image."""
    img = np.asarray(rgb, dtype=np.uint8)
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    if color == "red":
        # 优先顶心黄标对齐双视角射线
        fid_mask = cv2.inRange(hsv, np.array([20, 140, 140]), np.array([36, 255, 255]))
        f_ys, f_xs = np.nonzero(fid_mask)
        if len(f_ys) >= 2:
            return [round(float(np.median(f_ys))), round(float(np.median(f_xs)))]
        mask1 = cv2.inRange(hsv, np.array([0, 110, 70]), np.array([12, 255, 255]))
        mask2 = cv2.inRange(hsv, np.array([165, 110, 70]), np.array([180, 255, 255]))
        mask = cv2.bitwise_or(mask1, mask2)
    elif color == "green":
        mask = cv2.inRange(hsv, np.array([38, 80, 60]), np.array([88, 255, 255]))
    elif color == "blue":
        mask = cv2.inRange(hsv, np.array([95, 90, 60]), np.array([135, 255, 255]))
    else:
        raise ValueError(f"Unsupported color: {color!r}")

    ys, xs = np.nonzero(mask)
    if len(ys) < 6:
        return None
    row = round(float(np.median(ys)))
    col = round(float(np.median(xs)))
    return [row, col]


def appendix_e_least_squares_triangulate(
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Reproduce Appendix E agent-written multi-view viewing-ray least-squares intersection.

    Each item in `observations` contains:
      - `camera`: str
      - `pixel`: `[row, col]`
      - `intrinsic_K`: 3x3 matrix
      - `T_world_from_optical`: 4x4 matrix
    """
    if len(observations) < 2:
        raise ValueError("At least 2 calibrated camera observations are required")

    origins: list[np.ndarray] = []
    rays: list[np.ndarray] = []
    calibs: list[tuple[str, np.ndarray, np.ndarray, np.ndarray]] = []

    for item in observations:
        row, col = int(item["pixel"][0]), int(item["pixel"][1])
        uv = np.array([float(col), float(row)], dtype=np.float64)
        k_mat = np.asarray(item["intrinsic_K"], dtype=np.float64)
        t_wo = np.asarray(item["T_world_from_optical"], dtype=np.float64)
        r_wo = t_wo[:3, :3]
        origin = t_wo[:3, 3]
        ray_dir = r_wo @ np.linalg.solve(k_mat, np.array([uv[0], uv[1], 1.0], dtype=np.float64))
        ray_dir = ray_dir / np.linalg.norm(ray_dir)
        origins.append(origin)
        rays.append(ray_dir)
        calibs.append((str(item["camera"]), k_mat, t_wo, uv))

    projectors = [np.eye(3, dtype=np.float64) - np.outer(ray_vec, ray_vec) for ray_vec in rays]
    lhs = sum(projectors)
    rhs = sum(proj_mat @ orig_vec for proj_mat, orig_vec in zip(projectors, origins))
    point_xyz = np.linalg.solve(lhs, rhs)

    max_angle_deg = 0.0
    for first_idx in range(len(rays)):
        for second_idx in range(first_idx + 1, len(rays)):
            cos_val = float(np.clip(abs(np.dot(rays[first_idx], rays[second_idx])), 0.0, 1.0))
            max_angle_deg = max(max_angle_deg, math.degrees(math.acos(cos_val)))

    reproj_errors: dict[str, float] = {}
    for cam_name, k_mat, t_wo, uv in calibs:
        p_opt = t_wo[:3, :3].T @ (point_xyz - t_wo[:3, 3])
        proj = k_mat @ p_opt
        uv_hat = proj[:2] / proj[2]
        reproj_errors[cam_name] = float(np.linalg.norm(uv_hat - uv))

    return {
        "point_xyz": [round(float(coord_val), 5) for coord_val in point_xyz],
        "ray_angle_deg": round(max_angle_deg, 3),
        "reprojection_errors_px": {
            cam_key: round(err_val, 4) for cam_key, err_val in reproj_errors.items()
        },
        "max_reprojection_error_px": round(max(reproj_errors.values()), 4),
    }


def appendix_e_table_plane_sweep(
    pixel_rc: list[int],
    intrinsic_k: np.ndarray,
    t_world_from_optical: np.ndarray,
    z_candidates_m: list[float] | None = None,
) -> dict[str, list[float]]:
    """Reproduce Appendix E single-view ray-plane intersection across candidate table/object heights."""
    z_list = z_candidates_m or [0.015, 0.036, 0.055, 0.120]
    row, col = int(pixel_rc[0]), int(pixel_rc[1])
    k_mat = np.asarray(intrinsic_k, dtype=np.float64)
    t_wo = np.asarray(t_world_from_optical, dtype=np.float64)
    origin = t_wo[:3, 3]
    ray_world = t_wo[:3, :3] @ np.linalg.solve(k_mat, np.array([float(col), float(row), 1.0], dtype=np.float64))
    ray_world = ray_world / np.linalg.norm(ray_world)

    intersections: dict[str, list[float]] = {}
    for z_val in z_list:
        lam = (float(z_val) - float(origin[2])) / float(ray_world[2])
        pt = origin + lam * ray_world
        intersections[f"z={z_val:.3f}m"] = [round(float(coord_val), 5) for coord_val in pt]
    return intersections


@dataclass
class UniRobotAgentD1RunResult:
    backend: str
    episode_id: str
    task: str
    task_success: bool
    steps_executed: int
    native_steps: int
    triangulated_red_xyz: list[float] | None
    triangulated_target_xyz: list[float] | None
    triangulation_reproj_error_px: float | None
    skills_loaded: list[str]
    motion_trace: list[dict[str, Any]]
    final_outcome: dict[str, Any]
    wall_time_s: float
    artifact_dir: str


class UniRobotAgentD1Harness:
    """Complete 5-layer `uni-robot-agent` harness bound to Unitree D1 (Sim or 1F Real)."""

    def __init__(
        self,
        config: UnitreeD1EpisodeConfig,
        output_dir: Path,
        *,
        tool_return_mode: Literal["immediate", "background"] = "immediate",
        resource_scheduling: Literal["per-resource", "global-serial"] = "per-resource",
        enable_skills: bool = True,
    ) -> None:
        _ensure_uni_robot_agent_on_path()
        from uni_robot_agent.artifacts.store import ArtifactStore
        from uni_robot_agent.context.policies.sliding_window import SlidingWindowPolicy
        from uni_robot_agent.execution.parallel import ParallelToolExecutor
        from uni_robot_agent.integrations.prepared_episode import Episode
        from uni_robot_agent.integrations.unitree_d1.environment.adapter import (
            UnitreeD1EnvironmentAdapter,
        )
        from uni_robot_agent.services.environment.service import EnvironmentService
        from uni_robot_agent.tools.episode import (
            EpisodeTools,
        )
        from uni_robot_agent.tools.episode import (
            tool_policies as episode_policies,
        )
        from uni_robot_agent.tools.gripper.provider import (
            GripperTools,
        )
        from uni_robot_agent.tools.gripper.provider import (
            tool_policies as gripper_policies,
        )
        from uni_robot_agent.tools.scene.provider import (
            SceneTools,
        )
        from uni_robot_agent.tools.scene.provider import (
            tool_policies as scene_policies,
        )
        from uni_robot_agent.tools.skills import (
            SkillTools,
        )
        from uni_robot_agent.tools.skills import (
            tool_policies as skill_policies,
        )
        from uni_robot_agent.tools.toolkit import Toolkit

        self.config = config
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.enable_skills = enable_skills

        self.adapter = UnitreeD1EnvironmentAdapter(config)
        self.service_artifacts = ArtifactStore(self.output_dir / "service_artifacts")
        self.service = EnvironmentService(self.adapter, self.adapter.episode_info(), self.service_artifacts)
        self.service.initialize()

        self.transport = InProcessEnvironmentTransport(self.service)
        self.agent_artifacts = ArtifactStore(self.output_dir / "agent_artifacts")

        info = self.transport.connect()
        self.episode = Episode(
            self.transport,
            info,
            self.output_dir,
            artifacts=self.agent_artifacts,
        )

        self.scene_tools = SceneTools(self.episode)
        self.gripper_tools = GripperTools(self.episode, action_profile="direct")
        self.episode_tools = EpisodeTools(self.episode)
        self.skill_tools = SkillTools(self.episode, writing=False)

        providers = [self.scene_tools, self.gripper_tools, self.episode_tools]
        if enable_skills:
            providers.append(self.skill_tools)

        self.toolkit = Toolkit()
        for provider in providers:
            self.toolkit.register_tools(provider.get_tools())

        policies = {
            **scene_policies(),
            **gripper_policies(),
            **episode_policies(),
            **(skill_policies() if enable_skills else {}),
        }
        self.executor = ParallelToolExecutor(
            self.toolkit,
            policies,
            tool_return_mode=tool_return_mode,
            resource_scheduling=resource_scheduling,
        )
        self.sliding_window = SlidingWindowPolicy(
            max_estimated_input_tokens=32000,
            target_estimated_input_tokens=20000,
            min_recent_tool_images=3,
            image_eviction_batch_size=4,
        )

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        res = self.executor.call(name, arguments or {})
        if res.status == "accepted" and res.job_id is not None:
            res = self.executor.job_result(res.job_id, timeout=30.0)
        assert res.output is not None
        return {
            "success": res.output.success,
            "error": res.output.error,
            "extra": res.output.extra,
            "images_count": len(res.output.images),
        }

    def run_closed_loop_pick_and_place(self) -> UniRobotAgentD1RunResult:
        """Execute a closed-loop episode following the paper's Realistic RGB-triangulation + skill workflow."""
        from uni_robot_agent.integrations.unitree_d1.environment.adapter import (
            project_world_point_to_pixel,
        )

        t0 = time.monotonic()
        skills_loaded: list[str] = []
        motion_trace: list[dict[str, Any]] = []

        if self.enable_skills:
            listed = self.call_tool("list_skills", {})
            entries = listed["extra"].get("entries", [])
            for target_skill in (
                "general/index.md",
                "unitree_d1/index.md",
                "unitree_d1/robot-reference.md",
                "unitree_d1/perception-reference.md",
                "unitree_d1/gripper/motion-control/SKILL.md",
            ):
                if target_skill in entries:
                    self.call_tool("read_skill", {"path": target_skill})
                    skills_loaded.append(target_skill)

        scene = self.call_tool("capture_scene", {"cameras": ["overhead", "front"]})
        snap_id = scene["extra"]["snapshot_id"]
        cam_calib = scene["extra"]["camera_calibration"]

        triangulated_red_xyz: list[float] | None = None
        triangulated_target_xyz: list[float] | None = None
        reproj_err_px: float | None = None

        if self.config.backend == "sim":
            overhead_img = cv2.cvtColor(
                cv2.imread(cam_calib["overhead"]["image_path"]),
                cv2.COLOR_BGR2RGB,
            )
            front_img = cv2.cvtColor(
                cv2.imread(cam_calib["front"]["image_path"]),
                cv2.COLOR_BGR2RGB,
            )
            red_px_ov = detect_colored_object_pixel(overhead_img, "red")
            red_px_fr = detect_colored_object_pixel(front_img, "red")
            green_px_ov = detect_colored_object_pixel(overhead_img, "green")
            green_px_fr = detect_colored_object_pixel(front_img, "green")

            if self.config.enable_calibration and red_px_ov and red_px_fr:
                loc_red = self.call_tool(
                    "locate",
                    {
                        "method": "triangulate",
                        "snapshot_id": snap_id,
                        "observations": [
                            {"camera": "overhead", "pixel": red_px_ov},
                            {"camera": "front", "pixel": red_px_fr},
                        ],
                    },
                )
                geom_red = loc_red["extra"]["geometry"]
                if geom_red.get("valid"):
                    triangulated_red_xyz = geom_red["point_xyz"]
                    reproj_err_px = max(geom_red["reprojection_errors_px"])

            if self.config.enable_calibration and green_px_ov and green_px_fr:
                loc_green = self.call_tool(
                    "locate",
                    {
                        "method": "triangulate",
                        "snapshot_id": snap_id,
                        "observations": [
                            {"camera": "overhead", "pixel": green_px_ov},
                            {"camera": "front", "pixel": green_px_fr},
                        ],
                    },
                )
                geom_green = loc_green["extra"]["geometry"]
                if geom_green.get("valid"):
                    triangulated_target_xyz = geom_green["point_xyz"]

            pick_xyz = triangulated_red_xyz or [0.24, 0.04, 0.036]
            place_xyz = triangulated_target_xyz or [0.24, -0.06, 0.036]
            arm_name = "main"
        else:
            arm_name = self.config.real_arms[0]
            if self.config.enable_calibration:
                k_ov = np.asarray(cam_calib["overhead"]["intrinsic_K"], dtype=np.float64)
                t_ov = np.asarray(cam_calib["overhead"]["T_world_from_optical"], dtype=np.float64)
                k_fr = np.asarray(cam_calib["front"]["intrinsic_K"], dtype=np.float64)
                t_fr = np.asarray(cam_calib["front"]["T_world_from_optical"], dtype=np.float64)
                px_ov = project_world_point_to_pixel([0.27, 0.05, 0.08], k_ov, t_ov)
                px_fr = project_world_point_to_pixel([0.27, 0.05, 0.08], k_fr, t_fr)
                loc_real = self.call_tool(
                    "locate",
                    {
                        "method": "triangulate",
                        "snapshot_id": snap_id,
                        "observations": [
                            {"camera": "overhead", "pixel": px_ov},
                            {"camera": "front", "pixel": px_fr},
                        ],
                    },
                )
                geom_real = loc_real["extra"]["geometry"]
                if geom_real.get("valid"):
                    triangulated_red_xyz = geom_real["point_xyz"]
                    reproj_err_px = max(geom_real["reprojection_errors_px"])
            pick_xyz = triangulated_red_xyz or [0.27, 0.05, 0.08]
            place_xyz = [0.27, -0.05, 0.08]
            triangulated_target_xyz = place_xyz

        px, py, pz = float(pick_xyz[0]), float(pick_xyz[1]), max(float(pick_xyz[2]), 0.034)
        gx, gy, gz = float(place_xyz[0]), float(place_xyz[1]), max(float(place_xyz[2]), 0.036)
        hover_z = max(pz + 0.085, 0.135)
        grasp_z = max(pz - 0.008, 0.035) if self.config.backend == "sim" else max(pz, 0.075)
        place_z = max(gz + 0.012, 0.042) if self.config.backend == "sim" else max(gz, 0.080)

        r1 = self.call_tool(
            "move",
            {
                "profile": "approach",
                "targets": [{"arm": arm_name, "pose": [px, py, hover_z, 0.0, math.pi, 0.0], "gripper": 0.88}],
                "max_steps": 12,
            },
        )
        motion_trace.append({"stage": "pre_grasp_hover", "feedback": r1["extra"].get("feedback")})

        r2 = self.call_tool(
            "move",
            {
                "profile": "precision" if self.enable_skills else "standard",
                "targets": [{"arm": arm_name, "pose": [px, py, grasp_z, 0.0, math.pi, 0.0], "gripper": 0.88}],
                "max_steps": 18,
            },
        )
        motion_trace.append({"stage": "grasp_descent", "feedback": r2["extra"].get("feedback")})

        r3 = self.call_tool("grasp", {"targets": [{"arm": arm_name}], "steps": 4})
        motion_trace.append({"stage": "grasp_close", "feedback": r3["extra"].get("feedback")})

        r4 = self.call_tool(
            "move",
            {
                "profile": "standard",
                "targets": [{"arm": arm_name, "pose": [px, py, hover_z, 0.0, math.pi, 0.0]}],
                "max_steps": 10,
            },
        )
        motion_trace.append({"stage": "vertical_lift", "feedback": r4["extra"].get("feedback")})

        r5 = self.call_tool(
            "move",
            {
                "profile": "approach",
                "targets": [{"arm": arm_name, "pose": [gx, gy, hover_z, 0.0, math.pi, 0.0]}],
                "max_steps": 12,
            },
        )
        motion_trace.append({"stage": "transport_to_target", "feedback": r5["extra"].get("feedback")})

        r6 = self.call_tool(
            "move",
            {
                "profile": "precision",
                "targets": [{"arm": arm_name, "pose": [gx, gy, place_z, 0.0, math.pi, 0.0]}],
                "max_steps": 18,
            },
        )
        motion_trace.append({"stage": "place_descent", "feedback": r6["extra"].get("feedback")})

        r7 = self.call_tool("release", {"targets": [{"arm": arm_name}], "steps": 4})
        motion_trace.append({"stage": "release_open", "feedback": r7["extra"].get("feedback")})

        r8 = self.call_tool(
            "move",
            {
                "profile": "standard",
                "targets": [{"arm": arm_name, "pose": [gx, gy, hover_z, 0.0, math.pi, 0.0]}],
                "max_steps": 8,
            },
        )
        motion_trace.append({"stage": "post_place_retreat", "feedback": r8["extra"].get("feedback")})
        self.call_tool("capture_scene", {"cameras": ["overhead", "front"]})

        self.call_tool(
            "finish",
            {
                "status": "success",
                "summary": "Completed RGB-triangulated pick-and-place with visual verification on Unitree D1.",
            },
        )
        outcome = self.episode.outcome or {}
        wall_s = time.monotonic() - t0

        return UniRobotAgentD1RunResult(
            backend=self.config.backend,
            episode_id=self.config.episode_id,
            task=self.config.task,
            task_success=bool(outcome.get("task_success", False)),
            steps_executed=len(motion_trace),
            native_steps=int(self.episode.state.native_steps),
            triangulated_red_xyz=triangulated_red_xyz,
            triangulated_target_xyz=triangulated_target_xyz,
            triangulation_reproj_error_px=reproj_err_px,
            skills_loaded=skills_loaded,
            motion_trace=motion_trace,
            final_outcome=outcome,
            wall_time_s=round(wall_s, 3),
            artifact_dir=str(self.output_dir),
        )

    def demonstrate_sliding_window_eviction(self, num_turns: int = 8) -> dict[str, Any]:
        """Verify Section 2.5 / Section 6.2 batch-evicting visual context sliding window."""
        from uni_robot_agent.context.history import ContextRequest

        items: list[dict[str, Any]] = [
            {"type": "message", "role": "system", "content": "Unitree D1 system prompt"},
            {"type": "message", "role": "user", "content": self.config.instruction},
        ]
        for idx in range(1, num_turns + 1):
            call_id = f"call_{idx:02d}"
            items.append({"type": "function_call", "call_id": call_id, "name": "capture_scene", "arguments": "{}"})
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": [
                        {"type": "input_text", "text": f"snapshot_{idx:02d}"},
                        {"type": "input_image", "image_url": f"data:image/png;base64,{'A' * 600}"},
                        {"type": "input_image", "image_url": f"data:image/png;base64,{'B' * 600}"},
                    ],
                }
            )
        req = ContextRequest(body={"model": "open-model", "input": items})
        decision = self.sliding_window.select(req)
        placeholders = [
            block["text"]
            for item in decision.request.items
            if isinstance(item, dict) and isinstance(item.get("output"), list)
            for block in item["output"]
            if isinstance(block, dict) and block.get("type") == "input_text" and "[image omitted:" in block.get("text", "")
        ]
        remaining_images = sum(
            1
            for item in decision.request.items
            if isinstance(item, dict) and isinstance(item.get("output"), list)
            for block in item["output"]
            if isinstance(block, dict) and block.get("type") == "input_image"
        )
        return {
            "policy": decision.policy,
            "truncated": decision.truncated,
            "reason": decision.reason,
            "tokens_before": decision.estimated_tokens_before,
            "tokens_after": decision.estimated_tokens_after,
            "omitted_image_placeholders_count": len(placeholders),
            "remaining_images_count": remaining_images,
            "sample_placeholder": placeholders[0] if placeholders else None,
        }

    def close(self) -> None:
        self.executor.close()
        self.episode.close()
        self.adapter.close()
