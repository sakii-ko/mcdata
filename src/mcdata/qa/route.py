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
    points = [{"t": 0.0, "x": route[0][0], "z": route[0][1]}]
    for move, span in zip(move_spans, route_spans):
        start_t, end_t = move
        start_x, start_z = span["start"]
        end_x, end_z = span["end"]
        points.append({"t": start_t, "x": start_x, "z": start_z})
        points.append({"t": end_t, "x": end_x, "z": end_z})
    return _dedupe_track(points)


def interpolate_track(track: list[dict[str, float]], t: float) -> dict[str, float]:
    if not track:
        raise ValueError("cannot interpolate empty track")
    if t <= track[0]["t"]:
        return {"t": t, "x": track[0]["x"], "z": track[0]["z"]}
    if t >= track[-1]["t"]:
        return {"t": t, "x": track[-1]["x"], "z": track[-1]["z"]}
    times = [point["t"] for point in track]
    idx = bisect.bisect_right(times, t) - 1
    left = track[idx]
    right = track[idx + 1]
    span = right["t"] - left["t"]
    if span <= 0:
        return {"t": t, "x": right["x"], "z": right["z"]}
    ratio = (t - left["t"]) / span
    return {
        "t": t,
        "x": left["x"] + (right["x"] - left["x"]) * ratio,
        "z": left["z"] + (right["z"] - left["z"]) * ratio,
    }


def distance_xz(left: dict[str, float], right: dict[str, float]) -> float:
    return math.sqrt((left["x"] - right["x"]) ** 2 + (left["z"] - right["z"]) ** 2)


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


def _route_spans(route: list[tuple[float, float]]) -> list[dict[str, tuple[float, float]]]:
    spans: list[dict[str, tuple[float, float]]] = []
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
                spans.append({"start": current_start, "end": current_end})
            current_start = current
            current_heading = heading
        current_end = nxt
    if current_start is not None and current_end is not None:
        spans.append({"start": current_start, "end": current_end})
    return spans


def _dedupe_track(points: list[dict[str, float]]) -> list[dict[str, float]]:
    result: list[dict[str, float]] = []
    for point in points:
        if result and result[-1] == point:
            continue
        result.append(point)
    return result
