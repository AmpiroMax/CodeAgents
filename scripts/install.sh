#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_NAME="ca"
SERVICES_BIN_NAME="ca-services"
INSTALL_DIR="${CODEAGENTS_INSTALL_DIR:-$HOME/.local/bin}"

cd "$ROOT_DIR"

if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install -e .
cargo build --release -p codeagents-terminal

mkdir -p "$INSTALL_DIR"
ln -sf "$ROOT_DIR/target/release/$BIN_NAME" "$INSTALL_DIR/$BIN_NAME"
ln -sf "$ROOT_DIR/target/release/$SERVICES_BIN_NAME" "$INSTALL_DIR/$SERVICES_BIN_NAME"

cat <<EOF
Installed $BIN_NAME -> $INSTALL_DIR/$BIN_NAME
Installed $SERVICES_BIN_NAME -> $INSTALL_DIR/$SERVICES_BIN_NAME

If '$BIN_NAME' is not found, add this to your shell config:

  export PATH="$INSTALL_DIR:\$PATH"

Start the backend:

  $BIN_NAME serve

Or run the service manager:

  $SERVICES_BIN_NAME start
  $SERVICES_BIN_NAME status

Then open the terminal UI:

  $BIN_NAME
EOF
