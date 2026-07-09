from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Callable

from mcdata.config import load_yaml
from mcdata.scene_model import load_scene, walk_obstacles

StrategyBuilder = Callable[[str, dict[str, Any]], dict[str, Any]]


def generate_strategy(config_dir: Path, name: str, out: Path) -> dict[str, Any]:
    config = load_yaml(config_dir / "actions.yml")
    strategies = config.get("strategies", {})
    if name not in strategies:
        known = ", ".join(sorted(strategies))
        raise RuntimeError(f"Unknown strategy '{name}'. Known strategies: {known}")
    scene_obstacles = walk_obstacles(load_scene(config_dir))
    trajectory = build_trajectory(name, dict(strategies[name]), scene_obstacles=scene_obstacles)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(trajectory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return trajectory


def build_trajectory(
    name: str,
    spec: dict[str, Any],
    *,
    scene_obstacles: set[tuple[int, int]] | None = None,
) -> dict[str, Any]:
    spec = dict(spec)
    if scene_obstacles is not None:
        spec["_scene_obstacles"] = sorted(scene_obstacles)
    kind = spec.get("type")
    builder = STRATEGY_BUILDERS.get(str(kind))
    if builder is None:
        raise RuntimeError(f"Unsupported strategy type for {name}: {kind}")
    trajectory = builder(name, spec)
    if "events" not in trajectory:
        return trajectory
    result = dict(trajectory)
    result["events"] = sorted(result.get("events", []), key=lambda event: float(event.get("t", 0)))
    return result


def _from_spec(builder: Callable[[dict[str, Any]], dict[str, Any]]) -> StrategyBuilder:
    def wrapped(_name: str, spec: dict[str, Any]) -> dict[str, Any]:
        return builder(spec)

    return wrapped


def _external(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "type": "external", "spec": spec, "events": []}


def _scripted(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "scripted",
        "duration_sec": spec.get("duration_sec"),
        "events": spec.get("steps", []),
    }


def _random(spec: dict[str, Any]) -> dict[str, Any]:
    rng = random.Random(int(spec.get("seed", 1)))
    duration = float(spec.get("duration_sec", 300))
    min_hold = float(spec.get("min_hold_sec", 1.2))
    max_hold = float(spec.get("max_hold_sec", 4.0))
    turn_px = int(spec.get("turn_px", 260))
    vertical_px = int(spec.get("vertical_px", 40))
    jump_chance = float(spec.get("jump_chance", 0.2))
    sprint_chance = float(spec.get("sprint_chance", 0.25))
    move_sets = list(
        spec.get(
            "move_sets",
            [
                ["w"],
                ["w", "a"],
                ["w", "d"],
                ["a"],
                ["d"],
                ["s"],
            ],
        )
    )
    events: list[dict[str, Any]] = []
    t = 0.0
    while t < duration:
        hold = min(rng.uniform(min_hold, max_hold), max(0.0, duration - t))
        keys = [str(key) for key in rng.choice(move_sets)]
        if rng.random() < sprint_chance and "w" in keys:
            keys.append("left_shift")
        for key in keys:
            events.append({"t": round(t, 3), "key": key, "action": "down"})
        if rng.random() < jump_chance:
            events.append({"t": round(t + min(0.35, hold / 2), 3), "key": "space", "action": "tap"})
        events.append(
            {
                "t": round(t + 0.1, 3),
                "mouse_dx": rng.randint(-turn_px, turn_px),
                "mouse_dy": rng.randint(-vertical_px, vertical_px),
                "duration": round(max(0.2, hold - 0.2), 3),
            }
        )
        for key in reversed(keys):
            events.append({"t": round(t + hold, 3), "key": key, "action": "up"})
        t += hold + rng.uniform(0.05, 0.25)
    return {"type": "random", "duration_sec": duration, "events": events}


def _astar_walk(spec: dict[str, Any]) -> dict[str, Any]:
    start = _point(spec.get("start", [0, -14]))
    goals = [_point(point) for point in spec.get("goals", [[10, -8], [10, 10], [-10, 10], [-10, -8], [0, -14]])]
    blocked = {_point(point) for point in spec.get("blocked", [])}
    blocked.update(_point(point) for point in spec.get("_scene_obstacles", []) or [])
    for rect in spec.get("blocked_rects", []) or []:
        blocked.update(_points_in_rect(rect))
    bounds = spec.get("bounds", [-14, 14, -14, 14])
    min_x, max_x, min_z, max_z = (int(bounds[0]), int(bounds[1]), int(bounds[2]), int(bounds[3]))
    turn_px_per_degree = float(spec.get("turn_px_per_degree", 6.0))
    seconds_per_block = float(spec.get("seconds_per_block", 0.32))
    walk_startup_comp_sec = float(spec.get("walk_startup_comp_sec", 0.0))
    look_pitch_px = int(spec.get("look_pitch_px", 0))
    initial_pause_sec = float(spec.get("initial_pause_sec", 1.0))
    scan_pause_sec = float(spec.get("scan_pause_sec", 0.25))
    waypoint_actions = _waypoint_actions(spec)

    route = [start]
    cursor = start
    goal_indices: dict[tuple[int, int], list[int]] = {}
    for goal in goals:
        segment = _astar(cursor, goal, bounds=(min_x, max_x, min_z, max_z), blocked=blocked)
        route.extend(segment[1:])
        goal_indices.setdefault(goal, []).append(len(route) - 1)
        cursor = goal

    events: list[dict[str, Any]] = []
    t = initial_pause_sec
    heading = 0
    if look_pitch_px:
        events.append({"t": round(t, 3), "mouse_dx": 0, "mouse_dy": look_pitch_px, "duration": 0.4})
        t += 0.4 + scan_pause_sec

    route_index = 0
    waypoint_stop_indices = _waypoint_stop_indices(goal_indices, waypoint_actions)
    for desired, distance in _route_segments_with_waypoints(route, waypoint_stop_indices):
        turn = _shortest_turn(heading, desired)
        if abs(turn) > 0.001:
            for turn_step in _turn_steps(turn):
                events.append(
                    {
                        "t": round(t, 3),
                        "mouse_dx": round(turn_step * turn_px_per_degree),
                        "mouse_dy": 0,
                        "duration": 0.35,
                    }
                )
                t += 0.35 + scan_pause_sec
        heading = desired
        t = _hold_key(events, t, "w", distance * seconds_per_block + walk_startup_comp_sec)
        route_index += distance
        t = _apply_waypoint_actions(
            events,
            t,
            route[route_index],
            route_index=route_index,
            goal_indices=goal_indices,
            waypoint_actions=waypoint_actions,
            scan_pause_sec=scan_pause_sec,
        )

    return {
        "type": "astar_walk",
        "duration_sec": round(t, 3),
        "route": [{"x": x, "z": z} for x, z in route],
        "events": events,
    }


def _look_scan(spec: dict[str, Any]) -> dict[str, Any]:
    duration = float(spec.get("duration_sec", 90))
    sweep_px = int(spec.get("sweep_px", 720))
    sweep_sec = float(spec.get("sweep_sec", 7.5))
    vertical_px = int(spec.get("vertical_px", 50))
    events: list[dict[str, Any]] = []
    t = 0.0
    direction = 1
    while t < duration:
        span = min(sweep_sec, duration - t)
        events.append(
            {
                "t": round(t, 3),
                "mouse_dx": direction * sweep_px,
                "mouse_dy": vertical_px if direction > 0 else -vertical_px,
                "duration": round(span, 3),
            }
        )
        direction *= -1
        t += span + float(spec.get("pause_sec", 0.6))
    return {"type": "look_scan", "duration_sec": duration, "events": events}


def _scene_probe(spec: dict[str, Any]) -> dict[str, Any]:
    duration = float(spec.get("duration_sec", 90))
    scan_px = int(spec.get("scan_px", 620))
    pitch_px = int(spec.get("pitch_px", 95))
    sweep_sec = float(spec.get("sweep_sec", 5.0))
    move_sec = float(spec.get("move_sec", 2.0))
    pause_sec = float(spec.get("pause_sec", 0.4))
    initial_pause_sec = float(spec.get("initial_pause_sec", 1.0))
    events: list[dict[str, Any]] = []
    t = initial_pause_sec

    while t < duration:
        span = min(sweep_sec, duration - t)
        events.append({"t": round(t, 3), "mouse_dx": scan_px, "mouse_dy": 0, "duration": round(span, 3)})
        t += span + pause_sec
        if t >= duration:
            break
        span = min(sweep_sec, duration - t)
        events.append({"t": round(t, 3), "mouse_dx": -scan_px, "mouse_dy": 0, "duration": round(span, 3)})
        t += span + pause_sec
        if t >= duration:
            break
        events.append({"t": round(t, 3), "mouse_dx": 0, "mouse_dy": -pitch_px, "duration": 1.0})
        t += 1.0 + pause_sec
        events.append({"t": round(t, 3), "mouse_dx": 0, "mouse_dy": pitch_px, "duration": 1.0})
        t += 1.0 + pause_sec
        if t >= duration:
            break
        for key in ("w", "s", "d", "a"):
            if t >= duration:
                break
            span = min(move_sec, duration - t)
            t = _hold_key(events, t, key, span) + pause_sec
    return {"type": "scene_probe", "duration_sec": duration, "events": events}


def _grid_patrol(spec: dict[str, Any]) -> dict[str, Any]:
    loops = int(spec.get("loops", 4))
    forward_sec = float(spec.get("forward_sec", 7.0))
    strafe_sec = float(spec.get("strafe_sec", 3.0))
    turn_px = int(spec.get("turn_px", 430))
    turn_sec = float(spec.get("turn_sec", 1.2))
    events: list[dict[str, Any]] = []
    t = 0.0
    for _ in range(loops):
        t = _hold_key(events, t, "w", forward_sec)
        events.append({"t": round(t, 3), "mouse_dx": turn_px, "mouse_dy": 0, "duration": turn_sec})
        t += turn_sec
        t = _hold_key(events, t, "d", strafe_sec)
        t = _hold_key(events, t, "w", forward_sec)
        events.append({"t": round(t, 3), "mouse_dx": turn_px, "mouse_dy": 0, "duration": turn_sec})
        t += turn_sec
        t = _hold_key(events, t, "a", strafe_sec)
    return {"type": "grid_patrol", "duration_sec": t, "events": events}


def _hold_key(events: list[dict[str, Any]], t: float, key: str, duration: float) -> float:
    events.append({"t": round(t, 3), "key": key, "action": "down"})
    events.append({"t": round(t + duration, 3), "key": key, "action": "up"})
    return t + duration


def _waypoint_actions(spec: dict[str, Any]) -> dict[tuple[int, int], list[dict[str, Any]]]:
    result: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for item in spec.get("waypoint_actions", []) or []:
        at = _point(item.get("at"))
        result.setdefault(at, []).append(dict(item))
    return result


def _apply_waypoint_actions(
    events: list[dict[str, Any]],
    t: float,
    point: tuple[int, int],
    *,
    route_index: int,
    goal_indices: dict[tuple[int, int], list[int]],
    waypoint_actions: dict[tuple[int, int], list[dict[str, Any]]],
    scan_pause_sec: float,
) -> float:
    if route_index not in goal_indices.get(point, []):
        return t
    for action in waypoint_actions.get(point, []):
        pause_sec = float(action.get("pause_sec", 0))
        if pause_sec > 0:
            events.append({"t": round(t, 3), "duration": round(pause_sec, 3), "pause": True})
            t += pause_sec
        look_dy_px = int(action.get("look_dy_px", 0))
        if look_dy_px:
            events.append({"t": round(t, 3), "mouse_dx": 0, "mouse_dy": look_dy_px, "duration": 0.35})
            t += 0.35 + scan_pause_sec
            events.append({"t": round(t, 3), "mouse_dx": 0, "mouse_dy": -look_dy_px, "duration": 0.35})
            t += 0.35 + scan_pause_sec
    return t


def _waypoint_stop_indices(
    goal_indices: dict[tuple[int, int], list[int]],
    waypoint_actions: dict[tuple[int, int], list[dict[str, Any]]],
) -> set[int]:
    return {
        route_index
        for point in waypoint_actions
        for route_index in goal_indices.get(point, [])
    }


def _point(value: Any) -> tuple[int, int]:
    return (int(value[0]), int(value[1]))


def _points_in_rect(value: Any) -> set[tuple[int, int]]:
    min_x, min_z, max_x, max_z = (int(value[0]), int(value[1]), int(value[2]), int(value[3]))
    return {(x, z) for x in range(min_x, max_x + 1) for z in range(min_z, max_z + 1)}


def _astar(
    start: tuple[int, int],
    goal: tuple[int, int],
    *,
    bounds: tuple[int, int, int, int],
    blocked: set[tuple[int, int]],
) -> list[tuple[int, int]]:
    import heapq

    min_x, max_x, min_z, max_z = bounds
    queue: list[tuple[int, tuple[int, int]]] = [(0, start)]
    came_from: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    cost_so_far: dict[tuple[int, int], int] = {start: 0}
    while queue:
        _, current = heapq.heappop(queue)
        if current == goal:
            break
        x, z = current
        for nxt in ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)):
            nx, nz = nxt
            if nx < min_x or nx > max_x or nz < min_z or nz > max_z or nxt in blocked:
                continue
            new_cost = cost_so_far[current] + 1
            if nxt not in cost_so_far or new_cost < cost_so_far[nxt]:
                cost_so_far[nxt] = new_cost
                priority = new_cost + abs(goal[0] - nx) + abs(goal[1] - nz)
                heapq.heappush(queue, (priority, nxt))
                came_from[nxt] = current
    if goal not in came_from:
        raise RuntimeError(f"A* could not route from {start} to {goal}")
    path = [goal]
    cursor = goal
    while came_from[cursor] is not None:
        cursor = came_from[cursor]
        path.append(cursor)
    return list(reversed(path))


