from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
from pathlib import Path
from typing import Any

from mcdata.action_jump import DELIBERATE_JUMP_SEMANTIC

REPORT_SCHEMA_VERSION = 1
REPORT_FILENAME = "action_effect_report.json"
REQUIRED_JUMP_COUNT = 4
MAX_POSITION_GAP_SEC = 0.2
MAX_DISPATCH_LATENESS_SEC = 0.05
PRE_GROUND_WINDOW_SEC = 0.5
EFFECT_WINDOW_SEC = 1.5
MIN_PEAK_DELTA_Y = 0.8
GROUND_TOLERANCE_Y = 0.05
MIN_GROUND_SAMPLES = 2


class ActionEffectError(ValueError):
    """Raised when a persisted physical-action report is missing or inconsistent."""


def trajectory_planned_level(trajectory: Any) -> int:
    if not isinstance(trajectory, dict):
        return 1
    claim = trajectory.get("action_curriculum")
    if not isinstance(claim, dict):
        return 1
    level = claim.get("planned_level")
    return level if type(level) is int and level in {1, 2, 3, 4} else 1


def action_effect_required(trajectory: Any) -> bool:
    return trajectory_planned_level(trajectory) >= 2


def build_action_effect_report(run_dir: Path) -> dict[str, Any]:
    run_dir = Path(run_dir)
    paths = {
        "trajectory": run_dir / "trajectory.json",
        "replay_log": run_dir / "replay_log.jsonl",
        "positions": run_dir / "positions.jsonl",
    }
    trajectory, trajectory_errors = _load_object(paths["trajectory"])
    replay, replay_errors = _load_jsonl(paths["replay_log"])
    positions_raw, position_errors = _load_jsonl(paths["positions"])
    positions, position_shape_errors = _position_rows(positions_raw)
    level = trajectory_planned_level(trajectory)
    plans, sequence_errors = _jump_plans(trajectory)
    replay_rows, replay_alignment_errors = _semantic_replay_rows(trajectory, replay)
    jumps = [
        _jump_result(plan, replay_rows, positions)
        for plan in plans
    ]
    trace = _position_trace(positions, jumps)
    checks = {
        "planned_level_requires_jump_evidence": level >= 2,
        "source_artifacts_complete": not (
            trajectory_errors or replay_errors or position_errors
        ),
        "canonical_jump_sequences": not sequence_errors,
        "exact_required_jump_count": len(plans) == REQUIRED_JUMP_COUNT,
        "replay_semantic_alignment": not replay_alignment_errors,
        "semantic_jump_inputs_dispatched": len(jumps) == REQUIRED_JUMP_COUNT
        and all(item["dispatch"]["input_dispatch_observed"] for item in jumps),
        "replay_dispatch_complete": bool(jumps)
        and all(item["dispatch"]["passed"] for item in jumps),
        "replay_timing_aligned": bool(jumps)
        and all(item["dispatch"]["timing_passed"] for item in jumps),
        "position_rows_valid": not position_shape_errors,
        "position_cadence": trace["cadence_passed"],
        "position_window_coverage": trace["window_coverage_passed"],
        "all_jumps_physical": len(jumps) == REQUIRED_JUMP_COUNT
        and all(item["physical_effect_passed"] for item in jumps),
    }
    failures = _unique(
        trajectory_errors
        + replay_errors
        + position_errors
        + position_shape_errors
        + sequence_errors
        + replay_alignment_errors
        + [name for name, passed in checks.items() if not passed]
        + [
            f"{item['jump_id']}:{reason}"
            for item in jumps
            for reason in item["failure_reasons"]
        ]
    )
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "kind": "physical_deliberate_jump",
        "thresholds": {
            "required_jump_count": REQUIRED_JUMP_COUNT,
            "max_position_gap_sec": MAX_POSITION_GAP_SEC,
            "max_dispatch_lateness_sec": MAX_DISPATCH_LATENESS_SEC,
            "pre_ground_window_sec": PRE_GROUND_WINDOW_SEC,
            "effect_window_sec": EFFECT_WINDOW_SEC,
            "min_peak_delta_y": MIN_PEAK_DELTA_Y,
            "ground_tolerance_y": GROUND_TOLERANCE_Y,
            "min_ground_samples": MIN_GROUND_SAMPLES,
        },
        "source_artifacts": {
            name: _artifact(path, run_dir) for name, path in paths.items()
        },
        "planned_level": level,
        "planned_jump_count": len(plans),
        "verified_jump_count": sum(item["status"] == "pass" for item in jumps),
        "position_trace": trace,
        "jumps": jumps,
        "checks": checks,
        "failure_reasons": failures,
        "accepted": level >= 2 and all(checks.values()),
    }
    report["report_id"] = f"sha256:{_canonical_sha256(report)}"
    return report


