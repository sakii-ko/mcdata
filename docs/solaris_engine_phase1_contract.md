# Solaris Engine Phase 1: trace-only integration contract

## Decision

We should reuse Solaris Engine's **episode/role/skill organization and controller action
surface**, but not adopt its complete Docker collection stack. The audited source is
[`solaris-wm/solaris-engine@430f56f`](https://github.com/solaris-wm/solaris-engine/tree/430f56f787405d6a7818e79e95e4ddee026dd6b7).
It is a useful producer of diverse scripted multiplayer behavior (navigation, looking, building,
mining, PvP/PvE, and coordinated Alpha/Bravo roles). Its runtime, source Minecraft version,
capture timing, renderer, and world reset are not interchangeable with mcdata's MC26.2 N-way
visual replay contract.

Phase 1 therefore has one narrow boundary:

```text
Solaris skill/episode rollout (future instrumented producer)
  -> normalized 20 Hz controller-boundary JSONL
  -> strict, content-addressed mcdata native trace
  -> quarantined compiled trajectory
  -X-> MC26.2 replay or dataset admission
```

No Docker image, Paper server, camera bot, Mineflayer runtime, or Solaris postprocessor is imported
into mcdata. The checked-in fixture is synthetic. This integration has **not run Solaris or
Minecraft and has produced no accepted episode**.

## Action source and normalized boundary

Solaris is classified as `scripted_skill_agent`. It is deliberately distinct from:

- `scripted_astar`: a route planner, not an episode/skill program;
- `llm_skill_agent`: a model-generated high-level task program;
- `learned_visual_policy`: a visual policy such as VPT.

Like every external producer, `scripted_skill_agent` requires a content-addressed canonical native
trace. The importer accepts only `solaris_normalized_controller_boundary_v1`, exactly 20 Hz, with
contiguous zero-based ticks. It does not accept Solaris' existing render-sampled action arrays.
Every tick must explicitly contain:

- held `forward`, `back`, `left`, `right`, `jump`, `sprint`, and `sneak` booleans;
- camera `yaw` and `pitch` delta in degrees, using right-positive yaw and down-positive pitch;
- single-tick `attack` and `use` impulses, plus a 1-based `hotbar` selection or null;
- canonical semantic annotations and explicit annotation-only events.

The current Solaris file documents camera deltas in Mineflayer internal radians with the opposite
axis signs. A future upstream instrumenter must perform and test that conversion before this
importer. The importer will not infer units, axis order, timing, held state, or impulses. Two
consecutive `attack` or `use` impulses are rejected because canonical trace v1 cannot distinguish
two taps from one held input.

### Semantic actions are not controller inputs

The audited Mineflayer fork exposes one-off semantic events. In particular, `place_block` is a
placement-success notification, not proof of the exact earlier physical use-input tick. It must be
normalized as:

```json
{
  "action": "place_block_success",
  "mode": "annotation_only",
  "receipt_id": "producer-stable-id"
}
```

The adapter preserves that receipt as
`solaris_annotation_only:place_block_success:<receipt_id>` and leaves `use=false` unless an
independent controller-boundary use impulse is present. It never fabricates a use click.

`mount`, `dismount`, `mine`, and `place_entity` also have no lossless canonical-v1 replay primitive.
They are accepted only with `mode=annotation_only`; an attempt to mark any of them replayable fails
closed. Annotation-only events do not change the curriculum level. Real explicit jump/use/attack
inputs still set `requires_semantic_effect_validation`; they cannot be relabeled as accepted L2/L3/L4
without target-side effect evidence.

## Provenance and quarantine

`rollout_manifest.json` and the canonical trace bind all of the following:

- Solaris repository and exact commit `430f56f787405d6a7818e79e95e4ddee026dd6b7`;
- source Minecraft `1.21` and normalized action format at 20 Hz;
- episode ID, one of the 21 pinned episode types, and Alpha/Bravo role;
- `seedrandom` 3.0.5 base/episode seed record and its semantic SHA-256;
- world seed, immutable snapshot ID, snapshot artifact bytes, and SHA-256;
- normalized action artifact path, byte size, tick count, and SHA-256;
- target MC26.2 profile and camera-calibration artifact SHA-256.

The manifest semantic SHA binds these references, and import also requires the expected rollout SHA
as an independent trust root. Files are rehashed before parsing. Duplicate JSON keys, non-finite
numbers, path traversal, non-LF JSONL, unknown fields, non-contiguous ticks, and provenance drift are
rejected.

Two mandatory states remain literal and fail closed:

| Gate | Current state | Consequence |
| --- | --- | --- |
| source controller/capture timing | `source_timing_not_yet_validated` | trajectory cannot enter mcdata replay |
| MC1.21 -> MC26.2 replay/effects | `target_replay_not_yet_validated` | run manifest/dataset admission is rejected |

The CLI command `mcdata import-solaris-rollout` only writes quarantine artifacts. The main replay
entry point rejects their binding before Minecraft launch, and dataset indexing independently
rejects the same states.

## Audited blockers at commit `430f56f`

These are upstream facts that must be fixed or measured before either state can advance:

1. There are **33** direct `Math.random()` call sites under `controller/`. They bypass the shared
   per-episode `seedrandom` stream, so recording only `bot_rng_seed` does not reproduce all choices.
2. The viewer/recorder cadence is configured as **50 ms** (nominal 20 Hz), while one-off semantic
   actions behave as asynchronous tap windows. A 50 ms sample is not yet proven to capture the
   causal controller tick without loss, duplication, or phase offset.
3. `controller/act_recorder/act_recorder.py` writes `extra_info.seed = 42`, regardless of the actual
   world. For most runs `generate_compose.py` constructs the server seed from `instance_id` plus
   wall-clock time. Neither value is an immutable/restorable snapshot contract.
4. `initializePathfinder` enables both `movements.canDig` and `movements.canPlaceOn` by default, and
   several building paths re-enable them. Navigation may therefore mine or scaffold implicitly;
   a high-level `goto` transcript is not a lossless primitive action trace.
5. Existing action files are render sampled and use Mineflayer-internal radian camera deltas. They
   do not satisfy the normalized degree/controller-boundary input contract.
6. Solaris targets Minecraft 1.21; mcdata target rendering is MC26.2. Same-trace replay, action
   effects, inventory/hotbar semantics, collision, and world snapshot restoration are unvalidated.

## Promotion requirements

Before any real Solaris episode may be replayed, the producer needs an audited post-dispatch action
hook with one record per server/controller tick, deterministic replacement of all 33 random call
sites, disabled-or-explicit Pathfinder dig/place actions, and an immutable world snapshot export.
We then need clock/impulse conformance tests against server receipts, followed by same-snapshot
MC26.2 reference replay with route, camera, jump, placement, and combat effect QA as applicable.
Only a new versioned contract and evidence can replace either quarantine status.
