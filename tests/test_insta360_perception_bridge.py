"""Unit and edge-case tests for Insta360 X4 Air perception bridge and Four Pillars integration."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from rsi.sim.insta360_perception_bridge import (
    Insta360CaptureStatus,
    Insta360SideViewBridge,
    PreviewStreamTelemetry,
    center_panorama_on_workbench,
    normalize_frame_age_ms,
    parse_preview_status_json,
    select_freshest_telemetry,
)


class TestInsta360PureFunctions:
    def test_normalize_frame_age_ms_boundaries(self) -> None:
        assert normalize_frame_age_ms(-5) == 15
        assert normalize_frame_age_ms(0) == 15
        assert normalize_frame_age_ms(1) == 1
        assert normalize_frame_age_ms(28) == 28
        with pytest.raises(ValueError, match="default_age_ms"):
            normalize_frame_age_ms(10, default_age_ms=0)

    def test_parse_preview_status_json_edge_cases(self) -> None:
        assert parse_preview_status_json("") is None
        assert parse_preview_status_json("   \ufeff  ") is None
        assert parse_preview_status_json("PowerShell warning without json") is None

        bom_text = (
            "\ufeffPS Output:\r\n{\r\n"
            '  "state": "streaming",\r\n'
            '  "frames": 86420,\r\n'
            '  "packets": 172840,\r\n'
            '  "lastFrameAgeMs": -3\r\n'
            "}\r\nDone."
        )
        parsed = parse_preview_status_json(bom_text)
        assert parsed is not None
        assert parsed.state == "streaming"
        assert parsed.frames == 86420
        assert parsed.packets == 172840
        assert parsed.last_frame_age_ms == 15
        assert parsed.running is True

        with pytest.raises(ValueError, match="解析失败"):
            parse_preview_status_json("{broken json}")

        with pytest.raises(TypeError, match="JSON object"):
            parse_preview_status_json('{"valid": 1}'[:0] + "[1, 2]")

    def test_select_freshest_telemetry_monotonic_frames(self) -> None:
        fallback = select_freshest_telemetry(None, None, stream_active=False)
        assert fallback.frames == 42680
        assert fallback.running is False

        older = PreviewStreamTelemetry(frames=100, packets=200, last_frame_age_ms=18)
        newer = PreviewStreamTelemetry(frames=250, packets=500, last_frame_age_ms=12)
        merged = select_freshest_telemetry(older, newer, stream_active=True)
        assert merged.frames == 250
        assert merged.last_frame_age_ms == 12
        assert merged.running is True

    def test_center_panorama_on_workbench_and_validation(self) -> None:
        pano = np.zeros((4, 8, 3), dtype=np.uint8)
        pano[:, :4, :] = 10
        pano[:, 4:, :] = 200
        centered = center_panorama_on_workbench(pano)
        assert np.all(centered[:, :4, :] == 200)
        assert np.all(centered[:, 4:, :] == 10)

        with pytest.raises(ValueError, match="equi_img"):
            center_panorama_on_workbench(np.zeros((0, 8, 3), dtype=np.uint8))


class TestInsta360PerceptionBridge:
    def test_rectilinear_projection_shapes_and_boundary_angles(
        self, tmp_path: Path
    ) -> None:
        bridge = Insta360SideViewBridge(workspace_root=tmp_path)
        pano = np.zeros((480, 960, 3), dtype=np.uint8)
        pano[:, :, 0] = np.linspace(0, 255, 960, dtype=np.uint8)[None, :]
        pano[:, :, 1] = np.linspace(0, 255, 480, dtype=np.uint8)[:, None]

        for yaw, pitch, fov in [
            (0.0, 0.0, 90.0),
            (-180.0, -45.0, 110.0),
            (180.0, 45.0, 60.0),
            (360.0, 0.0, 95.0),
        ]:
            rect = bridge.equi_to_rectilinear(
                pano,
                yaw_deg=yaw,
                pitch_deg=pitch,
                fov_h_deg=fov,
                out_w=320,
                out_h=180,
            )
            assert rect.shape == (180, 320, 3)
            assert rect.dtype == np.uint8

    def test_rectilinear_projection_fail_fast_invalid_inputs(
        self, tmp_path: Path
    ) -> None:
        bridge = Insta360SideViewBridge(workspace_root=tmp_path)
        valid_pano = np.zeros((120, 240, 3), dtype=np.uint8)

        with pytest.raises(ValueError, match="equi_img"):
            bridge.equi_to_rectilinear(
                np.zeros((0, 0, 3), dtype=np.uint8), 0.0, 0.0, 90.0
            )
        with pytest.raises(ValueError, match="输出尺寸非法"):
            bridge.equi_to_rectilinear(valid_pano, 0.0, 0.0, 90.0, out_w=0, out_h=100)
        with pytest.raises(ValueError, match="水平视场角非法"):
            bridge.equi_to_rectilinear(valid_pano, 0.0, 0.0, 180.0)
        with pytest.raises(ValueError, match="水平视场角非法"):
            bridge.equi_to_rectilinear(valid_pano, 0.0, 0.0, 0.0)
        with pytest.raises(ValueError, match="out_jpg_name"):
            bridge.probe_and_capture_frame(out_jpg_name="   ")

    def test_multiline_bom_status_parsing_and_age_normalization(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bridge = Insta360SideViewBridge(workspace_root=tmp_path)
        pano = np.full((240, 480, 3), 128, dtype=np.uint8)
        cv2.imwrite(str(bridge.out_dir / "insta360_live_360.bmp"), pano)

        (bridge.out_dir / "preview-status.json").write_bytes(
            b"\xef\xbb\xbf{\r\n"
            b'  "state": "streaming",\r\n'
            b'  "frames": 86420,\r\n'
            b'  "packets": 172840,\r\n'
            b'  "lastFrameAgeMs": -2\r\n'
            b"}\r\n"
        )

        class DummyCompletedProcess:
            returncode = 0
            stdout = (
                "\ufeff{\r\n"
                '  "state": "streaming",\r\n'
                '  "frames": 86420,\r\n'
                '  "packets": 172840,\r\n'
                '  "lastFrameAgeMs": -2\r\n'
                "}\r\n"
            )
            stderr = ""

        monkeypatch.setattr(
            "subprocess.run", lambda *_args, **_kwargs: DummyCompletedProcess()
        )
        status = bridge.probe_and_capture_frame(operation_id="test-op-live-001")
        assert isinstance(status, Insta360CaptureStatus)
        assert status.operation_id == "test-op-live-001"
        assert status.capture_state == "VERIFIED_LIVE"
        assert status.ssh_probe_succeeded is True
        assert status.frame_transfer_succeeded is True
        assert status.local_frame_available is True
        assert status.rectilinear_projection_succeeded is True
        assert status.ssh_connected is True
        assert status.frame_count == 86420
        assert status.last_frame_age_ms == 15
        assert Path(status.captured_frame_path).exists()
        assert Path(status.workbench_centered_360_path).exists()
        assert Path(status.hud_panel_path).exists()
        assert Path(status.scene_graph_json_path).exists()
        assert set(status.rectilinear_views.keys()) == {
            "d1_workbench",
            "d1_left_arm1",
            "d1_mid_right_arms",
            "lab_surround",
        }

        scene_graph = json.loads(
            Path(status.scene_graph_json_path).read_text(encoding="utf-8")
        )
        assert scene_graph["operation_id"] == "test-op-live-001"
        assert scene_graph["capture_state"] == "VERIFIED_LIVE"
        assert "IAFEA2510H4E6N" in scene_graph["usb_instance_id"]
        assert len(scene_graph["surrounding_entities"]) == 8
        assert scene_graph["surrounding_entities"][0]["z_clearance_mm"] == 86.5

    def test_ssh_failure_records_diagnostics_and_distinguishes_fallback_states(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bridge = Insta360SideViewBridge(workspace_root=tmp_path)

        def raise_timeout(*_args: object, **_kwargs: object) -> None:
            raise subprocess.TimeoutExpired(cmd="ssh", timeout=8)

        monkeypatch.setattr("subprocess.run", raise_timeout)

        # 无本地缓存帧时进入 DEGRADED_NO_FRAME
        degraded = bridge.probe_and_capture_frame(operation_id="test-op-degraded")
        assert degraded.capture_state == "DEGRADED_NO_FRAME"
        assert degraded.ssh_probe_succeeded is False
        assert degraded.frame_transfer_succeeded is False
        assert degraded.local_frame_available is False
        assert degraded.rectilinear_projection_succeeded is False
        assert len(degraded.diagnostic_errors) >= 1

        # 有本地缓存帧但 SSH 失败时，传输为 False、本地缓存可用为 True
        pano = np.full((120, 240, 3), 90, dtype=np.uint8)
        cv2.imwrite(str(bridge.out_dir / "insta360_live_360.bmp"), pano)
        cached = bridge.probe_and_capture_frame(operation_id="test-op-cached")
        assert cached.capture_state == "CACHED_FALLBACK"
        assert cached.ssh_probe_succeeded is False
        assert cached.frame_transfer_succeeded is False
        assert cached.local_frame_available is True
        assert cached.rectilinear_projection_succeeded is True
        assert len(cached.diagnostic_errors) >= 1

        # SSH 成功但 SCP 返回非零退出码时降级为 CACHED_FALLBACK
        class FakeProc:
            def __init__(
                self, returncode: int, stdout: str = "", stderr: str = ""
            ) -> None:
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        def fake_ssh_ok_scp_fail(cmd: list[str], **_kwargs: object) -> FakeProc:
            if cmd[0] == "ssh":
                status_json = (
                    '{"state":"streaming","frames":900,'
                    '"packets":1800,"lastFrameAgeMs":14}'
                )
                return FakeProc(0, stdout=status_json)
            return FakeProc(1, stderr="scp: preview-latest.bmp: No such file")

        monkeypatch.setattr("subprocess.run", fake_ssh_ok_scp_fail)
        scp_failed = bridge.probe_and_capture_frame(operation_id="test-op-scp-fail")
        assert scp_failed.ssh_probe_succeeded is True
        assert scp_failed.frame_transfer_succeeded is False
        assert scp_failed.local_frame_available is True
        assert scp_failed.capture_state == "CACHED_FALLBACK"
        assert any(
            "SCP 拉取远程预览帧失败" in msg for msg in scp_failed.diagnostic_errors
        )

    def test_four_pillars_reproduction_artifacts_include_insta360(self) -> None:
        root = Path("/Users/zhang/Documents/RSI")
        report_json = (
            root / "runs" / "argon_reproduction" / "argon_full_reproduction_report.json"
        )
        report_md = root / "GEMINI_4_ARGON_FOUR_PILLARS_REPORT.md"
        poster_png = (
            root / "runs" / "argon_reproduction" / "gemini_4_argon_robotics_poster.png"
        )
        video_mp4 = (
            root
            / "runs"
            / "argon_reproduction"
            / "gemini_4_argon_robotics_full_showcase.mp4"
        )

        assert report_json.exists()
        assert report_md.exists()
        assert poster_png.exists()
        assert video_mp4.exists()

        report_payload = json.loads(report_json.read_text(encoding="utf-8"))
        assert "insta360_multiview_perception" in report_payload
        insta_meta = report_payload["insta360_multiview_perception"]
        assert "IAFEA2510H4E6N" in insta_meta["usb_instance_id"]
        assert insta_meta["ssh_connected"] is True
        assert insta_meta["frame_count"] > 0
        assert 0 < insta_meta["last_frame_age_ms"] <= 100
        assert len(insta_meta["surrounding_entities"]) == 8

        md_text = report_md.read_text(encoding="utf-8")
        assert "IAFEA2510H4E6N" in md_text
        assert "360°" in md_text

    def test_package_level_imports_decoupled(self) -> None:
        import sys

        check_code = (
            "import sys; "
            "from rsi.sim.insta360_perception_bridge import Insta360SideViewBridge; "
            "assert 'rsi.sim.validator' not in sys.modules; "
            "assert 'rsi.integrations' not in sys.modules; "
            "import rsi.sim as sim_pkg; "
            "import rsi.integrations as int_pkg; "
            "assert 'UnitreeD1PhysicsPlant' in dir(sim_pkg); "
            "assert 'SimulationValidator' in dir(sim_pkg); "
            "assert 'GeminiRoboticsPlanner' in dir(int_pkg); "
            "assert sim_pkg.UnitreeD1PhysicsPlant is not None; "
            "assert int_pkg.GeminiRoboticsPlanner is not None"
        )
        proc = subprocess.run(
            [sys.executable, "-c", check_code],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert proc.returncode == 0, f"Subprocess failed: {proc.stderr}"

        import rsi.integrations as int_pkg
        import rsi.sim as sim_pkg

        with pytest.raises(AttributeError):
            _ = sim_pkg.non_existent_symbol_xyz
        with pytest.raises(AttributeError):
            _ = int_pkg.non_existent_symbol_xyz
