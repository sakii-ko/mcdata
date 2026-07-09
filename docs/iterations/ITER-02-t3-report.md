# ITER-02 T3 Full-Matrix Report

Date: 2026-07-10

## Scope and execution mode

T3 covers the project north-star collection milestone after the ITER-03 matrix expansion:
19 configured matrix profiles on Minecraft 26.2, one deterministic ground trajectory, and a
manifest/QA-backed accepted cohort.

The original plan expected eight L40S GPUs for parallel throughput. Only one L40S was exposed by
the available container, so the same `matrix_shard.sh` contract was executed serially on lane
`gpu0`. This changes wall-clock time, not the world/trajectory/capture contract or correctness
criteria. Multi-GPU scale-out remains an optional throughput exercise rather than a dataset
correctness blocker.

All 19 profiles bootstrapped successfully with exact Minecraft 26.2 compatibility filtering before
collection.

## Rejected first batch

The first complete serial batch used clean main commit
`1596ffa9baa79e56ee620627526a06d9a7551e9b` (`iter-03-done`) and trajectory SHA-256
`cd2336bdda81ee3d335ca00394c7e2e6e1a94b25ea84d53796ec35cd0834b673` (133 grid cells,
46 events, 40.439 seconds). Video/probe metadata was complete for all 19 runs, but route QA passed
only 17/19:

| run | result | max deviation | mean deviation | reason |
|---|---|---:|---:|---|
| `20260709T185646Z_matrix_low__gpu0` | FAIL | 13.030 | 9.584 | physical collision at the glass-platform edge |
| `20260709T190726Z_matrix_dramatic_solas__gpu0` | FAIL | 13.030 | 9.569 | same deterministic collision |

Observed positions stopped near `x=5.444, z=-2.3`, where the zero-clearance route ran alongside
the occupied glass region (`x=5..14`). This was a route-planning defect, not random replay drift or
a profile-specific shader failure. The entire batch is rejected: its 17 passing runs cannot be
combined with later hotfix validation runs because their code commit and trajectory hash differ.
It is retained as evidence in local archive
`runs/remote_l40s/rejected_full19_1596ffa/`.

## Ground-clearance hotfix

Commit `fba8ba048ff233cddbcfe432bc2c7e2e3d10f675` applies configured Chebyshev obstacle
clearance to `_astar_walk`, matching the generalized mechanism already used by deterministic roam.
`ground_astar_loop` now uses bounds `[-16, 16, -14, 14]` and one-cell clearance around the merged
configuration/scene obstacle set. The regenerated route has:

- 117 grid cells;
- 60 events;
- duration `38.937` seconds;
- SHA-256 `9d48b980e8c37d01e22f2bcd5ac79155e13f4d7c9cf37608ff4a336aef2ff169`.

The golden trajectory, JSON visualization data, and PNG map were regenerated. The route reaches
the outer grass ring while remaining on supported y=64 ground and has no route cell within one
grid cell of any scene obstacle. Local validation passed all 94 tests then present with checker
0 failures; the only warning was the pre-existing `render/pipeline.py` file length. The hotfix was
merged to main as `7faeada8c604232241dc95fe058bc3b6e4cd7603`.

## Focused L40S validation

The two failed profiles were recaptured for 60 seconds on the NVIDIA L40S before starting another
full batch:

| run | route | max deviation | mean deviation | video | warnings |
|---|---|---:|---:|---|---:|
| `20260709T193704Z_matrix_low__gpu0` | PASS | 0.849 | 0.682 | 1280x720, 24 fps, 1440 frames | 0 |
| `20260709T193852Z_matrix_dramatic_solas__gpu0` | PASS | 0.661 | 0.439 | 1280x720, 24 fps, 1440 frames | 0 |

Their contact sheets were visually inspected: both retain the HUD, contain no loading/toast
overlay or black border, and traverse rather than collide with the glass-platform edge. These
runs are retained under `runs/remote_l40s/clearance_validation_fba8ba0/` as focused evidence,
not as members of the accepted final cohort.

## Rejected post-clearance batch and visual finding

A second complete 19-profile batch was collected from clean main commit
`7faeada8c604232241dc95fe058bc3b6e4cd7603` with the corrected trajectory. Automated gates
were all green:

- 19/19 route-reference PASS and zero QA warnings;
- worst per-run route deviation `1.458` blocks; maximum yaw error `0` degrees;
- every video 1280x720, 24 fps, 60 seconds, and 1440 frames;
- the 18 noon manifests had byte-identical complete world-state; all 19 became identical only
  after removing `time`, with `matrix_night_complementary` as the sole midnight variant;
- all-19 alignment PASS at max/mean `1.221 / 0.381` blocks; strict noon-18 alignment PASS at
  `1.221 / 0.383` blocks.

