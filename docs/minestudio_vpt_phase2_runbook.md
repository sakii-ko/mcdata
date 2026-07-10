# MineStudio/VPT Phase 2 neutral rollout runbook

## Scope and current status

This runbook prepares one reproducible **source-engine rollout artifact**. It does not claim that a
rollout has already run, that MineStudio state is directly restorable in Minecraft 26.2, or that a
target replay has passed compatibility QA. The heavy simulator, engine, checkpoint, Java 8, and GPU
environment stay outside the normal `mcdata` virtualenv.

The two environments must never be conflated:

| Role | Environment | Contract |
|---|---|---|
| source rollout | MineStudio 1.1.5 / MineRL engine, Minecraft 1.16-era (`1.16.5` in the artifact) | policy observes the neutral source renderer once and emits a 20 Hz primitive trace |
| target render | mcdata Minecraft 26.2 + Fabric/Iris profiles | restore/reconstruct state, replay the imported trace with a measured target-client camera calibration, then run compatibility/effect QA |

The source reset used here is explicitly `procedural_seeded_reset_only`. Its SHA binds the exact
reset recipe, but it is **not** evidence of a restorable binary world snapshot. Until target 26.2
state reconstruction and repeated reference replay pass, imported trajectories remain
`target_replay_not_yet_validated` and cannot enter exact prompt-edit pairs.

## Immutable pins

| Artifact | Revision | SHA-256 |
|---|---|---|
| MineStudio | version `1.1.5`, git `278aa8553668d591339dbf30d281594ed06ee882` | checkout commit is verified; tracked dirty state is rejected |
| `CraftJarvis/MineStudio_VPT.foundation_model_1x` | HF `17a5f43b30c4f734489902fdc6a55bf47781be3a` | `model.safetensors`: `475fbd0df655ad77c3e3f602d157f4273032bff8e6e82c3863a992f5b03753f9`; `config.json`: `d088a1f68ca44cac73d0efe1af7b7df4ade5994b39360da7fe74cfb6b282cbd2` |
| `CraftJarvis/SimulatorEngine` | HF `48d4809cfddc7e2b85295e8c39b3c5e8c6d46ae7` | `engine.zip`: `293fac6ac72245b3365dce0e8bfbb6396fb94df29b23b6538f3bd7e2eec13ec6`; extracted runtime JAR is hashed per rollout |

