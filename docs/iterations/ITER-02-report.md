# ITER-02 Report

Branch: `iter/02-gpu-collection`

## Commits

- T0 review fixes: `a93887402bb7fedd3251d4baf796510d1b63645d` `[fix] address ITER-01 review findings`
- T1 QA compatibility: `826a5e784f0550283fc64b78b64d4638474a0a57` `[fix] support older pillow in QA reports`
- T1 original 4090 samples: `e975ef3beb6619ef21285b48c3581b4b52eaf6e5` `[data] add ITER-02 4090 QA samples`
- T2 shard isolation: `69c91d4ed67edcdd25d3efd89312fc6530faf315` `[impl] add run-matrix shard isolation`
- T1b instrumentation: `83ed3d95118c152f717b994aec0a0eaacb8a8fd5` `[fix] add T1b alignment instrumentation`
- T1b first-sample gate: `e76782bbc03f28f23d3dfd1af38d8f6e3ea1ae05` `[fix] wait for first position probe sample`
- T1b compare mean reporting: `3fa0850b3f76792686e4fdfa86c1f6fafd980bf5` `[fix] report mean position alignment`
- T1b aligned QA samples: `a22042375c66558e6f536f60e65c7031b9cd7b6d` `[data] replace ITER-02 QA samples with aligned T1b batch`
- T1c Step 0 diagnosis: `744a1b33b2f68331cd1c57bd2cf7e12416a2bfb8` `[docs] record T1c step 0 timeline diagnosis`
- T1c route-reference gate: `2c7872f5f3ce6efc591be7676b29a7e3d289a345` `[qa] add T1c route reference gate`
- T1c debug isolation flags: `a22a9b80ef0f1c3fe5c5f17b678308dc085c28cd` `[fix] add T1c debug isolation flags`
- T1c replay key cleanup: `80e7edd5a88c696f39fd455aac9526382a7ed963` `[fix] release replay keys on teardown`
- T1c bootstrap CLI compatibility: `14776362deba5f11f85d4d47a5831ffd01bd40b8` `[fix] pass game version through bootstrap cli`

## Validation Commands

- `bash scripts/dev_check.sh`
  ```text
  WARN  R19: render/pipeline.py has 816 lines (>600) -- justify in report
  WARN  R19: render/pipeline.py:169 function launch_profile spans 223 lines (>80)
  check_standards: 0 failure(s), 2 warning(s)
  All checks passed!
  ....................................................                     [100%]
  52 passed in 2.42s
  ```
- 4090 preflight:
  - `ssh 4090 'nvidia-smi --query-gpu=index,name,memory.used,memory.free --format=csv,noheader,nounits && tmux ls 2>/dev/null || true'`
  - GPU 0 reported RTX 4090 with 11846 MiB free before the T1b retry batch.
- Code sync:
  - `scripts/sync_to_remote.sh 4090 /home/lyf/mcdata`
  - `.sync_commit` after final compare regeneration: `3fa0850b3f76792686e4fdfa86c1f6fafd980bf5`.
- 10s discard:
  - First T1b retry discard hit a transient PortableMC Fabric `SSLEOFError` and timed out waiting for join.
  - Successful discard command:
    ```text
    python3 -m mcdata.cli run --profile matrix_low --with-server --replay-actions --capture --strategy ground_astar_loop --duration 10 --game-version 26.2
    ```
  - Successful discard run: `runs/remote_4090/20260708T081832Z_matrix_low`.
- 4090 capture:
  - Initial bootstrap-enabled `run-matrix` retried and failed before capture on Modrinth `SSLEOFError`.
  - Successful 3-way command used cached instances and explicit version:
    ```text
    python3 -m mcdata.cli run-matrix --profiles matrix_low,matrix_textured,matrix_shader_high --strategy ground_astar_loop --duration 60 --game-version 26.2 --no-bootstrap
    ```
  - Night command:
    ```text
    python3 -m mcdata.cli run --profile matrix_night_complementary --with-server --replay-actions --capture --strategy ground_astar_loop --duration 60 --game-version 26.2
    ```
  - All four runs logged `join/re_apply_state`, `position_probe/first_sample`, `positions_written count=13`, and `manifest_written`.
- QA:
  - `python3 -m mcdata.cli qa-run <run> --frames 12 --out-dir <run>/qa` for all four runs.
  - `python3 -m mcdata.cli qa-compare <left> <right> --frames 12 --out-dir runs/qa_compare_t1b2_<pair>` for all six pairs.
- Pull and purge:
  - `scripts/pull_runs_from_remote.sh 4090 /home/lyf/mcdata/runs --purge`
  - Output included `verify: OK`, `purge: done`, local copy `runs/remote_4090`, size `123M`.

## T1b Results

Selected passing run dirs:

- `runs/remote_4090/20260708T082056Z_matrix_low`
- `runs/remote_4090/20260708T082306Z_matrix_textured`
- `runs/remote_4090/20260708T082510Z_matrix_shader_high`
- `runs/remote_4090/20260708T082753Z_matrix_night_complementary`

Run QA summary:

```text
matrix_low: 60.0s, 24.0fps, 1280x720, warnings=0, p50=19.6-127.4, positions=13, git=e76782bbc03f source=sync_commit, renderer=NVIDIA GeForce RTX 4090/PCIe/SSE2
matrix_textured: 60.0s, 24.0fps, 1280x720, warnings=0, p50=15.8-156.7, positions=13, git=e76782bbc03f source=sync_commit, renderer=NVIDIA GeForce RTX 4090/PCIe/SSE2
matrix_shader_high: 60.0s, 24.0fps, 1280x720, warnings=0, p50=23.0-164.9, positions=13, git=e76782bbc03f source=sync_commit, renderer=NVIDIA GeForce RTX 4090/PCIe/SSE2
matrix_night_complementary: 60.0s, 24.0fps, 1280x720, warnings=1, p50=8.0-85.1, positions=13, git=e76782bbc03f source=sync_commit, renderer=NVIDIA GeForce RTX 4090/PCIe/SSE2
```

Position-aware compare summary:

```text
low_vs_night: NCC min/mean/max 0.0474/0.6174/0.8938, position PASS max=0.157 mean=0.081
low_vs_shader_high: NCC min/mean/max 0.1464/0.5353/0.8733, position PASS max=0.166 mean=0.089
low_vs_textured: NCC min/mean/max 0.3430/0.6971/0.9330, position PASS max=0.421 mean=0.257
shader_high_vs_night: NCC min/mean/max -0.0128/0.6048/0.8463, position PASS max=0.103 mean=0.083
textured_vs_night: NCC min/mean/max 0.1704/0.6500/0.8980, position PASS max=0.267 mean=0.199
textured_vs_shader_high: NCC min/mean/max 0.4051/0.6136/0.9241, position PASS max=0.358 mean=0.265
```

The largest four-way position deviation is `0.421` blocks, below the T1b `2.0` block threshold. The t=30 representative frames were visually checked as the same water/kelp camera position across all four profiles.

## T1c Step 0

Compared local pulled textured runs before any T1c code changes:

- First batch, correct route: `runs/remote_4090/20260708T062500Z_matrix_textured`
- T1b batch, aligned but off-route: `runs/remote_4090/20260708T082306Z_matrix_textured`
- `trajectory.json` is byte-identical: sha256 `ad4930a8406093a04cfc03f23a747e2fb141cc2c2a6adf6da6621ebc4f5a2f3a`, 9346 bytes.

Timeline summary, relative to `capture/start`:

| event | first batch | T1b batch |
|---|---:|---:|
| `join/player_joined` | -16.237s | -17.234s |
| `warmup/start` | -16.234s | -17.232s |
| `warmup/end` | -1.233s | -2.232s |
| `join/re_apply_state` | absent | -2.230s |
| `capture/view_prepared` | -0.011s | -0.011s |
| `capture/start` | +0.000s | +0.000s |
| `position_probe/start` | absent | +0.001s |
| `position_probe/first_sample` | absent | +0.203s |
| `capture/stop` | +60.590s | +60.287s |
| `replay/thread_joined` | +62.215s | +61.161s |

Interval differences:

- Server startup and join timings differ only by normal run-to-run variance: server start wall time `13.022s` vs `14.019s`; join wait `32.564s` vs `30.558s`.
- Warmup duration is unchanged: `15.0009s` vs `15.0007s`.
- T1b inserts `join/re_apply_state` immediately after warmup end, then waits through the 1s re-apply settle plus view preparation. Net `warmup/end -> view_prepared` grew from `1.222s` to `2.221s`.
- `view_prepared -> capture/start` is unchanged: `0.0107s` vs `0.0106s`.
- T1b starts the position probe `0.001s` after capture start and receives `first_sample` `0.203s` after capture start before releasing replay.
- The first batch has no capture-time `re_apply_state`, no position probe commands, no first-sample gate, no `positions.jsonl` write.

Replay wall-clock alignment:

