#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: scripts/sync_to_remote.sh <host> <dest_dir>" >&2
  exit 2
fi

HOST="$1"
DEST_DIR="$2"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR"
git rev-parse HEAD > .sync_commit
rsync -az --delete \
  --exclude .git \
  --exclude .venv \
  --exclude .mcdata \
  --exclude runs \
  ./ "$HOST:$DEST_DIR/"
