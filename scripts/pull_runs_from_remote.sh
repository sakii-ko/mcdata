#!/usr/bin/env bash
# Pull render runs from a remote render host back to the local NAS, optionally
# purging them from the remote after a verified transfer.
#
# Policy (PLAN.md): render hosts (4090/L40S) are compute, not storage. Runs are
# pulled to ${MCDATA_OUTPUT_DIR}/remote_<host>/ soon after capture; the remote
# keeps only in-flight work.
#
# Usage:
#   scripts/pull_runs_from_remote.sh <host> [remote_runs_dir] [--purge]
#     host             ssh alias (e.g. 4090, l40s)
#     remote_runs_dir  default: /home/lyf/mcdata/runs (4090 layout)
#     --purge          after a clean second-pass rsync (zero transfers),
#                      delete the pulled run dirs on the remote
#
# Refuses to purge while an mcdata pipeline is active on the remote.
set -euo pipefail

HOST="${1:?usage: pull_runs_from_remote.sh <host> [remote_runs_dir] [--purge]}"
shift
REMOTE_DIR="/home/lyf/mcdata/runs"
PURGE=0
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    *) REMOTE_DIR="$arg" ;;
  esac
done

DEST_BASE="${MCDATA_OUTPUT_DIR:-$PWD/runs}"
DEST="$DEST_BASE/remote_$HOST"
mkdir -p "$DEST"

echo "pull: $HOST:$REMOTE_DIR/ -> $DEST/"
rsync -a --info=stats1 "$HOST:$REMOTE_DIR/" "$DEST/"

echo "verify: second pass must transfer zero files"
second_pass=$(rsync -a --itemize-changes "$HOST:$REMOTE_DIR/" "$DEST/" | grep -c '^>' || true)
if [[ "$second_pass" -ne 0 ]]; then
  echo "error: second rsync pass still transferred $second_pass file(s); remote may be mid-write. Not purging." >&2
  exit 1
fi
echo "verify: OK"

if [[ "$PURGE" -eq 1 ]]; then
  if ssh "$HOST" "pgrep -f 'mcdata.cli|portablemc|x11grab' >/dev/null 2>&1"; then
    echo "error: mcdata pipeline appears active on $HOST; refusing to purge." >&2
    exit 1
  fi
  echo "purge: removing pulled content from $HOST:$REMOTE_DIR"
  ssh "$HOST" "find '$REMOTE_DIR' -mindepth 1 -maxdepth 1 -exec rm -rf {} +"
  echo "purge: done"
fi

echo "local copy: $DEST"
du -sh "$DEST" | awk '{print "size: "$1}'
