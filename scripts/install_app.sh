#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="CodeAgents"
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

(
  cd "$ROOT_DIR/gui"
  npm install >/dev/null
  npm run build
)

rm -rf "$APP_BUNDLE" "$BUILD_DIR"
mkdir -p "$BUILD_DIR" "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources/gui"

cp "$ROOT_DIR/target/release/ca-services" "$APP_BUNDLE/Contents/Resources/ca-services"
cp -R "$ROOT_DIR/gui/dist/"* "$APP_BUNDLE/Contents/Resources/gui/"

swiftc \
  "$ROOT_DIR/scripts/macos/CodeAgentsApp.swift" \
  -o "$APP_BUNDLE/Contents/MacOS/CodeAgents" \
  -framework Cocoa \
  -framework WebKit

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
  <string>local.codeagents.app</string>
  <key>CFBundleVersion</key>
  <string>0.1.0</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.0</string>
  <key>CFBundleExecutable</key>
  <string>CodeAgents</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <!-- Pre-fill the prompt strings so the user understands why CodeAgents
       wants access. Without these macOS shows a generic "wants to access X"
       dialog and most users deny by reflex. -->
  <key>NSDocumentsFolderUsageDescription</key>
  <string>CodeAgents reads files from your Documents folder only when you ask the agent to work there.</string>
  <key>NSDownloadsFolderUsageDescription</key>
  <string>CodeAgents opens files from Downloads only when you attach them to a chat.</string>
  <key>NSDesktopFolderUsageDescription</key>
  <string>CodeAgents reads files from the Desktop only when you ask the agent to work there.</string>
  <key>NSAppleEventsUsageDescription</key>
  <string>CodeAgents needs Apple Events to start and stop its local services.</string>
  <key>NSLocalNetworkUsageDescription</key>
  <string>CodeAgents talks to its local API and Ollama on 127.0.0.1.</string>
  <key>CodeAgentsRoot</key>
  <string>$INSTALL_ROOT</string>
</dict>
</plist>
EOF

# Strip quarantine and apply an ad-hoc code signature. The signature gives
# the bundle a stable TCC identity so reinstalls don't lose previously
# granted permissions and don't re-trigger the entire prompt chain.
xattr -dr com.apple.quarantine "$APP_BUNDLE" 2>/dev/null || true
codesign --force --deep --sign - "$APP_BUNDLE" >/dev/null 2>&1 || true
touch "$APP_BUNDLE"

cat <<EOF
Установлено: $APP_BUNDLE

Запуск:
  - Spotlight: «CodeAgents»
  - Finder → Программы → CodeAgents
  - Терминал: open -a CodeAgents

Приложение само поднимает Ollama и HTTP API; чат открывается на http://127.0.0.1:8765/ui/.
Workspace по умолчанию — ~/CodeAgents (создаётся автоматически), чтобы избежать
системных промптов про Documents/Desktop/Downloads. Сменить можно через меню
Services → «Pin workspace folder…».
Закрытие окна останавливает сервисы.
EOF
