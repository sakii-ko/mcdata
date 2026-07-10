# External action rollouts: canonical trace and N-way replay

## Why this is a separate axis

A* and the feedback planner remain useful for safe coverage, deterministic camera paths, and cheap
L1 data. They are not a sufficient model of human play: they do not supply the long-tail timing and
co-occurrence of jumps, item selection, use/place, attack, recovery, and task transitions. We
therefore keep two orthogonal labels:

| Axis | Values | Meaning |
|---|---|---|
| action curriculum | `l1`, `l1_l2`, `l1_l2_l3`, `l1_l2_l3_l4` | what was semantically planned and proved by effect QA |
| action source | `scripted_astar`, `feedback_planner`, `human_demo`, `learned_visual_policy`, `llm_skill_agent` | who produced the primitive input stream |

A VPT rollout containing only navigation can still be L1. An A* trajectory with the controlled
placement executor can be L3. Primitive Space/use/attack input never upgrades a curriculum bucket
by itself; the existing semantic and physical-effect gates still apply.

## Canonical action artifact

`src/mcdata/schemas/canonical_action_trace.schema.json` defines schema v1. Every trace is exactly
20 Hz and records:

- producer name/version and model or agent-config SHA-256 (`null` only for a human producer);
- the source environment name/version, its own Minecraft version, action format, and tick rate;
- source world seed plus the exact starting snapshot/checkpoint ID and SHA-256;
- each tick's held movement buttons, derived press/release edges, pitch/yaw deltas in degrees,
  hotbar selection, use, attack, inventory, and optional human-reviewed semantic annotations;
- adapter name/version/parameters and a semantic `trace_sha256` computed from canonical JSON with
  the self-hash field removed.

Source and target Minecraft versions are intentionally different fields. In particular, importing
an older MineRL/VPT trace must not relabel its source as Minecraft 26.2.

The first executable adapters are pure Python and do not install MineRL/MineStudio:

- `MineStudioVPTEnvAdapter` consumes the decoded MineStudio `action_type="env"` dictionary
  (`forward`, `jump`, `attack`, `use`, `hotbar.1`...`hotbar.9`, and camera `[pitch,yaw]` in
  degrees). It rejects hierarchical `{buttons,camera}` policy output because decoding that integer
  action requires the exact version/config of MineStudio's `CameraHierarchicalMapping` and
  `ActionTransformer`. Capture the post-decoding env action instead of guessing the mapping.
- `OpenAIVPTRecorderV7Adapter` consumes the documented 7.x contractor JSONL shape. It derives
  degree deltas from recorded absolute yaw/pitch, requires an explicit 0- or 1-based hotbar choice,
  and rejects GUI or unknown controls rather than silently dropping them.

This matches the current MineStudio simulator contract, which exposes distinct agent and env action
spaces and performs an explicit `agent_action_to_env_action` transform. OpenAI's VPT release states
that demonstrations were downsampled to 20 Hz and pairs each segment with a starting checkpoint.
Primary references: [MineStudio simulator source](https://craftjarvis.github.io/MineStudio/_modules/minestudio/simulator/entry.html),
[OpenAI VPT data format](https://github.com/openai/video-pre-training#contractor-demonstrations).

## Library conversion spike

```python
from mcdata.action_trace import (
    CameraCalibration,
    build_native_trace,
    compile_native_trace,
)
from mcdata.external_action_adapters import MineStudioVPTEnvAdapter

trace = build_native_trace(
    MineStudioVPTEnvAdapter(),
    decoded_env_actions,  # one post-decoding env-action dict per 20 Hz tick
    producer={
        "name": "MineStudio VPT",
        "version": "checkpoint-release-id",
        "model_sha256": model_file_sha256,
    },
    source_environment={
        "name": "MineStudio",
        "version": minestudio_version,
        "minecraft_version": source_mc_version,
        "action_format": "minestudio_env_action_v1",
        "action_tick_rate_hz": 20,
    },
    world={
        "seed": source_seed,
        "snapshot_id": snapshot_name,
        "snapshot_sha256": snapshot_sha256,
    },
)

trajectory = compile_native_trace(
    trace,
    camera_calibration=CameraCalibration(
        calibration_id=calibration_id,
        artifact_sha256=calibration_artifact_sha256,
        yaw_pixels_per_degree=yaw_pixels_per_degree,
        pitch_pixels_per_degree=pitch_pixels_per_degree,
    ),
)
```

The compiler uses cumulative decimal rounding, so compiling the same trace and calibration is byte
deterministic and does not lose repeated sub-pixel turns. It has no default pixels-per-degree value:
the target client/profile calibration artifact is mandatory. A compiled trace with jump/use/attack
is marked `requires_semantic_effect_validation`; it is replayable, but it is not automatically an
accepted L2/L3/L4 sample.

## Roll out once, render N ways

The only valid paired-data order is:

1. Materialize and hash one starting world snapshot.
2. Run a human/policy/task agent once under a designated neutral rollout view; save its native
   action trace and any observations separately.
3. For each render endpoint, restore the exact same snapshot, apply the same scene/profile-independent
   reset, then replay the same `trace_sha256` with the endpoint's measured camera calibration.
4. Run action-effect and alignment QA; reject any endpoint whose world, inventory, entity, route, or
   timing outcome diverges beyond the declared contract.
5. Only then build edit pairs. Pair validation requires both endpoints to share the same canonical
   native-trace SHA; the dataset index records source counts and supports deterministic exact-count
   source sampling.

Running the policy independently under every material/shader is forbidden. It entangles policy
observation shift with the requested render edit and destroys causal pairing.

## Compatibility gate

Before a trace can enter accepted paired data, a source/target combination must prove all of the
following:

- the snapshot is loadable or reproducibly reconstructed in the target Minecraft version, with
  seed, player state, inventory, entities, blocks, and gamerules verified;
- every active source control is represented by the adapter and target replay backend;
- source actions are truly 20 Hz (or have an explicit, separately reviewed resampling artifact);
- the target camera calibration is measured and SHA-bound; no source pixel delta is reused;
- a no-style reference replay passes duration, dispatch, position/yaw, inventory, placement, and
  combat effect gates before N-way rendering;
- repeated reference replays have acceptable state drift. A trace from an incompatible old MineRL
  checkpoint can remain an offline action prior/demo, but cannot be called an exact 26.2 edit pair.

## Source expansion

- Human play/contractor data uses `human_demo`, subject to dataset license and privacy review.
- MineStudio/VPT and future visual policies use `learned_visual_policy`; model file hash and mapper
  version are mandatory.
- Mineflayer/Voyager-style task agents use `llm_skill_agent`. Their high-level code/task transcript
  is provenance, not the replay interface: primitive input must still be captured at the engine
  boundary and converted to the same trace. External LLM calls do not occur inside data replay.
- A* and online position/yaw feedback remain `scripted_astar` and `feedback_planner`. Legacy runs
  are mapped only from known trajectory type/execution-mode pairs; an unknown `external` trajectory
  fails rather than being mislabeled.

Phase 1 is the adapter/schema/compiler/dataset contract and golden tests. The Phase 2 neutral runner
and fail-closed import procedure are specified in
[`minestudio_vpt_phase2_runbook.md`](minestudio_vpt_phase2_runbook.md). Its presence is not evidence
that a heavy rollout or MC26.2 compatibility replay has already run. A target-version compatibility
smoke, semantic segmentation/effect binding, and a small accepted source-balanced cohort remain
required before any long or multi-style production run.
