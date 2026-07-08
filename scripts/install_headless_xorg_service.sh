#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "error: this installer must run as root" >&2
  exit 1
fi

GPU_INDEX="${MCDATA_GPU_INDEX:-0}"
DISPLAY_NUM="${MCDATA_HEADLESS_DISPLAY:-:77}"
SCREEN_SIZE="${MCDATA_XORG_SIZE:-1280x720}"
SERVICE_NAME="${MCDATA_XORG_SERVICE_NAME:-mcdata-xorg}"
DISPLAY_ID="${DISPLAY_NUM#:}"
CONF_FILE="/etc/X11/${SERVICE_NAME}-gpu${GPU_INDEX}.conf"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
LOG_FILE="/var/log/${SERVICE_NAME}-${DISPLAY_ID}.log"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "error: missing command: $1" >&2
    exit 1
  }
}

need nvidia-smi
need systemctl

if [[ -x /usr/lib/xorg/Xorg ]]; then
  XORG_BIN=/usr/lib/xorg/Xorg
elif command -v Xorg >/dev/null 2>&1; then
  XORG_BIN="$(command -v Xorg)"
else
  echo "error: missing Xorg" >&2
  exit 1
fi

raw_bus="$(nvidia-smi --query-gpu=index,pci.bus_id --format=csv,noheader | awk -F, -v idx="$GPU_INDEX" '$1 + 0 == idx {gsub(/^ +| +$/, "", $2); print $2; exit}')"
if [[ -z "$raw_bus" ]]; then
  echo "error: could not find GPU index $GPU_INDEX with nvidia-smi" >&2
  exit 1
fi

IFS=':.' read -r _domain bus_hex dev_hex func <<<"${raw_bus//./:}"
bus_id="$(printf 'PCI:%d:%d:%d' "$((16#$bus_hex))" "$((16#$dev_hex))" "$func")"
width="${SCREEN_SIZE%x*}"
height="${SCREEN_SIZE#*x}"

install -d -m 0755 /etc/X11
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

cat >"$SERVICE_FILE" <<EOF
[Unit]
Description=mcdata headless NVIDIA Xorg on ${DISPLAY_NUM}
After=multi-user.target nvidia-persistenced.service
Wants=nvidia-persistenced.service

[Service]
Type=simple
ExecStart=${XORG_BIN} ${DISPLAY_NUM} -config ${CONF_FILE} -noreset -novtswitch -sharevts -ac -nolisten tcp -logfile ${LOG_FILE}
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME.service"

echo "Installed $SERVICE_FILE"
echo "Xorg config: $CONF_FILE"
echo "Log file: $LOG_FILE"
echo "Use: export DISPLAY=$DISPLAY_NUM"
echo "Verify: DISPLAY=$DISPLAY_NUM glxinfo -B"