- First replay event is `mouse_dx=0, mouse_dy=30, duration=0.4, t=1.0`. Because replay logging happens after event dispatch, the event's estimated start is a better release proxy than its log timestamp.
- First batch: first event log at capture `+1.442s`; event start estimate `+1.042s`; inferred replay release `+0.041s`.
- T1b batch: first event log at capture `+1.643s`; event start estimate `+1.243s`; inferred replay release `+0.242s`.
- The first `w down` event similarly infers replay release at capture `+0.030s` for first batch and `+0.230s` for T1b.
- Therefore T1b releases replay about `0.20s` later relative to capture start, matching the first-sample wait (`+0.203s`). Scheduled replay timing after release remains equivalent.

Replay delivery comparison:

```text
first batch: 46 events, delay min/mean/max 0.0003/0.0056/0.0180s
T1b batch:   46 events, delay min/mean/max 0.0002/0.0063/0.0214s
```

Step 0 difference list:

1. T1b adds a second `apply_join_state` at warmup end; first batch goes directly from warmup end to capture view preparation.
2. T1b starts a server position probe immediately after capture start; first batch sends no capture-time `data get entity ... Pos` commands.
3. T1b waits for the first position probe response before setting the replay ready event; first batch releases replay immediately after capture start.
4. T1b replay starts about `0.20s` later relative to capture start, but its per-event replay schedule accuracy is still comparable to the first batch.
5. Trajectory content, launcher command, capture settings, warmup duration, and replay event count are unchanged between the two textured runs.

Step 0 conclusion: the zero-cost diagnosis narrows pre-replay/pre-second-turn behavioral differences to the T1b mechanisms called out by planner: capture-time `re_apply_state`, position probe command traffic, and the first-sample replay gate. The logs do not show a replay input delivery regression; the next step is to add the route-reference gate and run the A/B/C/D isolation matrix.

## T1c Step 1

Implemented route-reference QA gate in `2c7872f5f3ce6efc591be7676b29a7e3d289a345`:

- `positions.jsonl` now includes `t_rel` when probe send times and replay release time are available.
- `pipeline.jsonl` now logs `replay/released` at the ready-event release point.
- `qa-run` now emits `route_reference` in JSON/markdown when a run dir has both `positions.jsonl` and `trajectory.json`.
- Route-reference gate compares every `t_rel >= 0` sample against the trajectory-derived ideal `(x,z)` and enforces max deviation `<=3.0` plus `y` in `63.0..66.0`.
- Sanity check against the known off-route T1b textured positions, with approximate 5s `t_rel`, fails as expected: max deviation about `35.58` blocks and 8 y-out-of-range samples.

Validation:

```text
bash scripts/dev_check.sh
WARN  R19: render/pipeline.py has 825 lines (>600) -- justify in report
WARN  R19: render/pipeline.py:169 function launch_profile spans 232 lines (>80)
check_standards: 0 failure(s), 2 warning(s)
All checks passed!
..........................................................               [100%]
58 passed in 2.27s
```

Deviation from PLAN wording: the planner text named `src/mcdata/actions/simulate.py`, but repository architecture and R12 explicitly forbid `mcdata.qa` importing `mcdata.actions`; the route simulator lives in `src/mcdata/qa/route.py` instead. It still consumes only the trajectory JSON contract and is covered by pure unit tests.

## T1c Step 2

Implemented hidden isolation flags in `a22a9b80ef0f1c3fe5c5f17b678308dc085c28cd`:

- `mcdata run --debug-no-reapply` skips the capture-time second `apply_join_state` and logs `join/re_apply_state_skipped`.
- `mcdata run --debug-no-replay-gate` keeps the position probe running but skips the first-sample wait before replay release and logs `position_probe/first_sample_skipped`.
- Both flags are written to `metadata.json` for experiment provenance.
- CLI direct-call tests cover hidden flag propagation; pipeline tests cover the skipped re-apply and skipped replay-gate events.

Validation:

```text
bash scripts/dev_check.sh
WARN  R19: render/pipeline.py has 835 lines (>600) -- justify in report
WARN  R19: render/pipeline.py:169 function launch_profile spans 242 lines (>80)
check_standards: 0 failure(s), 2 warning(s)
All checks passed!
............................................................             [100%]
60 passed in 7.81s
```

The R19 warnings remain from the intentionally deferred `render/pipeline.py` split; no checker rule was changed.

4090 sync and A-group isolation run:

- Preflight:
  ```text
  ssh 4090 'hostname; nvidia-smi --query-gpu=index,name,memory.used,memory.free --format=csv,noheader,nounits; tmux ls 2>/dev/null || true'
  ```
  GPU 0 reported RTX 4090 with 9114 MiB free. Existing Xorg `:77` was running and no mcdata/portablemc/ffmpeg process was active.
- Code sync:
  ```text
  scripts/sync_to_remote.sh 4090 /home/lyf/mcdata
  ```
  Remote `.sync_commit`: `d40d84f551809b1ca6fb08e4cfb4ce3d4c5384eb`.
- A discard:
  ```text
  DISPLAY=:77 MCDATA_CAPTURE_SIZE=1280x720 MCDATA_CAPTURE_FPS=24 PYTHONPATH=src \
  python3 -m mcdata.cli run --profile matrix_low --with-server --replay-actions --capture \
    --strategy ground_astar_loop --duration 10 --game-version 26.2 --lane t1cA \
    --debug-no-reapply --debug-no-replay-gate
  ```
  Run dir: `runs/remote_4090/20260708T093746Z_matrix_low__t1cA`.
- A formal:
  ```text
  DISPLAY=:77 MCDATA_CAPTURE_SIZE=1280x720 MCDATA_CAPTURE_FPS=24 PYTHONPATH=src \
  python3 -m mcdata.cli run --profile matrix_low --with-server --replay-actions --capture \
    --strategy ground_astar_loop --duration 30 --game-version 26.2 --lane t1cA \
    --debug-no-reapply --debug-no-replay-gate
  python3 -m mcdata.cli qa-run runs/20260708T093856Z_matrix_low__t1cA --frames 12 \
    --out-dir runs/20260708T093856Z_matrix_low__t1cA/qa
  ```
  Run dir: `runs/remote_4090/20260708T093856Z_matrix_low__t1cA`.

A formal pipeline confirmed both debug skips before replay release:

```text
join/re_apply_state_skipped debug=True
capture/start
position_probe/start
position_probe/first_sample_skipped debug=True
replay/released
position_probe/positions_written count=7
teardown/manifest_written
```

A formal route-reference QA failed:

```text
route_reference: FAIL
route_max_deviation_blocks: 31.127
route_mean_deviation_blocks: 27.909
route_threshold_blocks: 3.0
route_y_range: 63.0..66.0
route_y_out_of_range_count: 5
video: 30.0s, 24.0fps, 1280x720
manifest git: d40d84f551809b1ca6fb08e4cfb4ce3d4c5384eb, source=sync_commit, dirty=false
```

Route samples show the run was already far from the trajectory reference by the first post-release sample:

| idx | t_rel | observed `(x,y,z)` | ideal `(x,z)` | deviation | y in range |
|---:|---:|---|---|---:|---|
| 1 | 5.001 | `(11.506,64.000,14.672)` | `(6.908,-12.000)` | 27.066 | yes |
| 2 | 10.001 | `(18.794,61.310,27.010)` | `(12.000,-3.184)` | 30.950 | no |
| 3 | 15.001 | `(13.761,58.117,30.561)` | `(4.000,1.004)` | 31.127 | no |
| 4 | 20.001 | `(16.375,55.892,35.972)` | `(8.911,10.000)` | 27.023 | no |
| 5 | 25.002 | `(13.969,53.392,33.934)` | `(3.000,10.000)` | 26.328 | no |
| 6 | 30.002 | `(7.545,51.000,32.073)` | `(-7.287,12.000)` | 24.958 | no |

Pull and purge:

```text
scripts/pull_runs_from_remote.sh 4090 /home/lyf/mcdata/runs --purge
verify: OK
purge: done
local copy: /home/chijw/workspace/projs/mcdata/runs/remote_4090
size: 132M
```

Step 2 conclusion: A disables both suspected T1b additions (`re_apply_state` repeat and replay first-sample gate) while leaving only the initial join state, capture, probe sampling, and replay itself. A still failed the route-reference gate badly, so I stopped before B/C/D as prescribed by PLAN.md. This means the off-route failure is not explained solely by either added mechanism from T1b Step 0; planner review is needed before choosing the Step 3 fix path.

## T1c Step 3

Implemented the replay cleanup fix in `80e7edd5a88c696f39fd455aac9526382a7ed963`:

- `replay_trajectory` now accepts `stop_event` and breaks out of event sleeps in <=0.25s chunks when stop is requested.
- Replay keeps a `held` key set via `_update_held`; `finally` releases any still-held key and writes a replay-log control record `released_keys`.
- XTEST replay startup queries the X server keymap for movement keys (`w/a/s/d/space/left_shift`), releases inherited pressed keys, and logs `inherited_stuck_keys` when present.
- xdotool replay startup cannot query key state, so it unconditionally sends keyup for the same movement-key set.
- `launch_profile` now creates a replay stop event, passes it to the replay thread, and sets it as the first teardown action before joining replay for up to 5s and only then terminating capture/game/server processes.
- `QUIET_CAPTURE_OPTIONS` now writes `rawMouseInput:true`.
- Unit coverage added for `_update_held`, interrupted replay key release, rawMouseInput, and bootstrap CLI game-version passthrough.

While executing Step 3.5, the prescribed command exposed a small CLI gap: `bootstrap_profile` already accepted `game_version`, but `mcdata bootstrap` did not expose `--game-version`. I fixed that in `14776362deba5f11f85d4d47a5831ffd01bd40b8` so the planner-specified verification command is runnable.

Validation:

```text
bash scripts/dev_check.sh
WARN  R19: render/pipeline.py has 839 lines (>600) -- justify in report
WARN  R19: render/pipeline.py:169 function launch_profile spans 245 lines (>80)
check_standards: 0 failure(s), 2 warning(s)
All checks passed!
...............................................................          [100%]
63 passed in 2.39s
```

## T1c Step 3.5

4090 sync and bootstrap:

```text
scripts/sync_to_remote.sh 4090 /home/lyf/mcdata
ssh 4090 'cd /home/lyf/mcdata && cat .sync_commit'
14776362deba5f11f85d4d47a5831ffd01bd40b8

ssh 4090 'cd /home/lyf/mcdata && export PYTHONPATH=src DISPLAY=:77 && \
  python3 -m mcdata.cli bootstrap --profile matrix_low --game-version 26.2 && \
  rg "^rawMouseInput:true$" .mcdata/instances/matrix_low/options.txt'
rawMouseInput:true
```

Started Step 3.5 exactly as specified: no discard run, first default 60s validation run `t1cval1` with pointer probe sidecar:

```text
python3 scripts/pointer_probe.py runs/pointer_probe_t1cval1.jsonl 95 &
python3 -m mcdata.cli run --profile matrix_low --with-server --replay-actions --capture \
  --strategy ground_astar_loop --duration 60 --game-version 26.2 --lane t1cval1
python3 -m mcdata.cli qa-run runs/20260708T102445Z_matrix_low__t1cval1 --frames 12 \
  --out-dir runs/20260708T102445Z_matrix_low__t1cval1/qa
```

Run dir: `runs/remote_4090/20260708T102445Z_matrix_low__t1cval1`.

Pipeline/replay evidence:

```text
join/player_joined
join/apply_join_state
warmup/end
join/re_apply_state
capture/start
position_probe/first_sample count=1
replay/released
capture/stop returncode=0
replay/thread_joined alive=false
position_probe/positions_written count=13
teardown/manifest_written
```

`replay_log.jsonl` had no `inherited_stuck_keys` or `released_keys` control records for this run; the replay thread finished normally before teardown. Manifest recorded `git.commit=14776362deba5f11f85d4d47a5831ffd01bd40b8`, `git.source=sync_commit`, `dirty=false`.

The first validation run still failed the route-reference gate:

```text
route_reference: FAIL
route_max_deviation_blocks: 25.895
route_mean_deviation_blocks: 16.601
route_threshold_blocks: 3.0
route_y_range: 63.0..66.0
route_y_out_of_range_count: 4
video: 60.0s, 24.0fps, 1280x720
```

Route samples:

| idx | t_rel | observed `(x,y,z)` | ideal `(x,z)` | deviation | y in range |
|---:|---:|---|---|---:|---|
| 1 | 4.797 | `(1.882,64.000,-10.833)` | `(6.272,-12.000)` | 4.542 | yes |
| 2 | 9.797 | `(3.707,64.000,-8.979)` | `(12.000,-3.822)` | 9.765 | yes |
| 3 | 14.797 | `(1.886,64.000,-7.078)` | `(4.000,0.366)` | 7.738 | yes |
| 4 | 19.797 | `(4.700,64.000,-0.495)` | `(8.273,10.000)` | 11.087 | yes |
| 5 | 24.798 | `(-0.320,64.000,-2.779)` | `(3.538,10.000)` | 13.349 | yes |
| 6 | 29.798 | `(-4.541,64.000,-6.906)` | `(-6.650,12.000)` | 19.023 | yes |
| 7 | 34.798 | `(-2.722,64.000,-13.381)` | `(-9.162,8.000)` | 22.330 | yes |
| 8 | 39.798 | `(5.044,64.000,-20.519)` | `(-4.000,-0.745)` | 21.744 | yes |
| 9 | 44.799 | `(0.612,59.167,-27.549)` | `(-12.000,-4.933)` | 25.895 | no |
| 10 | 49.799 | `(4.997,56.667,-32.904)` | `(-7.160,-14.000)` | 22.476 | no |
| 11 | 54.799 | `(9.822,54.167,-32.140)` | `(0.000,-14.000)` | 20.629 | no |
| 12 | 59.799 | `(9.822,51.667,-32.140)` | `(0.000,-14.000)` | 20.629 | no |

Pointer probe, aligned to `replay/released` through `capture/stop`:

```text
samples_total: 315
gameplay_samples: 93
edge_samples: 89
edge_parking_ratio: 0.957
first_gameplay: px=640, py=360, focus="Minecraft* 26.2 - Multiplayer (3rd-party Server)"
first_edge_samples: (0,3), (0,2), (0,2), (0,2), (0,0)
```

Step 3.5 conclusion: the first no-discard default validation run failed, so I stopped as PLAN.md requires. The scripted second run `t1cval2` had already started after `qa-run`; I interrupted it immediately, killed the orphaned server/client processes, and pulled its partial run dir only as evidence (`runs/remote_4090/20260708T102710Z_matrix_low__t1cval2`). I did not proceed to Step 4 and did not add any new mechanism beyond the prescribed Step 3 fix.

Pull and purge:

```text
scripts/pull_runs_from_remote.sh 4090 /home/lyf/mcdata/runs --purge
verify: OK
purge: done
local copy: /home/chijw/workspace/projs/mcdata/runs/remote_4090
size: 172M
```

## T1d Turn Calibration / Yaw Gate Validation

T1d code/data commits:

- `e397c56389e93dd9f496e4c80facd8b17c03be99` `[qa] add yaw route reference gate`
- `52832ea916ee57db6426b88b4732667eaae5c815` `[data] regenerate trajectories for P4 turn calibration`

Local validation before remote sync:

```text
scripts/dev_check.sh
WARN  R19: render/pipeline.py has 839 lines (>600) -- justify in report
WARN  R19: render/pipeline.py:169 function launch_profile spans 245 lines (>80)
check_standards: 0 failure(s), 2 warning(s)
All checks passed!
67 passed
```

Implemented T1d surface:

- `configs/actions.yml`: all four `astar_walk` strategies use `turn_px_per_degree: 6.6667`.
- `configs/actions.yml`: added `turn_calibration_probe`, scripted 8 x 600px turns at 2.5s intervals.
- `options.py`: `QUIET_CAPTURE_OPTIONS` now pins `mouseSensitivity: "0.5"` in addition to `rawMouseInput: "true"`.
- Position probe now sends `data get entity <user> Pos` and `Rotation` every cycle; `positions.jsonl` includes `yaw`.
- `simulate_track` emits an ideal yaw timeline, including circular interpolation through turn spans.
- Route reference QA now enforces yaw residual `<=10.0` degrees and fails if expected yaw samples are missing.

Preflight and sync:

```text
ssh 4090 'nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits'
0, NVIDIA GeForce RTX 4090, 16466, 24564, 0

ssh l40s 'nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits | head'
0, NVIDIA L40S, 37, 46068, 0
```

Both hosts were synced to `.sync_commit = 52832ea916ee57db6426b88b4732667eaae5c815`. The sync overwrote planner's temporary `turn_fidelity_probe` / `turn_probe_600` entries with the committed `turn_calibration_probe`. Bootstrap on both hosts wrote:

```text
mouseSensitivity:0.5
rawMouseInput:true
```

Cleanup requested by planner was performed:

- 4090: `/home/lyf/pointer_probe.py` absent after cleanup.
- l40s: `/root/mcdata/l40sval*.log` and `/root/mcdata/turn600.log` removed.
- Both remote run directories were purged after pulling evidence; l40s required manual `find ... -exec rm -rf` after `pull_runs_from_remote.sh` refused purge because `pgrep` matched its own check command.

### Turn calibration probe

4090 command:

```text
DISPLAY=:77 MCDATA_CAPTURE_SIZE=1280x720 MCDATA_CAPTURE_FPS=24 PYTHONPATH=src \
python3 -m mcdata.cli run --profile matrix_low --with-server --replay-actions --capture \
  --strategy turn_calibration_probe --duration 24 --game-version 26.2 \
  --server-port 25610 --lane t1dturn4090
```

