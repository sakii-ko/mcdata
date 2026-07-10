from __future__ import annotations

import math
import random
from typing import Any

from mcdata.actions.pathing import (
    Bounds,
    Point,
    astar,
    expand_blocked,
    inside_bounds,
    manhattan_distance,
    point,
    reduce_cardinal_turns,
    walk_blocked,
    walk_bounds,
)

MIN_OBSTACLE_CLEARANCE = 2


def build_feedback_roam(spec: dict[str, Any]) -> dict[str, Any]:
    """Plan a deterministic, closed route for an online feedback controller."""
    seed = _required_seed(spec)
    rng = random.Random(seed)
    start = point(spec.get("start", [0, -14]))
    bounds = walk_bounds(spec)
    clearance = int(spec.get("obstacle_clearance", MIN_OBSTACLE_CLEARANCE))
    if clearance < MIN_OBSTACLE_CLEARANCE:
        raise RuntimeError(
            f"feedback_roam obstacle_clearance must be at least {MIN_OBSTACLE_CLEARANCE}"
        )
    blocked = expand_blocked(walk_blocked(spec), bounds=bounds, clearance=clearance)
    if not inside_bounds(start, bounds) or start in blocked:
        raise RuntimeError(f"feedback_roam start {start} is not walkable inside bounds {bounds}")

    planning = _planning_parameters(spec)
    route, sampled_goals = _sample_closed_route(
        rng,
        start=start,
        bounds=bounds,
        blocked=blocked,
        target_duration_sec=planning["target_duration_sec"],
        seconds_per_block=planning["seconds_per_block"],
        min_goal_dist=planning["min_goal_dist"],
        min_goals=planning["min_goals"],
        max_goals=planning["max_goals"],
        goal_attempts=planning["goal_attempts"],
        recent_goal_window=planning["recent_goal_window"],
    )
    distance = len(route) - 1
    duration = _ceil_millis(distance * planning["seconds_per_block"])
    goals = [*sampled_goals, start]
    return {
        "type": "feedback_roam",
        "seed": seed,
        "duration_sec": duration,
        "goals": [_point_record(item) for item in goals],
        "route": [_point_record(item) for item in route],
        "planning": {
            "target_duration_sec": planning["target_duration_sec"],
            "route_distance_blocks": distance,
            "obstacle_clearance": clearance,
            "min_goal_dist": planning["min_goal_dist"],
            "sampled_goal_count": len(sampled_goals),
            "bounds": list(bounds),
            "closed": True,
        },
        "navigation": _navigation_parameters(spec, planning["seconds_per_block"]),
        "events": [],
    }


def _sample_closed_route(
    rng: random.Random,
    *,
    start: Point,
    bounds: Bounds,
    blocked: set[Point],
    target_duration_sec: float,
    seconds_per_block: float,
    min_goal_dist: int,
    min_goals: int,
    max_goals: int,
    goal_attempts: int,
    recent_goal_window: int,
) -> tuple[list[Point], list[Point]]:
    walkable = _walkable_points(bounds, blocked)
    route = [start]
    goals: list[Point] = []
    cursor = start
    for goal_number in range(1, max_goals + 1):
        recent_goals = goals[-recent_goal_window:] if recent_goal_window else []
        goal, segment = _sample_goal_segment(
            rng,
            walkable=walkable,
            cursor=cursor,
            start=start,
            recent_goals=recent_goals,
            min_goal_dist=min_goal_dist,
            goal_attempts=goal_attempts,
            bounds=bounds,
            blocked=blocked,
            goal_number=goal_number,
        )
        goals.append(goal)
        route.extend(segment[1:])
        cursor = goal
        return_segment = reduce_cardinal_turns(
            astar(cursor, start, bounds=bounds, blocked=blocked),
            bounds=bounds,
            blocked=blocked,
        )
        closed_distance = len(route) - 1 + len(return_segment) - 1
        if len(goals) >= min_goals and closed_distance * seconds_per_block >= target_duration_sec:
            route.extend(return_segment[1:])
            return route, goals
    raise RuntimeError(
        "feedback_roam could not reach target duration "
        f"{target_duration_sec}s within max_goals={max_goals}"
    )