The model card and Hugging Face API metadata currently contain no explicit license field. That is
not permission for dataset production: the runner writes `license_status=license_unknown` and
`usage_scope=research_only`. Keep every derived rollout in research-only storage until written
rights are recorded. Sources: [MineStudio repository](https://github.com/CraftJarvis/MineStudio),
[foundation model card](https://huggingface.co/CraftJarvis/MineStudio_VPT.foundation_model_1x), and
[SimulatorEngine repository](https://huggingface.co/CraftJarvis/SimulatorEngine).

## 1. Build the isolated runtime

Use a dedicated disk location; do not put the engine, model, videos, or environments in git.
MineStudio's simulator requires its own compatible Python environment and Java 8.

```bash
export PHASE2_ROOT=/large-disk/mcdata-minestudio-phase2
export MINESTUDIO_REPO="$PHASE2_ROOT/MineStudio"
export MINESTUDIO_HOME="$PHASE2_ROOT/runtime"
export MODEL_DIR="$PHASE2_ROOT/models/foundation_model_1x"

git clone https://github.com/CraftJarvis/MineStudio.git "$MINESTUDIO_REPO"
git -C "$MINESTUDIO_REPO" checkout --detach 278aa8553668d591339dbf30d281594ed06ee882
git -C "$MINESTUDIO_REPO" status --porcelain --untracked-files=no

python3.10 -m venv "$PHASE2_ROOT/venv"
source "$PHASE2_ROOT/venv/bin/activate"
python -m pip install --upgrade pip
python -m pip install -e "$MINESTUDIO_REPO"
java -version
```

The runner verifies that the imported `minestudio` module actually comes from this checkout, not a
different site-package with the same version string. It also rejects a non-Java-8 runtime and fixes
the CUDA deterministic workspace contract before importing Torch.

## 2. Materialize the pinned engine and model

Download through a pinned HF revision, retain `engine.zip`, then verify before extraction. The
commands below describe the operation; this repository task intentionally does not execute them.

```bash
mkdir -p "$MINESTUDIO_HOME" "$MODEL_DIR"
huggingface-cli download CraftJarvis/SimulatorEngine engine.zip \
  --revision 48d4809cfddc7e2b85295e8c39b3c5e8c6d46ae7 \
  --local-dir "$MINESTUDIO_HOME"
printf '%s  %s\n' \
  293fac6ac72245b3365dce0e8bfbb6396fb94df29b23b6538f3bd7e2eec13ec6 \
  "$MINESTUDIO_HOME/engine.zip" | sha256sum --check
unzip -q "$MINESTUDIO_HOME/engine.zip" -d "$MINESTUDIO_HOME"

huggingface-cli download CraftJarvis/MineStudio_VPT.foundation_model_1x \
  config.json model.safetensors \
  --revision 17a5f43b30c4f734489902fdc6a55bf47781be3a \
  --local-dir "$MODEL_DIR"
printf '%s  %s\n' \
  475fbd0df655ad77c3e3f602d157f4273032bff8e6e82c3863a992f5b03753f9 \
  "$MODEL_DIR/model.safetensors" | sha256sum --check
printf '%s  %s\n' \
  d088a1f68ca44cac73d0efe1af7b7df4ade5994b39360da7fe74cfb6b282cbd2 \
  "$MODEL_DIR/config.json" | sha256sum --check
```

## 3. Define the source reset

Copy `external/minestudio_vpt_neutral/reset_contract.example.json` outside the repository and
choose one fixed integer seed. Do not add arbitrary simulator kwargs: the strict field set is part
of the contract. The VPT observation stays 128×128, the source render is 640×360, and
`source_minecraft_version` must remain `1.16.5`.

The runner uses the reset JSON's raw byte SHA as the source snapshot reference. Therefore even a
formatting change creates a new reset artifact, which is intentional and fail-closed.

## 4. Execute one neutral rollout

Choose a fixed positive tick count. A 60-second smoke is exactly 1,200 ticks at 20 Hz.

```bash
python external/minestudio_vpt_neutral/runner.py \
  --minestudio-repo "$MINESTUDIO_REPO" \
  --minestudio-home "$MINESTUDIO_HOME" \
  --model-dir "$MODEL_DIR" \
  --reset-contract "$PHASE2_ROOT/reset_contract.json" \
  --output-dir "$PHASE2_ROOT/rollouts/neutral_seed401_ticks1200" \
  --ticks 1200 \
  --device cuda \
  --save-video
```

`--save-frames` optionally writes one post-step `.npy` image per action tick. `--save-video` uses
MineStudio's recorder as the third callback. Neither option changes action capture.

The simulator is always constructed with `action_type="agent"`. MineStudio performs the pinned
`CameraHierarchicalMapping` and `ActionTransformer.policy2env` mapping first. Callback order is then:

1. `neutral_mask`: force jump, use, attack, inventory, drop, and all hotbar selectors to zero;
2. `post_mapper_env_action_recorder`: copy the final env action;
3. optional video recorder.

Thus the raw JSONL preserves learned forward/back/strafe/sneak/sprint and degree camera deltas, while
advanced controls cannot leak into this first spike. The run is deterministic-policy inference with
fixed Python/NumPy/Torch seeds, deterministic Torch algorithms, exactly 20 Hz semantics, and exactly
the requested number of steps. Early termination is a hard failure. `sim.close()` runs on both the
success and exception paths, and the output directory is published only after all artifacts and
hashes are complete.

Expected output:

```text
rollout_manifest.json
reset_contract.json
env_actions.jsonl
capture.mp4       # only with --save-video
frames/           # only with --save-frames
```

## 5. Import without MineStudio dependencies

Measure the target MC26.2 client's camera response first. The calibration file has exactly these
fields; replace the illustrative values with measured values and bind the correct target profile.

```json
{
  "schema_version": 1,
  "calibration_id": "mc26.2-profile-and-sensitivity-calibration-id",
  "target_minecraft_version": "26.2",
  "target_client_profile": "neutral_reference_profile",
  "yaw_pixels_per_degree": 4.0,
  "pitch_pixels_per_degree": 4.0
}
```

Obtain the printed `rollout_sha256` from the runner or read it from the manifest. Both the expected
hash and expected tick count are mandatory trust-boundary inputs:

```bash
mcdata import-minestudio-rollout \
  "$PHASE2_ROOT/rollouts/neutral_seed401_ticks1200" \
  --expected-rollout-sha256 '<64-lowercase-hex>' \
  --expected-ticks 1200 \
  --camera-calibration "$PHASE2_ROOT/mc26.2-camera-calibration.json" \
  --trace-out "$PHASE2_ROOT/imported/native_trace.json" \
  --trajectory-out "$PHASE2_ROOT/imported/trajectory.json"
```

The lightweight importer verifies the manifest self-hash; exact pins; source/reset/model/engine
provenance; action artifact size and SHA; contiguous tick count; full env-action field set; neutral
mask; source 1.16.5 versus target 26.2 labels; and calibration bytes. It then uses the Phase 1 pure
adapter/compiler. The native trace binds the raw rollout and engine/reset hashes inside its adapter
descriptor. The trajectory binds the calibration SHA and remains L1-only. The run manifest preserves
`target_replay_not_yet_validated`, and `dataset-index` rejects such an episode; documentation alone
cannot upgrade it to accepted data.

## 6. Required compatibility work before accepted N-way data

1. Reconstruct or explicitly migrate the source start state in MC26.2; prove blocks, player pose,
   inventory, entities, gamerules, and world state.
2. Replay the imported trajectory in one neutral MC26.2 reference endpoint and run dispatch,
   position/yaw, inventory, and action-effect QA.
3. Repeat the same reference replay and define/meet a drift threshold.
4. Restore the same target snapshot for each render endpoint and replay the same native trace SHA.
5. Only after pair invariants pass may the episodes enter a strict prompt-edit pair.

Never re-run VPT independently under each material or shader. That would make policy observation
shift part of the render edit and invalidate the pair.