l40s command:

```text
MCDATA_TMP_ROOT=/root/nas/bigdata1/tmp/mcdata DISPLAY=:77 \
MCDATA_CAPTURE_SIZE=1280x720 MCDATA_CAPTURE_FPS=24 PYTHONPATH=src \
python3 -m mcdata.cli run --profile matrix_low --with-server --replay-actions --capture \
  --strategy turn_calibration_probe --duration 24 --game-version 26.2 \
  --server-port 25620 --lane t1dturnl40s
```

Pulled artifacts:

- `runs/remote_4090/20260708T111535Z_matrix_low__t1dturn4090`
- `runs/remote_l40s/20260708T111536Z_matrix_low__t1dturnl40s`
- NCC/contact sheet scratch output: `runs/t1d_turn_ncc/summary.json`

NCC was measured against the baseline frame at replay release + 1s, then +360 at release + 10.8s and +720 at release + 20.8s:

```text
4090 timestamps: 1.203s, 11.003s, 21.003s
4090 NCC baseline/+360: 0.7867
4090 NCC baseline/+720: 0.7887
4090 visual check: overlap by contact sheet; numeric NCC is just below 0.8, likely affected by animated lava.

l40s timestamps: 1.202s, 11.002s, 21.002s
l40s NCC baseline/+360: 0.9057
l40s NCC baseline/+720: 0.9105
l40s visual check: overlap by contact sheet.
```

The 4090 numeric NCC alone is below the T1d threshold, and the following ground validation also failed on both hosts, so T1d stopped before full recapture.

### Ground validation

4090 command:

```text
DISPLAY=:77 MCDATA_CAPTURE_SIZE=1280x720 MCDATA_CAPTURE_FPS=24 PYTHONPATH=src \
python3 -m mcdata.cli run --profile matrix_low --with-server --replay-actions --capture \
  --strategy ground_astar_loop --duration 60 --game-version 26.2 \
  --server-port 25611 --lane t1d4090a
PYTHONPATH=src python3 -m mcdata.cli qa-run runs/20260708T111716Z_matrix_low__t1d4090a \
  --frames 12 --out-dir runs/20260708T111716Z_matrix_low__t1d4090a/qa
```

l40s command:

```text
MCDATA_TMP_ROOT=/root/nas/bigdata1/tmp/mcdata DISPLAY=:77 \
MCDATA_CAPTURE_SIZE=1280x720 MCDATA_CAPTURE_FPS=24 PYTHONPATH=src \
python3 -m mcdata.cli run --profile matrix_low --with-server --replay-actions --capture \
  --strategy ground_astar_loop --duration 60 --game-version 26.2 \
  --server-port 25621 --lane t1dl40sa
PYTHONPATH=src python3 -m mcdata.cli qa-run \
  /root/nas/bigdata1/tmp/mcdata/runs/20260708T111650Z_matrix_low__t1dl40sa \
  --frames 12 --out-dir /root/nas/bigdata1/tmp/mcdata/runs/20260708T111650Z_matrix_low__t1dl40sa/qa
```

Both first ground validation runs failed the position gate while the yaw gate was effectively perfect and complete:

```text
4090 run: runs/remote_4090/20260708T111716Z_matrix_low__t1d4090a
route_reference: FAIL
route_max_deviation_blocks: 22.252
route_mean_deviation_blocks: 15.565
route_max_yaw_error_degrees: 0.000030
route_mean_yaw_error_degrees: 0.000022
route_missing_yaw_count: 0
route_sample_count: 12

l40s run: runs/remote_l40s/20260708T111650Z_matrix_low__t1dl40sa
route_reference: FAIL
route_max_deviation_blocks: 21.425
route_mean_deviation_blocks: 15.293
route_max_yaw_error_degrees: 0.000030
route_mean_yaw_error_degrees: 0.000022
route_missing_yaw_count: 0
route_sample_count: 12
```

Representative samples show the same pattern on both machines: yaw matches the ideal heading, but position is far behind/off the ideal route by the first post-release sample.

| host | idx | t_rel | observed `(x,z)` | ideal `(x,z)` | deviation | observed yaw | ideal yaw |
|---|---:|---:|---|---|---:|---:|---:|
| 4090 | 1 | 4.797 | `(1.901,-11.036)` | `(6.273,-12.000)` | 4.476 | -90.00001 | -90.0 |
| 4090 | 2 | 9.797 | `(3.714,-9.424)` | `(12.000,-3.820)` | 10.003 | -0.00001 | 0.0 |
| 4090 | 3 | 14.798 | `(1.847,-8.035)` | `(4.000,0.368)` | 8.674 | -0.00000 | 0.0 |
| 4090 | 4 | 19.798 | `(7.383,-4.346)` | `(8.274,10.000)` | 14.374 | -90.00001 | -90.0 |
| l40s | 1 | 4.799 | `(1.826,-11.026)` | `(6.278,-12.000)` | 4.558 | -90.00001 | -90.0 |
| l40s | 2 | 9.799 | `(3.693,-9.541)` | `(12.000,-3.815)` | 10.089 | -0.00001 | 0.0 |
| l40s | 3 | 14.799 | `(1.845,-8.170)` | `(4.000,0.373)` | 8.810 | -0.00000 | 0.0 |
| l40s | 4 | 19.799 | `(7.158,-4.346)` | `(8.279,10.000)` | 14.389 | -90.00001 | -90.0 |

Stop condition: T1d requires both hosts to pass two consecutive default `ground_astar_loop` 60s runs. The first completed run on both hosts failed the route position gate, so I stopped and did not run full recapture or replace `docs/qa_samples/iter02_4090_3way/`.

There is no evidence of yaw residual accumulating linearly. The yaw residuals are near zero and complete for every checked sample; the remaining failure is translational/position alignment, not turn angle.

Harness deviations:

- The first l40s tmux wrapper had a shell quoting bug: `$MCDATA_OUTPUT_DIR` expanded before reaching the remote shell, so it exited after writing the first ground run instead of running `qa-run` and B. I ran `qa-run` manually against the completed l40s A run and used that as the stop evidence.
- The 4090 tmux wrapper did not parse `qa_report.json` after A, and `qa-run` itself exits zero even when route_reference is FAIL. It briefly started `t1d4090b`; I interrupted and killed the session once the A FAIL was confirmed. The pulled `runs/remote_4090/20260708T111930Z_matrix_low__t1d4090b` is partial/empty and is evidence only, not part of acceptance.

## T1e Walk Calibration Stop

Implemented and pushed the T1e setup commits:

- `444a38686bc7c906590ab920cb8b627c2b60a739` `[runner] parameterize position probe interval`
- `5fe2bdd7864190d24df2c476d49f754c6ea7a735` `[actions] add walk calibration probe`

Local verification before l40s calibration:

```text
scripts/dev_check.sh

check_standards: 0 failure(s), 2 warning(s)
All checks passed!
69 passed in 3.81s
```

l40s calibration command:

```text
scripts/sync_to_remote.sh l40s /root/mcdata

MCDATA_TMP_ROOT=/root/nas/bigdata1/tmp/mcdata \
MCDATA_OUTPUT_DIR=/root/nas/bigdata1/tmp/mcdata/runs \
DISPLAY=:77 PYTHONPATH=src MCDATA_CAPTURE_SIZE=1280x720 MCDATA_CAPTURE_FPS=24 \
python3 -m mcdata.cli run --profile matrix_low --capture --with-server \
  --replay-actions --strategy walk_calibration_probe --duration 40 \
  --probe-interval 1 --lane walkcal --game-version 26.2
```

Run evidence:

```text
runs/remote_l40s/20260708T122513Z_matrix_low__walkcal
metadata: probe_interval=1.0, strategy=walk_calibration_probe, duration=40
pipeline: position_probe positions_written count=41
replay_log: all 14 events sent on schedule; no inherited/released-key records
capture: 40.000s, 960 frames, 1280x720
```

The calibration fit failed the PLAN stop threshold. Net displacement per 4.0s hold, using interpolated positions at the scheduled hold start/end:

```text
hold 1: d=4.546418, yaw samples=[0.0, 0.0, 0.0, 0.0]
hold 2: d=6.963183, yaw samples=[171.00002, 171.00002, 171.00002, 171.00002]
hold 3: d=12.661103, yaw samples=[-8.9999695, -8.9999695, -8.9999695, -8.9999695]
hold 4: d=5.398182, yaw samples=[171.00006, 171.00006, 171.00006, 171.00006]
mean_d=7.392221
effective_v_if_t0_zero=1.848055
max_abs_residual_to_equal_d=5.268882
```

The residual is far above the `0.5` block stop threshold, so I stopped before applying any P5 walk constants. A per-sample diagnostic fit also showed the data are not a usable constant-speed calibration:

```text
per_sample_fit_v=1.100631
t0=-1.915940
max_abs_residual=5.424831
n=12
```

Additional evidence points to an invalid calibration run, not a usable slow-walk measurement:

