#!/usr/bin/env bash
# Stub: populate evals/data/* from evals/manifest.toml once URLs are configured.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MANIFEST="$ROOT/evals/manifest.toml"
echo "Benchmark download stub — edit $MANIFEST with real sources, then implement fetch logic."
mkdir -p "$ROOT/evals/data"
echo "Created evals/data (empty). No downloads performed."
