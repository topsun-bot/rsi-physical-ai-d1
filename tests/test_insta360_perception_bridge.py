"""Unit and edge-case tests for Insta360 X4 Air perception bridge and Four Pillars integration."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from rsi.sim.insta360_perception_bridge import (
    Insta360CaptureStatus,
    Insta360SideViewBridge,
)


class TestInsta360PerceptionBridge:
    def test_rectilinear_projection_shapes_and_boundary_angles(self, tmp_path: Path) -> None:
        bridge = Insta360SideViewBridge(workspace_root=tmp_path)
        pano = np.zeros((480, 960, 3), dtype=np.uint8)
        pano[:, :, 0] = np.linspace(0, 255, 960, dtype=np.uint8)[None, :]
        pano[:, :, 1] = np.linspace(0, 255, 480, dtype=np.uint8)[:, None]

        for yaw, pitch, fov in [(0.0, 0.0, 90.0), (-180.0, -45.0, 110.0), (180.0, 45.0, 60.0), (360.0, 0.0, 95.0)]:
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

    def test_multiline_bom_status_parsing_and_age_normalization(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        bridge = Insta360SideViewBridge(workspace_root=tmp_path)
        pano = np.full((240, 480, 3), 128, dtype=np.uint8)
        cv2.imwrite(str(bridge.out_dir / "insta360_live_360.bmp"), pano)

        # Write a BOM + CRLF multiline preview-status.json with negative lastFrameAgeMs to test normalization
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

        monkeypatch.setattr("subprocess.run", lambda *a, **kw: DummyCompletedProcess())
        status = bridge.probe_and_capture_frame()
        assert isinstance(status, Insta360CaptureStatus)
        assert status.ssh_connected is True
        assert status.frame_count == 86420
        assert status.last_frame_age_ms == 15  # negative age normalized to 15ms
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

        sg = json.loads(Path(status.scene_graph_json_path).read_text(encoding="utf-8"))
        assert "IAFEA2510H4E6N" in sg["usb_instance_id"]
        assert len(sg["surrounding_entities"]) == 8
        assert sg["surrounding_entities"][0]["z_clearance_mm"] == 86.5

    def test_four_pillars_reproduction_artifacts_include_insta360(self) -> None:
        root = Path("/Users/zhang/Documents/RSI")
        report_json = root / "runs" / "argon_reproduction" / "argon_full_reproduction_report.json"
        report_md = root / "GEMINI_4_ARGON_FOUR_PILLARS_REPORT.md"
        poster_png = root / "runs" / "argon_reproduction" / "gemini_4_argon_robotics_poster.png"
        video_mp4 = root / "runs" / "argon_reproduction" / "gemini_4_argon_robotics_full_showcase.mp4"

        assert report_json.exists()
        assert report_md.exists()
        assert poster_png.exists()
        assert video_mp4.exists()

        data = json.loads(report_json.read_text(encoding="utf-8"))
        assert "insta360_multiview_perception" in data
        insta_meta = data["insta360_multiview_perception"]
        assert "IAFEA2510H4E6N" in insta_meta["usb_instance_id"]
        assert insta_meta["ssh_connected"] is True
        assert insta_meta["frame_count"] > 0
        assert 0 < insta_meta["last_frame_age_ms"] <= 100
        assert len(insta_meta["surrounding_entities"]) == 8

        md_text = report_md.read_text(encoding="utf-8")
        assert "IAFEA2510H4E6N" in md_text
        assert "360°" in md_text