- The first post-turn yaw settled near `171°`, not `180°`; subsequent turns alternated around `-9°` and `171°`.
- The path visibly interacted with scene/platform boundaries: positions clamp or stall near `z=-8.7`, `z=-15.3`, and later drift only slightly after the final hold.
- The l40s instance `options.txt` does contain `mouseSensitivity:0.5` and `rawMouseInput:true`, but this run's 2x600px turn did not reproduce the T1d l40s turn probe result.

I also found one specification issue in the PLAN wording: four equal-duration net displacements with `T=4.0` cannot uniquely identify both `v` and `t0` from `d = v * (T - t0)`. The design matrix is rank-deficient for net-only samples; it can identify only the product `v * (4.0 - t0)` unless additional durations or within-hold samples are used. I did not substitute a new calibration method because the run already failed the residual stop condition.

Cleanup:

```text
scripts/pull_runs_from_remote.sh l40s /root/nas/bigdata1/tmp/mcdata/runs --purge
verify: OK
error: mcdata pipeline appears active on l40s; refusing to purge.
```

The purge refusal was a false positive from the script's `pgrep` pattern matching its own ssh check. I verified there were no `portablemc`, `x11grab`, `mcdata.cli`, Minecraft Java, or ffmpeg processes and then manually removed the remote run directories under `/root/nas/bigdata1/tmp/mcdata/runs`.

Stop condition: because the l40s calibration residual exceeded `0.5` blocks, I did not set `seconds_per_block` / `walk_startup_comp_sec`, did not regenerate P5 golden/docs trajectories, did not run the two-host validation, and did not perform 4090 final recapture.

## T1e v2 Calibration And Validation Stop

Implemented and pushed the T1e v2 setup/data commits:

- `4260b02df84c3ac39de5016340f019fc4e91ce6b` `[actions] revise walk calibration probe for P6`
- `9e54003210be66bfaca7db5dbc8c116f15c9cdb3` `[data] regenerate trajectories for P5 P6 calibration`

Local verification before remote validation:

```text
scripts/dev_check.sh

check_standards: 0 failure(s), 2 warning(s)
All checks passed!
70 passed
```

P6 scene containment:

- `_scene_commands` now runs `fill 1 64 -11 3 64 -9 minecraft:glass` before `setblock 2 64 -10 minecraft:lava`.
- l40s fresh-lane server log confirms `Successfully filled 9 block(s)` immediately before the lava `setblock`.
- Screenshot evidence: `docs/qa_samples/t1e_v2/lava_basin_glass.jpg`.

P5 v2 calibration ran on l40s with a fresh lane:

```text
MCDATA_TMP_ROOT=/root/nas/bigdata1/tmp/mcdata \
MCDATA_OUTPUT_DIR=/root/nas/bigdata1/tmp/mcdata/runs \
DISPLAY=:77 PYTHONPATH=src MCDATA_CAPTURE_SIZE=1280x720 MCDATA_CAPTURE_FPS=24 \
python3 -m mcdata.cli run --profile matrix_low --capture --with-server \
  --replay-actions --strategy walk_calibration_probe --duration 32 \
  --probe-interval 1 --lane t1ev2walkcal --game-version 26.2
```

Calibration evidence:

```text
runs/remote_l40s/20260708T132328Z_matrix_low__t1ev2walkcal
positions_written count=33
replay_log: all 15 events sent on schedule
```

Static-segment fit table, using samples outside every event +/-0.5s:

| hold | T sec | d blocks | residual |
|---:|---:|---:|---:|
| 1 | 1.0 | 4.313362 | -0.000019 |
| 2 | 1.5 | 6.472017 | 0.000025 |
| 3 | 2.0 | 8.630607 | 0.000006 |
| 4 | 2.5 | 10.789198 | -0.000013 |

Fit result:

```text
v=4.317219527 blocks/sec
seconds_per_block=0.231630565
t0=0.000889052 sec
max_abs_residual=0.000025487 blocks
```

I used those measured values in `configs/actions.yml` for all `astar_walk` strategies and added the P6 glass/lava basin cells to every `blocked` list:

```text
[1,-11], [2,-11], [3,-11],
[1,-10], [2,-10], [3,-10],
[1,-9],  [2,-9],  [3,-9]
```

Golden trajectories and `docs/trajectories/*.{json,png}` were regenerated in the independent data commit listed above.

Validation then stopped on the first l40s 60s `ground_astar_loop` run:

```text
runs/remote_l40s/20260708T133709Z_matrix_low__t1ev2l40sA
route_reference: FAIL
route_max_deviation_blocks: 25.094
route_mean_deviation_blocks: 13.335
route_max_yaw_error_degrees: 35.903
route_missing_yaw_count: 0
route_y_out_of_range_count: 0
```

Representative route-reference samples:

| idx | t_rel | observed `(x,z)` | ideal `(x,z)` | dev | observed yaw | ideal yaw | yaw err |
|---:|---:|---|---|---:|---:|---:|---:|
| 1 | 4.799 | `(9.522,-11.561)` | `(9.429,-12.000)` | 0.449 | -90.000 | -90.000 | 0.000 |
| 2 | 9.799 | `(7.879,-2.499)` | `(6.739,-3.000)` | 1.246 | 90.000 | 90.000 | 0.000 |
| 3 | 14.799 | `(5.228,10.449)` | `(5.093,10.000)` | 0.469 | -90.000 | -90.000 | 0.000 |
| 4 | 19.800 | `(3.561,10.449)` | `(3.000,10.000)` | 0.718 | 54.000 | 35.097 | 18.903 |
| 5 | 24.800 | `(-6.082,13.020)` | `(-12.000,12.000)` | 6.005 | 99.000 | 134.903 | 35.903 |
| 9 | 44.800 | `(4.242,10.733)` | `(0.000,-14.000)` | 25.094 | -90.000 | -90.000 | 0.000 |

Interpretation: P6/P5 fixed the initial translation problem. The first three samples are within the 3-block position gate and have near-zero yaw error. The failure begins around the new mid-route detour/turn sequence introduced by the P6 blocked cells, with yaw gate failures at samples 4 and 5 and a later terminal position far off the route. I stopped before l40s run B, 4090 validation, and final recapture as required.

4090 note: after syncing commit `9e54003210be66bfaca7db5dbc8c116f15c9cdb3`, `nvidia-smi` queries on 4090 blocked and left stuck query processes. Because the l40s validation already failed, I did not start any 4090 Minecraft workload.

Remote cleanup:

- `scripts/pull_runs_from_remote.sh ... --purge` verified both l40s pulls, but refused purge due to its self-matching active-process check.
- I verified no l40s `portablemc`, `x11grab`, `mcdata.cli`, Minecraft Java, or ffmpeg processes remained, then manually removed `/root/nas/bigdata1/tmp/mcdata/runs/*`.

## T1f Scene Obstacles And Validation Stop

Implemented and pushed the T1f commits:

- `4087a58fdbf7fca978f046c10411fa21d4ff1d03` `[qa] ignore yaw samples during turn windows`
- `2c01dd774eb71acf1646530cdb5dc1c5ee1bddb9` `[data] cover P7 scene obstacles in astar routes`

Local verification:

```text
.venv/bin/python -m pytest tests/test_qa_report.py -q
10 passed

.venv/bin/python -m pytest tests/test_trajectory_contract.py tests/test_golden_trajectories.py -q
11 passed

scripts/dev_check.sh
check_standards: 0 failure(s), 3 warning(s)
All checks passed!
73 passed
```

Yaw gate change:

- `route_reference_report` now derives yaw ignore windows from every trajectory mouse event as `[t-0.5, t+duration+0.5]`.
- `check_route_reference` leaves position/y range checks unchanged, skips yaw scoring only inside those windows, and records `skipped_yaw_count`.
- Synthetic unit coverage verifies that a mid-turn yaw outlier is skipped while its position sample still participates in the position gate.

P7 scene obstacle audit, normalized to origin `[0,64,0]`:

| `_scene_commands` command(s) | y=64/65 occupied x,z cells | Previous config coverage | T1f final coverage |
|---|---|---|---|
| `fill -14 64 -2 -5 64 7 minecraft:water` | `x=-14..-5, z=-2..7` | `blocked_rects` yes | unchanged yes |
| `fill 5 64 -2 14 64 7 minecraft:glass` | `x=5..14, z=-2..7` | `blocked_rects` yes | unchanged yes |
| `fill -2 64 9 2 67 9 minecraft:oak_leaves` | `[-2,9],[-1,9],[0,9],[1,9],[2,9]` | no | added to every `astar_walk.blocked` |
| `fill -4 64 14 4 68 14 minecraft:white_concrete` | `[-4,14]..[4,14]` | no | added to every `astar_walk.blocked` |
| light row setblocks | `[-10,-10],[-7,-10],[-4,-10],[-1,-10],[5,-10],[8,-10],[11,-10]` | `blocked` yes | unchanged yes |
| P6 basin + lava source | `[1,-11]..[3,-9]` | `blocked` yes | unchanged yes |
| left corner marker `oak_log`/`oak_leaves` | `[-14,12]` | no | added to every `astar_walk.blocked` |
| right corner marker `polished_deepslate`/`glass` | `[14,12]` | no | added to every `astar_walk.blocked` |

