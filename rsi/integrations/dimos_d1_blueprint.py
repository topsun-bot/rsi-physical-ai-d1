"""DimensionalOS (`dimensionalOS/dimos`) Reactive Stream Blueprint & Skill Library for Unitree D1 x3.

Reproduces the DimOS agentive operating system programming model:
  -Typed reactive streams `In[T]` / `Out[T]`
  - `@rpc` lifecycle methods and `@skill` agent-callable physical skills
  - `Blueprint` & `autoconnect(...)` declarative module wiring
  - 5 continuous workbench skills for 3 Unitree D1 arms (10.10.137.101..103):
      1. `cloth_folding`   (双臂/三臂协同叠衣服：左袖翻折 → 右袖翻折 → 底部对折 → 抚平压实)
      2. `cyber_dance`     (三臂编排舞蹈：苏醒开合 → 镜像展翅 → 人浪传递 → 齐舞翻花拍手 → V字致敬)
      3. `gripper_workout` (夹爪开合专项：多角度全开/半开/闭合/节拍脉冲开合)
      4. `pick_relay`      (三臂视觉分拣接力：103取料 → 102空中交接 → 101定点码放)
      5. `surface_wipe`    (桌面S形清扫巡检与安全归零)
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")


def rpc(fn: Callable[..., Any]) -> Callable[..., Any]:
    """DimOS @rpc decorator marking remotely callable module lifecycle/control methods."""
    setattr(fn, "_dimos_rpc", True)
    return fn


def skill(fn: Callable[..., Any]) -> Callable[..., Any]:
    """DimOS @skill decorator exposing a physical robot capability to LLM/VLM agents."""
    setattr(fn, "_dimos_skill", True)
    return fn


class Out(Generic[T]):
    """DimOS reactive output stream port."""

    def __init__(self, name: str = "") -> None:
        self.name = name
        self._subscribers: list[Callable[[T], None]] = []
        self.last_message: T | None = None
        self.publish_count: int = 0

    def subscribe(self, callback: Callable[[T], None]) -> None:
        self._subscribers.append(callback)

    def publish(self, msg: T) -> None:
        self.last_message = msg
        self.publish_count += 1
        for sub in list(self._subscribers):
            sub(msg)


class In(Generic[T]):
    """DimOS reactive input stream port."""

    def __init__(self, name: str = "") -> None:
        self.name = name
        self._callbacks: list[Callable[[T], None]] = []
        self.last_message: T | None = None

    def subscribe(self, callback: Callable[[T], None]) -> None:
        self._callbacks.append(callback)

    def _receive(self, msg: T) -> None:
        self.last_message = msg
        for cb in list(self._callbacks):
            cb(msg)


class Module:
    """DimOS base subsystem Module."""

    @classmethod
    def blueprint(cls, **kwargs: Any) -> ModuleBlueprint:
        return ModuleBlueprint(cls, kwargs)


@dataclass
class ModuleBlueprint:
    module_cls: type[Module]
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass
class FleetWaypoint:
    """Simultaneous 7-DOF target (deg) for all 3 Unitree D1 arms: [J0, J1, J2, J3, J4, J5, Gripper]."""

    phase_name: str
    duration_s: float
    left_103_deg: list[float]
    center_102_deg: list[float]
    right_101_deg: list[float]


HOME_POSE_DEG = [0.0, -89.5, 89.0, 0.0, 5.0, 0.0, 0.0]


def build_skill_waypoints(skill_name: str, iteration: int = 0) -> list[FleetWaypoint]:
    """Return the multi-stage 3-arm trajectory waypoints for each physical skill."""
    if skill_name == "grasp_and_plug_loop":
        phase = iteration % 6
        sweep_extra = [-2.0, -5.0, -8.0, -10.0, -6.0, -9.0][phase]
        grasp_j0_d = [0.0, -2.0, 1.5, -3.5, -1.0, -2.5][phase]
        sock_j0_d = [0.0, 2.0, -1.5, 3.0, 1.0, 2.5][phase]
        return [
            FleetWaypoint(
                phase_name=f"抓取+插装 Iter#{iteration+1} [1/5]: 双臂张爪(48°)预定位 — 左臂103对准黑色插头 / 中臂102对准三脚架底座",
                duration_s=1.2,
                left_103_deg=[-18.0, -4.0, 24.0, 0.0, 36.0, 0.0, 0.0],
                center_102_deg=[round(-22.0 + grasp_j0_d, 2), 8.0, 20.0, 0.0, 35.0, 0.0, 48.0],
                right_101_deg=[33.0, -45.0, 40.0, 0.0, 25.0, 0.0, 35.0],
            ),
            FleetWaypoint(
                phase_name=f"抓取+插装 Iter#{iteration+1} [2/5]: 下探贴合桌面 — 中臂102闭爪(1°)夹紧底座三脚架 + 左臂103卡位黑色插头",
                duration_s=1.3,
                left_103_deg=[-22.0, 18.5, 11.0, 0.0, 42.0, 0.0, 0.0],
                center_102_deg=[round(-22.5 + grasp_j0_d, 2), 28.8, 8.0, 0.0, 36.0, 0.0, 1.0],
                right_101_deg=[35.0, -44.0, 39.0, 0.0, 26.0, 0.0, 10.0],
            ),
            FleetWaypoint(
                phase_name=f"抓取+插装 Iter#{iteration+1} [3/5]: 提拉验证与转移 — 中臂102提起物体并旋向灰色五孔插座 (J0=+37.5°)",
                duration_s=1.3,
                left_103_deg=[-27.0, 22.0, 6.0, 0.0, 44.0, 0.0, 0.0],
                center_102_deg=[round(36.5 + sock_j0_d, 2), 12.0, 18.0, 0.0, 36.0, 0.0, 48.0],
                right_101_deg=[32.0, -46.0, 41.0, 0.0, 25.0, 0.0, 40.0],
            ),
            FleetWaypoint(
                phase_name=f"抓取+插装 Iter#{iteration+1} [4/5]: 耦合直线扫掠 — 左臂103推黑色插头对准插孔 + 中臂102下探锁定灰色插座",
                duration_s=1.4,
                left_103_deg=[round(-36.0 + sweep_extra, 2), 28.5, -4.0, 0.0, 49.0, 0.0, 0.0],
                center_102_deg=[round(37.5 + sock_j0_d, 2), 28.5, 2.0, 0.0, 42.0, 0.0, 48.0],
                right_101_deg=[36.0, -45.0, 40.0, 0.0, 25.0, 0.0, 15.0],
            ),
            FleetWaypoint(
                phase_name=f"抓取+插装 Iter#{iteration+1} [5/5]: 双臂协同对插压紧 (插进去!) — 中臂闭爪(1°)夹持插座向插头对插咬合",
                duration_s=1.4,
                left_103_deg=[round(-38.0 + sweep_extra, 2), 29.5, -6.0, 0.0, 50.0, 0.0, 0.0],
                center_102_deg=[round(41.5 + sock_j0_d, 2), 30.8, -1.0, 0.0, 44.0, 0.0, 1.0],
                right_101_deg=[34.0, -45.0, 40.0, 0.0, 25.0, 0.0, 30.0],
            ),
        ]

    if skill_name == "cloth_folding":
        return [
            FleetWaypoint(
                phase_name="叠衣 Step 1/5: 展开双臂并张开夹爪准备抓取左袖",
                duration_s=1.4,
                left_103_deg=[-26.0, -48.0, 45.0, -12.0, 24.0, 0.0, 48.0],
                center_102_deg=[-16.0, -52.0, 48.0, -8.0, 22.0, 0.0, 48.0],
                right_101_deg=[12.0, -68.0, 65.0, 0.0, 15.0, 0.0, 10.0],
            ),
            FleetWaypoint(
                phase_name="叠衣 Step 2/5: 夹爪闭合捏住左袖边缘并向中心翻折",
                duration_s=1.6,
                left_103_deg=[16.0, -56.0, 52.0, 18.0, 20.0, 0.0, 2.0],
                center_102_deg=[10.0, -58.0, 54.0, 12.0, 18.0, 0.0, 2.0],
                right_101_deg=[24.0, -48.0, 45.0, 12.0, 24.0, 0.0, 48.0],
            ),
            FleetWaypoint(
                phase_name="叠衣 Step 3/5: 左袖松开释放 + 右臂夹爪闭合抓取右袖向内翻折",
                duration_s=1.6,
                left_103_deg=[-10.0, -68.0, 65.0, 0.0, 15.0, 0.0, 48.0],
                center_102_deg=[-12.0, -56.0, 52.0, -14.0, 20.0, 0.0, 2.0],
                right_101_deg=[-18.0, -55.0, 50.0, -18.0, 20.0, 0.0, 2.0],
            ),
            FleetWaypoint(
                phase_name="叠衣 Step 4/5: 三臂协同前探抓取衣摆底部向上对折",
                duration_s=1.6,
                left_103_deg=[0.0, -40.0, 36.0, 0.0, 28.0, 0.0, 48.0],
                center_102_deg=[0.0, -38.0, 34.0, 0.0, 28.0, 0.0, 48.0],
                right_101_deg=[0.0, -40.0, 36.0, 0.0, 28.0, 0.0, 48.0],
            ),
            FleetWaypoint(
                phase_name="叠衣 Step 5/5: 闭爪对折收拢 + 横向抚平压实衣物",
                duration_s=1.6,
                left_103_deg=[14.0, -64.0, 60.0, 0.0, 14.0, 0.0, 0.0],
                center_102_deg=[14.0, -62.0, 58.0, 0.0, 14.0, 0.0, 0.0],
                right_101_deg=[14.0, -64.0, 60.0, 0.0, 14.0, 0.0, 0.0],
            ),
        ]

    if skill_name == "cyber_dance":
        return [
            FleetWaypoint(
                phase_name="舞蹈 Act 1/6: 序幕·三臂同步苏醒抬升 + 夹爪呼吸全开",
                duration_s=1.3,
                left_103_deg=[0.0, -52.0, 46.0, 0.0, 22.0, 0.0, 49.0],
                center_102_deg=[0.0, -46.0, 40.0, 0.0, 26.0, 0.0, 49.0],
                right_101_deg=[0.0, -52.0, 46.0, 0.0, 22.0, 0.0, 49.0],
            ),
            FleetWaypoint(
                phase_name="舞蹈 Act 2/6: 镜像双翼大展翅 + 中臂高举手腕翻花 + 夹爪合拍",
                duration_s=1.4,
                left_103_deg=[-30.0, -45.0, 40.0, -32.0, 25.0, 0.0, 2.0],
                center_102_deg=[0.0, -36.0, 30.0, 35.0, 30.0, 0.0, 49.0],
                right_101_deg=[30.0, -45.0, 40.0, 32.0, 25.0, 0.0, 2.0],
            ),
            FleetWaypoint(
                phase_name="舞蹈 Act 3/6: 人浪传递波峰 (左臂103浪峰张爪 → 中右蓄力)",
                duration_s=1.2,
                left_103_deg=[-22.0, -36.0, 30.0, -25.0, 28.0, 0.0, 50.0],
                center_102_deg=[0.0, -58.0, 54.0, 0.0, 16.0, 0.0, 5.0],
                right_101_deg=[18.0, -72.0, 68.0, 15.0, 10.0, 0.0, 0.0],
            ),
            FleetWaypoint(
                phase_name="舞蹈 Act 4/6: 人浪传递波峰 (中臂102浪峰张爪 → 右臂101接浪)",
                duration_s=1.2,
                left_103_deg=[-12.0, -70.0, 66.0, 0.0, 12.0, 0.0, 0.0],
                center_102_deg=[0.0, -35.0, 28.0, -30.0, 30.0, 0.0, 50.0],
                right_101_deg=[24.0, -38.0, 32.0, 28.0, 28.0, 0.0, 50.0],
            ),
            FleetWaypoint(
                phase_name="舞蹈 Act 5/6: 三臂齐舞左摆 + 夹爪节拍闭合拍手",
                duration_s=1.3,
                left_103_deg=[-26.0, -48.0, 42.0, -25.0, 22.0, 0.0, 0.0],
                center_102_deg=[-26.0, -48.0, 42.0, -25.0, 22.0, 0.0, 0.0],
                right_101_deg=[-26.0, -48.0, 42.0, -25.0, 22.0, 0.0, 0.0],
            ),
            FleetWaypoint(
                phase_name="舞蹈 Act 6/6: 三臂齐舞右摆 → 终章V字向心致敬 + 夹爪全开!",
                duration_s=1.4,
                left_103_deg=[24.0, -42.0, 36.0, 25.0, 28.0, 0.0, 50.0],
                center_102_deg=[0.0, -36.0, 30.0, 0.0, 32.0, 0.0, 50.0],
                right_101_deg=[-24.0, -42.0, 36.0, -25.0, 28.0, 0.0, 50.0],
            ),
        ]

    if skill_name == "gripper_workout":
        return [
            FleetWaypoint(
                phase_name="夹爪开合 1/4: 抬臂展示位 — 夹爪全开 (50°)",
                duration_s=1.1,
                left_103_deg=[-15.0, -52.0, 48.0, 0.0, 22.0, 0.0, 50.0],
                center_102_deg=[0.0, -50.0, 46.0, 0.0, 22.0, 0.0, 50.0],
                right_101_deg=[15.0, -52.0, 48.0, 0.0, 22.0, 0.0, 50.0],
            ),
            FleetWaypoint(
                phase_name="夹爪开合 2/4: 精准咬合位 — 夹爪完全闭合 (0°)",
                duration_s=1.0,
                left_103_deg=[-15.0, -52.0, 48.0, -20.0, 22.0, 0.0, 0.0],
                center_102_deg=[0.0, -50.0, 46.0, 20.0, 22.0, 0.0, 0.0],
                right_101_deg=[15.0, -52.0, 48.0, -20.0, 22.0, 0.0, 0.0],
            ),
            FleetWaypoint(
                phase_name="夹爪开合 3/4: 交叉偏航位 — 夹爪二次全开 (50°)",
                duration_s=1.1,
                left_103_deg=[18.0, -46.0, 42.0, 20.0, 26.0, 0.0, 50.0],
                center_102_deg=[0.0, -42.0, 38.0, -20.0, 26.0, 0.0, 50.0],
                right_101_deg=[-18.0, -46.0, 42.0, 20.0, 26.0, 0.0, 50.0],
            ),
            FleetWaypoint(
                phase_name="夹爪开合 4/4: 夹爪脉冲闭合 (0°) → 半开校验 (28°)",
                duration_s=1.0,
                left_103_deg=[0.0, -58.0, 54.0, 0.0, 18.0, 0.0, 28.0],
                center_102_deg=[0.0, -58.0, 54.0, 0.0, 18.0, 0.0, 28.0],
                right_101_deg=[0.0, -58.0, 54.0, 0.0, 18.0, 0.0, 28.0],
            ),
        ]

    if skill_name == "pick_relay":
        return [
            FleetWaypoint(
                phase_name="分拣接力 1/4: 左臂103下探左工位张爪取料 (48°→2°)",
                duration_s=1.4,
                left_103_deg=[-26.0, -44.0, 40.0, -15.0, 26.0, 0.0, 2.0],
                center_102_deg=[-16.0, -54.0, 50.0, 0.0, 20.0, 0.0, 48.0],
                right_101_deg=[0.0, -68.0, 64.0, 0.0, 12.0, 0.0, 48.0],
            ),
            FleetWaypoint(
                phase_name="分拣接力 2/4: 左臂103与中臂102空中交接 (103开爪/102闭爪)",
                duration_s=1.4,
                left_103_deg=[22.0, -50.0, 46.0, 20.0, 22.0, 0.0, 48.0],
                center_102_deg=[-20.0, -50.0, 46.0, -20.0, 22.0, 0.0, 2.0],
                right_101_deg=[-18.0, -54.0, 50.0, -15.0, 20.0, 0.0, 48.0],
            ),
            FleetWaypoint(
                phase_name="分拣接力 3/4: 中臂102与右臂101空中交接 (102开爪/101闭爪)",
                duration_s=1.4,
                left_103_deg=[0.0, -70.0, 66.0, 0.0, 12.0, 0.0, 0.0],
                center_102_deg=[22.0, -50.0, 46.0, 20.0, 22.0, 0.0, 48.0],
                right_101_deg=[-22.0, -50.0, 46.0, -20.0, 22.0, 0.0, 2.0],
            ),
            FleetWaypoint(
                phase_name="分拣接力 4/4: 右臂101旋转至右工位定点码放释放 (48°)",
                duration_s=1.4,
                left_103_deg=[0.0, -72.0, 68.0, 0.0, 10.0, 0.0, 0.0],
                center_102_deg=[0.0, -72.0, 68.0, 0.0, 10.0, 0.0, 0.0],
                right_101_deg=[28.0, -44.0, 40.0, 15.0, 26.0, 0.0, 48.0],
            ),
        ]

    # Default / surface_wipe
    return [
        FleetWaypoint(
            phase_name="桌面清扫 1/3: 三臂下探贴近桌面左侧起始线",
            duration_s=1.3,
            left_103_deg=[-24.0, -46.0, 42.0, 0.0, 26.0, 0.0, 0.0],
            center_102_deg=[-24.0, -46.0, 42.0, 0.0, 26.0, 0.0, 0.0],
            right_101_deg=[-24.0, -46.0, 42.0, 0.0, 26.0, 0.0, 0.0],
        ),
        FleetWaypoint(
            phase_name="桌面清扫 2/3: S形横向同步擦拭清扫至桌面右侧",
            duration_s=1.6,
            left_103_deg=[24.0, -46.0, 42.0, 0.0, 26.0, 0.0, 0.0],
            center_102_deg=[24.0, -46.0, 42.0, 0.0, 26.0, 0.0, 0.0],
            right_101_deg=[24.0, -46.0, 42.0, 0.0, 26.0, 0.0, 0.0],
        ),
        FleetWaypoint(
            phase_name="桌面清扫 3/3: 清扫完成回中归位预备",
            duration_s=1.3,
            left_103_deg=[0.0, -75.0, 72.0, 0.0, 10.0, 0.0, 0.0],
            center_102_deg=[0.0, -75.0, 72.0, 0.0, 10.0, 0.0, 0.0],
            right_101_deg=[0.0, -75.0, 72.0, 0.0, 10.0, 0.0, 0.0],
        ),
    ]


class D1CameraPerceptionModule(Module):
    """DimOS Perception Module publishing overhead camera & SAM3 segmentation streams."""

    def __init__(self) -> None:
        self.color_image: Out[dict[str, Any]] = Out("color_image")
        self.sam3_segmentation: Out[dict[str, Any]] = Out("sam3_segmentation")

    @rpc
    def publish_frame_meta(self, frame_info: dict[str, Any]) -> None:
        self.color_image.publish(frame_info)


class GeminiArgonAgentModule(Module):
    """DimOS Agentive Control Module powered by Gemini 4 Argon / gemini-flash-latest."""

    def __init__(self) -> None:
        self.color_image: In[dict[str, Any]] = In("color_image")
        self.skill_command: Out[dict[str, Any]] = Out("skill_command")
        self.color_image.subscribe(self._on_image)

    def _on_image(self, msg: dict[str, Any]) -> None:
        skill_name = msg.get("requested_skill", "cloth_folding")
        self.dispatch_skill(skill_name)

    @skill
    def dispatch_skill(self, skill_name: str, iteration: int = 0) -> dict[str, Any]:
        waypoints = build_skill_waypoints(skill_name, iteration=iteration)
        packet = {
            "skill_name": skill_name,
            "iteration": iteration,
            "waypoint_count": len(waypoints),
            "waypoints": waypoints,
            "timestamp": time.time(),
        }
        self.skill_command.publish(packet)
        return packet


class UnitreeD1FleetModule(Module):
    """DimOS Hardware Driver Module receiving skill streams and publishing 3-arm joint telemetry."""

    def __init__(self) -> None:
        self.skill_command: In[dict[str, Any]] = In("skill_command")
        self.joint_telemetry: Out[dict[str, Any]] = Out("joint_telemetry")
        self.executed_skills: list[str] = []
        self.skill_command.subscribe(self._on_skill)

    def _on_skill(self, cmd: dict[str, Any]) -> None:
        skill_name = str(cmd.get("skill_name", "cloth_folding"))
        self.executed_skills.append(skill_name)
        self.joint_telemetry.publish(
            {
                "active_skill": skill_name,
                "waypoint_count": cmd.get("waypoint_count", 0),
                "status": "STREAMING_TO_3_ARMS",
            }
        )


class BlueprintGraph:
    """Built DimOS Blueprint graph wiring Out[T] and In[T] streams by port name."""

    def __init__(self, modules: list[Module]) -> None:
        self.modules = modules
        self._wire()

    def _wire(self) -> None:
        outs: dict[str, list[Out[Any]]] = {}
        ins: dict[str, list[In[Any]]] = {}
        for mod in self.modules:
            for attr_name, val in vars(mod).items():
                if isinstance(val, Out):
                    key = val.name or attr_name
                    outs.setdefault(key, []).append(val)
                elif isinstance(val, In):
                    key = val.name or attr_name
                    ins.setdefault(key, []).append(val)
        for key, out_ports in outs.items():
            for in_port in ins.get(key, []):
                for out_port in out_ports:
                    out_port.subscribe(in_port._receive)

    def build(self) -> BlueprintGraph:
        return self


def autoconnect(*blueprints: ModuleBlueprint) -> BlueprintGraph:
    """DimOS declarative `autoconnect(...)` blueprint composer."""
    instances = [bp.module_cls(**bp.kwargs) for bp in blueprints]
    return BlueprintGraph(instances)


def interpolate_cosine(start_deg: list[float], end_deg: list[float], alpha: float) -> list[float]:
    """Smooth minimum-jerk / cosine interpolation between 7-DOF joint targets."""
    u = 0.5 * (1.0 - math.cos(math.pi * max(0.0, min(1.0, alpha))))
    return [round(s + u * (e - s), 2) for s, e in zip(start_deg, end_deg)]
