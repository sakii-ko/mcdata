#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "Missing virtualenv Python at $PYTHON" >&2
  echo "Create it with: python3 -m venv .venv && .venv/bin/pip install -e '.[dev,qa]'" >&2
  exit 127
fi

cd "$ROOT_DIR"
"$PYTHON" -m compileall -q src
"$PYTHON" -m ruff check src tests
"$PYTHON" -m pytest -q
