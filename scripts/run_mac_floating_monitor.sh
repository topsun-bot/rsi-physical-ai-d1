#!/usr/bin/env bash
# D1 独立实时监控浮窗 — 启动 / 重新编译 / 一键回退 / 退出
#   ./scripts/run_mac_floating_monitor.sh            # 启动（没编译过会自动编译）
#   ./scripts/run_mac_floating_monitor.sh --rebuild  # 重新编译后启动
#   ./scripts/run_mac_floating_monitor.sh --rollback # 一键回退到 v1.0.0-stable 稳定版并重新编译启动
#   ./scripts/run_mac_floating_monitor.sh --stop     # 退出
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_BUNDLE="${REPO_ROOT}/D1LiveMonitor.app"
PROC_PATTERN="D1LiveMonitor.app/Contents/MacOS/D1LiveMonitor"

if [[ "${1:-}" == "--stop" ]]; then
    if pkill -f "${PROC_PATTERN}" >/dev/null 2>&1; then
        echo "✅ 已退出 D1 实时监控浮窗"
    else
        echo "ℹ️  D1 实时监控浮窗没有在运行"
    fi
    exit 0
fi

if [[ "${1:-}" == "--rollback" ]]; then
    TARGET_TAG="${2:-v1.0.0-stable}"
    echo "⏪ 正在将 D1LiveMonitor & D1Clothesline 回退至版本标签 ${TARGET_TAG}..."
    git -C "${REPO_ROOT}" checkout "${TARGET_TAG}" -- \
        macos_clothesline/D1Clothesline.swift \
        macos_clothesline/build_app.sh \
        macos_clothesline/README.md \
        macos_monitor/D1LiveMonitor.swift \
        macos_monitor/build_app.sh
    "${REPO_ROOT}/macos_clothesline/build_app.sh"
    "${REPO_ROOT}/macos_monitor/build_app.sh"
    pkill -f "${PROC_PATTERN}" >/dev/null 2>&1 || true
    sleep 0.3
    if ! open -a "${APP_BUNDLE}" >/dev/null 2>&1; then
        nohup "${APP_BUNDLE}/Contents/MacOS/D1LiveMonitor" >/dev/null 2>&1 &
        disown || true
    fi
    echo "✅ 已回退至 ${TARGET_TAG} 并重新启动 D1 实时监控浮窗"
    exit 0
fi

REBUILD=0
if [[ "${1:-}" == "--rebuild" ]]; then REBUILD=1; shift; fi
if [[ $REBUILD -eq 1 ]] || [[ ! -x "${APP_BUNDLE}/Contents/MacOS/D1LiveMonitor" ]]; then
    "${REPO_ROOT}/macos_monitor/build_app.sh"
fi

# Stop any previous instance cleanly
pkill -f "${PROC_PATTERN}" >/dev/null 2>&1 || true
sleep 0.6

if ! open -n -a "${APP_BUNDLE}" --args "$@" >/dev/null 2>&1; then
    nohup "${APP_BUNDLE}/Contents/MacOS/D1LiveMonitor" "$@" >/dev/null 2>&1 &
    disown || true
fi
echo "✅ Launched ${APP_BUNDLE} (Always-on-top draggable floating RTSP + Insta360 monitor)"
