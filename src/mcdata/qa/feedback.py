from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def feedback_route_report(
    run_dir: Path,
    trajectory: dict[str, Any],
    *,
    expected_duration_sec: float | None = None,
) -> dict[str, Any]:
    positions = _read_jsonl(run_dir / "positions.jsonl")
    navigation = _read_jsonl(run_dir / "navigation_log.jsonl")
    return check_feedback_route(
        positions,
        trajectory,
        navigation,
        expected_duration_sec=expected_duration_sec,
    )


def check_feedback_route(
    positions: list[dict[str, Any]],
    trajectory: dict[str, Any],
    navigation: list[dict[str, Any]],
    *,
    expected_duration_sec: float | None = None,
) -> dict[str, Any]:
    samples = [item for item in positions if float(item.get("t_rel", -1.0)) >= 0]
    settings = trajectory.get("navigation", {})
    min_y = float(settings.get("y_min", 63.0))
    max_y = float(settings.get("y_max", 66.0))
    move_yaw_limit = float(settings.get("move_yaw_limit_deg", 12.0))
    position_metrics = _position_metrics(
        samples,
        route=_route_centers(trajectory),
        min_y=min_y,
        max_y=max_y,
    )
    navigation_metrics = _navigation_metrics(navigation, trajectory)
    thresholds = _scoring_thresholds(
        expected_duration_sec,
        observed_span=position_metrics["observed_span"],
        navigation_span=navigation_metrics["navigation_span"],
        hard_deviation=float(settings.get("hard_deviation_blocks", 2.0)),
        max_route_segment=navigation_metrics["max_route_segment"],
    )
    passed = _feedback_passed(
        position_metrics,
        navigation_metrics,
        thresholds,
        move_yaw_limit=move_yaw_limit,
    )
    return _feedback_result(
        position_metrics,
        navigation_metrics,
        thresholds,
        passed=passed,
        min_y=min_y,
        max_y=max_y,
        move_yaw_limit=move_yaw_limit,
        expected_duration_sec=expected_duration_sec,
    )


def _position_metrics(
    samples: list[dict[str, Any]],
    *,
    route: set[tuple[float, float]],
    min_y: float,
    max_y: float,
) -> dict[str, Any]:
    deviations = [_nearest_route_point(item, route) for item in samples]
    y_values = [float(item["y"]) for item in samples]
    movement_steps = [_distance_xz(left, right) for left, right in zip(samples, samples[1:])]
    return {
        "count": len(samples),
        "max_deviation": max(deviations, default=None),
        "mean_deviation": sum(deviations) / len(deviations) if deviations else None,
        "observed_y_min": min(y_values) if y_values else None,
        "observed_y_max": max(y_values) if y_values else None,
        "y_out_of_range": sum(1 for value in y_values if not min_y <= value <= max_y),
        "missing_yaw": sum(1 for item in samples if "yaw" not in item),
        "total_movement": sum(movement_steps),
        "max_step": max(movement_steps, default=None),
        "unique_cells": len(
            {(math.floor(float(item["x"])), math.floor(float(item["z"]))) for item in samples}
        ),
        "observed_span": max(
            (float(item.get("t_rel", 0.0)) for item in samples),
            default=0.0,
        ),
        "minimum_10s_movement": _minimum_window_movement(samples, window_sec=10.0),
    }


def _navigation_metrics(
    navigation: list[dict[str, Any]],
    trajectory: dict[str, Any],
) -> dict[str, Any]:
    controls = [item for item in navigation if item.get("event") == "control"]
    moving_controls = [item for item in controls if item.get("moving") is True]
    moving_yaw_errors = [abs(float(item["yaw_error"])) for item in moving_controls]
    failures = [item for item in navigation if item.get("event") == "failure"]
    waypoints = [item for item in navigation if item.get("event") == "waypoint"]
    recoveries = [item for item in navigation if item.get("event") == "recovery"]
    nav_times = [float(item["t_rel"]) for item in navigation if "t_rel" in item]
    progress, progress_ordered = _ordered_route_progress(waypoints, trajectory)
    stop_count = sum(1 for item in navigation if item.get("event") == "stop")
    return {
        "control_count": len(controls),
        "moving_yaw_count": len(moving_yaw_errors),
        "max_moving_yaw": max(moving_yaw_errors, default=None),
        "mean_moving_yaw": (
            sum(moving_yaw_errors) / len(moving_yaw_errors) if moving_yaw_errors else None
        ),
        "skipped_yaw_count": len(controls) - len(moving_controls),
        "failure_count": len(failures),
        "waypoint_count": len(waypoints),
        "recovery_count": len(recoveries),
        "navigation_span": max(nav_times, default=0.0),
        "route_progress": progress,
        "progress_ordered": progress_ordered,
        "max_route_segment": _max_route_segment(trajectory),
        "terminal_stop": stop_count == 1
        and bool(navigation)
        and navigation[-1].get("event") == "stop",
        "stop_count": stop_count,
    }