The visual gate nevertheless rejected this batch. The 23.08-second sample for
`matrix_glowing_ores_unbound` contained a visible `New Recipe(s) Unlocked!` toast. Review of the
timeline also showed the non-persistent oak leaf wall decaying under random ticks and producing
dropped items; the shared `gpu0` world's player inventory had accumulated those items across
runs. This violates both capture hygiene and strict underlying-world/player-state consistency,
despite the numeric route/video gates passing.

All 19 runs, both compare reports, the visual-review frames, and the batch log are retained under
`runs/remote_l40s/rejected_toast_full19_7faeada/`. No run from this
directory is part of the accepted cohort. This finding is also evidence that automated
border/route checks do not replace visual review.

## Static-scene and player-state hardening

Main commit `822dea4ae36bf307cd365c8ee9fae4a79aa0bce9` adds four explicit, manifest-recorded
world-state controls:

- `random_tick_speed: 0` freezes random block ticks during the static render scene;
- `clear_dropped_items: true` removes stale item entities after scene reconstruction;
- `clear_inventory: true` resets persisted player inventory on join and again immediately before
  capture;
- `pregrant_recipes: true` grants all recipes before the 15-second warmup so later pickups cannot
  create a recipe toast.

The cleanup order is locked by tests as gamerules → time/weather → scene construction → item
entity cleanup. All 19 merged matrix profiles are tested to inherit the same controls. The remote
Minecraft 26.2 server confirmed the commands rather than merely accepting local configuration:

```text
Gamerule random_tick_speed is now set to: 0
Removed 12 item(s) from player mcdata_bot
Unlocked 1585 recipe(s) for mcdata_bot
No items were found on player mcdata_bot
No new recipes were learned
```

The 12 removed items came from the deliberately reused dirty `gpu0` world, proving that the reset
handles persisted state rather than only a fresh lane. A 60-second L40S validation of
`matrix_glowing_ores_unbound` on commit `822dea4` passed route QA using 12 position samples (max
deviation `1.022`), had 1440 frames and zero warnings, and was visually inspected at 60 extracted
frames: no recipe toast, dropped item, or leaf-wall decay, with an empty HUD inventory. The
clean-lane precursor and dirty-world final validation are retained as focused evidence—not accepted
cohort members—under `runs/remote_l40s/toast_fix_validation_18de3b0/`
and `runs/remote_l40s/player_reset_validation_822dea4/`.

Local verification after these fixes:

```text
WARN  R19: render/pipeline.py has 1357 lines (>600) -- justify in report
check_standards: 0 failure(s), 1 warning(s)
All checks passed!
103 passed
```

## Rejected state-hardened batch and resource-runtime audit

A third complete batch was then collected from clean main commit
`822dea4ae36bf307cd365c8ee9fae4a79aa0bce9`. Its capture-level evidence was green:

- 19/19 route-reference PASS with zero QA warnings and worst deviation `1.457` blocks;
- every video 1280x720, 24 fps, 60 seconds, and 1440 frames;
- strict noon-18 alignment PASS at max/mean `1.382 / 0.433` blocks, and the auxiliary
  all-19 position diagnostic PASS at `1.382 / 0.428` blocks;
- all 19 contact sheets and the 12-timepoint cross-profile comparison passed visual review,
  including the night variant and the previously contaminated glowing-ores profile.

The pre-acceptance resource audit nevertheless found a separate bootstrap-contract defect.
Minecraft had removed at least one requested resource pack from `options.txt` in seven profiles,
and the corresponding `Reloading ResourceManager` log line did not contain those packs. The ZIPs
were downloaded and therefore appeared in the old file-hash manifest, but were not active in the
renderer:

| affected profile(s) | rejected pack | Minecraft 26.2 finding |
|---|---|---|
| `matrix_textured`, `matrix_shader_high`, `matrix_faithful_sildurs` | Faithful 32x | `pack_format: 88` but mandatory `min_format` / `max_format` absent |
| `matrix_night_complementary` | Visual Enchantments | declared maximum resource format 84 |
| `matrix_simplista_unbound` | Simplista | declared maximum resource format 84 |
| `matrix_glowing_ores_unbound` | New Glowing Ores | legacy range ended at 69 and lacked new format fields |
| `matrix_connected_glass_bsl` | MT-FCT Default | legacy `supported_formats` crossed the new-format boundary without mandatory fields |

The authoritative `version.json` inside the installed official 26.2 client JAR reports resource
format `88.0` (distinct from data-pack format `107.1`). This audit converts the distinction between
"downloaded" and "active in ResourceManager" into an explicit acceptance gate. The whole batch is
rejected even though its numeric and visual gates passed; no member may be reused in a later
cohort. It is retained with both compare reports, visual evidence, and the batch log at
`runs/remote_l40s/rejected_assets_full19_822dea4/`.

