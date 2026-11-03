"""Tests for macOS D1Clothesline Notch hover state machine and D1LiveMonitor self-test."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLOTHESLINE_SWIFT_SRC = REPO_ROOT / "macos_clothesline" / "D1Clothesline.swift"
CLOTHESLINE_BUILD_SCRIPT = REPO_ROOT / "macos_clothesline" / "build_app.sh"
CLOTHESLINE_APP_BIN = (
    REPO_ROOT / "D1Clothesline.app" / "Contents" / "MacOS" / "D1Clothesline"
)

MONITOR_SWIFT_SRC = REPO_ROOT / "macos_monitor" / "D1LiveMonitor.swift"
MONITOR_BUILD_SCRIPT = REPO_ROOT / "macos_monitor" / "build_app.sh"
MONITOR_APP_BIN = (
    REPO_ROOT / "D1LiveMonitor.app" / "Contents" / "MacOS" / "D1LiveMonitor"
)


def test_d1_clothesline_source_contains_notch_geometry_and_state_machine() -> None:
    text = CLOTHESLINE_SWIFT_SRC.read_text(encoding="utf-8")
    assert "struct TopHoverGeometry" in text
    assert "typealias NotchHoverGeometry = TopHoverGeometry" in text
    assert "struct ClotheslineHoverState: Equatable" in text
    assert "struct ClotheslineHoverController" in text
    assert "static func pointInRectInclusive" in text
    assert "static func notchHotZone" in text
    assert "static func isInNotchZone" in text
    assert "func pollMouse(" in text
    assert "func handleScreenParametersChanged()" in text
    assert "var firstCollapseArmed: Bool" in text
    assert "struct Insta360SceneGraphParser" in text
    assert "beginActivity(" in text
    assert "func watchMouseMovement()" in text
    assert "SIGTSTP" in text
    assert "launchDateKey" not in text
    assert "static var action:" not in text
    assert "private func px(_ x:" not in text

    monitor_text = MONITOR_SWIFT_SRC.read_text(encoding="utf-8")
    assert "static func writeData(\n        _ payloadBytes: Data" in monitor_text
    assert "jsonData: Data" in monitor_text


def test_d1_clothesline_binary_self_test_passes() -> None:
    subprocess.run([str(CLOTHESLINE_BUILD_SCRIPT)], cwd=str(REPO_ROOT), check=True)
    proc = subprocess.run(
        [str(CLOTHESLINE_APP_BIN), "--self-test"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0, (
        f"Self-test failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    assert "Self-test summary: ALL PASSED" in proc.stdout
    assert (
        "Cursor clamped at Notch bottom boundary y == visibleFrame.maxY" in proc.stdout
    )
    assert (
        "Hovering at Notch bottom boundary (864, 1083.0) for 0.09s reveals clothesline"
        in proc.stdout
    )
    assert (
        "ClotheslineHoverController shortens wakeup when entering Notch" in proc.stdout
    )


def test_d1_live_monitor_binary_self_test_passes() -> None:
    subprocess.run([str(MONITOR_BUILD_SCRIPT)], cwd=str(REPO_ROOT), check=True)
    proc = subprocess.run(
        [str(MONITOR_APP_BIN), "--self-test"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0, (
        f"Monitor self-test failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    assert "Self-test summary: ALL PASSED" in proc.stdout


def test_no_sigstop_sent_to_macos_clothesline_or_monitor() -> None:
    engine_path = REPO_ROOT / "rsi" / "argon_12demos" / "real_hardware_engine.py"
    if engine_path.exists():
        engine_source = engine_path.read_text(encoding="utf-8")
        assert '"D1Clothesline"' not in engine_source
        assert '"D1LiveMonitor"' not in engine_source


def test_swift_sources_have_short_comments_and_no_single_letter_bindings() -> None:
    import re

    forbidden_abbreviations = (
        r"\b(?:let|var)\s+(?:cg|t0|t1|t2|vis|src|cfg|rep|cyc|txt|str|buf|sem|err|req|fmt|btn)\b"
    )
    for swift_file in (CLOTHESLINE_SWIFT_SRC, MONITOR_SWIFT_SRC):
        source = swift_file.read_text(encoding="utf-8")
        for line_no, raw_line in enumerate(source.splitlines(), 1):
            line_no_strings = re.sub(r"\"(?:\\.|[^\"\\])*\"", '""', raw_line)
            comment_match = re.search(r"//+(.*)", line_no_strings)
            if comment_match:
                comment_body = comment_match.group(1).strip()
                assert not comment_body.startswith("MARK:"), (
                    f"{swift_file.name}:{line_no} contains section-divider comment: "
                    f"{comment_body!r}"
                )
                assert len(comment_body) <= 20, (
                    f"{swift_file.name}:{line_no} comment exceeds 20 chars "
                    f"({len(comment_body)}): {comment_body!r}"
                )
            assert re.search(r"\b(?:let|var)\s+[a-zA-Z]\b", raw_line) is None, (
                f"{swift_file.name}:{line_no} has single-letter binding: {raw_line}"
            )
            assert re.search(r"\bfor\s+[a-zA-Z]\s+in\b", raw_line) is None, (
                f"{swift_file.name}:{line_no} has single-letter loop binding: {raw_line}"
            )
            assert (
                re.search(r"[_a-zA-Z0-9]+\s+[a-zA-Z]\s*:\s*[A-Z]", raw_line) is None
            ), f"{swift_file.name}:{line_no} has single-letter parameter: {raw_line}"
            assert (
                re.search(r"func\s+[a-zA-Z0-9_]+\(\s*[a-zA-Z]\s*:", raw_line) is None
            ), f"{swift_file.name}:{line_no} has unlabeled single-letter parameter: {raw_line}"
            assert re.search(forbidden_abbreviations, raw_line) is None, (
                f"{swift_file.name}:{line_no} has meaningless abbreviation: {raw_line}"
            )

