from __future__ import annotations

import bisect
import json
import math
from pathlib import Path
from typing import Any


def route_reference_report(run_dir: Path) -> dict[str, Any] | None:
    positions_path = run_dir / "positions.jsonl"
    trajectory_path = run_dir / "trajectory.json"
    if not positions_path.exists() or not trajectory_path.exists():
        return None
    trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    if not trajectory.get("route"):
        return None
    positions = _read_positions(run_dir)
    if positions is None:
        return None
    try:
        ideal_track = simulate_track(trajectory)
    except ValueError as exc:
        return {"passed": False, "error": str(exc)}
    return check_route_reference(
        positions,
        ideal_track,
        yaw_ignore_windows=_yaw_ignore_windows(trajectory),
    )


def check_route_reference(
    positions: list[dict[str, float]],
    ideal_track: list[dict[str, float]],
    *,
    max_dev: float = 3.0,
    max_yaw_dev_deg: float = 10.0,
    min_y: float = 63.0,
    max_y: float = 66.0,
    yaw_ignore_windows: list[tuple[float, float]] | None = None,
) -> dict[str, Any]:
    samples = []
    yaw_errors: list[float] = []
    missing_yaw_count = 0
    skipped_yaw_count = 0
    yaw_ignore_windows = yaw_ignore_windows or []
    for position in positions:
        t_rel = position.get("t_rel")
        if t_rel is None or t_rel < 0:
            continue
        ideal = interpolate_track(ideal_track, t_rel)
        deviation = distance_xz(position, ideal)
        y = float(position["y"])
        sample = {
            "idx": position.get("idx"),
            "t_rel": t_rel,
            "x": position["x"],
            "y": y,
            "z": position["z"],
            "ideal_x": ideal["x"],
            "ideal_z": ideal["z"],
            "deviation_blocks": deviation,
            "y_in_range": min_y <= y <= max_y,
        }
        if "yaw" in ideal:
            _score_yaw_sample(
                sample,
                position=position,
                ideal=ideal,
                t_rel=t_rel,
                yaw_ignore_windows=yaw_ignore_windows,
                yaw_errors=yaw_errors,
            )
            if sample.get("yaw_skipped"):
                skipped_yaw_count += 1
            elif "yaw_error_deg" not in sample:
                missing_yaw_count += 1
        samples.append(sample)
    return _route_check_result(
        samples,
        yaw_errors=yaw_errors,
        missing_yaw_count=missing_yaw_count,
        skipped_yaw_count=skipped_yaw_count,
        max_dev=max_dev,
        max_yaw_dev_deg=max_yaw_dev_deg,
        min_y=min_y,
        max_y=max_y,
    )


def compare_position_alignment(
    inputs: list[Path],
    *,
    max_threshold_blocks: float = 2.0,
) -> dict[str, Any] | None:
    series = [_read_positions(path) for path in inputs]
    if any(items is None for items in series):
        return None
    assert all(items is not None for items in series)
    pair_results = []
    for left in range(len(series)):
        for right in range(left + 1, len(series)):
            pair_results.append(
                _alignment_pair_result(
                    inputs,
                    series,
                    left=left,
                    right=right,
                    max_threshold_blocks=max_threshold_blocks,
                )
            )
    return _alignment_summary(pair_results, max_threshold_blocks=max_threshold_blocks)


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


def _score_yaw_sample(
    sample: dict[str, float],
    *,
    position: dict[str, float],
    ideal: dict[str, float],
    t_rel: float,
    yaw_ignore_windows: list[tuple[float, float]],
    yaw_errors: list[float],
) -> None:
    sample["ideal_yaw"] = ideal["yaw"]
    if _in_any_window(t_rel, yaw_ignore_windows):
        if "yaw" in position:
            sample["yaw"] = position["yaw"]
            sample["yaw_skipped"] = True
        return
    if "yaw" not in position:
        return
    yaw_error = yaw_error_deg(position["yaw"], ideal["yaw"])
    yaw_errors.append(yaw_error)
    sample.update(
        {
            "yaw": position["yaw"],
            "yaw_error_deg": yaw_error,
        }
    )


