#!/usr/bin/env bash
# D1 视频晾衣绳 — 启动 / 退出
#   ./scripts/run_mac_clothesline.sh            # 启动（没编译过会自动编译）
#   ./scripts/run_mac_clothesline.sh --rebuild  # 重新编译后启动
#   ./scripts/run_mac_clothesline.sh --rollback # 一键回退到 v1.0.0-stable 稳定版并重新编译启动
#   ./scripts/run_mac_clothesline.sh --stop     # 退出
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_BUNDLE="${REPO_ROOT}/D1Clothesline.app"
PROC_PATTERN="D1Clothesline.app/Contents/MacOS/D1Clothesline"

if [[ "${1:-}" == "--stop" ]]; then
    if pkill -f "${PROC_PATTERN}" >/dev/null 2>&1; then
        echo "✅ 已退出 D1 视频晾衣绳"
    else
        echo "ℹ️  D1 视频晾衣绳没有在运行"
    fi
    exit 0
fi

if [[ "${1:-}" == "--rollback" ]]; then
    TARGET_TAG="${2:-v1.0.0-stable}"
    echo "⏪ 正在将 D1Clothesline & D1LiveMonitor 回退至稳定标签 ${TARGET_TAG}..."
    git -C "${REPO_ROOT}" checkout "${TARGET_TAG}" -- \
        macos_clothesline/D1Clothesline.swift \
        macos_clothesline/build_app.sh \
        macos_clothesline/README.md \
        macos_monitor/D1LiveMonitor.swift \
        macos_monitor/build_app.sh \
        scripts/run_mac_floating_monitor.sh
    "${REPO_ROOT}/macos_clothesline/build_app.sh"
    "${REPO_ROOT}/macos_monitor/build_app.sh"
    pkill -f "${PROC_PATTERN}" >/dev/null 2>&1 || true
    sleep 0.3
    open -a "${APP_BUNDLE}"
    echo "✅ 已回退至 ${TARGET_TAG} 并重新启动 D1 视频晾衣绳"
    exit 0
fi

REBUILD=0
if [[ "${1:-}" == "--rebuild" ]]; then REBUILD=1; shift; fi
if [[ $REBUILD -eq 1 ]] || [[ ! -x "${APP_BUNDLE}/Contents/MacOS/D1Clothesline" ]]; then
    "${REPO_ROOT}/macos_clothesline/build_app.sh"
fi

pkill -f "${PROC_PATTERN}" >/dev/null 2>&1 || true
sleep 0.3
open -a "${APP_BUNDLE}" --args "$@"
echo "✅ 已启动 D1 视频晾衣绳 — 鼠标停在屏幕顶部菜单栏（或按 ⌃⌥D）放下晾衣绳；退出：$0 --stop"