def _sample_goal_segment(
    rng: random.Random,
    *,
    walkable: list[Point],
    cursor: Point,
    start: Point,
    recent_goals: list[Point],
    min_goal_dist: int,
    goal_attempts: int,
    bounds: Bounds,
    blocked: set[Point],
    goal_number: int,
) -> tuple[Point, list[Point]]:
    for _ in range(goal_attempts):
        candidate = rng.choice(walkable)
        if candidate == start or candidate in recent_goals:
            continue
        if manhattan_distance(cursor, candidate) < min_goal_dist:
            continue
        try:
            route = astar(cursor, candidate, bounds=bounds, blocked=blocked)
            return candidate, reduce_cardinal_turns(route, bounds=bounds, blocked=blocked)
        except RuntimeError:
            continue
    raise RuntimeError(
        f"feedback_roam could not sample reachable goal {goal_number} "
        f"at least {min_goal_dist} blocks from {cursor} after {goal_attempts} attempts"
    )


def _planning_parameters(spec: dict[str, Any]) -> dict[str, int | float]:
    values: dict[str, int | float] = {
        "target_duration_sec": float(spec.get("target_duration_sec", 604.0)),
        "seconds_per_block": float(spec.get("seconds_per_block", 0.231630565)),
        "min_goal_dist": int(spec.get("min_goal_dist", 6)),
        "min_goals": int(spec.get("min_goals", 8)),
        "max_goals": int(spec.get("max_goals", 512)),
        "goal_attempts": int(spec.get("goal_attempts", 100)),
        "recent_goal_window": int(spec.get("recent_goal_window", 16)),
    }
    for key in ("target_duration_sec", "seconds_per_block"):
        if values[key] <= 0:
            raise RuntimeError(f"feedback_roam {key} must be positive")
    for key in ("min_goal_dist", "min_goals", "max_goals", "goal_attempts"):
        if values[key] < 1:
            raise RuntimeError(f"feedback_roam {key} must be at least 1")
    if values["recent_goal_window"] < 0:
        raise RuntimeError("feedback_roam recent_goal_window must not be negative")
    if values["min_goals"] > values["max_goals"]:
        raise RuntimeError("feedback_roam min_goals must not exceed max_goals")
    return values


def _navigation_parameters(spec: dict[str, Any], seconds_per_block: float) -> dict[str, Any]:
    navigation = {
        "control_interval_sec": float(spec.get("control_interval_sec", 0.1)),
        "waypoint_tolerance_blocks": float(spec.get("waypoint_tolerance_blocks", 0.35)),
        "waypoint_center_offset": float(spec.get("waypoint_center_offset", 0.5)),
        "yaw_tolerance_deg": float(spec.get("yaw_tolerance_deg", 2.0)),
        "move_yaw_limit_deg": float(spec.get("move_yaw_limit_deg", 12.0)),
        "soft_deviation_blocks": float(spec.get("soft_deviation_blocks", 0.75)),
        "hard_deviation_blocks": float(spec.get("hard_deviation_blocks", 2.0)),
        "position_stale_sec": float(spec.get("position_stale_sec", 0.75)),
        "stuck_window_sec": float(spec.get("stuck_window_sec", 1.0)),
        "stuck_distance_blocks": float(spec.get("stuck_distance_blocks", 0.15)),
        "max_recovery_attempts": int(spec.get("max_recovery_attempts", 3)),
        "recovery_hold_sec": float(spec.get("recovery_hold_sec", 0.2)),
        "turn_px_per_degree": float(spec.get("turn_px_per_degree", 6.6667)),
        "turn_gain": float(spec.get("turn_gain", 1.0)),
        "turn_confirmation_samples": int(spec.get("turn_confirmation_samples", 1)),
        "turn_response_timeout_sec": float(spec.get("turn_response_timeout_sec", 1.0)),
        "turn_min_improvement_deg": float(spec.get("turn_min_improvement_deg", 2.0)),
        "max_turn_px": int(spec.get("max_turn_px", 360)),
        "progress_timeout_sec": float(spec.get("progress_timeout_sec", 3.0)),
        "progress_min_distance_blocks": float(spec.get("progress_min_distance_blocks", 0.25)),
        "min_moving_control_ratio": float(spec.get("min_moving_control_ratio", 0.5)),
        "max_recovery_count": int(spec.get("max_recovery_count", 3)),
        "long_run_gate_duration_sec": float(spec.get("long_run_gate_duration_sec", 600.0)),
        "min_long_run_progress_blocks": float(spec.get("min_long_run_progress_blocks", 1200.0)),
        "min_10s_movement_blocks": float(spec.get("min_10s_movement_blocks", 10.0)),
        "y_min": float(spec.get("y_min", 63.0)),
        "y_max": float(spec.get("y_max", 66.0)),
        "seconds_per_block": seconds_per_block,
    }
    _validate_navigation(navigation)
    return navigation