The closure fix preserves each upstream ZIP and source SHA, derives a deterministic
target-format-specific effective ZIP when required, records both source and effective SHA in the
run manifest, keeps invalid upstream ZIPs outside the instance-visible `resourcepacks/` directory,
and fails before capture unless the client log's active `file/...` set exactly matches the requested
set. A new clean 19-profile cohort is required on one post-fix commit before T3 can be accepted.

## Resource-runtime closure and preflight

Commit `2a4237596a97adeb77520761242c2a6a695fc3c1` implements the resource compatibility and
runtime contract; it was merged to main as capture commit
`dbca539c10317f6efee5ae4ec4210280d671e2ee`. Before the final cohort:

- all 19 profiles bootstrapped serially on the same main commit;
- five unique previously affected stacks (`matrix_textured`, night, simplista, glowing ores, and
  connected glass) passed independent 20-second capture/visual validation;
- 25 selected resource packs were audited, of which 14 required deterministic metadata
  normalization for official client resource format `88.0`;
- every source SHA-512 exactly matched Modrinth's upstream SHA-512, every effective SHA-256
  matched the instance-visible ZIP, and all profiles referenced official client JAR SHA-256
  `40896ee9f1e2bec3c934daac7e93d41e9e3d9c2f8ae0ca366d52ffbfd1afa290`;
- `options.txt`, the resolution sidecar, the instance directory, and the eventual
  `Reloading ResourceManager` `file/...` list agreed exactly in both set and order.

The normalizer never modifies its cached upstream source. The run manifest records the upstream
URL/size/SHA-512, source SHA-256/SHA-512, effective SHA-256, before/after metadata, normalization
decision, target resource format, and target client JAR SHA. Capture fails before ffmpeg starts if
the client removes, duplicates, reorders, or rejects any requested pack.

## Accepted final 19-profile cohort

The final serial batch ran on NVIDIA L40S / `DISPLAY=:77`, lane `gpu0`, from clean capture commit
`dbca539c10317f6efee5ae4ec4210280d671e2ee`. The batch command exited `0`; all 19 runtime
resource-pack gates passed and all 19 manifests were written. Every episode uses Minecraft 26.2,
world seed `1`, world profile `render_matrix_base`, the same 60-event trajectory SHA-256
`9d48b980e8c37d01e22f2bcd5ac79155e13f4d7c9cf37608ff4a336aef2ff169`, and a
1280×720 / 24fps / 60.0-second / 1440-frame H.264 capture.

Per-run offline QA used 12 uniformly spaced frames. Every row has zero warnings, no black border,
valid ground/yaw evidence, and route status PASS:

| profile | max deviation (blocks) | mean deviation (blocks) | min sampled p50 brightness |
|---|---:|---:|---:|
| `matrix_low` | 0.859 | 0.468 | 105.65 |
| `matrix_textured` | 1.329 | 1.135 | 109.09 |
| `matrix_shader_high` | 0.978 | 0.658 | 165.42 |
| `matrix_night_complementary` | 1.375 | 1.162 | 37.16 |
| `matrix_default_hd_bsl` | 1.433 | 1.149 | 110.59 |
| `matrix_default_hd128_bliss` | 0.890 | 0.735 | 126.44 |
| `matrix_dramatic_solas` | 1.330 | 1.136 | 111.54 |
| `matrix_faithful_sildurs` | 1.330 | 1.177 | 121.81 |
| `matrix_emissive_makeup` | 0.809 | 0.374 | 101.30 |
| `matrix_patrix_unbound` | 1.330 | 1.121 | 98.35 |
| `matrix_better_leaves_solas` | 0.809 | 0.437 | 110.12 |
| `matrix_default3d_miniature` | 0.810 | 0.604 | 131.65 |
| `matrix_simplista_unbound` | 1.762 | 1.307 | 99.33 |
| `matrix_stylista_bliss` | 0.812 | 0.374 | 103.41 |
| `matrix_realiscraft_bsl` | 0.860 | 0.403 | 105.01 |
| `matrix_glowing_ores_unbound` | 1.329 | 1.135 | 96.42 |
| `matrix_connected_glass_bsl` | 1.329 | 1.135 | 108.16 |
| `matrix_euphoria_complementary` | 0.862 | 0.354 | 165.42 |
| `matrix_solas_patrix` | 0.857 | 0.504 | 108.41 |

The complete world-state canonical hash yields exactly two cohorts: 18 byte-identical noon states
and one midnight `matrix_night_complementary` variant. No field other than the intentional time
override separates the night state from the base matrix controls. Cross-run checks are:

