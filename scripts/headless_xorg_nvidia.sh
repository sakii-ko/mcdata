#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-start}"
DISPLAY_NUM="${MCDATA_HEADLESS_DISPLAY:-:77}"
GPU_INDEX="${MCDATA_GPU_INDEX:-0}"
SCREEN_SIZE="${MCDATA_XORG_SIZE:-1280x720}"
DEFAULT_TMP_ROOT="${MCDATA_TMP_ROOT:-${TMPDIR:-$PWD/.mcdata/tmp}}"
WORK_DIR="${MCDATA_XORG_WORKDIR:-$DEFAULT_TMP_ROOT/headless-xorg}"
ALLOW_LOCAL_CLIENTS="${MCDATA_XORG_ALLOW_LOCAL_CLIENTS:-1}"
DISPLAY_ID="${DISPLAY_NUM#:}"
PID_FILE="$WORK_DIR/xorg-$DISPLAY_ID.pid"
LOG_FILE="$WORK_DIR/xorg-$DISPLAY_ID.log"
CONF_FILE="$WORK_DIR/xorg-gpu$GPU_INDEX.conf"

die() {
  echo "error: $*" >&2
  exit 1
}

need() {
  command -v "$1" >/dev/null 2>&1 || die "missing command: $1"
}

gpu_bus_id() {
  local raw bus_hex dev_hex func domain
  raw="$(nvidia-smi --query-gpu=index,pci.bus_id --format=csv,noheader | awk -F, -v idx="$GPU_INDEX" '$1 + 0 == idx {gsub(/^ +| +$/, "", $2); print $2; exit}')"
  [[ -n "$raw" ]] || die "could not find GPU index $GPU_INDEX with nvidia-smi"
  IFS=':.' read -r domain bus_hex dev_hex func <<<"${raw//./:}"
  printf 'PCI:%d:%d:%d\n' "$((16#$bus_hex))" "$((16#$dev_hex))" "$func"
}

write_config() {
  local bus_id width height
  bus_id="$(gpu_bus_id)"
  width="${SCREEN_SIZE%x*}"
  height="${SCREEN_SIZE#*x}"
  mkdir -p "$WORK_DIR"
  cat >"$CONF_FILE" <<EOF
Section "ServerLayout"
    Identifier "Layout0"
    Screen 0 "Screen0"
EndSection

Section "Device"
    Identifier "GPU$GPU_INDEX"
    Driver "nvidia"
    BusID "$bus_id"
    Option "AllowEmptyInitialConfiguration" "true"
    Option "UseDisplayDevice" "None"
EndSection

Section "Screen"
    Identifier "Screen0"
    Device "GPU$GPU_INDEX"
    DefaultDepth 24
    SubSection "Display"
        Depth 24
        Virtual $width $height
    EndSubSection
EndSection
EOF
}

xorg_bin() {
  if [[ -x /usr/lib/xorg/Xorg ]]; then
    echo /usr/lib/xorg/Xorg
  elif command -v Xorg >/dev/null 2>&1; then
    command -v Xorg
  else
    die "missing Xorg"
  fi
}

check_tmp() {
  local free_kb
  [[ -d /tmp && -w /tmp ]] || die "/tmp must be writable because Xorg creates /tmp/.X*-lock"
  free_kb="$(df -Pk /tmp | awk 'NR == 2 {print $4}')"
  [[ "${free_kb:-0}" -gt 65536 ]] || die "/tmp has less than 64 MiB free; Xorg lock/socket creation is likely to fail"
}

is_running() {
  [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

status() {
  if is_running; then
    echo "Xorg is running on $DISPLAY_NUM, pid $(cat "$PID_FILE")"
    DISPLAY="$DISPLAY_NUM" glxinfo -B || true
  else
    echo "Xorg is not running for $DISPLAY_NUM"
  fi
}

stop() {
  if is_running; then
    kill "$(cat "$PID_FILE")" || true
    sleep 1
  fi
  rm -f "$PID_FILE"
}

start() {
  need nvidia-smi
  need glxinfo
  check_tmp
  write_config
  if is_running; then
    status
    return 0
  fi
  if [[ -S "/tmp/.X11-unix/X$DISPLAY_ID" ]]; then
    die "display $DISPLAY_NUM already has an X socket"
  fi
  local bin
  bin="$(xorg_bin)"
  nohup "$bin" "$DISPLAY_NUM" \
    -config "$CONF_FILE" \
    -noreset \
    -novtswitch \
    -sharevts \
    $([[ "$ALLOW_LOCAL_CLIENTS" == "1" ]] && printf '%s' "-ac") \
    -nolisten tcp \
    -logfile "$LOG_FILE" \
    >"$WORK_DIR/xorg-$DISPLAY_ID.stdout" \
    2>"$WORK_DIR/xorg-$DISPLAY_ID.stderr" &
  echo "$!" >"$PID_FILE"
  sleep 5
  if ! is_running; then
    echo "Xorg failed to stay up. stderr:" >&2
    cat "$WORK_DIR/xorg-$DISPLAY_ID.stderr" >&2 2>/dev/null || true
    echo "Xorg log tail:" >&2
    tail -120 "$LOG_FILE" >&2 2>/dev/null || true
    rm -f "$PID_FILE"
    return 1
  fi
  local glx renderer
  glx="$(DISPLAY="$DISPLAY_NUM" glxinfo -B 2>&1)" || {
    echo "$glx" >&2
    return 1
  }
  renderer="$(printf '%s\n' "$glx" | awk -F: '/OpenGL renderer string/ {sub(/^ /, "", $2); print $2; exit}')"
  printf '%s\n' "$glx"
  if printf '%s' "$renderer" | grep -Eiq 'llvmpipe|softpipe'; then
    die "display $DISPLAY_NUM is software-rendered: $renderer"
  fi
  if ! printf '%s' "$glx" | grep -Eiq 'OpenGL vendor string: NVIDIA|OpenGL renderer string: NVIDIA'; then
    die "display $DISPLAY_NUM is not NVIDIA-backed: ${renderer:-unknown renderer}"
  fi
  echo "export DISPLAY=$DISPLAY_NUM"
}

case "$MODE" in
  start)
    start
    ;;
  probe)
    stop
    start
    stop
    ;;
  stop)
    stop
    ;;
  status)
    status
    ;;
  *)
    die "usage: $0 [start|probe|status|stop]"
    ;;
esac