def write_action_effect_report(run_dir: Path) -> dict[str, Any]:
    run_dir = Path(run_dir)
    report = build_action_effect_report(run_dir)
    path = run_dir / REPORT_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return report


def validate_action_effect_report(run_dir: Path) -> dict[str, Any]:
    path = Path(run_dir) / REPORT_FILENAME
    if not path.is_file() or path.is_symlink():
        raise ActionEffectError(f"physical action-effect report is missing: {path}")
    try:
        observed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ActionEffectError(f"physical action-effect report is unreadable: {path}") from exc
    expected = build_action_effect_report(Path(run_dir))
    if observed != expected:
        raise ActionEffectError("physical action-effect report does not match source artifacts")
    return expected


def action_effect_manifest_evidence(path: Path, report: dict[str, Any]) -> dict[str, Any]:
    if path.name != REPORT_FILENAME or not path.is_file() or path.is_symlink():
        raise ActionEffectError("physical action-effect report path is unsafe or missing")
    return {
        "kind": "physical_deliberate_jump",
        "schema_version": REPORT_SCHEMA_VERSION,
        "path": str(path),
        "sha256": _file_sha256(path),
        "size_bytes": path.stat().st_size,
        "report_id": report["report_id"],
        "planned_jump_count": report["planned_jump_count"],
        "verified_jump_count": report["verified_jump_count"],
        "accepted": report["accepted"],
    }


def _jump_plans(trajectory: Any) -> tuple[list[dict[str, Any]], list[str]]:
    events = trajectory.get("events", []) if isinstance(trajectory, dict) else []
    if not isinstance(events, list):
        return [], ["trajectory_events_invalid"]
    semantic = [
        event
        for event in events
        if isinstance(event, dict)
        and event.get("semantic_action") == DELIBERATE_JUMP_SEMANTIC
    ]
    plans: list[dict[str, Any]] = []
    errors: list[str] = []
    index = 0
    while index < len(semantic):
        press = semantic[index]
        if press.get("action") == "down" and press.get("semantic_phase") == "press":
            release = semantic[index + 1] if index + 1 < len(semantic) else None
            canonical = _matching_release(press, release)
            if not canonical:
                errors.append(f"jump_sequence_{len(plans) + 1}_not_canonical")
            plans.append(_plan(press, release if canonical else None, index, canonical))
            index += 2 if canonical else 1
            continue
        if press.get("action") == "tap":
            errors.append(f"jump_sequence_{len(plans) + 1}_legacy_tap")
            plans.append(_plan(press, None, index, False))
        else:
            errors.append(f"jump_sequence_{len(plans) + 1}_orphan_event")
            plans.append(_plan(press, None, index, False))
        index += 1
    return plans, errors


def _matching_release(press: dict[str, Any], release: Any) -> bool:
    return bool(
        isinstance(release, dict)
        and release.get("action") == "up"
        and release.get("semantic_phase") == "release"
        and release.get("jump_id") == press.get("jump_id")
        and release.get("route_index") == press.get("route_index")
        and release.get("hold_duration_sec") == press.get("hold_duration_sec")
        and _finite(release.get("t"))
        and _finite(press.get("t"))
        and math.isclose(
            float(release["t"]) - float(press["t"]),
            float(press.get("hold_duration_sec", float("nan"))),
            rel_tol=0.0,
            abs_tol=1e-9,
        )
    )


