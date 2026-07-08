#!/usr/bin/env bash
# Bring up a GPU-backed headless Xorg inside an L40S container that was
# started WITHOUT graphics capabilities (NVIDIA_DRIVER_CAPABILITIES=compute,utility).
#
# Verified 2026-07-08 on cci L40S container (Ubuntu 22.04, driver 580.173.02):
#   - Client GL libs (libGLX_nvidia/libEGL_nvidia/glcore) for the running driver
#     version are already injected read-only by the container toolkit.
#   - X-server-side modules (nvidia_drv.so, libglxserver_nvidia.so) exist only
#     as EMPTY STUB files bind-mounted over the standard paths, so neither dpkg
#     nor cp can replace them ("Invalid cross-device link" / "resource busy").
#   - Workaround: extract the matching xserver-xorg-video-nvidia-<ver> deb into
#     /opt/nvidia-xorg and put that dir FIRST in the Xorg ModulePath.
#   - L40S in this environment runs a "virtual display": do NOT set
#     Option "UseDisplayDevice" "None" (the driver rejects it); plain
#     AllowEmptyInitialConfiguration works.
#
# Usage (as root inside the container):
#   scripts/l40s_container_gpu_display.sh install   # apt + module extraction + conf
#   scripts/l40s_container_gpu_display.sh start     # launch Xorg
#   scripts/l40s_container_gpu_display.sh verify    # glxinfo must show NVIDIA
# Env overrides: MCDATA_GPU_INDEX (default 0), MCDATA_HEADLESS_DISPLAY (default :77),
#                MCDATA_XORG_SIZE (default 1280x720)
set -euo pipefail

GPU_INDEX="${MCDATA_GPU_INDEX:-0}"
DISPLAY_NUM="${MCDATA_HEADLESS_DISPLAY:-:77}"
SCREEN_SIZE="${MCDATA_XORG_SIZE:-1280x720}"
CONF_FILE="/etc/X11/mcdata-l40s-gpu${GPU_INDEX}.conf"
LOG_FILE="/var/log/mcdata-xorg-${DISPLAY_NUM#:}.log"
MODULE_DIR="/opt/nvidia-xorg"

cmd="${1:?usage: l40s_container_gpu_display.sh install|start|verify}"

driver_version() {
  nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1 | tr -d ' '
}

do_install() {
  [[ "$(id -u)" -eq 0 ]] || { echo "error: must run as root" >&2; exit 1; }
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq xserver-xorg-core mesa-utils xdotool x11-utils

  local ver branch
  ver="$(driver_version)"
  branch="${ver%%.*}"
  if [[ ! -s "$MODULE_DIR/drivers/nvidia_drv.so" ]]; then
    local tmp
    tmp="$(mktemp -d)"
    ( cd "$tmp" && apt-get download "xserver-xorg-video-nvidia-${branch}=${ver}-1ubuntu1" \
        && dpkg-deb -x xserver-xorg-video-nvidia-*.deb extracted )
    install -D "$tmp/extracted/usr/lib/xorg/modules/drivers/nvidia_drv.so" \
      "$MODULE_DIR/drivers/nvidia_drv.so"
    install -D "$tmp/extracted/usr/lib/xorg/modules/extensions/libglxserver_nvidia.so.${ver}" \
      "$MODULE_DIR/extensions/libglxserver_nvidia.so.${ver}"
    ln -sf "libglxserver_nvidia.so.${ver}" "$MODULE_DIR/extensions/libglxserver_nvidia.so"
    rm -rf "$tmp"
  fi

  local raw bus_hex dev_hex func bus width height
  raw="$(nvidia-smi --query-gpu=index,pci.bus_id --format=csv,noheader \
      | awk -F, -v idx="$GPU_INDEX" '$1 + 0 == idx {gsub(/ /, "", $2); print $2; exit}')"
  [[ -n "$raw" ]] || { echo "error: GPU index $GPU_INDEX not found" >&2; exit 1; }
  IFS=':.' read -r _domain bus_hex dev_hex func <<<"$raw"
  bus="PCI:$((16#$bus_hex)):$((16#$dev_hex)):$((10#$func))"
  width="${SCREEN_SIZE%x*}"
  height="${SCREEN_SIZE#*x}"

  mkdir -p /etc/X11
  cat >"$CONF_FILE" <<EOF
Section "Files"
    ModulePath "$MODULE_DIR"
    ModulePath "/usr/lib/xorg/modules"
EndSection

Section "ServerLayout"
    Identifier "Layout0"
    Screen 0 "Screen0"
EndSection

Section "Device"
    Identifier "GPU$GPU_INDEX"
    Driver "nvidia"
    BusID "$bus"
    Option "AllowEmptyInitialConfiguration" "true"
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
  echo "installed: $MODULE_DIR (driver $ver), $CONF_FILE (BusID $bus)"
}

do_start() {
  if DISPLAY="$DISPLAY_NUM" xdpyinfo >/dev/null 2>&1; then
    echo "Xorg already serving $DISPLAY_NUM"
    return 0
  fi
  nohup Xorg "$DISPLAY_NUM" -config "$CONF_FILE" -noreset -novtswitch -sharevts \
    -ac -nolisten tcp -logfile "$LOG_FILE" >/dev/null 2>&1 &
  sleep 4
  DISPLAY="$DISPLAY_NUM" xdpyinfo >/dev/null 2>&1 \
    || { echo "error: Xorg failed to start; see $LOG_FILE" >&2; exit 1; }
  echo "Xorg serving $DISPLAY_NUM (log: $LOG_FILE)"
}

do_verify() {
  DISPLAY="$DISPLAY_NUM" glxinfo -B | grep -E "renderer|vendor|direct rendering" || {
    echo "error: glxinfo failed on $DISPLAY_NUM" >&2
    exit 1
  }
  if ! DISPLAY="$DISPLAY_NUM" glxinfo -B | grep -q "NVIDIA"; then
    echo "error: renderer is not NVIDIA (software fallback?); unusable for real captures" >&2
    exit 1
  fi
  echo "verify: NVIDIA-backed display OK on $DISPLAY_NUM"
}

case "$cmd" in
  install) do_install ;;
  start) do_start ;;
  verify) do_verify ;;
  *) echo "unknown command: $cmd" >&2; exit 1 ;;
esac
