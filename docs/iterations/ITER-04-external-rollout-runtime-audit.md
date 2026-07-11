# ITER-04 external rollout runtime audit

Date: 2026-07-11

Status: the Solaris trace-only boundary is implemented and quarantined. Two real
MineStudio/VPT source rollouts completed, but both were rejected before import. No
external-policy episode has been replayed in MC26.2 or admitted to a prompt-edit pair.

## Architecture decision

The adopted design is a hybrid control plane:

```text
feedback/A* planner       -> stable navigation baseline
Solaris/Mineflayer skills -> future scripted exploration and interaction coverage
MineStudio/VPT            -> future human-like local action/camera distribution
human demos               -> future real long tail
             all producers -> canonical 20 Hz trace
                           -> neutral MC26.2 reference replay/effect QA
                           -> same snapshot + same trace SHA -> N visual endpoints
```

The existing OS-input executor is retained for exact replay of known canonical
traces. Mineflayer is not a wholesale replacement for the render/data pipeline; it is
a future producer of exploratory traces. This preserves the already working MC26.2
profile, manifest, scene, effect-QA and N-way machinery.

The Solaris architecture audit supports the colleague's main recommendation, with
three important qualifications:

1. protocol-level control removes X focus, stuck-key and pixel-calibration failures,
   but Pathfinder can still time out, dig, place or parkour; realized effect/route QA
   remains mandatory;
2. Solaris action and camera streams are post-aligned by wallclock to the first later
   frame, not emitted atomically by one render callback, so timing QA cannot be
   deleted;
3. VPT's value is not primarily a file format. Its useful contribution is a learned,
   human-like temporal distribution; the runtime experiments below demonstrate why
   sampling and realized coverage must both be measured.

## Solaris phase-1 result

Commit `99492f51b8fd91b8044f3fe189b899b7ac3e3376` adds the
`scripted_skill_agent` source category, a strict normalized 20 Hz importer, canonical
trace compilation, provenance schemas and two independent quarantine gates. The
checked-in fixture is synthetic; no Solaris Docker or Minecraft runtime was launched.

The bridge binds Solaris commit `430f56f787405d6a7818e79e95e4ddee026dd6b7`,
Minecraft 1.21, episode/role/RNG records, world snapshot bytes, normalized actions and
target calibration. `place_block_success`, mount/dismount, mine and place-entity
receipts remain annotation-only; they never fabricate replay input or raise the
curriculum level.

Both states are intentionally immutable in phase 1:

- `source_timing_not_yet_validated` blocks replay;
- `target_replay_not_yet_validated` independently blocks dataset admission.

The detached clean-commit validation for `99492f5` passed standards and Ruff plus
`564` tests. The full branch later passed `573` tests after the MineStudio and lighting
work was added. Detailed blockers and promotion requirements are in
`docs/solaris_engine_phase1_contract.md`.

## MineStudio runtime and pins

The isolated L40S environment used:

- MineStudio 1.1.5 checkout
  `278aa8553668d591339dbf30d281594ed06ee882`;
- foundation-model revision `17a5f43b30c4f734489902fdc6a55bf47781be3a`;
- simulator-engine revision `48d4809cfddc7e2b85295e8c39b3c5e8c6d46ae7`;
- Java 8 and a CUDA policy device;
- seed 401, exactly 1,200 actions at 20 Hz, and a 60-second source video.

The checkpoint/model-card license remains unknown, so all artifacts are research-only
even if later compatibility gates pass.

## Rollout 1: deterministic argmax rejection

The first real rollout mechanically completed, but the policy collapsed:

- movement, sprint and sneak: `0/1200` ticks;
- camera: one unique vector, exactly `[0.0, -0.6153942662]` on every tick;
- camera transitions: zero.

The video confirms a stationary player with constant one-way pan. Rollout SHA-256 was
`e7e30a68bf06aabb1cc190609bfb1e28fc7645c54773fbae6d4d9548316b35b3`.
This reproduces the behavior the user rejected and proves that deterministic argmax
is not an acceptable proxy for VPT action diversity.

Commit `758ec06ebe43d2bf93472e371ffa0351e2849c83` therefore added an explicit
`--sampling-mode` contract. The legacy argmax default is preserved. Seeded stochastic
mode fixes Python, NumPy and Torch RNGs but conservatively records
`seeded_rng_not_cross_run_validated` rather than claiming untested reproducibility.

## Rollout 2: stochastic diversity pass, navigation coverage rejection

The seeded-stochastic rerun mechanically completed with rollout SHA-256
`dfb706e937fd65b46a87700c03a31c8550febce2481e926a4cb9b0025b4031bb`.
It fixed the action collapse:

| Signal | Observed result |
| --- | ---: |
| movement-any | 722/1200 ticks (60.17%) |
| distinct movement states / transitions | 19 / 643 |
| distinct camera vectors / transitions | 94 / 695 |
| pitch / yaw sign changes | 162 / 127 |
| combined action states / transitions | 239 / 1028 |
| full no-op | 46/1200 ticks (3.83%) |

It still failed realized navigation:

- the initial motor no-op lasted 256 ticks (12.8 seconds);
- from roughly 24 through 58 seconds the video remains around the same grass/ledge
  area despite high movement-key duty;
- no source position truth was recorded, so the likely inability to cross the ledge
  with jump masked remains a visual hypothesis, not a claimed fact.

The strict audit verdict is `rejected_realized_navigation_coverage_failure`.
`import_allowed`, `target_replay_allowed`, and `dataset_allowed` are all false. The
rollout was deliberately not imported and not replayed in MC26.2; replaying a source
failure would produce activity, not useful autonomous roaming evidence.

Local ignored evidence:

- `runs/evidence/iter04_minestudio_phase2/source_rollout_neutral_seed401_ticks1200_v1/`;
- `runs/evidence/iter04_minestudio_phase2/source_rollout_neutral_seed401_ticks1200_seeded_stochastic_v1/`;
- stochastic audit SHA-256:
  `0572af252b1cc904411fcca8b6fefd47d1c6e76c1280193cd931f4c108490c91`;
- stochastic 2-second contact sheet SHA-256:
  `85d4d17ba71ba3b65cc49f3ac0a391c95015d85dcc77dc73a4cafdc0754515b6`.

## Next promotion gate

Before another VPT episode can be imported, the source runner must record contiguous
per-tick position truth and enforce displacement/coverage/stall gates. The next
experiment should be explicitly L1+L2 with jump enabled while use, attack, inventory,
drop and hotbar remain masked. Source jump permission is not target-side jump-effect
proof, so the imported artifact must remain quarantined until MC26.2 position/effect
QA passes.

In parallel, a real Solaris rollout-only producer should first fix all unseeded
randomness, capture post-dispatch actions at a proven controller/physics tick boundary,
disable implicit Pathfinder dig/place/parkour for L1, and export a restorable scene
snapshot. Orbit/chase/walk are the first useful episodes; dynamic PvP remains blocked
until a multi-actor trace bundle exists.