def _validate_navigation(navigation: dict[str, Any]) -> None:
    positive = (
        "control_interval_sec",
        "waypoint_tolerance_blocks",
        "yaw_tolerance_deg",
        "move_yaw_limit_deg",
        "soft_deviation_blocks",
        "hard_deviation_blocks",
        "position_stale_sec",
        "stuck_window_sec",
        "recovery_hold_sec",
        "turn_px_per_degree",
        "turn_gain",
        "turn_response_timeout_sec",
        "turn_min_improvement_deg",
        "max_turn_px",
        "progress_timeout_sec",
        "progress_min_distance_blocks",
        "long_run_gate_duration_sec",
        "min_long_run_progress_blocks",
        "min_10s_movement_blocks",
        "seconds_per_block",
    )
    for key in positive:
        if navigation[key] <= 0:
            raise RuntimeError(f"feedback_roam {key} must be positive")
    if not 0 <= navigation["waypoint_center_offset"] < 1:
        raise RuntimeError("feedback_roam waypoint_center_offset must be in [0, 1)")
    if navigation["stuck_distance_blocks"] < 0:
        raise RuntimeError("feedback_roam stuck_distance_blocks must not be negative")
    if navigation["max_recovery_attempts"] < 1:
        raise RuntimeError("feedback_roam max_recovery_attempts must be at least 1")
    if navigation["max_recovery_count"] < 0:
        raise RuntimeError("feedback_roam max_recovery_count must not be negative")
    if navigation["hard_deviation_blocks"] <= navigation["soft_deviation_blocks"]:
        raise RuntimeError("feedback_roam hard_deviation_blocks must exceed soft_deviation_blocks")
    if navigation["move_yaw_limit_deg"] < navigation["yaw_tolerance_deg"]:
        raise RuntimeError("feedback_roam move_yaw_limit_deg must cover yaw_tolerance_deg")
    if navigation["turn_gain"] > 1:
        raise RuntimeError("feedback_roam turn_gain must not exceed 1")
    if not 0 < navigation["min_moving_control_ratio"] <= 1:
        raise RuntimeError("feedback_roam min_moving_control_ratio must be in (0, 1]")
    if navigation["turn_confirmation_samples"] < 0:
        raise RuntimeError("feedback_roam turn_confirmation_samples must not be negative")
    if navigation["position_stale_sec"] < navigation["control_interval_sec"]:
        raise RuntimeError("feedback_roam position_stale_sec must cover a control interval")
    if navigation["stuck_window_sec"] < navigation["control_interval_sec"]:
        raise RuntimeError("feedback_roam stuck_window_sec must cover a control interval")
    if navigation["y_min"] > navigation["y_max"]:
        raise RuntimeError("feedback_roam y_min must not exceed y_max")


def _required_seed(spec: dict[str, Any]) -> int:
    if "seed" not in spec:
        raise RuntimeError("feedback_roam strategy requires an explicit seed")
    return int(spec["seed"])


def _walkable_points(bounds: Bounds, blocked: set[Point]) -> list[Point]:
    min_x, max_x, min_z, max_z = bounds
    return [
        (x, z)
        for x in range(min_x, max_x + 1)
        for z in range(min_z, max_z + 1)
        if (x, z) not in blocked
    ]


def _ceil_millis(value: float) -> float:
    return math.ceil(value * 1000) / 1000


def _point_record(value: Point) -> dict[str, int]:
    return {"x": value[0], "z": value[1]}
