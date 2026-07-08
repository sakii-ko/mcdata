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

## Artifacts

- Full pulled runs, ignored by git: `runs/remote_4090/`
  - Passing T1b run dirs listed above.
  - Failed/retry evidence also pulled locally, including `20260708T081124Z_matrix_low` and `20260708T081832Z_matrix_low`.
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
- `scripts/check_standards.py` still warns about `render/pipeline.py` size and `launch_profile` length. I left the larger pipeline refactor out of T1b/T2 because the plan required scoped fixes and checker rules were not changed.
- T3 was not run; it depends on the user-provided 8-card container. T2 code is ready for per-GPU shard launches.

## Review Focus

- T1b ordering in `launch_profile`: `join/re_apply_state`, capture start, position probe start, first sample wait, then replay release.
- `qa-compare` position failure semantics: both run dirs with `positions.jsonl` produce max/mean by `idx`; max over `2.0` blocks marks FAIL in the report header.
- `.sync_commit` provenance for rsync-without-git render hosts.
- T2 lane semantics remain unchanged: run dir suffix `__gpuN`, isolated server/world directory, per-lane matrix trajectory, and manifest top-level `lane`.
