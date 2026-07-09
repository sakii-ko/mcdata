# ITER-03 Review

Verdict: **PASS**

Reviewed branch: `iter/03-roaming-scene`

## Acceptance summary

### T0 — scene.yml source of truth

- `configs/scene.yml` is the single data source for render commands and walk obstacles.
- Command golden matches the previous hardcoded sequence byte for byte, including split
  air fills.
- Existing trajectory goldens did not change.
- L40S 60-second route/video QA passed.

### T1 — deterministic roam

- `roam` requires an explicit seed, samples reachable goals with a bounded retry count,
  concatenates A* segments, and shares `_walk_events` with `astar_walk`.
- `roam_a` through `roam_f` have byte-level goldens and reviewed route maps.
- Contract tests cover determinism, seed diversity, bounds, scene collision avoidance,
  minimum adjacent-goal distance, and physical obstacle clearance.
- The initial L40S collision failure was correctly retained as evidence and generalized
  into configured one-cell clearance rather than a route-specific patch.
- Accepted `roam_a` / `roam_d` 60-second runs passed route, yaw, frame-rate, size, border,
  and visual checks with maximum deviations 1.082 / 0.933 blocks.

### T2 — render matrix expansion

- Exact Modrinth 26.2 filtering was used; no replacement slugs were introduced.
- `matrix_euphoria_complementary` and `matrix_solas_patrix` were added with valid asset
  cross-references and shared `render_matrix_base` / port 25570 contracts.
- Photon, Rethinking Voxels, Super Duper Vanilla, Nostalgia, and Kappa were correctly
  skipped because their requested slugs have no 26.2 version.
- Both supported profiles bootstrapped and completed 10-second L40S smoke/QA runs.
- Runtime logs prove the selected mod, shader, and resource pack were actually loaded.
- Two reviewed representative frames are committed below the 300 KB limit.

## Verification

```text
scripts/dev_check.sh
```

```text
WARN  R19: render/pipeline.py has 1357 lines (>600) -- justify in report
check_standards: 0 failure(s), 1 warning(s)
All checks passed!
94 passed in 44.84s
```

All accepted remote manifests identify a clean `sync_commit`; all run batches were pulled
to local NAS with a zero-transfer second pass and purged remotely after an active-process
check.

## Non-blocking findings

1. `render/pipeline.py` remains at 1357 lines. This is the pre-existing R19 warning already
   recorded in the report; future material work in that file should perform the planned
   module split.
2. `actions/strategies.py` is now 597 lines, close to the 600-line warning threshold. A
   future strategy addition should split walk planning/events into focused modules.
3. ITER-02 T3 still needs a multi-GPU rendering environment for the planned parallel full
   matrix batch; this does not block ITER-03 correctness.
