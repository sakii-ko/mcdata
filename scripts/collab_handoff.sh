#!/usr/bin/env bash
# Atomic handoff: notify the other agent, then immediately become my listener.
# Eliminates the forgotten-listener-restart failure mode: run this ONE command
# (in the background) at the end of every work cycle.
#
# Usage: scripts/collab_handoff.sh <my-role: planner|coder> [message...]
# Example (coder finishing a task):
#   scripts/collab_handoff.sh coder "T4 done, see report §T4"
set -euo pipefail

ME="${1:?usage: collab_handoff.sh <planner|coder> [message...]}"
shift || true
case "$ME" in
  planner) OTHER="coder" ;;
  coder) OTHER="planner" ;;
  *) echo "error: role must be planner|coder" >&2; exit 2 ;;
esac

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$HERE/collab_notify.sh" "$OTHER" "$@"
exec "$HERE/collab_wait.sh" "$ME"
