#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "=== CodeAgents full rebuild ==="
echo ""

# 1. Stop running services so binaries can be replaced
echo "[1/4] Stopping services..."
pkill -f "CodeAgentsServices" 2>/dev/null || true
if command -v ca-services &>/dev/null; then
    ca-services stop 2>/dev/null || true
fi
lsof -ti :8765 -sTCP:LISTEN 2>/dev/null | xargs kill -TERM 2>/dev/null || true
sleep 1

# 2. Python package
echo "[2/4] Installing Python package..."
if [[ ! -d ".venv" ]]; then
    python3 -m venv .venv
fi
.venv/bin/python -m pip install -e . -q

# 3. Rust binaries (ca + ca-services)
echo "[3/4] Building Rust binaries (release)..."
cargo build --release -p codeagents-terminal -q

INSTALL_DIR="${CODEAGENTS_INSTALL_DIR:-$HOME/.local/bin}"
mkdir -p "$INSTALL_DIR"
ln -sf "$ROOT_DIR/target/release/ca" "$INSTALL_DIR/ca"
ln -sf "$ROOT_DIR/target/release/ca-services" "$INSTALL_DIR/ca-services"

# 4. macOS app
echo "[4/4] Building macOS app..."
bash "$ROOT_DIR/scripts/install_app.sh" 2>&1 | tail -1

echo ""
echo "=== Done ==="
echo ""
echo "  ca            — terminal UI (updated)"
echo "  ca-services   — service manager (updated)"
echo "  CodeAgents Services.app — macOS app (updated in /Applications)"
echo ""
echo "  Launch 'CodeAgents Services' app to start Ollama + API."
