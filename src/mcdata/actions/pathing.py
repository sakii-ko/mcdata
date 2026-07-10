from __future__ import annotations

import heapq
from typing import Any

Point = tuple[int, int]
Bounds = tuple[int, int, int, int]


def point(value: Any) -> Point:
    return int(value[0]), int(value[1])


def points_in_rect(value: Any) -> set[Point]:
    min_x, min_z, max_x, max_z = (int(value[0]), int(value[1]), int(value[2]), int(value[3]))
    return {(x, z) for x in range(min_x, max_x + 1) for z in range(min_z, max_z + 1)}


def walk_bounds(spec: dict[str, Any]) -> Bounds:
    configured = spec.get("bounds", [-14, 14, -14, 14])
    min_x, max_x, min_z, max_z = (
        int(configured[0]),
        int(configured[1]),
        int(configured[2]),
        int(configured[3]),
    )
    if min_x > max_x or min_z > max_z:
        raise RuntimeError(f"Invalid walk bounds: {(min_x, max_x, min_z, max_z)}")
    return min_x, max_x, min_z, max_z


def walk_blocked(spec: dict[str, Any]) -> set[Point]:
    blocked = {point(item) for item in spec.get("blocked", [])}
    blocked.update(point(item) for item in spec.get("_scene_obstacles", []) or [])
    for rect in spec.get("blocked_rects", []) or []:
        blocked.update(points_in_rect(rect))
    return blocked


def expand_blocked(
    blocked: set[Point],
    *,
    bounds: Bounds,
    clearance: int,
) -> set[Point]:
    if clearance < 0:
        raise RuntimeError("obstacle_clearance must not be negative")
    if clearance == 0:
        return set(blocked)
    min_x, max_x, min_z, max_z = bounds
    return {
        (x + dx, z + dz)
        for x, z in blocked
        for dx in range(-clearance, clearance + 1)
        for dz in range(-clearance, clearance + 1)
        if min_x <= x + dx <= max_x and min_z <= z + dz <= max_z
    }


def inside_bounds(value: Point, bounds: Bounds) -> bool:
    min_x, max_x, min_z, max_z = bounds
    return min_x <= value[0] <= max_x and min_z <= value[1] <= max_z


def manhattan_distance(first: Point, second: Point) -> int:
    return abs(first[0] - second[0]) + abs(first[1] - second[1])


def astar_route(
    start: Point,
    goals: list[Point],
    *,
    bounds: Bounds,
    blocked: set[Point],
) -> list[Point]:
    route = [start]
    cursor = start
    for goal in goals:
        segment = astar(cursor, goal, bounds=bounds, blocked=blocked)
        route.extend(segment[1:])
        cursor = goal
    return route


def astar(
    start: Point,
    goal: Point,
    *,
    bounds: Bounds,
    blocked: set[Point],
) -> list[Point]:
    min_x, max_x, min_z, max_z = bounds
    queue: list[tuple[int, Point]] = [(0, start)]
    came_from: dict[Point, Point | None] = {start: None}
    cost_so_far: dict[Point, int] = {start: 0}
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


def reduce_cardinal_turns(
    route: list[Point],
    *,
    bounds: Bounds,
    blocked: set[Point],
) -> list[Point]:
    """Deterministically string-pull a grid route through safe orthogonal chords."""
    if len(route) < 2:
        return list(route)
    result = [route[0]]
    cursor_index = 0
    while cursor_index < len(route) - 1:
        selection: tuple[int, list[Point]] | None = None
        for candidate_index in range(len(route) - 1, cursor_index, -1):
            for axis_order in ("x_then_z", "z_then_x"):
                candidate = _orthogonal_path(
                    route[cursor_index],
                    route[candidate_index],
                    axis_order=axis_order,
                )
                if all(
                    inside_bounds(point, bounds) and point not in blocked for point in candidate
                ):
                    selection = candidate_index, candidate
                    break
            if selection is not None:
                break
        if selection is None:
            raise RuntimeError("Could not reduce a valid cardinal route")
        cursor_index, chord = selection
        result.extend(chord[1:])
    return result


def _orthogonal_path(start: Point, goal: Point, *, axis_order: str) -> list[Point]:
    route = [start]
    x, z = start
    axes = ("x", "z") if axis_order == "x_then_z" else ("z", "x")
    for axis in axes:
        target = goal[0] if axis == "x" else goal[1]
        current = x if axis == "x" else z
        step = 1 if target > current else -1
        while current != target:
            current += step
            if axis == "x":
                x = current
            else:
                z = current
            route.append((x, z))
    return route
