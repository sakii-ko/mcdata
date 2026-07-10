# ITER-04 L3 deterministic placement acceptance

## Current verdict

Implementation and CPU evidence tests pass; **true-GPU acceptance is still pending**. No episode may
enter `l1_l2_l3` until the 60-second GPU showcase, post-run manifest recomputation, route QA, and video
review all pass. This document does not claim that the current open-loop aim is already calibrated on
the remote Minecraft 26.2 client.

## Showcase contract

`curriculum_l3_place_showcase_60s` preserves the fixed L2 showcase's 93-point route and exact
58.973-second duration. It keeps four 0.160-second running Space down/up pairs at route indices
22, 34, 56, and 71, and adds:

The canonical generated trajectory and checked-in golden are byte-identical at SHA-256
`1ae2682a1404d1a71923a601660875ce943f13002b3b08942ef358b32a2f326d`; the route visualization is
`docs/trajectories/curriculum_l3_place_showcase_60s.png`.

| action | route index | hotbar | block | target | support / face |
|---|---:|---:|---|---|---|
| `place_gold_north` | 26 | 1 | `minecraft:gold_block` | `[-2,65,-8]` | `[-2,65,-9]` / south |
| `place_emerald_east` | 80 | 2 | `minecraft:emerald_block` | `[12,65,-12]` | `[13,65,-12]` / west |

Both target cells are outside the walking route. The trajectory emits an explicit -600/+600 px
horizontal and -150 px vertical aim before the respective placements, then the exact inverse. The
runtime executor is not allowed to teleport or otherwise correct the player's pose during capture.

## Evidence lifecycle

Before capture, the managed server path clears player inventory, dropped items, and non-player
entities; resets targets to air; provisions glass supports and two fixed hotbar stacks; and waits for
unique conditional server markers. The replay worker starts only after this evidence is available.

During capture, each semantic placement executes exactly two extra input primitives: its declared
number key and mouse button 3. The replay record remains
`input_dispatched_pending_probe`; this status is never sufficient to count L3.
Each placement must also be immediately surrounded by a tagged 0.35-second camera aim and its exact
inverse restore. Their dx/dy, route index, semantic-event duration, and restore timestamp are bound
to the placement spec; a missing/tampered event or a false input-backend result rejects the run.
Action IDs, hotbar slots, and placed block types must all be unique within this minimal showcase, so
the pre-capture slot receipt plus the post-capture exact block type binds each number-key selection to
one result instead of allowing an ambiguous same-item slot claim.

After ffmpeg stops but before the client/server are terminated, the pipeline waits for replay to end,
probes each exact target for the declared block, then clears the action item, target, and support. A
second conditional marker proves cleanup. Reset and finalization evidence include command/probe
counts, exact receipt lines, and the byte size plus SHA-256 of the corresponding `server.log` prefix.
The hashed `replay_log.jsonl` binds those records to the trajectory and manifest.
Receipt polling starts at the log byte offset immediately before its command and accepts only an
actual `[Server] <marker>` say-success line, never an earlier receipt or command-error echo. The
post-capture control must be the final replay record and its server-log prefix must strictly extend
the reset prefix.

The episode fails closed if any planned placement input is absent, only some placements dispatch,
the target or face differs, reset/finalization is missing, a receipt line is missing, cleanup fails, the
server-log prefix is absent/tampered, the client exits before finalization, or any cumulative L1/L2
capability is unobserved. In particular, replay evidence for four jump key pairs does not replace the
independent four-rise/four-landing physical-effect gate in
`ITER-04-L2-jump-acceptance.md`.
Rejected runs also attempt a separate, uniquely receipted cleanup for every configured arena before
the managed server is terminated; cleanup failure remains an explicit run error and never upgrades
partial action evidence.

`render/pipeline.py` remains above the R19 file-size guideline because it was already the monolithic
RunPlan/RunState lifecycle owner. The new placement mechanics and evidence validation are split into
`render/placement.py` and `action_placement.py`; pipeline retains only phase ordering, worker error
propagation, and the pre/post-capture hooks so those lifecycle invariants are not duplicated.

## GPU acceptance still required

The first remote showcase must run only after the fixed L2 physical-effect and route gates pass. It
must specifically verify that open-loop waypoint error and Minecraft mouse
sensitivity still put the crosshair on both one-block support faces. A miss is an expected rejected run,
not permission to add an in-capture teleport or to weaken the target probe. Only after both blocks are
visibly placed, the route remains within QA bounds, the manifest independently recomputes two L3
actions, and cleanup receipts pass may the run be indexed in `l1_l2_l3`.