| comparison | members / pairs | position max / mean | status | NCC min / mean (diagnostic) |
|---|---:|---:|---|---:|
| strict noon matrix | 18 / 153 | 1.989 / 0.518 blocks | PASS | 0.1989 / 0.7551 |
| all-data diagnostic | 19 / 171 | 1.989 / 0.531 blocks | PASS | -0.1238 / 0.7167 |

The negative all-data NCC minimum is expected from the deliberately dark midnight column; it is
not used as a pass/fail similarity threshold. Position alignment is the correctness gate.

Each run now carries a portable snapshot of its client `latest.log`. All 18 shader-bearing
profiles contain the exact `Using shaderpack: <manifest filename>` line and the vanilla baseline
contains the explicit Iris disabled line. ResourceManager runtime set/order, resolution
provenance, shader activation, capture metadata, trajectory, positions, QA report, and compare
inputs are all hash-bound by the dataset indexer.

## Visual acceptance

The original-resolution 19-profile labeled contact-sheet montage, strict 18-way sheet, all-19
sheet, and focused connected-glass / glowing-ores / simplista / night sheets were inspected.
The result is PASS:

- HUD and empty hotbar remain visible;
- no loading screen, recipe/advancement/chat toast, dropped item, leaf decay, black border, or
  purple-black missing texture is visible;
- same-time columns remain spatially aligned;
- connected glass, emissive materials, shader light/water/shadow, and texture differences are
  clearly present;
- the midnight variant remains readable; the warm `stylista_bliss` sky is its intended shader
  appearance rather than contamination.

Compressed repository evidence (each image under 300 KB):

- `docs/qa_samples/iter02_l40s_full19/all19_labeled_overview.jpg`;
- `docs/qa_samples/iter02_l40s_full19/all19_alignment_overview.jpg`.

## Dataset index, checksums, and storage closure

The fail-closed batch packager was implemented in commit
`402e001491256b0224630c22008edc5445abc087`; generator provenance was made explicit in
`8e47190a494d323b03d9f0ad1f4420bd6cb78f00`. It rejects missing/duplicate profiles,
dirty/error runs, provenance or capture drift, stale QA/compare evidence, incomplete position
pairs, resource runtime/resolution/hash mismatch, shader-log mismatch, self-referential visual
evidence, symlinks, and partial checksum publication. QA and compare reports bind the actual
manifest/video/trajectory/positions SHA-256 values.

Canonical accepted archive:

```text
/root/nas/bigdata1/cjw/projs/mcdata/runs/remote_l40s/accepted_full19_dbca539/
```

Its `dataset_index.json` validates against schema v1 and records separately:

- capture commit: `dbca539c10317f6efee5ae4ec4210280d671e2ee`;
- index generator commit: `8e47190a494d323b03d9f0ad1f4420bd6cb78f00`;
- dataset ID: `sha256:3e99daab2b54933518267043bd77f8a91f0d67f2f638716ca6aac7c0207d1dc9`;
- status / membership: `accepted`, 19 episodes, strict 18 + midnight 1;
- checksum manifest: 277 files, all `sha256sum -c SHA256SUMS` checks PASS.

Generating the index twice produces byte-identical `dataset_index.json` and `SHA256SUMS`.
The full remote staging tree—including all rejected/focused evidence—was copied to local
`runs/remote_l40s/` by the two-pass pull guard (about 1.4 GB total). The verified second pass
transferred zero files; the L40S `runs/` directory was then purged and no Minecraft, ffmpeg,
matrix, rsync, or capture process remained.

Final repository validation after the packager and bounded compare-memory fix:

```text
WARN  R19: render/pipeline.py has 1523 lines (>600) -- justify in report
check_standards: 0 failure(s), 1 warning(s)
All checks passed!
162 passed
```

The sole warning is the pre-existing pipeline orchestration file length. This closure added no
new size warning: dataset validation is split into a 160-line facade plus support modules, with a
largest module of 464 lines and no function over 80 lines. Further pipeline phase extraction is
deferred because it is unrelated to the accepted data contract and would add capture regression
risk at the milestone boundary.

An independent final read-only acceptance review rebuilt the index in memory, recomputed the
dataset ID, verified the schema and all 277 checksums, independently ffprobed all videos, audited
all resource/shader logs, and reconciled report values with the artifacts. Its result was PASS
with no blocking finding. The only operational watch item is that the observed 1.989-block
cross-run maximum is close to the fixed 2.0-block alignment threshold; this batch is below the
threshold, while future recaptures should continue to monitor the margin.

## T3 conclusion

T3 and the project north-star goal are complete. The accepted strict dataset is the 18-way noon
cohort; the nineteenth midnight episode is a controlled auxiliary variant, not a mislabeled
rendering-only member. Multi-GPU execution remains an optional throughput optimization and is no
longer a correctness or publication blocker.