Ground/floor fills at `y=63` are not A* obstacles for the player centerline. The new contract tests hardcode the y=64/65 occupied set mirrored from `_scene_commands`, assert every `astar_walk` config covers it via `blocked`/`blocked_rects`, and assert every generated route avoids it.

Golden/data note: `tests/golden/*.json` and `docs/trajectories/*.json` remained byte-identical because the current integer-center routes already avoid the newly added cells. The four `docs/trajectories/*.png` files changed because the visualization now draws the newly configured blocked points. I did not inflate blocked radii beyond the PLAN-specified occupied cells.

l40s formal validation used the NAS-backed environment:

```text
cd /root/mcdata
export MCDATA_TMP_ROOT=/root/nas/bigdata1/tmp/mcdata
source scripts/mcdata_env.sh
export DISPLAY=:77 PYTHONPATH=src
python3 -m mcdata.cli run --profile matrix_low --capture --with-server \
  --replay-actions --strategy ground_astar_loop --duration 60 \
  --game-version 26.2 --display :77 --server-port 25652 --lane t1fl40snasA
python3 -m mcdata.cli qa-run \
  /root/nas/bigdata1/tmp/mcdata/runs/20260708T144528Z_matrix_low__t1fl40snasA \
  --frames 12 --out-dir .../qa
```

Validation stopped on l40s A:

```text
runs/remote_l40s/20260708T144528Z_matrix_low__t1fl40snasA
route_reference: FAIL
route_max_deviation_blocks: 23.813
route_mean_deviation_blocks: 13.381
route_max_yaw_error_degrees: 0.000030
route_mean_yaw_error_degrees: 0.000024
route_yaw_sample_count: 8
route_skipped_yaw_count: 4
route_missing_yaw_count: 0
route_y_out_of_range_count: 0
```

Representative route-reference samples:

| idx | t_rel | observed `(x,z)` | ideal `(x,z)` | dev | observed yaw | ideal yaw | yaw status |
|---:|---:|---|---|---:|---:|---:|---|
| 1 | 4.769 | `(8.659,-11.561)` | `(9.297,-12.000)` | 0.775 | -90.000 | -90.000 | scored |
| 2 | 9.769 | `(8.527,-2.499)` | `(6.870,-3.000)` | 1.731 | 90.000 | 90.000 | scored |
| 3 | 14.799 | `(4.601,10.449)` | `(5.092,10.000)` | 0.665 | -36.000 | -90.000 | skipped: turn window |
| 4 | 19.799 | `(6.584,10.449)` | `(3.000,10.000)` | 3.612 | 90.000 | 35.171 | skipped: turn window |
| 5 | 24.799 | `(-2.531,10.666)` | `(-12.000,12.000)` | 9.562 | 90.000 | 134.834 | skipped: turn window |
| 6 | 29.799 | `(1.865,10.300)` | `(-4.000,4.795)` | 8.044 | 180.000 | 180.000 | scored |
| 8 | 39.800 | `(3.261,8.704)` | `(-2.759,-14.000)` | 23.488 | -90.000 | -90.000 | scored |
| 9 | 44.800 | `(7.182,8.704)` | `(0.000,-14.000)` | 23.813 | -90.000 | -90.000 | scored |

Interpretation: the T1f yaw gate fix works: all non-skipped yaw samples are effectively exact, and mid-turn samples are no longer counted as yaw failures. The remaining failure is purely positional. Because T1f requires stopping on any validation FAIL, I did not run l40s B, did not run final recapture, and did not replace `docs/qa_samples/iter02_4090_3way/`.

Operational notes:

- The first l40s T1f wrapper omitted `source scripts/mcdata_env.sh`, so it wrote a non-acceptance evidence run under `/root/mcdata/runs/20260708T143629Z_matrix_low__t1fl40sA`. I pulled it, QA also failed with `route_max_deviation_blocks=25.124` and `route_max_yaw_error_degrees=0.000030`, then removed the remote root-disk copy.
- The formal NAS-backed run above was pulled with `scripts/pull_runs_from_remote.sh l40s /root/nas/bigdata1/tmp/mcdata/runs`; the second rsync pass was zero-transfer (`verify: OK`). I then manually removed `/root/nas/bigdata1/tmp/mcdata/runs/*t1fl40snas*` and confirmed no l40s `portablemc`, Minecraft Java, `ffmpeg`, `mcdata.cli`, or `x11grab` process remained.
- Per the latest operator instruction, I did not perform further 4090 work in this T1f pass. The current stop evidence is l40s-only.

## T1g Scene Receipts, Replay Baseline, And Validation Stop

Commit:

- `b655ea622a49a492be700434313971f77caabe6a` `[fix] guard scene build receipts and replay baseline`

Local verification:

```text
.venv/bin/python -m pytest tests/test_pipeline_files.py tests/test_replay_log.py tests/test_configs.py -q
27 passed

.venv/bin/python -m ruff check src/mcdata/render/server.py src/mcdata/render/pipeline.py \
  src/mcdata/actions/replay.py tests/test_pipeline_files.py tests/test_replay_log.py tests/test_configs.py
All checks passed!

scripts/dev_check.sh
check_standards: 0 failure(s), 3 warning(s)
All checks passed!
79 passed
```

Implementation notes:

- P8 air clear split into two vanilla-safe fills: `37x23x37=31,487` and `37x6x37=8,214`.
- `launch_profile` now calls `verify_scene_commands` after server start. The count is for receipted scene commands (`fill`/`setblock`) and intentionally excludes `forceload`, whose success line is not one of the PLAN-listed receipt patterns.
- `replay_log.jsonl` now starts with `{"event":"start","mono":...}` after focus and key hygiene, before replay event scheduling. `positions.jsonl` uses that mono baseline when replay is active and falls back to the old ready-event baseline with a warning if the start row is missing.
- Capture start now logs `capture/window_geometry` from `xwininfo` into `pipeline.jsonl`.

l40s validation used the NAS-backed environment and the user-requested l40s-only scope:

```text
scripts/sync_to_remote.sh l40s /root/mcdata
ssh l40s 'cd /root/mcdata && \
  export PYTHONPATH=src MCDATA_TMP_ROOT=/root/nas/bigdata1/tmp/mcdata && \
  source scripts/mcdata_env.sh && export DISPLAY=:77 && \
  python3 -m mcdata.cli run --profile matrix_low --with-server --replay-actions --capture \
    --strategy ground_astar_loop --duration 60 --game-version 26.2 \
    --display :77 --server-port 25662 --lane t1gl40sA && \
  python3 -m mcdata.cli qa-run \
    /root/nas/bigdata1/tmp/mcdata/runs/20260708T163311Z_matrix_low__t1gl40sA \
    --frames 12 --out-dir .../qa'
```

Validation stopped on l40s A:

```text
runs/remote_l40s/20260708T163311Z_matrix_low__t1gl40sA
route_reference: FAIL
route_max_deviation_blocks: 25.244
route_mean_deviation_blocks: 13.333
route_max_yaw_error_degrees: 0.000030
route_mean_yaw_error_degrees: 0.000024
route_yaw_sample_count: 8
route_skipped_yaw_count: 4
route_missing_yaw_count: 0
route_y_out_of_range_count: 0
```

P8 evidence from the validation run:

```text
server/scene_verified: expected_fill_count=24 receipt_count=24
server.log failure pattern counts:
  Too many blocks: 0
  Cannot place: 0
  Expected: 0
  Unknown: 0
receipt lines:
  Successfully filled: 10
  Changed the block: 12
  No blocks were filled: 2
```

P9 evidence:

```text
replay_log.jsonl first row:
{"event": "start", "mono": 641527.8362912}

positions.jsonl head:
idx=0 t_rel=-0.277 x=0.500 z=-13.500 yaw=0.000
idx=1 t_rel=4.723 x=9.306 z=-11.561 yaw=-90.00001
idx=2 t_rel=9.723 x=7.879 z=-2.715 yaw=90.00000
```

Representative route-reference samples:

