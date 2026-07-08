#!/usr/bin/env bash
# Drop a message into the other agent's mailbox to wake it up.
#
# Usage: scripts/collab_notify.sh <planner|coder> [message...]
# The message should be a one-line pointer; put the real content in repo docs
# (report/PLAN) and push first. The receiver reads repo state after waking.
set -euo pipefail

TARGET="${1:?usage: collab_notify.sh <planner|coder> [message...]}"
shift || true
case "$TARGET" in planner|coder) ;; *) echo "error: target must be planner|coder" >&2; exit 2 ;; esac

BOX="/tmp/mcdata-collab/to-$TARGET"
mkdir -p "$BOX"
STAMP="$(date +%Y%m%dT%H%M%S)-$$"
HEAD_REF="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
{
  echo "from: $(whoami)@$(hostname)"
  echo "at: $(date -Is)"
  echo "head: $HEAD_REF"
  echo "message: ${*:-"(no message; check repo)"}"
} > "$BOX/msg-$STAMP"
echo "notified $TARGET: $BOX/msg-$STAMP"
