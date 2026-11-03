#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_BUNDLE="${REPO_ROOT}/D1LiveMonitor.app"
CONTENTS_DIR="${APP_BUNDLE}/Contents"
MACOS_DIR="${CONTENTS_DIR}/MacOS"
RESOURCES_DIR="${CONTENTS_DIR}/Resources"

echo "[1/3] Creating macOS .app bundle structure at ${APP_BUNDLE}..."
rm -rf "${APP_BUNDLE}"
mkdir -p "${MACOS_DIR}" "${RESOURCES_DIR}"

cat > "${CONTENTS_DIR}/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>zh_CN</string>
    <key>CFBundleExecutable</key>
    <string>D1LiveMonitor</string>
    <key>CFBundleIdentifier</key>
    <string>com.huazhijian.topsun.d1livemonitor</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>D1实时监控浮窗</string>
    <key>CFBundleDisplayName</key>
    <string>桦之坚 D1 实时监控浮窗</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSMinimumSystemVersion</key>
    <string>13.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsArbitraryLoads</key>
        <true/>
    </dict>
</dict>
</plist>
PLIST

echo -n "APPL????" > "${CONTENTS_DIR}/PkgInfo"

if [[ -f "${REPO_ROOT}/assets/branding/huazhijian_logo_white.png" ]]; then
    cp "${REPO_ROOT}/assets/branding/huazhijian_logo_white.png" "${RESOURCES_DIR}/"
fi

echo "[2/3] Compiling native Swift/AppKit binary D1LiveMonitor..."
/usr/bin/swiftc -O \
    -framework Cocoa \
    -framework Foundation \
    "${SCRIPT_DIR}/D1LiveMonitor.swift" \
    -o "${MACOS_DIR}/D1LiveMonitor"

chmod +x "${MACOS_DIR}/D1LiveMonitor"

echo "[3/3] Signing ad-hoc bundle for macOS..."
codesign --force --deep --sign - "${APP_BUNDLE}" >/dev/null 2>&1 || true

echo "✅ Built native macOS floating monitor app: ${APP_BUNDLE}"