| idx | t_rel | observed `(x,z)` | ideal `(x,z)` | dev | observed yaw | ideal yaw | yaw status |
|---:|---:|---|---|---:|---:|---:|---|
| 1 | 4.723 | `(9.306,-11.561)` | `(9.100,-12.000)` | 0.485 | -90.000 | -90.000 | scored |
| 2 | 9.723 | `(7.879,-2.715)` | `(7.068,-3.000)` | 0.860 | 90.000 | 90.000 | scored |
| 3 | 14.723 | `(5.228,10.449)` | `(4.765,10.000)` | 0.646 | -90.000 | -90.000 | skipped: turn window |
| 4 | 19.723 | `(3.561,10.449)` | `(3.000,10.000)` | 0.718 | 54.000 | 54.695 | skipped: turn window |
| 5 | 24.723 | `(-6.047,13.020)` | `(-12.000,12.000)` | 6.040 | 108.000 | 115.303 | skipped: turn window |
| 6 | 29.724 | `(-2.471,12.210)` | `(-4.000,5.123)` | 7.250 | 180.000 | 180.000 | scored |
| 8 | 39.724 | `(1.370,10.771)` | `(-3.086,-14.000)` | 25.169 | -90.000 | -90.000 | scored |
| 9 | 44.724 | `(4.860,10.772)` | `(0.000,-14.000)` | 25.244 | -90.000 | -90.000 | scored |

Black-border evidence:

- `capture/window_geometry` logged `geometry=null`, requested `1280x720`, desktop false.
- `capture/start` used x11grab input `:77` rather than a window-offset input because `xwininfo` did not return a usable Minecraft window geometry.
- `qa-run` emitted black-border warnings for all 12 sampled frames; the first sampled frame flagged bottom and right edges as black (`mean=0.0`, `var=0.0`).

Operational notes:

- `scripts/pull_runs_from_remote.sh l40s /root/nas/bigdata1/tmp/mcdata/runs --purge` pulled the run and the second rsync pass was zero-transfer, but purge was refused by the script's active-process check. A follow-up `pgrep` showed no actual `mcdata.cli`, `portablemc`, `x11grab`, `ffmpeg`, Minecraft server, or Minecraft Java process, so I manually removed the pulled remote run directory. The remote `trajectories` directory was left because it is small and regenerated by CLI.
- Because the first l40s validation failed the position gate, I did not run l40s B, did not run four-way recapture, and did not replace `docs/qa_samples/iter02_4090_3way/`.
- Per the latest user instruction, I did not perform 4090 checks or workloads for T1g.

## T1h P10 Sink Pool, Toast Suppression, Window Geometry, And Final l40s Recapture

Commits:

- `dd876dbdc17ec2b8e46765a14cfd0e34f4b05dde` `[fix] sink pool and capture minecraft window geometry`
- `5a84c1db400c42c113ab72c79b0c35ca8545b5da` `[fix] keep minecraft capture window onscreen`

Local verification:

```text
scripts/dev_check.sh
check_standards: 0 failure(s), 3 warning(s)
All checks passed!
84 passed
```

Implementation notes:

- P10 pool fix: `server.py` now places pool water at `oy-1` and blue concrete at `oy-2`, so the water surface is one block below the walk plane. The pool remains blocked by config, but the y=64 scene mirror no longer treats pool water as occupied.
- Toast source: t=22-26 crops from T1g showed a Minecraft recipe toast (`New Recipes Unlocked...`) clipped at the top-right of the full-display capture, not an OS/window-manager overlay. Jar constants confirmed recipe toasts flow through Minecraft's `ToastManager` and `notificationDisplayTime`; no separate recipe-toast option was visible.
- Toast suppression: quiet capture options now write `inGameNotification:false`, `musicToast:"never"`, `realmsNotifications:false`, and `notificationDisplayTime:0.0`. Minecraft logged that `0.0` is outside the option range `[0.5:10.0]` on some fresh profile launches, but the formal l40s validation and recapture frames had no visible toast and QA warnings stayed empty. Review should decide whether to keep this empirical setting or clamp it to Minecraft's documented minimum in a later cleanup.
- Window geometry: capture now prefers `xdotool search --name Minecraft`, selects the largest matching window, parses `xdotool getwindowgeometry --shell`, and records `capture/window_geometry` in `pipeline.jsonl`. If xdotool reports a negative coordinate, the code first moves the window to `(0,0)` and rereads geometry; if it remains offscreen, capture falls back to the display rather than generating invalid x11grab inputs such as `:77+-128,-72`.

Non-acceptance retry note:

- The first post-geometry l40s retry (`20260708T171210Z_matrix_low__t1hl40sA`) exposed the offscreen case: xdotool reported `x=-128,y=-72,width=1280,height=720`, ffmpeg was invoked with `:77+-128,-72`, and the MP4 had no video stream. I stopped treating that as business validation, added the onscreen move/fallback guard in commit `5a84c1d`, reran `dev_check`, pushed, resynced to l40s, and restarted validation.

l40s validation, user-requested l40s-only scope:

```text
scripts/sync_to_remote.sh l40s /root/mcdata
ssh l40s '/tmp/t1h_verify_one.sh t1hl40sA2 25673'
ssh l40s '/tmp/t1h_verify_one.sh t1hl40sB2 25674'
```

Validation results:

| run | route | max dev | max yaw | skipped yaw | warnings | geometry |
|---|---|---:|---:|---:|---:|---|
| `20260708T171833Z_matrix_low__t1hl40sA2` | PASS | 0.916 | 0.000030 | 4 | 0 | `0,0 1280x720` |
| `20260708T172041Z_matrix_low__t1hl40sB2` | PASS | 0.924 | 0.000030 | 4 | 0 | `0,0 1280x720` |

Manual frame check: t=26 frame from `t1hl40sA2` shows the pool visibly below the platform rim, no overflow on the walk surface, no black border, and no top-right toast.

Final l40s recapture:

```text
python3 -m mcdata.cli run-matrix \
  --profiles matrix_low,matrix_textured,matrix_shader_high \
  --strategy ground_astar_loop --duration 60 --game-version 26.2 \
  --display :77 --server-port 25675 --lane t1hfinal --no-bootstrap

python3 -m mcdata.cli run --profile matrix_night_complementary \
  --with-server --replay-actions --capture \
  --strategy ground_astar_loop --duration 60 --game-version 26.2 \
  --display :77 --server-port 25675 --lane t1hfinal
```

The missing l40s NAS-backed instances for `matrix_textured`, `matrix_shader_high`, and `matrix_night_complementary` were bootstrapped before recapture. One Modrinth CDN timeout occurred while downloading `vanilla-glowing-ores-0.2.zip`, then the retry completed and bootstrap succeeded.

Final recapture run dirs:

- `runs/remote_l40s/20260708T172734Z_matrix_low__t1hfinal`
- `runs/remote_l40s/20260708T172923Z_matrix_textured__t1hfinal`
- `runs/remote_l40s/20260708T173106Z_matrix_shader_high__t1hfinal`
- `runs/remote_l40s/20260708T173252Z_matrix_night_complementary__t1hfinal`

Per-run QA:

| profile | route | max dev | max yaw | warnings | geometry |
|---|---|---:|---:|---:|---|
| `matrix_low` | PASS | 0.778 | 0.000030 | 0 | `0,0 1280x720` |
| `matrix_textured` | PASS | 0.847 | 0.000030 | 0 | `0,0 1280x720` |
| `matrix_shader_high` | PASS | 0.923 | 0.000030 | 0 | `0,0 1280x720` |
| `matrix_night_complementary` | PASS | 0.955 | 0.000030 | 0 | `0,0 1280x720` |

Pairwise compare:

| pair | position | max distance | mean distance |
|---|---|---:|---:|
| low vs textured | PASS | 0.305 | 0.206 |
| low vs shader_high | PASS | 0.305 | 0.214 |
| low vs night | PASS | 0.251 | 0.185 |
| textured vs shader_high | PASS | 0.216 | 0.053 |
| textured vs night | PASS | 0.475 | 0.280 |
| shader_high vs night | PASS | 0.320 | 0.254 |

Artifacts and cleanup:

- `docs/qa_samples/iter02_4090_3way/` was replaced with the l40s T1h final QA reports, positions, pairwise compare reports, and four t=30 representative frames. The directory name is unchanged per PLAN.
- Full l40s evidence was pulled to ignored `runs/remote_l40s/` with a verified zero-transfer second rsync pass.
- Remote `/root/nas/bigdata1/tmp/mcdata/runs` was manually purged after confirming no real `mcdata.cli`, `portablemc`, `x11grab`, `ffmpeg`, Minecraft server, or Minecraft Java process remained.
- I did not perform any 4090 work after the user redirected scope to l40s-only.

## Artifacts

