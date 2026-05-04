#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="CodeAgents Services"
INSTALL_DIR="${CODEAGENTS_APP_INSTALL_DIR:-/Applications}"
APP_BUNDLE="$INSTALL_DIR/$APP_NAME.app"
INSTALL_ROOT="${CODEAGENTS_APP_ROOT:-$ROOT_DIR}"
BUILD_DIR="$ROOT_DIR/build/app"

cd "$ROOT_DIR"

if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install -e . >/dev/null
cargo build --release -p codeagents-terminal --bin ca-services

rm -rf "$APP_BUNDLE" "$BUILD_DIR"
mkdir -p "$BUILD_DIR" "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources"

cp "$ROOT_DIR/target/release/ca-services" "$APP_BUNDLE/Contents/Resources/ca-services"

swiftc \
  "$ROOT_DIR/scripts/macos/CodeAgentsServicesApp.swift" \
  -o "$APP_BUNDLE/Contents/MacOS/CodeAgentsServices" \
  -framework Cocoa

ICON_PNG="$BUILD_DIR/icon.png"
ICONSET_DIR="$BUILD_DIR/AppIcon.iconset"
ICNS_PATH="$APP_BUNDLE/Contents/Resources/AppIcon.icns"

mkdir -p "$ICONSET_DIR"

swift "$ROOT_DIR/scripts/macos/make_icon.swift" "$ICON_PNG"

for spec in \
  "16,icon_16x16.png" \
  "32,icon_16x16@2x.png" \
  "32,icon_32x32.png" \
  "64,icon_32x32@2x.png" \
  "128,icon_128x128.png" \
  "256,icon_128x128@2x.png" \
  "256,icon_256x256.png" \
  "512,icon_256x256@2x.png" \
  "512,icon_512x512.png" \
  "1024,icon_512x512@2x.png"
do
  size="${spec%%,*}"
  name="${spec##*,}"
  sips -z "$size" "$size" "$ICON_PNG" --out "$ICONSET_DIR/$name" >/dev/null
done

iconutil -c icns "$ICONSET_DIR" -o "$ICNS_PATH"

cat > "$APP_BUNDLE/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>$APP_NAME</string>
  <key>CFBundleDisplayName</key>
  <string>$APP_NAME</string>
  <key>CFBundleIdentifier</key>
  <string>local.codeagents.services</string>
  <key>CFBundleVersion</key>
  <string>0.1.0</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.0</string>
  <key>CFBundleExecutable</key>
  <string>CodeAgentsServices</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>CodeAgentsRoot</key>
  <string>$INSTALL_ROOT</string>
</dict>
</plist>
EOF

xattr -dr com.apple.quarantine "$APP_BUNDLE" 2>/dev/null || true
touch "$APP_BUNDLE"

cat <<EOF
Installed: $APP_BUNDLE

Launch options:
  - Spotlight (Cmd+Space): "CodeAgents Services"
  - Launchpad: search "CodeAgents Services"
  - Terminal: open -a "$APP_NAME"

Closing the app stops Ollama and the CodeAgents API.
EOF
