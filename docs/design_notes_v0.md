# Pipeline Plan

## Version and Platform Choice

Use Minecraft Java Edition first.

- Linux automation is direct; Bedrock on Linux normally means Android emulation, Wine/Proton,
  or unofficial launch paths.
- Java Edition has Fabric, Sodium, Iris, resource packs, shader packs, and launch metadata APIs.
- PortableMC handles official runtime, assets, libraries, Fabric loader, and classpath generation.

Current online resolution on 2026-07-07:

- Mojang latest release: `26.2`.
- Fabric/Sodium/Iris support exists for `26.2`, so the default modded profile also uses `26.2`.

The code still resolves this dynamically so future runs can move forward when the mod ecosystem
updates.

## Repository Split

`mcdata.actions`

- Scripted actions: deterministic camera pans, walking grids, fixed scene probes.
- Random actions: reproducible random walks and camera jitter.
- External-policy hook: place for MineRL/VPT/Voyager/MineDojo style adapters.

`mcdata.render`

- Instance bootstrap with PortableMC.
- Low/medium/high graphics options.
- Launch and ffmpeg x11 capture.
- Remote tmux command generation.

`mcdata.packs`

- Modrinth resolution for Fabric mods.
- Resource pack installation.
- Shader pack installation and Iris config.

## Execution Stages

1. Local bootstrap on H100 host.
   - Run `mcdata doctor`.
   - Run `mcdata bootstrap --profile fabric_low`.
   - Run `mcdata run --profile fabric_low --dry-run --with-server --replay-actions --strategy idle_pan --duration 5`.
   - This validates downloads, version selection, configs, and launch command generation.

2. First true render on a graphics-capable host.
   - Use RTX 4090 or L40S with an active X server and NVIDIA GL stack.
   - Run `export DISPLAY=:0`.
   - Run `export MCDATA_CAPTURE_SIZE=1920x1080`.
   - Run `mcdata run --profile fabric_low --with-server --replay-actions --capture --strategy idle_pan --duration 60`.

3. Quality expansion.
   - Bootstrap `fabric_faithful_bsl`, `fabric_complementary_high`, and `barebones_fast`.
- Record a matrix over profile x world seed x action strategy.
- Each run starts a local offline-mode dedicated server and connects the client to it.

4. Policy expansion.
   - Add real policy adapters under `src/mcdata/actions`.
   - Expected first integrations: MineRL-style keyboard/mouse policies, VPT-like behavior cloning
     policies, scripted exploration for coverage, and external command streaming.

## Remote 4090 Flow

Create `configs/hosts.yml` from `configs/hosts.yml.example`, then print a tmux command:

```bash
mcdata remote-command --host rtx4090 --profile fabric_complementary_high --capture
```

That command starts a detached tmux session on the remote project directory. The remote machine
still needs a prepared checkout and Python environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

## Current Blocker for Local True Rendering

The H100 host has no `DISPLAY`, no `Xvfb`, and no `glxinfo`/desktop GL check installed. That is
fine for bootstrap and dry-run validation, but not enough for real Minecraft frame capture.
The real rendering host also needs `xdotool` for trajectory replay.

On the current 4090 host, `DISPLAY=:99` is an Xvfb session backed by Mesa llvmpipe. It can record
Minecraft for smoke tests, but high-quality shader capture should use the real `:0` desktop or a
VirtualGL/TurboVNC session that exposes the NVIDIA OpenGL device.
