#!/usr/bin/env bash
set -euo pipefail

IMAGE="${MCDATA_DOCKER_IMAGE:-mcdata-l40s:latest}"
PROJECT_DIR="${MCDATA_PROJECT_DIR:-$PWD}"
TMP_ROOT="${MCDATA_TMP_ROOT:-/workspace/tmp/mcdata}"
GPU_DEVICE="${MCDATA_GPU_DEVICE:-0}"
DISPLAY_NUM="${DISPLAY:-:77}"

cat <<EOF
# Preferred mode: host/root has already started a NVIDIA-backed Xorg display.
docker run --rm -it \\
  --gpus '"device=${GPU_DEVICE}"' \\
  --ipc=host \\
  --shm-size=16g \\
  -e NVIDIA_DRIVER_CAPABILITIES=all \\
  -e DISPLAY=${DISPLAY_NUM} \\
  -e MCDATA_TMP_ROOT=${TMP_ROOT} \\
  -v /tmp/.X11-unix:/tmp/.X11-unix \\
  -v "${PROJECT_DIR}:/workspace/mcdata" \\
  -v "/root/nas/bigdata1/tmp:/workspace/tmp" \\
  -w /workspace/mcdata \\
  ${IMAGE} bash

# If the platform allows privileged containers, Xorg can be started inside:
docker run --rm -it \\
  --gpus '"device=${GPU_DEVICE}"' \\
  --privileged \\
  --ipc=host \\
  --shm-size=16g \\
  -e NVIDIA_DRIVER_CAPABILITIES=all \\
  -e MCDATA_TMP_ROOT=${TMP_ROOT} \\
  -v "${PROJECT_DIR}:/workspace/mcdata" \\
  -v "/root/nas/bigdata1/tmp:/workspace/tmp" \\
  -w /workspace/mcdata \\
  ${IMAGE} bash -lc 'scripts/headless_xorg_nvidia.sh start && export DISPLAY=:77 && mcdata doctor'
EOF
