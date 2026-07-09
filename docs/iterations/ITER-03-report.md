# ITER-03 Report

Branch: `iter/03-roaming-scene`

## Commits

- T0 scene.yml source of truth: `13272a3e6c2b04c35d497f8cb63368933a18176e` `[impl] add scene.yml source of truth`

## T0 — scene.yml Source Of Truth

Implemented `configs/scene.yml` and the pure `mcdata.scene_model` module. Render scene setup now consumes `scene_model.scene_commands()`, and configured `astar_walk` trajectories receive `blocked`/`blocked_rects` plus `walk_obstacles(load_scene(...))`.

Behavior-preserving checks:

- New command golden asserts `scene_commands()` exactly matches the previous hardcoded command list, including the two split air-fill commands.
- `tests/test_trajectory_contract.py` now derives scene occupied cells from `scene.yml` and asserts every configured `astar_walk` blocking set matches that derived set.
- Golden trajectory bytes and `docs/trajectories/` did not change.
- The water pool entry has `walk_obstacle: true` so the derived obstacle set remains identical to the existing config blocking while the water itself stays below the walk surface.

## Validation Commands

Local:

```text
.venv/bin/python -m pytest tests/test_configs.py tests/test_trajectory_contract.py tests/test_golden_trajectories.py tests/test_scene_verify.py -q
scripts/dev_check.sh
git diff --name-only -- tests/golden docs/trajectories
```

Key output:

```text
24 passed in 1.54s
WARN  R19: render/pipeline.py has 1357 lines (>600) -- justify in report
check_standards: 0 failure(s), 1 warning(s)
All checks passed!
87 passed in 3.71s
```

`git diff --name-only -- tests/golden docs/trajectories` produced no output.

l40s sync and validation:

```text
rsync -az --delete --exclude .git --exclude .venv --exclude .mcdata/runs --exclude runs ./ l40s:/root/mcdata/
ssh l40s 'cd /root/mcdata && printf "%s\n" 13272a3e6c2b04c35d497f8cb63368933a18176e > .sync_commit'
ssh l40s 'cd /root/mcdata && export PYTHONPATH=src MCDATA_TMP_ROOT=/root/nas/bigdata1/tmp/mcdata DISPLAY=:77 && python3 -m mcdata.cli run --profile matrix_low --with-server --replay-actions --capture --strategy ground_astar_loop --duration 60 --game-version 26.2 --display :77 --server-port 25678 --lane t0scene_l40s'
ssh l40s 'cd /root/mcdata && export PYTHONPATH=src && python3 -m mcdata.cli qa-run "$RUN" --frames 12 --out-dir "$RUN/qa"'
```

Validation result:

| run | route | max dev | mean dev | max yaw | yaw samples | skipped yaw | warnings | renderer |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `20260709T014048Z_matrix_low__t0scene_l40s` | PASS | 0.756 | 0.479 | 0.000 | 8 | 4 | 0 | `NVIDIA L40S/PCIe/SSE2` |

Run manifest records `git.source=sync_commit`, `git.commit=13272a3e6c2b04c35d497f8cb63368933a18176e`, `dirty=false`, `lane=t0scene_l40s`.

## Artifacts

- Scene config: `configs/scene.yml`
- Shared model: `src/mcdata/scene_model.py`
- Local pulled validation run: `runs/remote_l40s/20260709T014048Z_matrix_low__t0scene_l40s`
- QA report: `runs/remote_l40s/20260709T014048Z_matrix_low__t0scene_l40s/qa/qa_report.md`

Remote cleanup:

```text
MCDATA_OUTPUT_DIR=/root/nas/bigdata1/cjw/projs/mcdata/runs scripts/pull_runs_from_remote.sh l40s /root/nas/bigdata1/tmp/mcdata/runs
ssh l40s 'if pgrep -af "[m]cdata.cli|[p]ortablemc|[x]11grab"; then exit 1; fi; find /root/nas/bigdata1/tmp/mcdata/runs -mindepth 1 -maxdepth 1 -exec rm -rf {} +'
```

The pull script completed a zero-transfer verification pass; remote runs were then purged after a non-self-matching process check found no active pipeline.

## Deviations / Notes

- The first l40s command failed before launch because `PYTHONPATH=src` was missing on the remote shell. No run directory was produced. I reran with `PYTHONPATH=src`; the accepted run is the one listed above.
- I did not use `pull_runs_from_remote.sh --purge` because its current active-process guard can self-match the literal `pgrep` pattern, as noted in ITER-02. I used the non-self-matching pattern before manual purge.
- Remaining checker warning: `render/pipeline.py` is still above the R19 file-size threshold. This was pre-existing and is not changed materially by T0; the report records it for review.

## Review Focus

- Whether `walk_obstacle: true` on the below-surface water pool is the right explicit representation for "not solid, but intentionally blocked for route planning" until ITER-03+ scene semantics are expanded.
- `_profile_with_scene()` injects scene.yml entries into profile world state at run/bootstrap planning time; profiles can still override `enabled` and `origin`, but entries remain sourced from `scene.yml`.
- T0 should be behavior-preserving: scene command golden, trajectory golden, and l40s validation all pass without committed trajectory/viz changes.
