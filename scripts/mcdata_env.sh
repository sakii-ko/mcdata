#!/usr/bin/env bash
# Source this file before local data collection to keep heavy/generated outputs
# off the system disk.

set -euo pipefail

pick_tmp_root() {
  local candidate
  for candidate in \
    "${MCDATA_TMP_ROOT:-}" \
    /root/mas/bigdata1/tmp/mcdata \
    /root/nas/bigdata1/tmp/mcdata \
    "$PWD/.mcdata/tmp"; do
    [[ -n "$candidate" ]] || continue
    if mkdir -p "$candidate" 2>/dev/null && [[ -w "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

MCDATA_TMP_ROOT="$(pick_tmp_root)"
export MCDATA_TMP_ROOT
export TMPDIR="$MCDATA_TMP_ROOT/tmp"
export XDG_CACHE_HOME="$MCDATA_TMP_ROOT/xdg-cache"
export MCDATA_OUTPUT_DIR="$MCDATA_TMP_ROOT/runs"
export MCDATA_MAIN_DIR="$MCDATA_TMP_ROOT/launcher"
export MCDATA_WORK_DIR="$MCDATA_TMP_ROOT/instances"

mkdir -p \
  "$TMPDIR" \
  "$XDG_CACHE_HOME" \
  "$MCDATA_OUTPUT_DIR" \
  "$MCDATA_MAIN_DIR" \
  "$MCDATA_WORK_DIR" \
  "$MCDATA_TMP_ROOT/logs" \
  "$MCDATA_TMP_ROOT/downloads" \
  "$MCDATA_TMP_ROOT/recordings" \
  "$MCDATA_TMP_ROOT/xorg"

echo "MCDATA_TMP_ROOT=$MCDATA_TMP_ROOT"
echo "MCDATA_OUTPUT_DIR=$MCDATA_OUTPUT_DIR"