def _heading_degrees(current: tuple[int, int], nxt: tuple[int, int]) -> int:
    dx = nxt[0] - current[0]
    dz = nxt[1] - current[1]
    if dz > 0:
        return 0
    if dx < 0:
        return 90
    if dz < 0:
        return 180
    if dx > 0:
        return 270
    return 0


def _route_segments(route: list[tuple[int, int]]) -> list[tuple[int, int]]:
    return _route_segments_with_waypoints(route, set())


def _route_segments_with_waypoints(
    route: list[tuple[int, int]],
    stop_indices: set[int],
) -> list[tuple[int, int]]:
    segments: list[tuple[int, int]] = []
    current_heading: int | None = None
    distance = 0
    for idx, (current, nxt) in enumerate(zip(route, route[1:]), 1):
        heading = _heading_degrees(current, nxt)
        if current_heading is None:
            current_heading = heading
            distance = 1
        elif heading == current_heading:
            distance += 1
        else:
            segments.append((current_heading, distance))
            current_heading = heading
            distance = 1
        if idx in stop_indices:
            segments.append((current_heading, distance))
            current_heading = None
            distance = 0
    if current_heading is not None:
        segments.append((current_heading, distance))
    return segments


def _turn_steps(turn: float) -> list[float]:
    if abs(turn) <= 90:
        return [turn]
    half = turn / 2
    return [half, half]


def _shortest_turn(current: float, desired: float) -> float:
    return (desired - current + 180) % 360 - 180


STRATEGY_BUILDERS: dict[str, StrategyBuilder] = {
    "scripted": _from_spec(_scripted),
    "astar_walk": _from_spec(_astar_walk),
    "scene_probe": _from_spec(_scene_probe),
    "look_scan": _from_spec(_look_scan),
    "grid_patrol": _from_spec(_grid_patrol),
    "random": _from_spec(_random),
    "external": _external,
}
