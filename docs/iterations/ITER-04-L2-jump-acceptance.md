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

Input dispatch is necessary but not sufficient. Until physical effect evidence is added to the
manifest schema, every candidate L2/L3/L4 run must pass a separate independent audit bound to the
exact trajectory, replay-log, positions, and video SHA-256 values. The audit must use position probes
at no worse than 0.10-second cadence and prove all of the following:

- four planned and four fully dispatched press/release pairs, with no inherited/released-key anomaly;
- for each actual press time, a baseline-relative `peak_delta_y >= 0.8` block before the next jump;
- a post-peak sample returning within 0.05 block of that jump's grounded baseline;
- exactly four disjoint elevation groups attributable to the four declared windows and no comparable
  unplanned vertical excursion;
- route-reference PASS at `max_deviation <= 3.0` blocks, no route warning, and visible rise/landing in
  independently extracted keyframes.

The manifest's semantic count alone must not override this gate. L3 and L4 may be launched for final
acceptance only after the fresh L2 run passes it; otherwise the cascade stops and all higher buckets
remain empty.
