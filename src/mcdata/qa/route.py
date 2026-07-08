from __future__ import annotations

import bisect
import math
from typing import Any


def simulate_track(trajectory: dict[str, Any]) -> list[dict[str, float]]:
    route = _route_points(trajectory)
    move_spans = _forward_spans(trajectory)
    route_spans = _route_spans(route)
    if len(move_spans) != len(route_spans):
        raise ValueError(
            "trajectory route span count does not match forward movement count: "
            f"{len(route_spans)} route spans vs {len(move_spans)} movement spans"
        )
    if not route:
        return []
    points = [{"t": 0.0, "x": route[0][0], "z": route[0][1], "yaw": 0.0}]
    turn_events = _turn_events(trajectory)
    turn_idx = 0
    current_yaw = 0.0
    current_position = route[0]
    previous_move_end = 0.0
    for move, span in zip(move_spans, route_spans):
        start_t, end_t = move
        start_x, start_z = span["start"]
        end_x, end_z = span["end"]
        desired_yaw = span["yaw"]
        relevant_turns: list[dict[str, float]] = []
        while turn_idx < len(turn_events) and turn_events[turn_idx]["end"] <= previous_move_end:
            turn_idx += 1
        scan_idx = turn_idx
        while scan_idx < len(turn_events) and turn_events[scan_idx]["t"] <= start_t:
            if turn_events[scan_idx]["end"] >= previous_move_end:
                relevant_turns.append(turn_events[scan_idx])
            scan_idx += 1
        turn_idx = scan_idx
        current_yaw = _append_turn_points(
            points,
            current_position,
            current_yaw=current_yaw,
            desired_yaw=desired_yaw,
            turn_events=relevant_turns,
        )
        points.append({"t": start_t, "x": start_x, "z": start_z, "yaw": desired_yaw})
        points.append({"t": end_t, "x": end_x, "z": end_z, "yaw": desired_yaw})
        current_yaw = desired_yaw
        current_position = (end_x, end_z)
        previous_move_end = end_t
    return _dedupe_track(points)


def interpolate_track(track: list[dict[str, float]], t: float) -> dict[str, float]:
    if not track:
        raise ValueError("cannot interpolate empty track")
    if t <= track[0]["t"]:
        return _track_point_at_time(track[0], t)
    if t >= track[-1]["t"]:
        return _track_point_at_time(track[-1], t)
    times = [point["t"] for point in track]
    idx = bisect.bisect_right(times, t) - 1
    left = track[idx]
    right = track[idx + 1]
    span = right["t"] - left["t"]
    if span <= 0:
        return _track_point_at_time(right, t)
    ratio = (t - left["t"]) / span
    result = {
        "t": t,
        "x": left["x"] + (right["x"] - left["x"]) * ratio,
        "z": left["z"] + (right["z"] - left["z"]) * ratio,
    }
    if "yaw" in left and "yaw" in right:
        result["yaw"] = interpolate_yaw(left["yaw"], right["yaw"], ratio)
    return result


def distance_xz(left: dict[str, float], right: dict[str, float]) -> float:
    return math.sqrt((left["x"] - right["x"]) ** 2 + (left["z"] - right["z"]) ** 2)


def yaw_error_deg(left: float, right: float) -> float:
    return abs(_angular_delta(right, left))


def interpolate_yaw(left: float, right: float, ratio: float) -> float:
    return _normalize_yaw(left + _angular_delta(left, right) * ratio)


def _route_points(trajectory: dict[str, Any]) -> list[tuple[float, float]]:
    return [
        (float(point["x"]), float(point["z"]))
        for point in trajectory.get("route", [])
    ]


def _forward_spans(trajectory: dict[str, Any]) -> list[tuple[float, float]]:
    spans: list[tuple[float, float]] = []
    down_at: float | None = None
    for event in sorted(trajectory.get("events", []), key=lambda item: float(item.get("t", 0))):
        if event.get("key") != "w":
            continue
        action = event.get("action")
        if action == "down":
            down_at = float(event["t"])
        elif action == "up" and down_at is not None:
            spans.append((down_at, float(event["t"])))
            down_at = None
    if down_at is not None:
        raise ValueError("trajectory has an unclosed forward movement span")
    return spans


def _turn_events(trajectory: dict[str, Any]) -> list[dict[str, float]]:
    events: list[dict[str, float]] = []
    for event in sorted(trajectory.get("events", []), key=lambda item: float(item.get("t", 0))):
        mouse_dx = float(event.get("mouse_dx", 0) or 0)
        if abs(mouse_dx) <= 0:
            continue
        start = float(event.get("t", 0))
        events.append(
            {
                "t": start,
                "end": start + float(event.get("duration", 0) or 0),
                "mouse_dx": mouse_dx,
            }
        )
    return events


def _route_spans(route: list[tuple[float, float]]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    current_start: tuple[float, float] | None = None
    current_end: tuple[float, float] | None = None
    current_heading: tuple[float, float] | None = None
    for current, nxt in zip(route, route[1:]):
        heading = (nxt[0] - current[0], nxt[1] - current[1])
        if current_heading is None:
            current_start = current
            current_heading = heading
        elif heading != current_heading:
            if current_start is not None and current_end is not None:
                spans.append(
                    {
                        "start": current_start,
                        "end": current_end,
                        "yaw": _yaw_from_heading(current_heading),
                    }
                )
            current_start = current
            current_heading = heading
        current_end = nxt
    if current_start is not None and current_end is not None:
        spans.append(
            {
                "start": current_start,
                "end": current_end,
                "yaw": _yaw_from_heading(current_heading or (0.0, 1.0)),
            }
        )
    return spans


def _append_turn_points(
    points: list[dict[str, float]],
    position: tuple[float, float],
    *,
    current_yaw: float,
    desired_yaw: float,
    turn_events: list[dict[str, float]],
) -> float:
    if not turn_events:
        return desired_yaw
    total_dx = sum(event["mouse_dx"] for event in turn_events)
    if abs(total_dx) <= 0:
        return desired_yaw
    total_turn = _angular_delta(current_yaw, desired_yaw)
    yaw = current_yaw
    x, z = position
    for event in turn_events:
        points.append({"t": event["t"], "x": x, "z": z, "yaw": yaw})
        yaw = _normalize_yaw(yaw + total_turn * (event["mouse_dx"] / total_dx))
        points.append({"t": event["end"], "x": x, "z": z, "yaw": yaw})
    return desired_yaw


def _track_point_at_time(point: dict[str, float], t: float) -> dict[str, float]:
    result = {"t": t, "x": point["x"], "z": point["z"]}
    if "yaw" in point:
        result["yaw"] = point["yaw"]
    return result


def _yaw_from_heading(heading: tuple[float, float]) -> float:
    dx, dz = heading
    return _normalize_yaw(math.degrees(math.atan2(-dx, dz)))


def _angular_delta(left: float, right: float) -> float:
    return (right - left + 180.0) % 360.0 - 180.0


def _normalize_yaw(value: float) -> float:
    normalized = (value + 180.0) % 360.0 - 180.0
    if normalized == -180.0:
        return 180.0
    return normalized


def _dedupe_track(points: list[dict[str, float]]) -> list[dict[str, float]]:
    result: list[dict[str, float]] = []
    for point in points:
        if result and result[-1] == point:
            continue
        result.append(point)
    return result
