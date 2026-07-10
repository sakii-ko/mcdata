from __future__ import annotations

from typing import Any


def trajectory_route_stop_indices(
    trajectory: dict[str, Any], *, route_length: int
) -> set[int]:
    values = [
        event["route_index"]
        for event in trajectory.get("events", [])
        if isinstance(event, dict) and "route_index" in event
    ]
    invalid = [
        value
        for value in values
        if isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value < route_length
    ]
    if invalid:
        raise ValueError(f"trajectory event has invalid route_index: {invalid[0]!r}")
    return {int(value) for value in values}
