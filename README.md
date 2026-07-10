# mcdata

Minecraft rendering data collection pipeline for Linux.

The repo is split into three surfaces:

- `mcdata.actions`: action strategies that generate camera/key trajectories.
- `mcdata.render`: Minecraft instance bootstrap, launch, capture, and remote tmux runs.
- `mcdata.packs`: resource-pack, shader-pack, and mod management.

The default path is Java Edition. It is easier to automate on Linux than Bedrock, does not
require Proton, and has mature Fabric/Sodium/Iris support for performance and shaders.

## Quick start

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .

mcdata doctor
mcdata bootstrap --profile fabric_low
mcdata run --profile fabric_low --dry-run --with-server --replay-actions --strategy idle_pan --duration 5
```

On a real rendering machine with an X/GL desktop:

```bash
export DISPLAY=:0
export MCDATA_CAPTURE_SIZE=1920x1080
export MCDATA_CAPTURE_FPS=24
mcdata run --profile fabric_low --with-server --replay-actions --capture --strategy idle_pan --duration 60
```

For a 3-way render matrix with the same server world and the same generated trajectory:

```bash
export DISPLAY=:0
export MCDATA_CAPTURE_SIZE=1920x1080
export MCDATA_CAPTURE_FPS=24
mcdata run-matrix \
  --profiles matrix_low,matrix_textured,matrix_shader_high \
  --strategy ground_astar_loop \
  --duration 60
```

All 19 matrix profiles share `world_profile: render_matrix_base`, seed, scene, player reset, and
replayed action JSON. The 18 daytime profiles additionally share the exact complete world-state,
so only their render stack changes. `matrix_night_complementary` deliberately changes time to
midnight, so it is a separate controlled world-state variant rather than part of the strict
rendering-only cohort. The baseline uses vanilla resources, the textured pass uses
Faithful 32x with BSL, and the high pass uses Faithful/Fresh Animations with Complementary
Reimagined. The default `ground_astar_loop` strategy plans a deterministic A* route over the
ground around water, glass, foliage, lava, torches, redstone lights, sea lanterns, glowstone, and
a beacon so shader water/reflection/emission differences are visible in a real walking capture.
It uses one-cell obstacle clearance and currently contains 117 grid cells / 60 events over
38.937 seconds. Matrix world setup also disables random block ticks, removes stale dropped items,
clears persisted player inventory, and grants recipes before the 15-second warmup so scene/HUD
state remains stable and recipe notifications are gone before capture.

Additional render profiles are available for broader material/shader coverage:
`matrix_night_complementary`, `matrix_default_hd_bsl`, `matrix_default_hd128_bliss`,
`matrix_dramatic_solas`, `matrix_faithful_sildurs`, `matrix_emissive_makeup`,
`matrix_patrix_unbound`, `matrix_better_leaves_solas`, `matrix_default3d_miniature`,
`matrix_simplista_unbound`, `matrix_stylista_bliss`, `matrix_realiscraft_bsl`,
`matrix_glowing_ores_unbound`, `matrix_connected_glass_bsl`,
`matrix_euphoria_complementary`, and `matrix_solas_patrix`.
The night profile keeps the same ground route and uses brighter client-side options so the frame is
not black while still exercising night/moon/emissive lighting.

For a persistent remote 4090 run:

```bash
mcdata remote-command --host rtx4090 --profile fabric_complementary_high --capture --duration 60
```

Copy the printed command to the 4090 login node or run it through your own SSH orchestration.
The project intentionally does not guess credentials or hostnames.

## Profiles

Profiles live in `configs/profiles.yml`.

Shader profiles can declare Iris pack options without relying on prior GUI state. Keys and values
must be quoted, single-token strings; bootstrap writes them deterministically to the sidecar for
the exact resolved ZIP filename:

```yaml
shader_options:
  MATERIAL_FORMAT: "1"
  WATER_REFLECTIONS: "true"
```

Configuring non-empty `shader_options` on a profile without one selected shader ZIP is an error.

- `vanilla_low`: latest official release, low graphics.
- `fabric_low`: latest version that supports Fabric API, Sodium, and Iris.
- `fabric_faithful_bsl`: Faithful 32x + BSL shader.
- `fabric_complementary_high`: Faithful/Fresh Animations + Complementary Reimagined.
- `matrix_low`, `matrix_textured`, `matrix_shader_high`: aligned 3-way render-quality profiles
  for the same world/action trajectory.

Version resolution is dynamic:

- Mojang version manifest supplies the latest official release.
- Modrinth API supplies the newest compatible Fabric mods, resource packs, and shaders.

## Data Layout

Generated files are kept out of source control:

```text
.mcdata/
  launcher/          # shared libraries/assets/versions downloaded by PortableMC
  instances/         # per-profile game dirs: mods, options, resourcepacks, shaderpacks
  servers/           # per-profile local dedicated servers and worlds
runs/
  <timestamp>/       # capture outputs and metadata
```

After per-run QA, strict-cohort comparison, and manual visual review, build a deterministic
batch index and checksum manifest:

```bash
mcdata dataset-index runs/accepted_full19 \
  --expected-profiles "$ALL_19_MATRIX_PROFILES" \
  --primary-profile matrix_low \
  --generator-commit "$(git rev-parse HEAD)" \
  --strict-compare-report runs/accepted_full19/qa_compare_noon18/qa_compare_report.json \
  --diagnostic-compare-report runs/accepted_full19/qa_compare_all19/qa_compare_report.json \
  --visual-review runs/accepted_full19/visual_review/review.json
sha256sum -c runs/accepted_full19/SHA256SUMS
```

The index automatically groups the 18 identical noon world states into the strict rendering
matrix and keeps the midnight run as a controlled variant. It is marked `accepted` only when the
expected profile set, manifests, actual artifact hashes, 60-second 1280×720/24fps captures,
resource-pack runtime gates, route/alignment QA, and explicit visual review all pass.

## Notes

This machine currently only needs to be able to bootstrap and dry-run. True rendering requires
an X/GL stack (`DISPLAY`, OpenGL/Vulkan driver, window manager) and benefits from a consumer or
workstation GPU such as RTX 4090 or L40S. H100 is usable for data processing, but usually not the
right target for interactive game rendering.

Action replay uses `xdotool`, so install it on the real rendering host. The client joins a local
offline-mode dedicated server with a fixed seed, which avoids recording only the menu screen.

For Xvfb smoke tests, use the full virtual screen size:

```bash
source scripts/mcdata_env.sh
Xvfb :99 -screen 0 1280x720x24 -ac -nolisten tcp &
export DISPLAY=:99
export MCDATA_CAPTURE_SIZE=1280x720
export MCDATA_CAPTURE_FPS=24
mcdata run --profile vanilla_low --with-server --capture --duration 90
```

Xvfb usually reports Mesa `llvmpipe`, so it validates window/capture automation and shader-pack
startup, but not RTX 4090 rendering performance or final water/reflection quality. For real
shader data, use an accessible NVIDIA-backed Xorg/VirtualGL/TurboVNC session. See
[`docs/headless_gpu.md`](docs/headless_gpu.md) for the headless NVIDIA Xorg and L40S container
setup.
