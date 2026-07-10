# ITER-04 L4 controlled-combat acceptance

## Current verdict

The cumulative L4 executor, deterministic trajectory, evidence validators, and CPU tests are
implemented. **True-GPU acceptance is still pending.** No episode may enter `l1_l2_l3_l4` until a
60-second Minecraft 26.2 showcase passes the independently recomputed manifest, route QA, server
evidence audit, and video review. A camera miss or command/log mismatch is an expected rejected run,
not permission to weaken the probes or add an in-capture teleport.

## Cumulative showcase

`curriculum_l4_combat_showcase_60s` keeps the L1/L2/L3 93-cell route and exact 58.973-second budget.
It retains deliberate running jumps at route indices 22/34/56/71 and deterministic placements at
26/80, then adds one encounter at route index 48:

| field | fixed value |
|---|---|
| target | `minecraft:iron_golem` at exact entity position `[4.5,64,12.5]`, two horizontal blocks east of the stopped player center |
| identity | tag `mcdata_l4_sparring_target`, UUID `4d434441-5441-4c34-8000-000000000004` |
| snapshot | NoAI, fixed `[yaw=0,pitch=0]`, 20 HP, knockback resistance 1.0 |
| player equipment | one `minecraft:wooden_sword` in hotbar slot 3 |
| capture input | slot-3 key, then mouse button 1; no `tp`, `damage`, or server-side attack |

The generated trajectory and checked-in golden are byte-identical at SHA-256
`219af517550950d0f0a71a9a6a2bdcd5b3cc52c4588b1cd18b017e6608d8d0f2`. The reviewed top-down map is
`docs/trajectories/curriculum_l4_combat_showcase_60s.png`; it marks the off-route target and the
route-index-48 aim segment separately from placement targets and jumps.

Taxonomy v1 intentionally permits only this iron-golem target. The managed server is peaceful, so
advertising husk/zombie/skeleton/pillager support without an explicit difficulty snapshot/restore
would be false. Minecraft 26.2 command/serializer bytecode was checked for UUID/NoAI/Health/Rotation
SNBT, `minecraft:knockback_resistance`, scaled `data get ... Health`, `execute on attacker`, and the
snake-case `gamerule spawn_mobs`. The configured lookdev profiles already pin `spawn_mobs=false`;
L4 proves that value at reset/finalization and never changes it.

## Fail-closed evidence lifecycle

Before capture, the composed executor runs the complete L3 reset, proves the combat tag/UUID absent,
creates a fresh `mcdata_l4` scoreboard objective, summons exactly one fixed target, sets/verifies the
knockback attribute, provisions slot 3, and records exact score queries for target count, initial
health, knockback, and `spawn_mobs`. A stale objective makes the unique create-success line absent and
rejects the run. The L4 root reset embeds both the L3 and combat evidence and binds the latest
`server.log` prefix.

During capture the semantic event sends only the declared number key and real left click. After a
0.25-second delay, the server evaluates the target's `on attacker` relation and requires it to resolve
to `mcdata_bot`. This check is immediate because Minecraft clears that relation after roughly 100
ticks. Input dispatch plus a health change is not sufficient without the player-attacker score,
conditional `[Server]` receipt, and an intermediate log-prefix SHA-256.

After ffmpeg stops, L3 verifies/cleans both blocks. L4 then requires exactly one tagged UUID target and
stores the remaining health; a conditional receipt proves `0 < health_after < health_before` while the
fixed entity snapshot still matches. Cleanup removes the exact UUID target, wooden sword, and all item
drops, proves that weapon absent across inventory/hotbar/armor/offhand, rechecks `spawn_mobs=false`,
and accepts only the unique
`Removed objective [mcdata_l4]` server success line. The replay's only post-capture control is last,
and its prefixes must order strictly as reset < player-attacker < final. Any missing cumulative L1-L3
observation, wrong input, entity drift, non-positive damage, killed/missing target, stale/prior marker,
score mismatch, cleanup residue, objective failure, or log/hash tamper rejects the episode.

## CPU verification

`scripts/dev_check.sh` must pass with zero standards failures and a clean Ruff run. Tests cover
the configured cumulative route, real button-1 dispatch, immediate attacker receipt, positive health
delta, UUID/tag/slot projection, objective lifecycle, prior-line rejection, server-log tampering,
post-capture control ordering, concurrent position-probe log lines, and best-effort cleanup after an
earlier probe fails. An independent review also exercised the command subset on a clean vanilla 26.2
server and confirmed the objective lines, `spawn_mobs`, fixed entity SNBT, attribute get/set, scaled
Health, and score output. The only standards output is the documented R19 size warning below.

## GPU acceptance still required

The first fresh-lane run must start only after the fixed L2 run proves all four physical rises and
landings and passes route QA. L4 must then verify the 26.2 server's actual English score lines, SNBT parsing,
creative wooden-sword damage, target red-flash/combat readability, two-block reach, `-20 px` vertical
aim, objective create/remove lines, and cleanup. Route QA must separately show that adding the off-route
NoAI target does not change the subsequent L1 route. Source/target render endpoints must independently
produce the same trajectory hash, fixed target snapshot, attacker identity, and exact remaining-health
score before an edit pair can be accepted.

`action_curriculum.py` and `render/pipeline.py` remain above the R19 size guideline because they own the
existing cross-level evidence reducer and process lifecycle, respectively; splitting their L4 branch
would duplicate ordering invariants. `render/combat.py` keeps the stateful prepare/dispatch/finalize/
failure-cleanup transaction and its 26.2 command builders together for the first hardware acceptance;
it should be split only after the accepted log format is frozen. `actions/replay.py` remains a
zero-`mcdata`-dependency input boundary and is only marginally above the guideline.