def _scoring_thresholds(
    expected_duration_sec: float | None,
    *,
    observed_span: float,
    navigation_span: float,
    hard_deviation: float,
    max_route_segment: int,
) -> dict[str, Any]:
    scored_span = expected_duration_sec or max(navigation_span, observed_span)
    duration_ratio = (
        navigation_span / expected_duration_sec
        if expected_duration_sec is not None and expected_duration_sec > 0
        else None
    )
    minimum_distance = max(2.0, (expected_duration_sec or scored_span) * 1.0)
    return {
        "route_threshold": hard_deviation + 0.75,
        "scored_span": scored_span,
        "duration_ratio": duration_ratio,
        "minimum_samples": max(2, math.floor((expected_duration_sec or 0.0) * 2.0)),
        "minimum_distance": minimum_distance,
        "minimum_cells": max(3, math.floor((expected_duration_sec or scored_span) / 10.0)),
        "minimum_route_progress": max(1.0, minimum_distance - max_route_segment),
    }


def _feedback_passed(
    position: dict[str, Any],
    navigation: dict[str, Any],
    thresholds: dict[str, Any],
    *,
    move_yaw_limit: float,
) -> bool:
    duration_ratio = thresholds["duration_ratio"]
    position_duration_ok = (
        duration_ratio is None or position["observed_span"] / thresholds["scored_span"] >= 0.95
    )
    window_movement = position["minimum_10s_movement"]
    return bool(
        position["count"] >= thresholds["minimum_samples"]
        and navigation["control_count"]
        and navigation["failure_count"] == 0
        and navigation["terminal_stop"]
        and navigation["progress_ordered"]
        and navigation["route_progress"] >= thresholds["minimum_route_progress"]
        and position["max_deviation"] is not None
        and position["max_deviation"] <= thresholds["route_threshold"]
        and position["y_out_of_range"] == 0
        and position["missing_yaw"] == 0
        and position["total_movement"] >= thresholds["minimum_distance"]
        and position["unique_cells"] >= thresholds["minimum_cells"]
        and position["max_step"] is not None
        and position["max_step"] <= 3.0
        and navigation["max_moving_yaw"] is not None
        and navigation["max_moving_yaw"] <= move_yaw_limit + 0.01
        and (duration_ratio is None or duration_ratio >= 0.95)
        and position_duration_ok
        and (window_movement is None or window_movement >= 2.0)
    )


