#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-plan}"
DAYS="${MCDATA_CLEAN_DAYS:-1}"
MINUTES="${MCDATA_CLEAN_MINUTES:-}"
TMP_SCAN_ROOT="${MCDATA_CLEAN_ROOT:-/tmp}"
OWNER="${MCDATA_CLEAN_OWNER:-$(id -un)}"
WORK_ROOT="${MCDATA_TMP_ROOT:-${TMPDIR:-/dev/shm}}"
WORK_DIR="$WORK_ROOT/mcdata-clean-tmp"
FILES="$WORK_DIR/files.txt"
DIRS="$WORK_DIR/dirs.txt"

usage() {
  cat <<EOF
Usage: $0 [plan|apply]

Plans or applies a conservative cleanup for $TMP_SCAN_ROOT.

Environment:
  MCDATA_CLEAN_DAYS   Minimum mtime age in days, default: $DAYS
  MCDATA_CLEAN_MINUTES Minimum mtime age in minutes; overrides days when set
  MCDATA_CLEAN_OWNER  Owner to clean, default: $OWNER
  MCDATA_CLEAN_ROOT   Root to scan, default: $TMP_SCAN_ROOT
  MCDATA_TMP_ROOT     Where candidate lists/logs are written
EOF
}

case "$MODE" in
  plan|apply) ;;
  -h|--help|help)
    usage
    exit 0
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

mkdir -p "$WORK_DIR"
: >"$FILES"
: >"$DIRS"

if [[ -n "$MINUTES" ]]; then
  age_args=(-mmin +"$MINUTES")
  age_label="${MINUTES} minutes"
else
  age_args=(-mtime +"$DAYS")
  age_label="${DAYS} days"
fi

find "$TMP_SCAN_ROOT" -xdev -maxdepth 1 -user "$OWNER" -type f "${age_args[@]}" \( \
  -name 'mcdata-*' -o \
  -name 'mcdata_*' -o \
  -name 'magick-*' -o \
  -name 'df_*.xwd' -o \
  -name 'df_*.png' -o \
  -name 'cached_test.mp4' -o \
  -name 'streamed.mp4' -o \
  -name 'test_transcode.mp4' \
\) -print >"$FILES" 2>/dev/null || true

find "$TMP_SCAN_ROOT" -xdev -mindepth 1 -maxdepth 1 -user "$OWNER" -type d "${age_args[@]}" \( \
  -name 'tmp*' -o \
  -name 'offload_*' -o \
  -name 'spatial_qa_video_cache' -o \
  -name 'hf_cache' -o \
  -name 'torchinductor_*' \
\) -print >"$DIRS" 2>/dev/null || true

echo "scan_root=$TMP_SCAN_ROOT"
echo "owner=$OWNER"
echo "min_age=$age_label"
echo "work_dir=$WORK_DIR"
echo "file_candidates=$(wc -l <"$FILES")"
echo "dir_candidates=$(wc -l <"$DIRS")"

echo "candidate_size:"
xargs -r du -sch -- <"$FILES" 2>/dev/null | tail -1 || true
xargs -r du -sch -- <"$DIRS" 2>/dev/null | tail -1 || true

echo "open file refs:"
xargs -r lsof -- <"$FILES" 2>/dev/null | sed -n '1,120p' || true

echo "open dir refs:"
xargs -r lsof +D -- <"$DIRS" 2>/dev/null | sed -n '1,160p' || true

echo "largest selected paths:"
while IFS= read -r path; do
  [[ -e "$path" ]] && du -sh -- "$path" 2>/dev/null
done < <(cat "$FILES" "$DIRS") | sort -h | tail -60

if [[ "$MODE" == "plan" ]]; then
  echo "plan only; rerun with: $0 apply"
  exit 0
fi

if [[ -s "$FILES" ]] && xargs -r lsof -- <"$FILES" >/dev/null 2>&1; then
  echo "refusing to delete because some file candidates are open" >&2
  exit 1
fi
if [[ -s "$DIRS" ]] && xargs -r lsof +D -- <"$DIRS" >/dev/null 2>&1; then
  echo "refusing to delete because some directory candidates are open" >&2
  exit 1
fi

xargs -r rm -f -- <"$FILES"
xargs -r rm -rf -- <"$DIRS"
sync || true
df -h "$TMP_SCAN_ROOT"
