#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_BUNDLE="${REPO_ROOT}/D1LiveMonitor.app"

if [[ "${1:-}" == "--rebuild" ]] || [[ ! -x "${APP_BUNDLE}/Contents/MacOS/D1LiveMonitor" ]]; then
    "${REPO_ROOT}/macos_monitor/build_app.sh"
fi

# Stop any previous instance cleanly
pkill -f "D1LiveMonitor.app/Contents/MacOS/D1LiveMonitor" >/dev/null 2>&1 || true
sleep 0.2

open -a "${APP_BUNDLE}"
echo "✅ Launched ${APP_BUNDLE} (Always-on-top draggable floating RTSP monitor)"