def _feedback_result(
    position: dict[str, Any],
    navigation: dict[str, Any],
    thresholds: dict[str, Any],
    *,
    passed: bool,
    min_y: float,
    max_y: float,
    move_yaw_limit: float,
    expected_duration_sec: float | None,
) -> dict[str, Any]:
    scored_span = thresholds["scored_span"]
    movement_rate = position["total_movement"] / scored_span if scored_span > 0 else 0.0
    return {
        "mode": "online_position_yaw_feedback",
        "passed": passed,
        "threshold_blocks": thresholds["route_threshold"],
        "yaw_threshold_degrees": move_yaw_limit,
        "y_min": min_y,
        "y_max": max_y,
        "count": position["count"],
        "minimum_sample_count": thresholds["minimum_samples"],
        "max_deviation_blocks": position["max_deviation"],
        "mean_deviation_blocks": position["mean_deviation"],
        "max_yaw_error_degrees": navigation["max_moving_yaw"],
        "mean_yaw_error_degrees": navigation["mean_moving_yaw"],
        "yaw_sample_count": navigation["moving_yaw_count"],
        "missing_yaw_count": position["missing_yaw"],
        "skipped_yaw_count": navigation["skipped_yaw_count"],
        "observed_y_min": position["observed_y_min"],
        "observed_y_max": position["observed_y_max"],
        "y_out_of_range_count": position["y_out_of_range"],
        "movement_distance_blocks": position["total_movement"],
        "minimum_movement_distance_blocks": thresholds["minimum_distance"],
        "movement_rate_blocks_per_sec": movement_rate,
        "max_position_step_blocks": position["max_step"],
        "unique_occupied_cells": position["unique_cells"],
        "minimum_unique_cells": thresholds["minimum_cells"],
        "navigation_control_count": navigation["control_count"],
        "waypoints_reached": navigation["waypoint_count"],
        "recovery_count": navigation["recovery_count"],
        "failure_count": navigation["failure_count"],
        "navigation_span_sec": navigation["navigation_span"],
        "expected_duration_sec": expected_duration_sec,
        "navigation_duration_ratio": thresholds["duration_ratio"],
        "position_duration_ratio": (
            position["observed_span"] / scored_span if scored_span > 0 else None
        ),
        "minimum_10s_movement_blocks": position["minimum_10s_movement"],
        "ordered_route_progress_blocks": navigation["route_progress"],
        "minimum_route_progress_blocks": thresholds["minimum_route_progress"],
        "route_progress_ordered": navigation["progress_ordered"],
        "terminal_stop": navigation["terminal_stop"],
        "terminal_stop_count": navigation["stop_count"],
        "samples": [],
    }


def _ordered_route_progress(
    waypoints: list[dict[str, Any]],
    trajectory: dict[str, Any],
) -> tuple[int, bool]:
    route_distance = max(0, len(trajectory.get("route", [])) - 1)
    previous = -1
    for item in waypoints:
        cycle = item.get("cycle")
        route_index = item.get("route_index")
        if not isinstance(cycle, int) or not isinstance(route_index, int):
            return 0, False
        if cycle < 0 or not 0 < route_index <= route_distance:
            return 0, False
        progress = cycle * route_distance + route_index
        if progress <= previous:
            return 0, False
        previous = progress
    return max(0, previous), bool(waypoints)


def _max_route_segment(trajectory: dict[str, Any]) -> int:
    route = [(int(item["x"]), int(item["z"])) for item in trajectory.get("route", [])]
    directions = [(right[0] - left[0], right[1] - left[1]) for left, right in zip(route, route[1:])]
    if not directions:
        return 0
    maximum = run = 1
    for previous, current in zip(directions, directions[1:]):
        run = run + 1 if current == previous else 1
        maximum = max(maximum, run)
    return maximum


def _minimum_window_movement(
    samples: list[dict[str, Any]],
    *,
    window_sec: float,
) -> float | None:
    if len(samples) < 2 or float(samples[-1]["t_rel"]) - float(samples[0]["t_rel"]) < window_sec:
        return None
    prefix = [0.0]
    for left, right in zip(samples, samples[1:]):
        prefix.append(prefix[-1] + _distance_xz(left, right))
    minimum: float | None = None
    right_index = 0
    for left_index, left in enumerate(samples):
        right_index = max(right_index, left_index)
        while right_index < len(samples) and (
            float(samples[right_index]["t_rel"]) - float(left["t_rel"]) < window_sec
        ):
            right_index += 1
        if right_index >= len(samples):
            break
        distance = prefix[right_index] - prefix[left_index]
        minimum = distance if minimum is None else min(minimum, distance)
    return minimum


def _route_centers(trajectory: dict[str, Any]) -> set[tuple[float, float]]:
    offset = float(trajectory.get("navigation", {}).get("waypoint_center_offset", 0.5))
    return {
        (float(item["x"]) + offset, float(item["z"]) + offset)
        for item in trajectory.get("route", [])
    }


def _nearest_route_point(
    position: dict[str, Any],
    route: set[tuple[float, float]],
) -> float:
    if not route:
        return math.inf
    x = float(position["x"])
    z = float(position["z"])
    return min(math.hypot(x - route_x, z - route_z) for route_x, route_z in route)


def _distance_xz(left: dict[str, Any], right: dict[str, Any]) -> float:
    return math.hypot(
        float(left["x"]) - float(right["x"]),
        float(left["z"]) - float(right["z"]),
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
