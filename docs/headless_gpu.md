# Headless GPU rendering

Minecraft Java runs natively on Linux. For this project it should use Java + Fabric + Sodium/Iris,
not Proton and not Bedrock. Bedrock on Linux adds unnecessary compatibility layers and is less
useful for deterministic modded rendering automation.

The hard requirement is not a physical monitor. The hard requirement is a GPU-backed OpenGL display
that GLFW/LWJGL can open. `Xvfb` is useful for smoke tests, but it normally reports Mesa
`llvmpipe`, which is CPU rendering and cannot validate shader or water-reflection quality.

## Preferred headless setup

Run a headless NVIDIA Xorg server on the render host:

```bash
MCDATA_GPU_INDEX=0 \
MCDATA_HEADLESS_DISPLAY=:77 \
MCDATA_XORG_SIZE=1280x720 \
scripts/headless_xorg_nvidia.sh start

export DISPLAY=:77
glxinfo -B
mcdata doctor
```

The `OpenGL vendor string` or `OpenGL renderer string` must be NVIDIA. If it says `llvmpipe` or
`softpipe`, the run is only a smoke test.

Common host-side blockers:

- `/tmp` must have free space because Xorg creates `/tmp/.X*-lock` and `/tmp/.X11-unix/X*`.
- Non-root SSH users may be blocked by `/etc/X11/Xwrapper.config` unless it has
  `allowed_users=anybody`.
- The render user normally needs `video` and/or `render` group membership for graphics devices.
- Rootless Xorg may also fail at `/dev/tty0` or `systemd-logind: failed to take device`. In that
  case use a root-started Xorg service or ask an administrator to grant the render user the right
  device/session permissions.
- An existing login-manager display such as `:0` is only usable if the user has matching Xauthority
  or an admin grants access.

On a Debian/Ubuntu host, an administrator can usually unblock this with:

```bash
sudo usermod -aG video,render $USER
sudo sed -i 's/^allowed_users=.*/allowed_users=anybody/' /etc/X11/Xwrapper.config
```

Then log out and back in so the group changes apply.

For persistent headless hosts, a cleaner option is a root-owned systemd unit that starts Xorg on a
fixed display, for example `:77`, and leaves users to connect through `DISPLAY=:77`. This avoids
requiring every SSH user to start an X server or touch `/dev/tty0`.

This repo includes a root-only installer for that pattern:

```bash
sudo MCDATA_GPU_INDEX=0 MCDATA_HEADLESS_DISPLAY=:77 scripts/install_headless_xorg_service.sh
export DISPLAY=:77
glxinfo -B
```

The Xorg command is started with `-ac` and `-nolisten tcp`, so local processes can connect through
the Unix socket but it does not listen on a TCP port.

## L40S containers

An L40S is a suitable target for Minecraft Java rendering. It has NVIDIA graphics/OpenGL support,
unlike H100-style compute-only expectations. The container must expose graphics capabilities and a
real display backend.

Minecraft Java does not require container-in-container. Use one GPU-enabled container unless the
cluster scheduler itself forces an outer sandbox.

Minimum container requirements:

- NVIDIA Container Toolkit on the host.
- `NVIDIA_DRIVER_CAPABILITIES` includes `graphics` and `display`; `all` is fine.
- `/dev/nvidia*` is visible in the container.
- Xorg/Wayland display backend is available either from the host or started inside the container.
- Java, Python, `ffmpeg`, `xdotool`, `glxinfo`, and Xorg client/server packages are installed.
- Enough `/dev/shm`; use at least `--shm-size=8g` for shader-heavy runs.

Example container shape for an inside-container Xorg:

```bash
docker run --rm -it \
  --gpus '"device=0"' \
  --privileged \
  --ipc=host \
  --shm-size=16g \
  -e NVIDIA_DRIVER_CAPABILITIES=all \
  -v "$PWD:/workspace/mcdata" \
  -w /workspace/mcdata \
  <image> bash

scripts/headless_xorg_nvidia.sh start
export DISPLAY=:77
mcdata doctor
```

If the platform does not allow `--privileged`, run the NVIDIA Xorg server on the host and mount the
X socket into the container instead:

```bash
docker run --rm -it \
  --gpus '"device=0"' \
  --ipc=host \
  --shm-size=16g \
  -e NVIDIA_DRIVER_CAPABILITIES=all \
  -e DISPLAY=:77 \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  -v "$PWD:/workspace/mcdata" \
  -w /workspace/mcdata \
  <image> bash
```

In both cases, `mcdata doctor` must report NVIDIA-backed OpenGL before collecting final
shader/water-reflection data.

The repo includes:

- `docker/l40s.Dockerfile`: a baseline Ubuntu 24.04 image with Java 21, Python, ffmpeg, X11 tools,
  and GLVND libraries.
- `docker/l40s-run.example.sh`: commands for host-Xorg and privileged inside-container-Xorg modes.

## Temporary storage

Use `scripts/mcdata_env.sh` before local runs to keep generated data away from the system disk:

```bash
source scripts/mcdata_env.sh
mcdata run-matrix --profiles matrix_low,matrix_textured,matrix_shader_high --duration 60
```

The script prefers `/root/mas/bigdata1/tmp/mcdata`, then `/root/nas/bigdata1/tmp/mcdata`, and falls
back to `.mcdata/tmp` if neither is writable. It sets `TMPDIR`, `MCDATA_OUTPUT_DIR`,
`MCDATA_MAIN_DIR`, and `MCDATA_WORK_DIR`.

For planned cleanup of a full `/tmp`, use:

```bash
MCDATA_TMP_ROOT=/dev/shm scripts/clean_tmp_plan.sh plan
MCDATA_TMP_ROOT=/dev/shm scripts/clean_tmp_plan.sh apply
```

The cleanup script only targets same-owner temporary patterns and refuses to delete candidates that
are still open by a process.