def _plan(
    press: dict[str, Any], release: dict[str, Any] | None, semantic_index: int, canonical: bool
) -> dict[str, Any]:
    ordinal = semantic_index // 2 + 1 if canonical else semantic_index + 1
    jump_id = press.get("jump_id")
    return {
        "jump_id": jump_id if isinstance(jump_id, str) and jump_id else f"legacy_jump_{ordinal}",
        "route_index": press.get("route_index") if type(press.get("route_index")) is int else None,
        "press_event": press,
        "release_event": release,
        "press_replay_index": semantic_index,
        "release_replay_index": semantic_index + 1 if canonical else None,
        "canonical": canonical,
    }


def _semantic_replay_rows(
    trajectory: Any, replay: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    events = trajectory.get("events", []) if isinstance(trajectory, dict) else []
    planned = [
        event
        for event in events
        if isinstance(event, dict)
        and event.get("semantic_action") == DELIBERATE_JUMP_SEMANTIC
    ]
    observed = [
        row
        for row in replay
        if isinstance(row.get("event"), dict)
        and row["event"].get("semantic_action") == DELIBERATE_JUMP_SEMANTIC
    ]
    errors: list[str] = []
    if len(observed) != len(planned):
        errors.append("replay_semantic_event_count_mismatch")
    if [row.get("event") for row in observed] != planned:
        errors.append("replay_semantic_events_do_not_match_trajectory")
    return observed, errors


def _jump_result(
    plan: dict[str, Any], replay: list[dict[str, Any]], positions: list[dict[str, float]]
) -> dict[str, Any]:
    press_row = _at(replay, plan["press_replay_index"])
    release_row = _at(replay, plan["release_replay_index"])
    press_t = _event_time(plan["press_event"])
    release_t = _event_time(plan["release_event"])
    actual_press = _numeric(press_row.get("actual_t") if press_row else None)
    actual_release = _numeric(release_row.get("actual_t") if release_row else None)
    dispatch = _dispatch_result(plan, press_row, release_row, press_t, release_t)
    align_t = actual_press if actual_press is not None else press_t
    baseline, peak, landing = _physical_result(positions, align_t)
    failures = []
    if not plan["canonical"]:
        failures.append("noncanonical_input_sequence")
    if not dispatch["passed"]:
        failures.append("dispatch_incomplete")
    if not dispatch["timing_passed"]:
        failures.append("dispatch_timing_mismatch")
    if not baseline["passed"]:
        failures.append("pre_ground_baseline_missing_or_unstable")
    if not peak["passed"]:
        failures.append("peak_delta_y_below_threshold")
    if not landing["passed"]:
        failures.append("landing_not_observed")
    physical = baseline["passed"] and peak["passed"] and landing["passed"]
    passed = plan["canonical"] and dispatch["passed"] and dispatch["timing_passed"] and physical
    return {
        "jump_id": plan["jump_id"],
        "route_index": plan["route_index"],
        "scheduled_press_t_sec": _rounded(press_t),
        "scheduled_release_t_sec": _rounded(release_t),
        "actual_press_t_sec": _rounded(actual_press),
        "actual_release_t_sec": _rounded(actual_release),
        "input_sequence": "down_hold_up" if plan["canonical"] else "noncanonical",
        "dispatch": dispatch,
        "pre_ground": baseline,
        "peak": peak,
        "landing": landing,
        "physical_effect_passed": physical,
        "failure_reasons": failures,
        "status": "pass" if passed else "fail",
    }


def _dispatch_result(
    plan: dict[str, Any], press: Any, release: Any, press_t: float | None, release_t: float | None
) -> dict[str, Any]:
    press_ok = _dispatch_row_matches(press, plan["press_event"], press_t)
    release_ok = _dispatch_row_matches(release, plan["release_event"], release_t)
    press_late = _lateness(press, press_t)
    release_late = _lateness(release, release_t)
    timing = bool(
        press_late is not None
        and release_late is not None
        and abs(press_late) <= MAX_DISPATCH_LATENESS_SEC
        and abs(release_late) <= MAX_DISPATCH_LATENESS_SEC
    )
    return {
        "press_status": press.get("execution_status") if isinstance(press, dict) else None,
        "release_status": release.get("execution_status") if isinstance(release, dict) else None,
        "press_lateness_sec": _rounded(press_late),
        "release_lateness_sec": _rounded(release_late),
        "input_dispatch_observed": press_ok
        and press_late is not None
        and abs(press_late) <= MAX_DISPATCH_LATENESS_SEC,
        "passed": plan["canonical"] and press_ok and release_ok,
        "timing_passed": plan["canonical"] and timing,
    }


def _dispatch_row_matches(row: Any, event: Any, scheduled_t: float | None) -> bool:
    return bool(
        isinstance(row, dict)
        and isinstance(event, dict)
        and row.get("event") == event
        and row.get("execution_status") == "executed"
        and _finite(row.get("scheduled_t"))
        and scheduled_t is not None
        and float(row["scheduled_t"]) == scheduled_t
        and _finite(row.get("actual_t"))
    )


def _physical_result(
    positions: list[dict[str, float]], press_t: float | None
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if press_t is None:
        return _empty_baseline(), _empty_peak(), _empty_landing()
    before = [row for row in positions if press_t - PRE_GROUND_WINDOW_SEC <= row["t"] <= press_t]
    ys = [row["y"] for row in before]
    baseline_y = statistics.median(ys) if ys else None
    span = max(ys) - min(ys) if ys else None
    nearest_gap = press_t - before[-1]["t"] if before else None
    baseline_pass = bool(
        len(before) >= MIN_GROUND_SAMPLES
        and span is not None
        and span <= GROUND_TOLERANCE_Y
        and nearest_gap is not None
        and nearest_gap <= MAX_POSITION_GAP_SEC
    )
    baseline = {
        "baseline_y": _rounded(baseline_y),
        "sample_count": len(before),
        "span_y": _rounded(span),
        "nearest_pre_sample_gap_sec": _rounded(nearest_gap),
        "passed": baseline_pass,
    }
    window = [row for row in positions if press_t <= row["t"] <= press_t + EFFECT_WINDOW_SEC]
    peak_row = max(window, key=lambda row: row["y"], default=None)
    delta = peak_row["y"] - baseline_y if peak_row and baseline_y is not None else None
    peak_pass = baseline_pass and delta is not None and delta >= MIN_PEAK_DELTA_Y
    peak = {
        "t_sec": _rounded(peak_row["t"] if peak_row else None),
        "y": _rounded(peak_row["y"] if peak_row else None),
        "delta_y": _rounded(delta),
        "passed": peak_pass,
    }
    landing = _landing_result(window, peak_row, baseline_y, peak_pass)
    return baseline, peak, landing


def _landing_result(
    window: list[dict[str, float]], peak: dict[str, float] | None, baseline: float | None, valid: bool
) -> dict[str, Any]:
    consecutive = 0
    landing_row = None
    for row in window:
        if peak is None or row["t"] <= peak["t"] or baseline is None:
            continue
        if abs(row["y"] - baseline) <= GROUND_TOLERANCE_Y:
            consecutive += 1
            if consecutive >= MIN_GROUND_SAMPLES:
                landing_row = row
                break
        else:
            consecutive = 0
    return {
        "t_sec": _rounded(landing_row["t"] if landing_row else None),
        "y": _rounded(landing_row["y"] if landing_row else None),
        "delta_from_baseline_y": _rounded(
            landing_row["y"] - baseline if landing_row and baseline is not None else None
        ),
        "consecutive_ground_sample_count": consecutive,
        "passed": bool(valid and landing_row),
    }


def _position_trace(
    positions: list[dict[str, float]], jumps: list[dict[str, Any]]
) -> dict[str, Any]:
    press_times = [item["actual_press_t_sec"] for item in jumps if item["actual_press_t_sec"] is not None]
    if not press_times:
        return _empty_trace(len(positions))
    start = min(press_times) - PRE_GROUND_WINDOW_SEC
    end = max(press_times) + EFFECT_WINDOW_SEC
    relevant = [row for row in positions if start <= row["t"] <= end]
    gaps = [right["t"] - left["t"] for left, right in zip(relevant, relevant[1:])]
    max_gap = max(gaps, default=None)
    cadence = bool(gaps and max_gap is not None and max_gap <= MAX_POSITION_GAP_SEC)
    coverage = bool(positions and positions[0]["t"] <= start and positions[-1]["t"] >= end)
    return {
        "sample_count": len(positions),
        "relevant_sample_count": len(relevant),
        "t_min_sec": _rounded(positions[0]["t"] if positions else None),
        "t_max_sec": _rounded(positions[-1]["t"] if positions else None),
        "relevant_t_start_sec": _rounded(start),
        "relevant_t_end_sec": _rounded(end),
        "max_gap_sec": _rounded(max_gap),
        "cadence_passed": cadence,
        "window_coverage_passed": coverage,
    }


def _position_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, float]], list[str]]:
    result: list[dict[str, float]] = []
    errors: list[str] = []
    previous_t: float | None = None
    for index, row in enumerate(rows):
        if not all(_finite(row.get(name)) for name in ("t_rel", "y")):
            errors.append(f"position_row_{index}_missing_finite_time_or_y")
            continue
        t_rel = float(row["t_rel"])
        if previous_t is not None and t_rel <= previous_t:
            errors.append(f"position_row_{index}_time_not_strictly_increasing")
        previous_t = t_rel
        result.append({"t": t_rel, "y": float(row["y"])})
    return result, errors


