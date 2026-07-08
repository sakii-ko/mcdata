# ITER-02 Report

Branch: `iter/02-gpu-collection`

## Commits

- T0 review fixes: `a93887402bb7fedd3251d4baf796510d1b63645d` `[fix] address ITER-01 review findings`
- T1 QA compatibility follow-up: `826a5e784f0550283fc64b78b64d4638474a0a57` `[fix] support older pillow in QA reports`
- T1 4090 samples: `e975ef3beb6619ef21285b48c3581b4b52eaf6e5` `[data] add ITER-02 4090 QA samples`
- T2 shard isolation: `69c91d4ed67edcdd25d3efd89312fc6530faf315` `[impl] add run-matrix shard isolation`

## Validation Commands

- `bash scripts/dev_check.sh`
  - T0 final: 36 tests passed.
  - T1 Pillow follow-up: 37 tests passed.
  - T2 final:
    ```text
    WARN  R19: render/pipeline.py has 762 lines (>600) -- justify in report
    WARN  R19: render/pipeline.py:161 function launch_profile spans 192 lines (>80)
    check_standards: 0 failure(s), 2 warning(s)
    All checks passed!
    ..............................................                           [100%]
    46 passed in 1.54s
    ```
- 4090 preflight:
  - `ssh 4090 'nvidia-smi --query-gpu=index,name,memory.used,memory.free --format=csv,noheader,nounits | head -4'`
  - GPU 0 had about 15GB free; `DISPLAY=:77 glxinfo -B` reported RTX 4090.
- Code sync to 4090:
  - `rsync -az --delete --exclude .git --exclude .venv --exclude .mcdata --exclude runs ./ 4090:/home/lyf/mcdata/`
- 4090 capture:
  - Initial planned `run-matrix` command hit a transient Modrinth SSL EOF while resolving `latest_modded`.
  - Actual collection used the same profiles/strategy/duration through `launch_profile(..., game_version="26.2")`.
  - 3-way profiles: `matrix_low`, `matrix_textured`, `matrix_shader_high`.
  - Night profile: `matrix_night_complementary`.
  - Remote logs `runs/remote_4090/iter02_3way.log` and `runs/remote_4090/iter02_night.log` include `Using graphics device: NVIDIA GeForce RTX 4090/PCIe/SSE2`, `capture: stop`, and `manifest_written`.
- QA on 4090:
  - `python3 -m mcdata.cli qa-run <run> --frames 12` for all four runs.
  - Pairwise compare for 3-way plus `shader_high_vs_night`.
  - Summary:
    ```text
    matrix_low: 60.0s, 24.0fps, 1280x720, warnings=0, p50=19.2-140.0
    matrix_textured: 60.0s, 24.0fps, 1280x720, warnings=0, p50=36.8-154.8
    matrix_shader_high: 60.0s, 24.0fps, 1280x720, warnings=0, p50=27.1-160.4
    matrix_night_complementary: 60.0s, 24.0fps, 1280x720, warnings=0, p50=12.0-135.9
    low_vs_textured NCC min/mean/max -0.1829/0.4338/0.9174
    low_vs_shader_high NCC min/mean/max -0.2119/0.2891/0.9289
    textured_vs_shader_high NCC min/mean/max 0.4245/0.7794/0.9592
    shader_high_vs_night NCC min/mean/max -0.4102/0.3444/0.7276
    ```
- Pull and purge:
  - `scripts/pull_runs_from_remote.sh 4090 /home/lyf/mcdata/runs --purge`
  - Output included `verify: OK`, `purge: done`, `size: 42M`.
  - Follow-up check: remote `/home/lyf/mcdata/runs` has 0 top-level entries.
- T2 checks:
  - `bash -n scripts/matrix_shard.sh`
  - `.venv/bin/python -m pytest -q tests/test_pipeline_files.py::test_concurrent_dry_run_processes_isolate_lane_port_and_display -vv`
    - Passed; the test launches two Python processes with different lane/port/display and validates independent manifest values.
  - Missing instance guard:
    ```text
    matrix_shard_missing_instance_rc=1
    error: missing pre-bootstrapped instance dir(s):
      /tmp/.../instances/matrix_low
    hint: run serial bootstrap first; matrix shards always use --no-bootstrap
    ```

## Artifacts

- Full pulled runs, ignored by git: `runs/remote_4090/`
  - `20260708T062243Z_matrix_low`
  - `20260708T062500Z_matrix_textured`
  - `20260708T062705Z_matrix_shader_high`
  - `20260708T063529Z_matrix_night_complementary`
  - `iter02_3way.log`, `iter02_night.log`, `iter02_qa_logs/`, `iter02_qa_compare/`
- Committed QA samples: `docs/qa_samples/iter02_4090_3way/`
  - Four `*_qa_report.{json,md}` files.
  - Four `*_qa_compare_report.{json,md}` files.
  - Four representative frames, all under 30KB.
- T2 implementation:
  - `scripts/matrix_shard.sh`
  - `src/mcdata/cli.py` options: `--display`, `--server-port`, `--lane`.
  - `src/mcdata/manifest.py` / `src/mcdata/schemas/manifest.schema.json`: schema v2 with top-level `lane`.
  - `docs/examples/run_manifest_example.json` updated to schema v2.

## Deviations / Notes

- The exact T1 `run-matrix` command failed twice before capture because Modrinth metadata resolution for `latest_modded` returned an SSL EOF. I used explicit `game_version="26.2"` for the real capture, matching the resolved working version from ITER-01 and avoiding network metadata during the run.
- Because the required rsync excludes `.git`, captured 4090 manifests have `git.commit = null`. The branch/commit provenance is preserved in this report and in the pushed commits.
- T1 exposed that 4090 has an older Pillow without `Image.Resampling`; `826a5e7` adds a compatibility fallback and test. This was needed for remote `qa-run` to complete.
- `scripts/check_standards.py` still warns about `render/pipeline.py` size and `launch_profile` length. I did not split it in T2 because the plan explicitly prescribed scoped parallelization changes, and deeper pipeline decomposition remains backlog after T2/T3 observability.
- T3 was not run; it depends on the user-provided 8-card container. T2 code is ready for per-GPU shard launches.

## Review Focus

- T2 lane semantics: run dir suffix `__gpuN`, server/world directory `world_profile__gpuN`, matrix trajectory `ground_astar_loop_matrix_gpuN.json`, manifest top-level `lane`.
- Whether `run-matrix` should gain an explicit CLI `--game-version` in a future iteration to avoid the T1 Modrinth resolution failure without needing API-level invocation.
- Whether manifest provenance should accept an explicit commit override for rsync-without-git render hosts.
- Confirm that `matrix_shard.sh --no-bootstrap` policy is acceptable for T3; it deliberately fails fast when instance dirs are missing.
