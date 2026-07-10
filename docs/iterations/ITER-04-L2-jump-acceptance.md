# ITER-04 L2 deliberate-jump acceptance

## Current verdict

The input and route fix is implemented and CPU-verified, but **fresh true-GPU acceptance is still
pending**. The earlier `e8ed51b` run remains rejected: all four instantaneous `xdotool key space`
commands appeared as executed in the replay log, but only one produced a measured jump
(`peak_delta_y=1.249`); the route later clipped the solid scene pillar at `[5,64,9]` and reached
`max_route_deviation=11.696` blocks. Its manifest `l1_l2` claim is not accepted training evidence.

## Fixed showcase contract

`curriculum_l2_jump_showcase_60s` now uses a dedicated 93-cell, 58.973-second action route. L2, L3,
and L4 use byte-identical horizontal routes. The route has two-cell obstacle clearance; the known
pillar at x/z `[5,9]` is three Chebyshev cells from the closest route cell. The canonical compact
route digest
`sha256(json.dumps(route, sort_keys=True, separators=(",", ":")))` is
`401193ab057d4423ab685008ace049392aa7d13849717fe80c99982b94ce187c`.

Four jumps occur while the player is already running on long straight corridors:

| jump | route index | x/z | Space hold |
|---|---:|---|---:|
| `jump_west_run` | 22 | `[-6,-6]` | 0.160 s |
| `jump_north_run` | 34 | `[-2,2]` | 0.160 s |
| `jump_south_run` | 56 | `[2,4]` | 0.160 s |
| `jump_east_run` | 71 | `[6,-7]` | 0.160 s |

Each semantic action is exactly two trajectory events: Space `down`, then Space `up` 0.160 seconds
later. Both carry the same unique `jump_id`, route index, duration, and explicit press/release phase.
The full hold lies inside a `W` hold with at least 0.30 seconds of running lead-in and landing margin.
The accepted duration range is 0.12–0.18 seconds so the input spans multiple 20 TPS client ticks
without turning an ordinary jump into a long, ambiguous key state. A backend failure on either event
aborts replay; a missing event, wrong execution status, mismatched pair, unclosed key, or partial replay
cannot count L2. Navigator recovery Space/S remains a separate controller statistic.

The generated trajectory, checked-in golden, and documented JSON are byte-identical at SHA-256
`a24853679b40271ee883eff511612347ea1910ff82eaaf2ecabb1b1a7d8f3710`. The route visualization is
`docs/trajectories/curriculum_l2_jump_showcase_60s.png`.

## Mandatory physical-effect gate

Input dispatch is necessary but not sufficient. The capture pipeline now writes deterministic
`action_effect_report.json` after `positions.jsonl` and before `manifest.json`. Schema v1 binds the
exact trajectory, replay log, and position trace SHA-256 values; the manifest binds the report SHA and
`report_id`. The audit requires a maximum observed position-sample gap of 0.20 seconds (formal action
showcases request 0.10 seconds) and proves all of the following:

- four planned and four fully dispatched press/release pairs, with no inherited/released-key anomaly;
- for each actual press time, a baseline-relative `peak_delta_y >= 0.8` block before the next jump;
- a post-peak sample returning within 0.05 block of that jump's grounded baseline;
- every press/release dispatch is `executed`, aligned to its trajectory time within 0.05 seconds, and
  belongs to the semantic down/hold/up sequence rather than recovery Space;
- every evidence window is fully covered without missing/non-monotonic samples.

`mcdata qa-run` recomputes the report, includes its hash in QA evidence, emits a warning and exits 2
when it fails. Dataset indexing recomputes it again and requires `accepted=true`, four planned/four
verified jumps, exact semantic-count agreement, and an exact manifest claim; this cumulative gate also
applies to L3/L4 without weakening their server receipt gates. The compact checked-in regression data
at `tests/fixtures/action_effect_first_l2_rejected.json` preserves the first rejected run's exact four
scheduled/actual dispatch times and measured peak deltas: all four legacy semantic tap inputs were
dispatched, but only one reached the 0.8-block rise and landing threshold, so it remains rejected.