def _load_object(path: Path) -> tuple[dict[str, Any], list[str]]:
    if not path.is_file() or path.is_symlink():
        return {}, [f"source_missing:{path.name}"]
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, [f"source_invalid_json:{path.name}"]
    return (value, []) if isinstance(value, dict) else ({}, [f"source_not_object:{path.name}"])


def _load_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.is_file() or path.is_symlink():
        return [], [f"source_missing:{path.name}"]
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"{path.name}:row_{index}_invalid_json")
            continue
        if not isinstance(value, dict):
            errors.append(f"{path.name}:row_{index}_not_object")
            continue
        rows.append(value)
    if not rows:
        errors.append(f"source_empty:{path.name}")
    return rows, errors


def _artifact(path: Path, root: Path) -> dict[str, Any]:
    present = path.is_file() and not path.is_symlink()
    return {
        "path": path.relative_to(root).as_posix(),
        "present": present,
        "sha256": _file_sha256(path) if present else None,
        "size_bytes": path.stat().st_size if present else None,
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _numeric(value: Any) -> float | None:
    return float(value) if _finite(value) else None


def _event_time(event: Any) -> float | None:
    return _numeric(event.get("t")) if isinstance(event, dict) else None


def _lateness(row: Any, scheduled_t: float | None) -> float | None:
    actual = _numeric(row.get("actual_t")) if isinstance(row, dict) else None
    return actual - scheduled_t if actual is not None and scheduled_t is not None else None


def _rounded(value: float | None) -> float | None:
    return round(float(value), 6) if value is not None and math.isfinite(float(value)) else None


def _at(items: list[Any], index: int | None) -> Any:
    return items[index] if index is not None and 0 <= index < len(items) else None


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _empty_baseline() -> dict[str, Any]:
    return {
        "baseline_y": None,
        "sample_count": 0,
        "span_y": None,
        "nearest_pre_sample_gap_sec": None,
        "passed": False,
    }


def _empty_peak() -> dict[str, Any]:
    return {"t_sec": None, "y": None, "delta_y": None, "passed": False}


def _empty_landing() -> dict[str, Any]:
    return {
        "t_sec": None,
        "y": None,
        "delta_from_baseline_y": None,
        "consecutive_ground_sample_count": 0,
        "passed": False,
    }


def _empty_trace(sample_count: int) -> dict[str, Any]:
    return {
        "sample_count": sample_count,
        "relevant_sample_count": 0,
        "t_min_sec": None,
        "t_max_sec": None,
        "relevant_t_start_sec": None,
        "relevant_t_end_sec": None,
        "max_gap_sec": None,
        "cadence_passed": False,
        "window_coverage_passed": False,
    }