def _route_check_result(
    samples: list[dict[str, float]],
    *,
    yaw_errors: list[float],
    missing_yaw_count: int,
    skipped_yaw_count: int,
    max_dev: float,
    max_yaw_dev_deg: float,
    min_y: float,
    max_y: float,
) -> dict[str, Any]:
    deviations = [sample["deviation_blocks"] for sample in samples]
    y_values = [sample["y"] for sample in samples]
    y_out_of_range = sum(1 for sample in samples if not sample["y_in_range"])
    max_deviation = max(deviations) if deviations else None
    max_yaw_error = max(yaw_errors) if yaw_errors else None
    return {
        "threshold_blocks": max_dev,
        "yaw_threshold_degrees": max_yaw_dev_deg,
        "y_min": min_y,
        "y_max": max_y,
        "count": len(samples),
        "max_deviation_blocks": max_deviation,
        "mean_deviation_blocks": sum(deviations) / len(deviations) if deviations else None,
        "max_yaw_error_degrees": max_yaw_error,
        "mean_yaw_error_degrees": sum(yaw_errors) / len(yaw_errors) if yaw_errors else None,
        "yaw_sample_count": len(yaw_errors),
        "missing_yaw_count": missing_yaw_count,
        "skipped_yaw_count": skipped_yaw_count,
        "observed_y_min": min(y_values) if y_values else None,
        "observed_y_max": max(y_values) if y_values else None,
        "y_out_of_range_count": y_out_of_range,
        "passed": (
            max_deviation is not None
            and max_deviation <= max_dev
            and missing_yaw_count == 0
            and (max_yaw_error is None or max_yaw_error <= max_yaw_dev_deg)
            and y_out_of_range == 0
        ),
        "samples": samples,
    }


def _alignment_pair_result(
    inputs: list[Path],
    series: list[list[dict[str, float]] | None],
    *,
    left: int,
    right: int,
    max_threshold_blocks: float,
) -> dict[str, Any]:
    left_items = series[left] or []
    right_items = series[right] or []
    count = min(len(left_items), len(right_items))
    distances = [
        _position_distance(left_items[idx], right_items[idx])
        for idx in range(count)
    ]
    max_distance = max(distances) if distances else None
    mean_distance = sum(distances) / len(distances) if distances else None
    return {
        "left": str(inputs[left]),
        "right": str(inputs[right]),
        "count": count,
        "max_distance_blocks": max_distance,
        "mean_distance_blocks": mean_distance,
        "passed": max_distance is not None and max_distance <= max_threshold_blocks,
    }


def _alignment_summary(
    pair_results: list[dict[str, Any]],
    *,
    max_threshold_blocks: float,
) -> dict[str, Any]:
    overall_max = max(
        (item["max_distance_blocks"] for item in pair_results if item["max_distance_blocks"] is not None),
        default=None,
    )
    total_count = sum(int(item["count"]) for item in pair_results)
    total_distance = sum(
        float(item["mean_distance_blocks"]) * int(item["count"])
        for item in pair_results
        if item["mean_distance_blocks"] is not None
    )
    overall_mean = total_distance / total_count if total_count else None
    return {
        "threshold_blocks": max_threshold_blocks,
        "passed": overall_max is not None and overall_max <= max_threshold_blocks,
        "max_distance_blocks": overall_max,
        "mean_distance_blocks": overall_mean,
        "pairs": pair_results,
    }


def _yaw_ignore_windows(trajectory: dict[str, Any], *, margin_sec: float = 0.5) -> list[tuple[float, float]]:
    windows = []
    for event in trajectory.get("events", []):
        if "mouse_dx" not in event and "mouse_dy" not in event:
            continue
        start = float(event.get("t", 0.0))
        duration = float(event.get("duration", 0.0) or 0.0)
        windows.append((start - margin_sec, start + duration + margin_sec))
    return windows


def _in_any_window(value: float, windows: list[tuple[float, float]]) -> bool:
    return any(start <= value <= end for start, end in windows)


def _read_positions(input_path: Path) -> list[dict[str, float]] | None:
    path = input_path / "positions.jsonl" if input_path.is_dir() else input_path.parent / "positions.jsonl"
    if not path.exists():
        return None
    items: list[dict[str, float]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        item = {"x": float(row["x"]), "y": float(row["y"]), "z": float(row["z"])}
        if "idx" in row:
            item["idx"] = int(row["idx"])
        if "t_rel" in row:
            item["t_rel"] = float(row["t_rel"])
        if "yaw" in row:
            item["yaw"] = float(row["yaw"])
        items.append(item)
    return items


def _position_distance(left: dict[str, float], right: dict[str, float]) -> float:
    return math.sqrt(
        (left["x"] - right["x"]) ** 2
        + (left["y"] - right["y"]) ** 2
        + (left["z"] - right["z"]) ** 2
    )


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
