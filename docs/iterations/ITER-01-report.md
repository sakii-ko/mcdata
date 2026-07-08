# ITER-01 Report

Branch: `iter/01-foundations`

## Commits

- T0: `0515844152f2e78e6ce03b6305a689ce221912b0` `[test] add development check tooling`
- T1 golden: `afcd5bad4a2eb0e0b44206dce310989be5d29a11` `[test] add golden trajectories from baseline behavior`
- T1 refactor/tests: `4d160706c9f8ba531223814c546570c5f82e33fd` `[impl] refactor action strategies behind pure builder`
- T2 manifest/logging: `51fd9752974e669519e8383cf67e69a890da056a` `[impl] add run manifest and structured logging`
- T2 example manifest: `36108766b9fd82c860354845b4e0d1b244aa5467` `[docs] add run manifest example`
- T2 standards follow-up: `c778d85071c5e4b9f3ae923d666c1e469ff01ca8` `[fix] move capture settings to env boundary`
- T3 QA: `03ffc97c64bfb0de5002b8ab653345afd685b380` `[qa] add offline video QA commands`
- T4 routes/viz: `c3991d17efbee62d4c47b1d507f85ff5aed04f1e` `[impl] add trajectory visualization and route variants`

Planner/main updates were merged during the iteration in `8960a68dd89cdaf1b0297ddebd74c5aa8971618e` and `7226f5e79bb53c7ea2b9a6b19b372a5fd2e45953` to pick up updated standards, host notes, and scripts.

## Validation Commands

- `.venv/bin/pip install -e '.[dev,qa]'`
  - Installed successfully from the configured PyPI mirror.
- `bash scripts/dev_check.sh`
  - Final output:
    ```text
    WARN  R19: render/pipeline.py has 724 lines (>600) -- justify in report
    WARN  R19: render/pipeline.py:150 function launch_profile spans 178 lines (>80)
    check_standards: 0 failure(s), 2 warning(s)
    All checks passed!
    .................................                                        [100%]
    33 passed in 0.65s
    ```
- `.venv/bin/mcdata run --profile matrix_low --dry-run --with-server --replay-actions --strategy ground_astar_loop --duration 10`
  - Produced `runs/20260708T053312Z_matrix_low/{manifest.json,pipeline.jsonl,trajectory.json,metadata.json}`.
  - Manifest schema validation passed; copied to `docs/examples/run_manifest_example.json`.
- `.venv/bin/mcdata qa-run ... --frames 12`
  - Ran on:
    - `runs/screen_recordings/matrix_low_ground_astar_final_20260707T173901`
    - `runs/screen_recordings/matrix_low_ground_astar_stable_24fps_20260707T170812`
    - `runs/screen_recordings/matrix_emissive_makeup_nochat_20260707T171753`
- `.venv/bin/mcdata qa-compare <final> <stable> --frames 12 --out-dir docs/qa_samples/matrix_low_compare_tmp`
  - Completed successfully; final copied artifacts are in `docs/qa_samples/`.
- `.venv/bin/mcdata make-trajectory ...` and `.venv/bin/mcdata viz-trajectory ...`
  - Generated JSON/PNG for `ground_astar_loop`, `water_edge_loop`, `glass_edge_loop`, and `light_closeup_tour`.

## Artifacts

- Golden trajectories: `tests/golden/*.json`, including all non-external strategies.
- Manifest schema and sample:
  - `src/mcdata/schemas/manifest.schema.json`
  - `docs/examples/run_manifest_example.json`
- QA samples:
  - `docs/qa_samples/matrix_low_ground_astar_final_qa_report.{json,md}`
  - `docs/qa_samples/matrix_low_ground_astar_final_contact_sheet.jpg`
  - `docs/qa_samples/matrix_low_ground_astar_stable_qa_report.{json,md}`
  - `docs/qa_samples/matrix_low_ground_astar_stable_contact_sheet.jpg`
  - `docs/qa_samples/matrix_emissive_makeup_nochat_qa_report.{json,md}`
  - `docs/qa_samples/matrix_emissive_makeup_nochat_contact_sheet.jpg`
  - `docs/qa_samples/matrix_low_ground_astar_compare_report.{json,md}`
  - `docs/qa_samples/matrix_low_ground_astar_compare_contact_sheet.jpg`
- Trajectory review outputs:
  - `docs/trajectories/ground_astar_loop.{json,png}`
  - `docs/trajectories/water_edge_loop.{json,png}`
  - `docs/trajectories/glass_edge_loop.{json,png}`
  - `docs/trajectories/light_closeup_tour.{json,png}`

All committed QA and trajectory images are under 300KB.

## Deviations / Notes

- T1 uncovered a pre-existing `random_walk` contract issue: generated events were deterministic but not sorted by `t`. `replay_trajectory` already sorted before playback, so real playback timing was unaffected. I fixed `build_trajectory` to sort events at the pure-function boundary and updated only `random_walk` golden accordingly.
- T2 originally put `CaptureSettings` in `pipeline.py`; after planner updated `CODE_STANDARDS.md`, I moved it to `src/mcdata/settings.py`, added `scripts/check_standards.py` to `dev_check.sh`, and removed the checker R2 temporary baseline for `render/pipeline.py`.
- `scripts/check_standards.py` still warns that `render/pipeline.py` and `launch_profile` are large. I did not split pipeline in ITER-01 because the plan explicitly keeps deeper pipeline decomposition in the backlog after T2/T3 observability is in place. The warning is recorded here for review.
- T2 integration used dry-run rather than Xvfb capture. It still produced manifest/pipeline/trajectory artifacts and validates the new provenance path without requiring local X/GPU.
- `qa-run` currently records p5/p50/p95 brightness and border metrics; histogram rendering is not included as a separate chart. Contact sheets plus JSON metrics are committed.

## Review Focus

- Manifest schema field naming and whether any additional provenance should be mandatory before ITER-02.
- `CaptureSettings` boundary and checker changes after the mid-iteration standards update.
- `waypoint_actions` semantics: pause/look is triggered only when the configured point is one of the route goals, so incidental path crossings do not create extra stops.
- QA thresholds for black border and low brightness before using reports as release gates.
