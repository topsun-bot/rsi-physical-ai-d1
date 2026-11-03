#!/usr/bin/env bash
# Build D1Clothesline.app — live D1 camera feeds hung on a line under the menu bar.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_BUNDLE="${REPO_ROOT}/D1Clothesline.app"
CONTENTS_DIR="${APP_BUNDLE}/Contents"
MACOS_DIR="${CONTENTS_DIR}/MacOS"

echo "[1/3] Creating ${APP_BUNDLE}..."
rm -rf "${APP_BUNDLE}"
mkdir -p "${MACOS_DIR}" "${CONTENTS_DIR}/Resources"

cat > "${CONTENTS_DIR}/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>zh_CN</string>
    <key>CFBundleExecutable</key>
    <string>D1Clothesline</string>
    <key>CFBundleIdentifier</key>
    <string>com.huazhijian.topsun.d1clothesline</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>D1视频晾衣绳</string>
    <key>CFBundleDisplayName</key>
    <string>桦之坚 D1 视频晾衣绳</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSMinimumSystemVersion</key>
    <string>13.0</string>
    <key>LSUIElement</key>
    <true/>
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

echo "[2/3] Compiling D1Clothesline.swift..."
/usr/bin/swiftc -O \
    -framework Cocoa -framework Carbon -framework QuartzCore -framework AVFoundation \
    "${SCRIPT_DIR}/D1Clothesline.swift" \
    -o "${MACOS_DIR}/D1Clothesline"

echo "[3/3] Ad-hoc signing..."
codesign --force --deep --sign - "${APP_BUNDLE}" >/dev/null 2>&1 || true
echo "✅ Built ${APP_BUNDLE}"