- Full pulled runs, ignored by git: `runs/remote_4090/`
  - Passing T1b run dirs listed above.
  - Failed/retry evidence also pulled locally, including `20260708T081124Z_matrix_low` and `20260708T081832Z_matrix_low`.
  - T1c A isolation evidence:
    - Discard: `20260708T093746Z_matrix_low__t1cA`
    - Formal FAIL: `20260708T093856Z_matrix_low__t1cA`
  - T1c Step 3.5 evidence:
    - Formal FAIL: `20260708T102445Z_matrix_low__t1cval1`
    - Interrupted partial second run: `20260708T102710Z_matrix_low__t1cval2`
    - Pointer probe: `pointer_probe_t1cval1.jsonl`, `pointer_probe_t1cval1_meta.json`, `pointer_probe_t1cval1.log`
  - T1d evidence:
    - Turn probe: `20260708T111535Z_matrix_low__t1dturn4090`
    - Formal FAIL: `20260708T111716Z_matrix_low__t1d4090a`
    - Interrupted partial B: `20260708T111930Z_matrix_low__t1d4090b`
    - Remote wrapper log: `t1d_verify_4090.log`
  - T1d l40s evidence is pulled under ignored `runs/remote_l40s/`:
    - Turn probe: `20260708T111536Z_matrix_low__t1dturnl40s`
    - Formal FAIL: `20260708T111650Z_matrix_low__t1dl40sa`
    - Remote wrapper log: `t1d_verify_l40s.log`
  - T1e l40s evidence:
    - Walk calibration FAIL: `20260708T122513Z_matrix_low__walkcal`
    - v2 walk calibration PASS: `20260708T132328Z_matrix_low__t1ev2walkcal`
    - v2 first validation FAIL: `20260708T133709Z_matrix_low__t1ev2l40sA`
  - T1f l40s evidence:
    - Root-path wrapper mistake, evidence only: `20260708T143629Z_matrix_low__t1fl40sA`
    - Formal NAS-backed validation FAIL: `20260708T144528Z_matrix_low__t1fl40snasA`
    - Wrapper logs: `t1f_l40s_verify_root.log`, `t1f_l40s_nas_verify.log`
  - T1g l40s evidence:
    - Formal NAS-backed validation FAIL: `20260708T163311Z_matrix_low__t1gl40sA`
    - Remote wrapper log: `/root/nas/bigdata1/tmp/mcdata/logs/t1gl40sA.log` on l40s during execution; pulled run contains pipeline/server/replay/QA artifacts.
  - T1h l40s evidence:
    - Non-acceptance offscreen capture retry: `20260708T171210Z_matrix_low__t1hl40sA`
    - Validation PASS: `20260708T171833Z_matrix_low__t1hl40sA2`, `20260708T172041Z_matrix_low__t1hl40sB2`
    - Final recapture PASS: `20260708T172734Z_matrix_low__t1hfinal`, `20260708T172923Z_matrix_textured__t1hfinal`, `20260708T173106Z_matrix_shader_high__t1hfinal`, `20260708T173252Z_matrix_night_complementary__t1hfinal`
    - Compare outputs: `qa_compare_t1hfinal`, `qa_pair_*`
  - T1d NCC/contact-sheet scratch output is under ignored `runs/t1d_turn_ncc/`.
  - Older ITER-02 local pull artifacts remain in the ignored directory; review should use the exact passing run dirs listed in this report.
- Committed QA samples: `docs/qa_samples/iter02_4090_3way/`
  - Four `*_qa_report.{json,md}` files.
  - Four `*_positions.jsonl` files with 13 samples each.
  - Six `*_qa_compare_report.{json,md}` files with position PASS, max, mean, and threshold in the markdown header.
  - Four t=30 representative frames, all 1280x720 and under 300KB.
- T2 implementation artifacts:
  - `scripts/matrix_shard.sh`
  - `src/mcdata/cli.py` options: `--display`, `--server-port`, `--lane`, `--game-version`.
  - `src/mcdata/manifest.py` / `src/mcdata/schemas/manifest.schema.json`: schema v2 with top-level `lane`.
  - `docs/examples/run_manifest_example.json` updated to schema v2.

## Deviations / Notes

- The first post-instrumentation recapture still failed T1b acceptance: `matrix_low` had already moved at `idx=0`, producing max position deviation about `2.47` blocks. Commit `e76782b` fixes this by waiting for the first position probe sample before releasing replay.
- The successful 3-way used `run-matrix --no-bootstrap` after bootstrap-time Modrinth SSL EOF failures. This stayed on the CLI path, used `--game-version 26.2`, and used already bootstrapped instances from the same 4090 workspace.
- Captured run manifests record `git.source = sync_commit` and `git.commit = e76782b...` because render-host sync excludes `.git`. The compare reports were regenerated after commit `3fa0850` so their markdown/JSON include top-level position mean.
- `matrix_night_complementary` has one low median brightness warning. This matches the intentional night profile; there were no black-border or FPS warnings.
- T1c Step 2 B/C/D were not run because A, with both debug skips enabled, already failed the route-reference gate. This follows the PLAN.md stop condition for A off-route.
- T1c Step 3.5 failed on the first default 60s no-discard run despite replay key cleanup, normal replay thread completion, and no inherited-stuck-key replay log record. I stopped before Step 4 per PLAN.md.
- T1d stopped before full recapture. The first completed `ground_astar_loop` run on both 4090 and l40s failed the position gate while yaw samples were present and matched the ideal yaw timeline. I did not implement the waypoint yaw resync fallback because the yaw residual did not accumulate or exceed threshold.
- The T1d turn probe on 4090 visually overlapped at +360/+720 but measured NCC `0.7867/0.7887`, just below the numeric `0.8` threshold; l40s measured `0.9057/0.9105`. The ground validation failures independently satisfy the stop condition.
- T1e v2 stopped after l40s validation A failed. Calibration itself passed with residual `0.000025` blocks, so the remaining failure is not the same lava/translation fit issue as T1e v1.
- T1f stopped after the first formal l40s validation A run failed the position gate. Yaw de-artifacting worked (`route_max_yaw_error_degrees=0.000030`, `route_skipped_yaw_count=4`), so the remaining T1f failure is not a yaw-gate artifact.
- T1f P7 added the PLAN-specified occupied cells to every `astar_walk.blocked`, but the route JSON did not change because the generated integer-center routes already avoided those exact cells. The visualization PNGs changed to show the new blocked points; I did not expand blocked cells beyond the audited y=64/65 occupied grid.
- T1g stopped after the first formal l40s validation A run failed the position gate. P8 and P9 instrumentation behaved as expected: scene receipts were complete, server.log had no scene failure pattern, and `positions.jsonl` used the replay-log start baseline.
- T1g black-border investigation found `xwininfo` geometry unavailable (`geometry=null`), capture falling back to full display `:77`, and black-border warnings on all sampled frames. I recorded the data and did not attempt a display/window workaround without planner direction.
- T1h completed on l40s-only scope: both 60s validation runs and all four final recapture runs passed route/yaw gates, QA black-border checks, and visual toast checks. The largest final pairwise position distance was `0.475` blocks.
- I did not start 4090 validation after l40s validation A failed. 4090 `nvidia-smi` was also blocked by other active/stuck GPU-query state at the time of validation.
- After the user redirected work to l40s-only, I did not perform further 4090 checks or workloads.
- `scripts/check_standards.py` still warns about `render/pipeline.py` size and `launch_profile` length. I left the larger pipeline refactor out of T1b/T2 because the plan required scoped fixes and checker rules were not changed.
- T3 was not run; it depends on the user-provided 8-card container. T2 code is ready for per-GPU shard launches.

## Review Focus

- T1b ordering in `launch_profile`: `join/re_apply_state`, capture start, position probe start, first sample wait, then replay release.
- `qa-compare` position failure semantics: both run dirs with `positions.jsonl` produce max/mean by `idx`; max over `2.0` blocks marks FAIL in the report header.
- `.sync_commit` provenance for rsync-without-git render hosts.
- T1c A failure interpretation: both hidden skips are active in the formal run, but route-reference still fails with max deviation `31.127` blocks and y drop to `51.0`.
- T1c Step 3.5 failure interpretation: key-state cleanup behaved as designed (`thread_joined alive=false`, no inherited/released replay-control records), but route-reference still failed and pointer edge parking was 95.7% during gameplay.
- T1d failure interpretation: after P4 correction, yaw aligns to the ideal route on both hosts, but observed position diverges from the ideal path by >21 blocks. This points away from residual turn calibration and toward movement/translation timing or route-model calibration.
- T1e v2 failure interpretation: initial route samples now pass position/yaw after P5/P6; validation fails later around the new P6 detour with yaw residuals >10 degrees and then terminal position divergence.
- T1f failure interpretation: yaw residuals are removed after turn-window filtering, scene occupied cells are now covered by config/tests, but l40s still fails the position gate by `23.813` blocks on A. Review should focus on the remaining position-model / collision-margin mismatch rather than P3/P4/P5/yaw sampling.
- T1g failure interpretation: scene construction is now verified cleanly and `t_rel` starts from actual replay start, but l40s still fails position by `25.244` blocks with yaw effectively exact. Review should also decide the black-border/window-geometry path because `xwininfo` did not find usable Minecraft geometry on l40s.
- T1h review focus: P10 pool sinking fixed the route divergence; xdotool geometry plus onscreen move removed black borders; toast did not recur in validation/recapture, though Minecraft logs show `notificationDisplayTime:0.0` is outside the accepted range.
- T2 lane semantics remain unchanged: run dir suffix `__gpuN`, isolated server/world directory, per-lane matrix trajectory, and manifest top-level `lane`.
