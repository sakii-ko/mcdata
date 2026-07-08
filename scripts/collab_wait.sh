#!/usr/bin/env bash
# Block until a message arrives in my mailbox, print it, archive it, exit.
# Run this in the background; the process exiting = wake-up signal.
#
# Usage: scripts/collab_wait.sh <planner|coder> [poll_sec=5]
# Mailbox semantics: pending messages are picked up immediately on start,
# so nothing is lost while the agent is busy.
set -euo pipefail

ME="${1:?usage: collab_wait.sh <planner|coder> [poll_sec]}"
POLL="${2:-5}"
case "$ME" in planner|coder) ;; *) echo "error: role must be planner|coder" >&2; exit 2 ;; esac

BOX="/tmp/mcdata-collab/to-$ME"
DONE="/tmp/mcdata-collab/processed-$ME"
mkdir -p "$BOX" "$DONE"

while true; do
  files=$(ls -1 "$BOX" 2>/dev/null || true)
  if [ -n "$files" ]; then
    echo "=== mailbox for $ME: $(echo "$files" | wc -l) message(s) ==="
    for f in $files; do
      echo "--- $f ---"
      cat "$BOX/$f"
      mv "$BOX/$f" "$DONE/$f"
    done
    exit 0
  fi
  sleep "$POLL"
done
