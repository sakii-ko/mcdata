#!/usr/bin/env bash
set -euo pipefail

PROFILE="${1:-fabric_low}"
STRATEGY="${2:-idle_pan}"
DURATION="${3:-60}"

: "${DISPLAY:=:0}"
export DISPLAY

. .venv/bin/activate
mcdata bootstrap --profile "$PROFILE"
mcdata run \
  --profile "$PROFILE" \
  --with-server \
  --replay-actions \
  --capture \
  --strategy "$STRATEGY" \
  --duration "$DURATION"
