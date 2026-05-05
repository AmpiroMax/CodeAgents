#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DMG_PATH="$ROOT_DIR/dist/CodeAgents.dmg"
STAGING_DIR="$ROOT_DIR/dist/dmg-staging"
INSTALL_ROOT="${CODEAGENTS_APP_ROOT:-$ROOT_DIR}"

rm -rf "$STAGING_DIR" "$DMG_PATH"
mkdir -p "$STAGING_DIR"

CODEAGENTS_APP_INSTALL_DIR="$ROOT_DIR/dist" \
CODEAGENTS_APP_ROOT="$INSTALL_ROOT" \
  bash "$ROOT_DIR/scripts/install_app.sh"

cp -R "$ROOT_DIR/dist/CodeAgents.app" "$STAGING_DIR/"
ln -sf /Applications "$STAGING_DIR/Applications"

hdiutil create \
  -volname "CodeAgents" \
  -srcfolder "$STAGING_DIR" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

echo "Created $DMG_PATH"
echo "Перетащите CodeAgents.app в Программы и запустите из Finder."
