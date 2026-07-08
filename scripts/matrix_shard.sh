#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: scripts/matrix_shard.sh <gpu_index> <profiles_csv> [duration=60]" >&2
}

if [[ $# -lt 2 || $# -gt 3 ]]; then
  usage
  exit 2
fi

GPU_INDEX="$1"
PROFILES_CSV="$2"
DURATION="${3:-60}"

if [[ ! "$GPU_INDEX" =~ ^[0-9]+$ ]]; then
  echo "error: gpu_index must be a non-negative integer" >&2
  exit 2
fi

if [[ -z "$PROFILES_CSV" ]]; then
  echo "error: profiles_csv must not be empty" >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

DISPLAY_NUM=":$((77 + GPU_INDEX))"
SERVER_PORT="$((25600 + GPU_INDEX))"
LANE="gpu${GPU_INDEX}"

cd "$ROOT_DIR"
export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

"$PYTHON" - "$PROFILES_CSV" <<'PY'
import sys

from mcdata.config import load_profile
from mcdata.paths import ProjectPaths

profiles = [item.strip() for item in sys.argv[1].split(",") if item.strip()]
if not profiles:
    raise SystemExit("error: profiles_csv contains no profile names")

paths = ProjectPaths.from_root()
missing = []
for profile in profiles:
    load_profile(paths.configs, profile)
    if not paths.instance_dir(profile).is_dir():
        missing.append(str(paths.instance_dir(profile)))

if missing:
    print("error: missing pre-bootstrapped instance dir(s):", file=sys.stderr)
    for path in missing:
        print(f"  {path}", file=sys.stderr)
    print("hint: run serial bootstrap first; matrix shards always use --no-bootstrap", file=sys.stderr)
    raise SystemExit(1)
PY

"$PYTHON" -m mcdata.cli run-matrix \
  --profiles "$PROFILES_CSV" \
  --strategy ground_astar_loop \
  --duration "$DURATION" \
  --display "$DISPLAY_NUM" \
  --server-port "$SERVER_PORT" \
  --lane "$LANE" \
  --no-bootstrap
