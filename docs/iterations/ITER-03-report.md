# ITER-03 Report

Branch: `iter/03-roaming-scene`

## Commits

- T0 scene.yml source of truth: `13272a3e6c2b04c35d497f8cb63368933a18176e` `[impl] add scene.yml source of truth`
- T1 deterministic roam trajectories: `f0a9bb9448c757d39cd54ec5cada87febf9d12d0` `[impl] add deterministic roam trajectories`
- T1 physical obstacle clearance: `b9c95346ce52b63fae8583825c0f33acc6222956` `[fix] keep roam routes clear of obstacles`

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

## T1 — Deterministic Programmatic Roaming

Implemented the `roam` strategy with an explicit `random.Random(seed)`, reachable-goal
sampling, a 100-attempt rejection limit, A* concatenation, sampled pause/look actions,
and shared `_walk_events(route, spec)` event generation with `astar_walk`. Added
`roam_a` through `roam_f` for seeds 101–106.

The generated trajectory records both the route and sampled goals. All six strategies
have byte-level golden JSON files and reviewed maps under `docs/trajectories/roam/`.
The visualization command now includes obstacles derived from `scene.yml`, so the maps
directly show the route/obstacle contract instead of relying only on unit tests.

### Physical-clearance finding and fix

The first L40S `roam_a` run exposed a real collision that integer-grid tests could not:
the path passed directly beside the beacon at `(11, -10)`. Player-center offset and
sub-block movement error caused the eight-block `(12, -9) -> (4, -9)` span to collide,
leaving all later positions roughly eight blocks east of the reference route.

The rejected run was
`20260709T181359Z_matrix_low__t1roam_a_l40s` at commit `f0a9bb9`:

- route reference: FAIL;
- maximum/mean deviation: 8.728 / 7.015 blocks;
- video checks still passed at 1280x720, 24 fps, 1440 frames, with no black border.

The general fix is configured `obstacle_clearance: 1` for roam. The roam builder expands the
derived/configured obstacle set by one grid cell for goal sampling and A*, without
changing the calibrated existing `astar_walk` routes. A contract test now asserts every
roam route maintains the configured Chebyshev clearance. All six goldens and maps were
regenerated and reviewed after this change.

### Local validation

```text
scripts/dev_check.sh
```

Key output after the clearance fix:

```text
WARN  R19: render/pipeline.py has 1357 lines (>600) -- justify in report
check_standards: 0 failure(s), 1 warning(s)
All checks passed!
93 passed in 8.11s
```

The six route maps are 34–39 KB each, show the scene-derived obstacle grid, and were
reviewed as a 3x2 montage. Routes are distinct across all six seeds and do not enter the
configured one-cell clearance envelope.

### L40S validation

Both accepted runs used `matrix_low`, `DISPLAY=:77`, Minecraft 26.2, a 60-second
capture, and manifest commit `b9c95346ce52b63fae8583825c0f33acc6222956` with
`git.source=sync_commit` and `dirty=false`.

| run | route | max dev | mean dev | max yaw | yaw samples | skipped yaw | warnings | video |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `20260709T182143Z_matrix_low__t1roam_a_clear_l40s` | PASS | 1.082 | 0.628 | 0.000 | 7 | 5 | 0 | 1280x720 / 24 fps / 1440 frames |
| `20260709T182420Z_matrix_low__t1roam_d_clear_l40s` | PASS | 0.933 | 0.748 | 0.000 | 6 | 6 | 0 | 1280x720 / 24 fps / 1440 frames |

Both contact sheets were inspected: HUD is retained; there are no black borders,
toast/chat overlays, stuck views, or scene-loading frames; water, glass, lights, lava,
and the background markers are visible across the samples.

Artifacts were pulled with a zero-transfer second rsync verification to
`runs/remote_l40s/`, including the rejected diagnostic run. After confirming no active
pipeline process, the remote runs directory was purged.
